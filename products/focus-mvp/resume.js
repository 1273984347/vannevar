// resume.js — Resume 摘要引擎
// 基于快照标签页信息生成 session 摘要：场景识别、关键内容、下一步建议
// 优先尝试 AI 增强（若已在 options 配置 API key）；未配置或失败时降级为纯规则引擎。

import { generateAISummary } from './ai.js';

// 场景识别规则：域名/URL 模式 → 场景标签
const SCENE_RULES = [
  { pattern: /github\.com|gitlab\.com|bitbucket\.org|stackoverflow\.com|developer\.mozilla\.org|dev\.to|medium\.com\/tag\/(programming|coding|javascript|python)/i, scene: '开发', icon: `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>` },
  { pattern: /docs\.google\.com|notion\.so|yuque\.com|feishu\.cn|quip\.com|coda\.io/i, scene: '文档协作', icon: `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>` },
  { pattern: /figma\.com|dribbble\.com|behance\.net|canva\.com|sketch\.com/i, scene: '设计', icon: `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19l7-7 3 3-7 7-3-3z"></path><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"></path><path d="M2 2l7.586 7.586"></path><circle cx="11" cy="11" r="2"></circle></svg>` },
  { pattern: /youtube\.com|bilibili\.com|vimeo\.com/i, scene: '视频/学习', icon: `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polygon points="10 8 16 12 10 16 10 8"></polygon></svg>` },
  { pattern: /twitter\.com|x\.com|weibo\.com|reddit\.com|news\.ycombinator\.com/i, scene: '资讯/社交', icon: `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>` },
  { pattern: /amazon\.[a-z.]+|taobao\.com|jd\.com|pinduoduo\.com/i, scene: '购物', icon: `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="21" r="1"></circle><circle cx="20" cy="21" r="1"></circle><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"></path></svg>` },
  { pattern: /gmail\.com|outlook\.live\.com|mail\.qq\.com|163\.com/i, scene: '邮件', icon: `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22 6 12 13 2 6"></polyline></svg>` },
  { pattern: /calendar\.google\.com|calendar\.outlook\.com|calendly\.com/i, scene: '日程', icon: `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>` },
  { pattern: /slack\.com|discord\.com|teams\.microsoft\.com|telegram\.org/i, scene: '沟通', icon: `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7A8.38 8.38 0 0 1 4 11.5a8.5 8.5 0 0 1 17 0z"></path></svg>` },
  { pattern: /wikipedia\.org|baike\.baidu\.com|zhihu\.com/i, scene: '知识查阅', icon: `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>` },
];

// 从 URL 提取域名（去除 www 等前缀）
function extractDomain(url) {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

// 从标题提取关键词（去除常见噪声词）
const STOP_WORDS = new Set([
  'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by',
  'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
  'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can', 'this', 'that', 'these', 'those',
  'i', 'you', 'he', 'she', 'it', 'we', 'they', 'what', 'which', 'who', 'when', 'where', 'why', 'how',
  '的', '了', '是', '在', '我', '你', '他', '她', '它', '们', '和', '与', '或', '但', '在', '为', '对',
  '从', '到', '向', '上', '下', '中', '这', '那', '一', '个', '些', '有', '无', '不', '也', '都', '就',
  'page', 'home', 'search', 'results', 'loading', 'error', '404', '500',
  '-', '|', '·', '—', '–', ':', '【', '】', '(', ')', '[', ']',
  // F-LOW-01: 1-2 字母无意义词（保留技术缩写如 AI/ML/GO/API/UX/UI 等）
  'en', 'de', 'es', 'fr', 'it', 'pt', 'ru', 'ja', 'ko', 'zh',
  'no', 'so', 'if', 'as', 'at', 'by', 'up', 'me', 'my', 'we', 'us',
  'am', 'pm', 'ok',
]);

// 中文 N-gram 滑动切分：对每个连续中文段落生成 2~max 字符的所有子串。
// P2-27 修复：原 /[\u4e00-\u9fa5]{2,4}/g 贪婪匹配会丢失中间位置的 2-gram，
// 例如 "上下文管理" 仅匹配到 "上下文管"（4 字），无法得到 "下文管"、"文管理" 等。
// 改为 N-gram 滑动窗口后，所有相邻组合都能进入频率统计，关键词提取更稳定。
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

function extractKeywords(tabs, max = 8) {
  const freq = new Map();
  for (const tab of tabs) {
    const text = `${tab.title || ''} ${extractDomain(tab.url)}`;
    // 英文词（F-LOW-01: {1,30} 保留 2 字母技术缩写如 AI/ML/GO/API/UX/UI）
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

// 生成 session 摘要（异步）
// 优先尝试 AI 增强；未配置 / 失败时降级到规则引擎，保持原有返回结构。
export async function generateResume(snapshot) {
  if (!snapshot || !snapshot.tabs || snapshot.tabs.length === 0) {
    return {
      summary: '该上下文没有标签页信息。',
      scenes: [],
      topSites: [],
      keywords: [],
      suggestions: [],
    };
  }

  // 先尝试 AI 增强
  const aiResult = await generateAISummary(snapshot.tabs);
  if (aiResult && aiResult._timedOut) {
    // F-PERF-01：AI 超时，降级到规则引擎并标记，由调用方 toast 提示
    const base = ruleEngineResume(snapshot);
    return { ...base, aiEnhanced: false, aiTimeout: true };
  }
  if (aiResult) {
    // 在规则引擎统计字段的基础上，用 AI 内容覆盖 summary / keywords / suggestions
    // 保持返回结构向后兼容，额外标注 aiEnhanced 与 scene / nextSteps / contextTags
    const base = ruleEngineResume(snapshot);
    return {
      ...base,
      summary: typeof aiResult.summary === 'string' && aiResult.summary.trim()
        ? aiResult.summary.trim()
        : base.summary,
      keywords: Array.isArray(aiResult.keywords) && aiResult.keywords.length
        ? aiResult.keywords.slice(0, 8)
        : base.keywords,
      suggestions: Array.isArray(aiResult.nextSteps) && aiResult.nextSteps.length
        ? aiResult.nextSteps.slice(0, 4)
        : base.suggestions,
      scene: typeof aiResult.scene === 'string' ? aiResult.scene.trim() : '',
      nextSteps: Array.isArray(aiResult.nextSteps) ? aiResult.nextSteps : [],
      contextTags: Array.isArray(aiResult.contextTags) ? aiResult.contextTags.slice(0, 3) : [],
      aiEnhanced: true,
    };
  }

  // 降级到规则引擎（保留原有逻辑）
  return ruleEngineResume(snapshot);
}

// 规则引擎：纯本地统计，无外部依赖
function ruleEngineResume(snapshot) {
  const tabs = snapshot.tabs;
  const totalTabs = tabs.length;

  // 1. 场景识别：统计每个场景的标签页数
  const sceneMap = new Map();
  for (const tab of tabs) {
    for (const rule of SCENE_RULES) {
      if (rule.pattern.test(tab.url) || rule.pattern.test(tab.title || '')) {
        sceneMap.set(rule.scene, {
          scene: rule.scene,
          icon: rule.icon,
          count: (sceneMap.get(rule.scene)?.count || 0) + 1,
        });
        break;
      }
    }
  }
  const scenes = [...sceneMap.values()].sort((a, b) => b.count - a.count);

  // 2. 热门站点：按域名分组统计
  const domainMap = new Map();
  for (const tab of tabs) {
    const domain = extractDomain(tab.url);
    if (!domain) continue;
    if (!domainMap.has(domain)) {
      domainMap.set(domain, { domain, count: 0, titles: [] });
    }
    const entry = domainMap.get(domain);
    entry.count++;
    if (entry.titles.length < 3) entry.titles.push(tab.title || domain);
  }
  const topSites = [...domainMap.values()]
    .sort((a, b) => b.count - a.count)
    .slice(0, 5);

  // 3. 关键词提取
  const keywords = extractKeywords(tabs);

  // 4. 生成摘要文本
  const sceneText = scenes.length > 0
    ? scenes.map(s => `${s.scene}(${s.count})`).join('、')
    : '综合浏览';
  const topSiteText = topSites.slice(0, 3).map(s => s.domain).join('、');
  const summary = `本次上下文包含 ${totalTabs} 个标签页，主要涉及：${sceneText}。高频站点：${topSiteText}。`;

  // 5. 下一步建议（基于场景生成）
  const suggestions = generateSuggestions(scenes, topSites, keywords);

  return {
    summary,
    scenes,
    topSites,
    keywords,
    suggestions,
  };
}

function generateSuggestions(scenes, topSites, keywords) {
  const suggestions = [];

  // 基于场景的建议
  const sceneNames = scenes.map(s => s.scene);
  if (sceneNames.includes('开发')) {
    suggestions.push('📌 检查是否有未提交的代码或未 review 的 PR');
    suggestions.push('📌 记录当前调试进度，便于下次快速恢复');
  }
  if (sceneNames.includes('文档协作')) {
    suggestions.push('📌 确认文档已保存/同步，记录待续段落');
  }
  if (sceneNames.includes('设计')) {
    suggestions.push('📌 截图保存设计进度，记录未完成的设计稿');
  }
  if (sceneNames.includes('视频/学习')) {
    suggestions.push('📌 记录视频学习进度和待复习的知识点');
  }
  if (sceneNames.includes('邮件')) {
    suggestions.push('📌 检查是否有待回复的重要邮件');
  }
  if (sceneNames.includes('沟通')) {
    suggestions.push('📌 回顾聊天中的待办事项和约定');
  }

  // 基于站点数量的建议
  if (topSites.length > 0 && topSites[0].count >= 5) {
    suggestions.push(`📌 ${topSites[0].domain} 有 ${topSites[0].count} 个标签页，考虑整理或收藏常用页`);
  }

  // 通用建议
  if (suggestions.length === 0) {
    suggestions.push('📌 考虑为这次工作命名，便于后续快速恢复');
    suggestions.push('📌 检查是否有未完成的任务需要记录');
  }

  if (suggestions.length > 4) suggestions.length = 4;
  return suggestions;
}
