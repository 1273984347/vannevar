// core/resume.js — Resume 摘要引擎（灵感场景）
// 基于 inspirations 列表生成摘要：氛围分布、热门来源、关键词、时间分布
// 优先尝试 AI 增强（若已在 options 配置 API key）；未配置或失败时降级为纯规则引擎。
// 参照 focus-mvp/resume.js，适配灵感数据结构（title/url/note/mood/selectedText/createdAt）

import { generateAISummary } from './ai.js';

// 从 URL 提取域名（去除 www 等前缀）
export function extractDomain(url) {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

// 关键词提取的停用词（英文虚词 + 中文虚词 + 常见页面噪声）
const STOP_WORDS = new Set([
  'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by',
  'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
  'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can', 'this', 'that', 'these', 'those',
  'i', 'you', 'he', 'she', 'it', 'we', 'they', 'what', 'which', 'who', 'when', 'where', 'why', 'how',
  '的', '了', '是', '在', '我', '你', '他', '她', '它', '们', '和', '与', '或', '但', '为', '对',
  '从', '到', '向', '上', '下', '中', '这', '那', '一', '个', '些', '有', '无', '不', '也', '都', '就',
  'page', 'home', 'search', 'results', 'loading', 'error', '404', '500',
  '-', '|', '·', '—', '–', ':', '【', '】', '(', ')', '[', ']',
  // M-LOW-01: 1-2 字母无意义词（保留技术缩写如 AI/ML/GO/API/UX/UI 等）
  'en', 'de', 'es', 'fr', 'it', 'pt', 'ru', 'ja', 'ko', 'zh',
  'no', 'so', 'if', 'as', 'at', 'by', 'up', 'me', 'my', 'we', 'us',
  'am', 'pm', 'ok',
]);

// 中文 N-gram 滑动切分：对每个连续中文段落生成 2~max 字符的所有子串。
// M-LOW-01: 原简单正则 /[\u4e00-\u9fa5]{2,4}/g 贪婪匹配会丢失中间位置的 2-gram，
// 改为 N-gram 滑动窗口后所有相邻组合都能进入频率统计，关键词提取更稳定。
function extractChineseNGrams(text, max = 3) {
  const segments = text.match(/[\u4e00-\u9fa5]+/g) || [];
  const ngrams = [];
  for (const segment of segments) {
    const upper = Math.min(max, segment.length);
    for (let n = 2; n <= upper; n++) {
      for (let i = 0; i <= segment.length - n; i++) {
        ngrams.push(segment.substring(i, i + n));
      }
    }
  }
  return ngrams;
}

// 关键词提取：合并 title / note / selectedText / 来源域名，按词频取 top N
function extractKeywords(inspirations, max = 8) {
  const freq = new Map();
  for (const item of inspirations) {
    const text = `${item.title || ''} ${item.note || ''} ${item.selectedText || ''} ${extractDomain(item.url)}`;
    // 英文词（{1,30} 保留 2 字母技术缩写如 AI/ML/GO/API/UX/UI）
    const enWords = text.toLowerCase().match(/[a-z][a-z0-9_-]{1,30}/g) || [];
    // 中文 N-gram（2~3 字），覆盖所有相邻组合
    const cnWords = extractChineseNGrams(text, 3);
    for (const w of [...enWords, ...cnWords]) {
      if (STOP_WORDS.has(w) || w.length < 2) continue;
      freq.set(w, (freq.get(w) || 0) + 1);
    }
  }
  return [...freq.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, max)
    .map(([word]) => word);
}

// 时间段分组：今天 / 昨天 / 本周 / 更早
function getTimeDistribution(inspirations) {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startOfYesterday = startOfToday - 86400000;
  const startOfWeek = startOfToday - 6 * 86400000; // 今天起向前 7 天内归入"本周"
  const dist = { today: 0, yesterday: 0, thisWeek: 0, earlier: 0 };
  for (const item of inspirations) {
    const t = item.createdAt;
    if (!Number.isFinite(t)) { dist.earlier++; continue; }
    if (t >= startOfToday) dist.today++;
    else if (t >= startOfYesterday) dist.yesterday++;
    else if (t >= startOfWeek) dist.thisWeek++;
    else dist.earlier++;
  }
  return dist;
}

// 生成灵感 Resume 摘要（异步）
// 优先尝试 AI 增强；未配置 / 失败时降级到规则引擎，保持原有返回结构。
export async function generateInspirationResume(inspirations) {
  if (!Array.isArray(inspirations) || inspirations.length === 0) {
    return {
      summary: '还没有灵感记录，开始捕捉你的第一份灵感吧。',
      total: 0,
      moodDistribution: [],
      topSites: [],
      keywords: [],
      timeDistribution: { today: 0, yesterday: 0, thisWeek: 0, earlier: 0 },
      suggestions: []
    };
  }

  // 先尝试 AI 增强
  const aiResult = await generateAISummary(inspirations);
  if (aiResult) {
    // 在规则引擎统计字段的基础上，用 AI 内容覆盖 summary / suggestions
    // 保持返回结构向后兼容，额外标注 aiEnhanced 与 theme / moods / recommendedMoods
    const base = ruleEngineInspirationResume(inspirations);
    return {
      ...base,
      summary: typeof aiResult.summary === 'string' && aiResult.summary.trim()
        ? aiResult.summary.trim()
        : base.summary,
      suggestions: Array.isArray(aiResult.suggestions) && aiResult.suggestions.length
        ? aiResult.suggestions.slice(0, 4)
        : base.suggestions,
      theme: typeof aiResult.theme === 'string' ? aiResult.theme.trim() : '',
      moods: Array.isArray(aiResult.moods) ? aiResult.moods : [],
      recommendedMoods: Array.isArray(aiResult.recommendedMoods) ? aiResult.recommendedMoods.slice(0, 5) : [],
      aiEnhanced: true
    };
  }

  // 降级到规则引擎（保留原有逻辑）
  return ruleEngineInspirationResume(inspirations);
}

// 规则引擎：纯本地统计，无外部依赖
function ruleEngineInspirationResume(inspirations) {
  const total = inspirations.length;

  // 1. 氛围分布
  const moodMap = new Map();
  for (const item of inspirations) {
    const mood = item.mood && item.mood.trim() ? item.mood : '未标注';
    moodMap.set(mood, (moodMap.get(mood) || 0) + 1);
  }
  const moodDistribution = [...moodMap.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([mood, count]) => ({ mood, count }));

  // 2. 来源域名 topSites
  const domainMap = new Map();
  for (const item of inspirations) {
    const domain = extractDomain(item.url);
    if (!domain) continue;
    domainMap.set(domain, (domainMap.get(domain) || 0) + 1);
  }
  const topSites = [...domainMap.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([domain, count]) => ({ domain, count }));

  // 3. 关键词提取
  const keywords = extractKeywords(inspirations);

  // 4. 时间段分布
  const timeDistribution = getTimeDistribution(inspirations);

  // 5. 生成摘要文本
  const topMoodText = moodDistribution.slice(0, 3).map((m) => `${m.mood}(${m.count})`).join('、');
  const topSiteText = topSites.slice(0, 3).map((s) => s.domain).join('、');
  const recentCount = timeDistribution.today + timeDistribution.yesterday + timeDistribution.thisWeek;
  const summary = `共有 ${total} 条灵感，氛围分布：${topMoodText || '未标注'}。`
    + `近 7 天新增 ${recentCount} 条，高频来源：${topSiteText || '暂无'}。`;

  // 6. 下一步建议
  const suggestions = generateSuggestions(moodDistribution, topSites, timeDistribution, keywords, total);

  return {
    summary,
    total,
    moodDistribution,
    topSites,
    keywords,
    timeDistribution,
    suggestions
  };
}

// 基于统计生成下一步建议
function generateSuggestions(moodDistribution, topSites, timeDistribution, keywords, total) {
  const suggestions = [];

  // 氛围建议：本周内某氛围占比高 → 整理成 moodboard
  const topMood = moodDistribution[0];
  if (topMood && topMood.mood !== '未标注' && topMood.count >= 3) {
    const recentCount = timeDistribution.today + timeDistribution.yesterday + timeDistribution.thisWeek;
    if (recentCount >= topMood.count * 0.5) {
      suggestions.push(`📌 本周「${topMood.mood}」类灵感最多（${topMood.count} 条），试试整理成 moodboard`);
    }
  }

  // 来源建议：某域名灵感密集 → 可作为长期关注源
  if (topSites.length > 0 && topSites[0].count >= 3) {
    suggestions.push(`📌 ${topSites[0].domain} 贡献了 ${topSites[0].count} 条灵感，可作为长期灵感来源`);
  }

  // 时间分布建议
  if (timeDistribution.today >= 3) {
    suggestions.push('📌 今天已捕捉多条灵感，趁记忆鲜活补充备注或氛围标签');
  } else if (timeDistribution.earlier >= total * 0.6 && total >= 5) {
    suggestions.push('📌 多数灵感是较早记录的，可以回顾并归档整理一次');
  }

  // 关键词建议
  if (keywords.length >= 3) {
    suggestions.push(`📌 高频关键词：${keywords.slice(0, 3).join('、')}，围绕它们做一次主题串联`);
  }

  // 通用建议
  if (suggestions.length === 0) {
    suggestions.push('📌 继续捕捉灵感，积累到一定数量后回顾整理');
  }

  if (suggestions.length > 4) suggestions.length = 4;
  return suggestions;
}
