// core/ai-router.js — 阶段2.9 AI 调用统一层（Muse 版，三级降级：cloud → local → rule）
// 三端 AI 调用收敛为统一接口，支持云端/本地/规则引擎三级降级。
// 与 Focus 的 ai-router.js 逻辑一致，仅 import 路径不同（./ai.js + ./local-model.js）。

const FALLBACK_ORDER = {
  cloud: ['cloud', 'local', 'rule'],
  local: ['local', 'cloud', 'rule'],
  rule: ['rule'],
};

/**
 * 统一 AI 调用接口（三级降级）
 * @param {Object} options
 * @param {*} options.context - 上下文数据（inspirations 等）
 * @param {string} options.prefer - 优先级：'cloud' | 'local' | 'rule'
 * @param {Function} [options.onChunk] - 流式回调（text 增量）
 * @param {Function} [options.cloudCall] - 云端调用 (ctx, onChunk) => result | null
 * @param {Function} [options.localCall] - 本地调用 (ctx, onChunk) => result | null
 * @param {Function} [options.ruleCall] - 规则引擎兜底 (ctx) => result
 * @returns {Promise<{result: *, source: string, attempts: string[]}>}
 */
export async function aiComplete({ context, prefer = 'cloud', onChunk, cloudCall, localCall, ruleCall }) {
  const order = FALLBACK_ORDER[prefer] || FALLBACK_ORDER.cloud;
  const attempts = [];
  let lastError = null;

  for (const tier of order) {
    attempts.push(tier);
    try {
      if (tier === 'cloud' && typeof cloudCall === 'function') {
        const result = await cloudCall(context, onChunk);
        if (result && result._timedOut) {
          lastError = new Error('云端 AI 超时');
          console.warn('[ai-router] 云端 AI 超时，降级到下一级');
          continue;
        }
        if (result !== null && result !== undefined) {
          return { result, source: 'cloud', attempts };
        }
        lastError = new Error('云端 AI 返回空');
        continue;
      }
      if (tier === 'local' && typeof localCall === 'function') {
        const result = await localCall(context, onChunk);
        if (result !== null && result !== undefined) {
          return { result, source: 'local', attempts };
        }
        lastError = new Error('本地模型返回空');
        continue;
      }
      if (tier === 'rule' && typeof ruleCall === 'function') {
        const result = ruleCall(context);
        return { result, source: 'rule', attempts };
      }
    } catch (err) {
      lastError = err;
      console.warn(`[ai-router] ${tier} 层失败，降级到下一级:`, err.message);
    }
  }

  console.error('[ai-router] 所有 AI 层级均失败，返回空结果', lastError);
  return { result: null, source: 'none', attempts };
}

export async function isCloudAvailable() {
  try {
    const { hasAIConfig } = await import('./ai.js');
    return await hasAIConfig();
  } catch (_) {
    return false;
  }
}

export async function isLocalAvailable() {
  try {
    const { isLocalModelAvailable } = await import('./local-model.js');
    return await isLocalModelAvailable();
  } catch (_) {
    return false;
  }
}

export async function getAvailableTiers() {
  const [cloud, local] = await Promise.all([
    isCloudAvailable(),
    isLocalAvailable(),
  ]);
  return { cloud, local, rule: true };
}
