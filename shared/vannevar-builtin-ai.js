// vannevar-builtin-ai.js — Chrome 内置 AI（端侧 Prompt API）适配层（T-10）
//
// 共享层模块：Focus（lib/ 副本）与 Muse（shared/ 副本）字节一致，
// 副本一致性由 scripts/check_shared_sync.py 的 STRICT 组强制（漂移即阻断）。
//
// 定位（降级链中的「端侧层」）：
//   1. 用户显式配置的云端 OpenAI 兼容 API 优先（尊重显式选择）；
//   2. 未配置云端时 → 端侧 AI（零配置、零上传，模型由 Chrome 管理）；
//   3. 端侧不可用 / 失败 → 返回 null → 调用方降级到本地规则引擎。
//
// 可用性判定（保守策略）：
//   - 仅 availability() === 'available'（兼容旧实现的 'readily'）视为可用；
//   - 'downloadable' / 'downloading' 不视为可用 —— 不静默触发数百 MB 的模型下载，
//     下载属用户显式决策，交由 Chrome 自身 UI 引导；
//   - 任何异常一律按「不可用」处理，绝不阻断调用方主流程。
//
// 会话生命周期：进程内懒加载单例；prompt 带 AbortController 超时（默认 30s，
// 与 ai.js 云端请求超时一致）。

const DEFAULT_TIMEOUT_MS = 30000;

function _getLanguageModel() {
  try {
    if (typeof self !== 'undefined' && self && self.LanguageModel) return self.LanguageModel;
  } catch (_) { /* ignore */ }
  try {
    if (typeof globalThis !== 'undefined' && globalThis && globalThis.LanguageModel) return globalThis.LanguageModel;
  } catch (_) { /* ignore */ }
  return null;
}

export function createBuiltinAI({ timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  let _session = null;

  // 返回原始可用性字符串：'available' | 'readily' | 'downloadable' | 'downloading' | 'unavailable' | 'no'
  async function availability() {
    const LM = _getLanguageModel();
    if (!LM || typeof LM.availability !== 'function') return 'no';
    try {
      const a = await LM.availability();
      return typeof a === 'string' ? a : 'no';
    } catch (_) {
      return 'no';
    }
  }

  // 可用 = 模型已在设备上就绪；downloadable/downloading 不算（不静默下载）
  async function isAvailable() {
    const a = await availability();
    return a === 'available' || a === 'readily';
  }

  async function _getSession() {
    const LM = _getLanguageModel();
    if (!LM || typeof LM.create !== 'function') return null;
    if (_session) return _session;
    _session = await LM.create();
    return _session;
  }

  // 单轮 prompt；返回文本或 null（不可用 / 超时 / 任何异常均静默降级）
  async function prompt(text, { timeout = timeoutMs } = {}) {
    if (!text || typeof text !== 'string') return null;
    if (!(await isAvailable())) return null;
    try {
      const session = await _getSession();
      if (!session || typeof session.prompt !== 'function') return null;
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeout);
      try {
        const out = await session.prompt(text, { signal: ctrl.signal });
        return (typeof out === 'string' && out.trim()) ? out.trim() : null;
      } finally {
        clearTimeout(timer);
      }
    } catch (_) {
      return null;
    }
  }

  return { availability, isAvailable, prompt };
}
