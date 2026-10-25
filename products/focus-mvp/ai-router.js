// ai-router.js — 阶段2.9 AI 调用统一层（三级降级：cloud → local → rule）
// 三端 AI 调用收敛为统一接口，支持云端/本地/规则引擎三级降级。
// 设计原则：
//   1. prefer 指定优先级（'cloud' | 'local' | 'rule'）
//   2. 失败降级到下一级
//   3. 最终降级到规则引擎（永不失败）
//
// 用法：
//   import { aiComplete } from './ai-router.js';
//   const result = await aiComplete({
//     context: { tabs: [...] },           // 上下文数据
//     prefer: 'cloud',                    // 优先级
//     onChunk: (text) => { ... },         // 可选：流式回调
//     cloudCall: (ctx, onChunk) => ...,   // 云端调用函数（ai.js 的 generateAISummaryStream）
//     localCall: (ctx, onChunk) => ...,   // 本地调用函数（local-model.js 的 generateLocalSuggestionStream）
//     ruleCall: (ctx) => ...,             // 规则引擎兜底（resume.js 的 ruleEngineResume，由调用方注入）
//   });

const FALLBACK_ORDER = {
  cloud: ['cloud', 'local', 'rule'],
  local: ['local', 'cloud', 'rule'],
  rule: ['rule'],
};

/**
 * 统一 AI 调用接口（三级降级）
 * @param {Object} options
 * @param {*} options.context - 上下文数据（tabs / inspirations 等）
 * @param {string} options.prefer - 优先级：'cloud' | 'local' | 'rule'
 * @param {Function} [options.onChunk] - 流式回调（text 增量）
 * @param {Function} [options.cloudCall] - 云端调用 (ctx, onChunk) => result | null
 * @param {Function} [options.localCall] - 本地调用 (ctx, onChunk) => result | null
 * @param {Function} [options.ruleCall] - 规则引擎兜底 (ctx) => result
 * @returns {Promise<{result: *, source: string, attempts: string[]}>}
 *   - result: 最终结果
 *   - source: 'cloud' | 'local' | 'rule'（实际成功使用的层级）
 *   - attempts: 尝试过的层级列表
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
        // 超时特殊标志：不算成功，继续降级
        if (result && result._timedOut) {
          lastError = new Error('云端 AI 超时');
          console.warn('[ai-router] 云端 AI 超时，降级到下一级');
          continue;
        }
        if (result !== null && result !== undefined) {
          return { result, source: 'cloud', attempts };
        }
        // result 为 null：配置缺失或调用失败，继续降级
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
        // 规则引擎永不失败（纯本地统计）
        const result = ruleCall(context);
        return { result, source: 'rule', attempts };
      }
    } catch (err) {
      lastError = err;
      console.warn(`[ai-router] ${tier} 层失败，降级到下一级:`, err.message);
    }
  }

  // 所有层级都失败：返回空结果（理论上 rule 层不应失败）
  console.error('[ai-router] 所有 AI 层级均失败，返回空结果', lastError);
  return { result: null, source: 'none', attempts };
}

/**
 * 检查云端 AI 是否可用（配置存在）
 * @returns {Promise<boolean>}
 */
export async function isCloudAvailable() {
  try {
    const { hasAIConfig } = await import('./ai.js');
    return await hasAIConfig();
  } catch (_) {
    return false;
  }
}

/**
 * 检查本地模型是否可用（配置启用 + WebGPU 可用）
 * @returns {Promise<boolean>}
 */
export async function isLocalAvailable() {
  try {
    const { isLocalModelAvailable } = await import('./local-model.js');
    return await isLocalModelAvailable();
  } catch (_) {
    return false;
  }
}

/**
 * 获取当前 AI 可用层级（供 UI 显示降级状态）
 * @returns {Promise<{cloud: boolean, local: boolean, rule: boolean}>}
 */
export async function getAvailableTiers() {
  const [cloud, local] = await Promise.all([
    isCloudAvailable(),
    isLocalAvailable(),
  ]);
  return { cloud, local, rule: true };
}
