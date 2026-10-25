// local-model.js — 阶段2.8 端侧本地模型（WebLLM 实验性）
// 隐私保护：数据不出设备，本地推理生成轻量建议。
// 限制：
//   - WebGPU 仅在 Chrome 113+ 可用，Safari/Firefox 部分支持
//   - SW 中无法使用 WebGPU，本模块只能在 popup/options 页面执行
//   - popup 关闭会中断推理；建议在 options 页（可独立窗口）运行
//   - 首次加载约 600MB 模型，按需加载 + 进度条提示

const STORAGE_KEY_LOCAL_MODEL = 'vannevar_local_model_config';

// 默认模型：Llama-3.2-1B（约 600MB，平衡速度与质量）
const DEFAULT_MODEL_ID = 'Llama-3.2-1B-Instruct-q4f32_1-MLC';

// 推理引擎单例（避免重复加载模型）
let _engine = null;
let _loadingPromise = null;

// 读取本地模型配置
export async function getLocalModelConfig() {
  const result = await chrome.storage.local.get(STORAGE_KEY_LOCAL_MODEL);
  return result[STORAGE_KEY_LOCAL_MODEL] || { enabled: false, modelId: DEFAULT_MODEL_ID };
}

// 写入本地模型配置
export async function setLocalModelConfig(config) {
  await chrome.storage.local.set({ [STORAGE_KEY_LOCAL_MODEL]: config });
}

// 检测 WebGPU 是否可用（navigator.gpu 仅在支持的浏览器存在）
export function isWebGPUAvailable() {
  return typeof navigator !== 'undefined' && !!navigator.gpu;
}

// 检测本地模型是否可用（配置启用 + WebGPU 可用）
export async function isLocalModelAvailable() {
  const config = await getLocalModelConfig();
  if (!config.enabled) return false;
  return isWebGPUAvailable();
}

// 动态加载 WebLLM 引擎 + 模型
// onProgress(0~1): 加载进度回调，供 UI 显示进度条
// 返回 WebLLM engine 实例；失败抛错
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

// 释放本地模型引擎（释放显存）
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

// 本地推理生成轻量建议
// 输入：上下文文本（如标签页摘要 / 灵感摘要）
// 输出：建议文本；失败返回 null，调用方降级到规则引擎
// 注意：调用方需确保在 popup/options 页面执行，且用户保持窗口打开
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
      { role: 'system', content: '你是项目协调助手，用简洁中文给出可执行建议（100字以内）。' },
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

// 本地推理流式输出（阶段2.8 与 2.7 流式打通）
// onChunk(text): 每收到增量调用一次
// 返回完整文本；失败返回 null
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
      { role: 'system', content: '你是项目协调助手，用简洁中文给出可执行建议（100字以内）。' },
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
