// shared/moderation.js — Muse 内容审核：最小化敏感词检测
// 策略：不阻止操作，只在检测到敏感词时返回 warning，让 UI 显示提示

// 最小化敏感词集（演示用，与 Bridge moderation.py 对齐）
const SENSITIVE_WORDS = {
  violence: ['杀人', '炸弹', '恐怖袭击'],
  spam: ['加微信', '免费领取', '点击链接', '中奖了'],
};

/**
 * 检测文本是否包含敏感词
 * @param {string} text - 待检测文本
 * @returns {{flagged: boolean, words: string[], warning: string}}
 */
export function moderateText(text) {
  if (!text || typeof text !== 'string' || !text.trim()) {
    return { flagged: false, words: [], warning: '' };
  }

  const hitWords = [];
  for (const category of Object.keys(SENSITIVE_WORDS)) {
    for (const w of SENSITIVE_WORDS[category]) {
      if (text.includes(w)) {
        hitWords.push(w);
      }
    }
  }

  if (hitWords.length === 0) {
    return { flagged: false, words: [], warning: '' };
  }

  return {
    flagged: true,
    words: [...new Set(hitWords)],
    warning: `内容包含敏感词（${[...new Set(hitWords)].join('、')}），导出后分享时请注意。`
  };
}
