// core/local-model.js — 阶段2.8 端侧本地模型（WebLLM 实验性，Muse 版）
// 隐私保护：数据不出设备，本地推理生成轻量灵感建议。
// 限制同 Focus 版：WebGPU Chrome 113+、仅 popup/options、popup 关闭中断推理。

const STORAGE_KEY_LOCAL_MODEL = 'vannevar_local_model_config';

// 默认模型：Llama-3.2-1B（约 600MB）
const DEFAULT_MODEL_ID = 'Llama-3.2-1B-Instruct-q4f32_1-MLC';

let _engine = null;
let _loadingPromise = null;

export async function getLocalModelConfig() {
  const result = await chrome.storage.local.get(STORAGE_KEY_LOCAL_MODEL);
  return result[STORAGE_KEY_LOCAL_MODEL] || { enabled: false, modelId: DEFAULT_MODEL_ID };
}

export async function setLocalModelConfig(config) {
  await chrome.storage.local.set({ [STORAGE_KEY_LOCAL_MODEL]: config });
}

export function isWebGPUAvailable() {
  return typeof navigator !== 'undefined' && !!navigator.gpu;
}

export async function isLocalModelAvailable() {
  const config = await getLocalModelConfig();
  if (!config.enabled) return false;
  return isWebGPUAvailable();
}

export async function loadLocalModel(onProgress) {
  if (_engine) return _engine;
  if (_loadingPromise) return _loadingPromise;

  if (!isWebGPUAvailable()) {
    throw new Error('WebGPU 不可用，请使用 Chrome 113+ 或启用 WebGPU 的浏览器');
  }

  const config = await getLocalModelConfig();
  const modelId = config.modelId || DEFAULT_MODEL_ID;

  _loadingPromise = (async () => {
    try {
      // V1 修复：MV3 CSP 阻止远程脚本加载，await import('https://esm.run/...') 必然失败。
      // 当前版本未将 WebLLM 库打包进扩展，端侧模型不可用。未来打包后可改为本地 import。
      let webllm;
      try {
        webllm = await import('https://esm.run/@mlc-ai/web-llm@0.2.78');
      } catch (_) {
        throw new Error('WebLLM 库未打包进扩展，端侧模型不可用。请改用云端 AI 或规则引擎。');
      }
      const engine = await webllm.CreateMLCEngine(
        modelId,
        { initProgressCallback: (report) => {
          if (onProgress && typeof report.progress === 'number') {
            onProgress(report.progress);
          }
        }}
      );
      _engine = engine;
      return engine;
    } finally {
      _loadingPromise = null;
    }
  })();

  return _loadingPromise;
}

export async function unloadLocalModel() {
  if (_engine) {
    try {
      await _engine.unload();
    } catch (_) {
      // 忽略卸载错误
    }
    _engine = null;
  }
}

// Muse 版：灵感场景的轻量建议
export async function generateLocalSuggestion(context, options = {}) {
  const { maxTokens = 200, temperature = 0.7 } = options;

  let engine;
  try {
    engine = await loadLocalModel();
  } catch (err) {
    console.warn('[local-model] 模型加载失败，降级到规则引擎:', err.message);
    return null;
  }

  try {
    const messages = [
      { role: 'system', content: '你是善于洞察的阅读者与策展人，用简洁中文给出灵感整理建议（100字以内）。' },
      { role: 'user', content: context },
    ];
    const reply = await engine.chat.completions.create({
      messages,
      max_tokens: maxTokens,
      temperature,
    });
    const content = reply.choices?.[0]?.message?.content;
    if (!content || !content.trim()) return null;
    return content.trim();
  } catch (err) {
    console.warn('[local-model] 推理失败，降级到规则引擎:', err.message);
    return null;
  }
}

export async function generateLocalSuggestionStream(context, onChunk, options = {}) {
  const { maxTokens = 200, temperature = 0.7 } = options;

  let engine;
  try {
    engine = await loadLocalModel();
  } catch (err) {
    console.warn('[local-model] 模型加载失败:', err.message);
    return null;
  }

  try {
    const messages = [
      { role: 'system', content: '你是善于洞察的阅读者与策展人，用简洁中文给出灵感整理建议（100字以内）。' },
      { role: 'user', content: context },
    ];
    const chunks = await engine.chat.completions.create({
      messages,
      max_tokens: maxTokens,
      temperature,
      stream: true,
    });
    let fullText = '';
    for await (const chunk of chunks) {
      const delta = chunk.choices?.[0]?.delta?.content;
      if (delta) {
        fullText += delta;
        onChunk(delta);
      }
    }
    return fullText || null;
  } catch (err) {
    console.warn('[local-model] 流式推理失败:', err.message);
    return null;
  }
}
