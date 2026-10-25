// core/ai.js — AI 增强模块（可选，灵感场景）
// 无 API key 时所有生成函数返回 null，调用方降级到规则引擎。
// 通过 chrome.storage.local 存储 API key，不硬编码任何凭据。

const STORAGE_KEY_API = 'vannevar_ai_config';
const STORAGE_KEY_DEMO = 'demoMode';

// AI 请求超时时间（与 Focus 统一架构）：30s 后中止并降级到规则引擎
const AI_REQUEST_TIMEOUT_MS = 30000;

// 演示模式预设结果：无需 API Key 即可展示 AI 增强效果
const DEMO_SUMMARY = {
  theme: '写作技法与东方美学',
  summary: '近期灵感聚焦于写作技法与东方美学，氛围以克制和深思为主，适合在创作低谷期回顾。',
  moods: ['克制', '深思'],
  suggestions: ['尝试将「留白」主题的灵感整理成一篇短文', '回顾克制氛围的灵感，寻找跨领域的共性'],
  recommendedMoods: ['克制', '东方美学', '写作技法'],
};

const DEMO_MOOD_TAGS = ['克制', '东方美学', '写作技法'];

// 读取演示模式状态
export async function getDemoMode() {
  const result = await chrome.storage.local.get(STORAGE_KEY_DEMO);
  return result[STORAGE_KEY_DEMO] === true;
}

// 设置演示模式状态
export async function setDemoMode(enabled) {
  await chrome.storage.local.set({ [STORAGE_KEY_DEMO]: !!enabled });
}

// 创建一个带超时的 AbortController，返回 { signal, cancelTimer }
function createTimeoutController(timeoutMs = AI_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return {
    signal: controller.signal,
    cancelTimer: () => clearTimeout(timer)
  };
}

// 判断错误是否为超时（AbortController 触发）
function isTimeoutError(err) {
  if (!err) return false;
  return err.name === 'AbortError' || /aborted/i.test(err.message || '');
}

// 读取 AI 配置；未配置时返回 null
export async function getAIConfig() {
  const result = await chrome.storage.local.get(STORAGE_KEY_API);
  return result[STORAGE_KEY_API] || null;
}

// 写入 AI 配置
export async function setAIConfig(config) {
  await chrome.storage.local.set({ [STORAGE_KEY_API]: config });
}

// 清除 AI 配置
export async function clearAIConfig() {
  await chrome.storage.local.remove(STORAGE_KEY_API);
}

// 是否已配置可用的 AI（apiKey + baseUrl 均存在）
export async function hasAIConfig() {
  const config = await getAIConfig();
  return !!(config && config.apiKey && config.baseUrl);
}

// 用最小请求测试连接是否可用
// 成功返回 true，失败抛出 Error（含状态码或消息）
export async function testAIConnection(config) {
  if (!config || !config.apiKey || !config.baseUrl) {
    throw new Error('请先填写 API Base URL 和 API Key');
  }
  const { signal, cancelTimer } = createTimeoutController();
  let response;
  try {
    response = await fetch(config.baseUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${config.apiKey}`
      },
      body: JSON.stringify({
        model: config.model || 'gpt-4o-mini',
        messages: [{ role: 'user', content: 'ping' }],
        max_tokens: 8,
        temperature: 0
      }),
      signal
    });
  } catch (err) {
    if (isTimeoutError(err)) {
      throw new Error('AI 响应超时（30s），请检查网络或 API 服务可用性');
    }
    throw err;
  } finally {
    cancelTimer();
  }
  if (!response.ok) {
    let detail = '';
    try { detail = (await response.text()) || ''; } catch (_) { /* 忽略 */ }
    throw new Error(`API ${response.status}${detail ? '：' + detail.slice(0, 200) : ''}`);
  }
  return true;
}

// 调用 AI 生成灵感回顾摘要
// 输入：灵感记录数组（title / url / note / mood / selectedText / createdAt）
// 输出：{ theme, summary, moods, suggestions }；未配置或非超时失败返回 null；
//       超时返回 { _timedOut: true }，调用方据此显示超时提示并降级到规则引擎
export async function generateAISummary(inspirations) {
  // 演示模式：返回预设结果，跳过真实 API 调用
  if (await getDemoMode()) {
    return DEMO_SUMMARY;
  }

  const config = await getAIConfig();
  if (!config || !config.apiKey || !config.baseUrl) {
    return null; // 降级到规则引擎
  }

  const { signal, cancelTimer } = createTimeoutController();
  try {
    const items = inspirations.slice(0, 40).map(it => ({
      title: it.title,
      url: it.url,
      note: it.note,
      mood: it.mood,
      selectedText: it.selectedText
    }));

    const prompt = `你是一位善于洞察的阅读者与策展人。请深入分析以下灵感记录，生成一份有洞见的灵感回顾摘要。

每条灵感包含：网页标题、来源 URL、用户备注、氛围标签、选中的原文片段。

灵感列表：
${JSON.stringify(items, null, 2)}

分析要求：
1. 不要只罗列统计，要从内容中提炼出 1-2 条隐含的主题脉络、情绪走向或创作倾向。
2. summary 需具体：引用灵感中的关键词、来源或氛围，说明它们之间如何相互关联。
3. theme 用 4-10 个字概括核心主题，避免空泛词汇。
4. moods 给出 2-4 个精确的情绪/氛围标签，可从灵感已有氛围标签中延伸。
5. suggestions 给出 2-3 条可执行的下一步建议（如整理成 moodboard、深入某个关键词、补充某类来源），必须结合具体内容，不能是泛泛而谈。
6. recommendedMoods 给出 3-5 个推荐的氛围标签，用于后续灵感记录时快速选择，应基于本次灵感的内容特征，风格多样（如：温暖、冷峻、荒诞、诗意、理性等）。

请严格用 JSON 格式返回，不要包含任何额外说明：
{
  "theme": "核心主题",
  "summary": "2-3 句有洞见的概述",
  "moods": ["标签1", "标签2"],
  "suggestions": ["具体建议1", "具体建议2"],
  "recommendedMoods": ["推荐氛围1", "推荐氛围2", "推荐氛围3"]
}`;

    const response = await fetch(config.baseUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${config.apiKey}`
      },
      body: JSON.stringify({
        model: config.model || 'gpt-4o-mini',
        messages: [{ role: 'user', content: prompt }],
        temperature: 0.3,
        response_format: { type: 'json_object' }
      }),
      signal
    });

    if (!response.ok) throw new Error(`API ${response.status}`);
    const data = await response.json();
    const content = data.choices?.[0]?.message?.content;
    if (!content) throw new Error('空响应');

    return JSON.parse(content);
  } catch (err) {
    if (isTimeoutError(err)) {
      // 超时返回特殊标志，调用方降级到规则引擎并 toast 提示
      return { _timedOut: true };
    }
// P2: 静默处理非关键扩展错误
    return null;
  } finally {
    cancelTimer();
  }
}

// P0-AI: 实时推荐氛围标签
// 输入：页面信息（title / url / selectedText / note）
// 输出：字符串数组（3-5 个推荐氛围标签）；未配置或失败返回 null
export async function recommendMoodTags(pageInfo) {
  // 演示模式：返回预设推荐标签，跳过真实 API 调用
  if (await getDemoMode()) {
    return DEMO_MOOD_TAGS;
  }

  const config = await getAIConfig();
  if (!config || !config.apiKey || !config.baseUrl) {
    return null; // 降级，调用方使用默认标签
  }

  const { signal, cancelTimer } = createTimeoutController();
  try {
    const prompt = `基于以下网页信息，推荐 3-5 个氛围标签，用于标记这条灵感记录。

网页信息：
${JSON.stringify({ title: pageInfo.title, url: pageInfo.url, selectedText: (pageInfo.selectedText || '').slice(0, 500), note: pageInfo.note }, null, 2)}

要求：
1. 标签 2-4 个字，简洁有画面感（如：温暖、冷峻、荒诞、诗意、理性、怀旧、未来感等）
2. 基于内容特征，不要泛泛而谈
3. 风格多样，覆盖不同情绪维度

请严格用 JSON 格式返回：
{"moods": ["标签1", "标签2", "标签3"]}`;

    const response = await fetch(config.baseUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${config.apiKey}`
      },
      body: JSON.stringify({
        model: config.model || 'gpt-4o-mini',
        messages: [{ role: 'user', content: prompt }],
        temperature: 0.4,
        response_format: { type: 'json_object' }
      }),
      signal
    });

    if (!response.ok) throw new Error(`API ${response.status}`);
    const data = await response.json();
    const content = data.choices?.[0]?.message?.content;
    if (!content) throw new Error('空响应');

    const result = JSON.parse(content);
    return Array.isArray(result.moods) ? result.moods.slice(0, 5) : null;
  } catch (err) {
    if (isTimeoutError(err)) {
      // 超时静默降级到默认标签（推荐场景无需特殊提示）
      return null;
    }
// P2: 静默处理非关键扩展错误
    return null;
  } finally {
    cancelTimer();
  }
}

// 阶段2.7: 流式生成灵感回顾摘要（SSE 风格逐字输出）
// 输入：灵感记录数组 + onChunk 回调
// 输出：完整摘要文本（流结束后返回）；未配置/失败返回 null，调用方降级到规则引擎
// onChunk(text): 每收到一个文本增量调用一次，供 UI 实时显示
export async function generateAISummaryStream(inspirations, onChunk) {
  // 演示模式：返回预设结果并按段切片模拟流式
  if (await getDemoMode()) {
    const full = DEMO_SUMMARY.summary;
    const chunkSize = 10;
    for (let i = 0; i < full.length; i += chunkSize) {
      onChunk(full.slice(i, i + chunkSize));
      await new Promise(r => setTimeout(r, 20));
    }
    return DEMO_SUMMARY;
  }

  const config = await getAIConfig();
  if (!config || !config.apiKey || !config.baseUrl) {
    return null; // 降级到规则引擎
  }

  const { signal, cancelTimer } = createTimeoutController();
  try {
    const items = inspirations.slice(0, 40).map(it => ({
      title: it.title, url: it.url, note: it.note, mood: it.mood, selectedText: it.selectedText
    }));
    const prompt = `你是一位善于洞察的阅读者与策展人。请深入分析以下灵感记录，生成一份有洞见的灵感回顾摘要（2-3 句话）。

灵感列表：
${JSON.stringify(items, null, 2)}

请用纯文本输出概述，不要 JSON 包装。`;

    const response = await fetch(config.baseUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${config.apiKey}`
      },
      body: JSON.stringify({
        model: config.model || 'gpt-4o-mini',
        messages: [{ role: 'user', content: prompt }],
        temperature: 0.3,
        stream: true
      }),
      signal
    });

    if (!response.ok) throw new Error(`API ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let fullText = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') {
            return fullText || null;
          }
          try {
            const chunk = JSON.parse(data);
            const text = chunk.choices?.[0]?.delta?.content;
            if (text) {
              fullText += text;
              onChunk(text);
            }
          } catch (_) {
            // 忽略解析失败的行
          }
        }
      }
    }
    return fullText || null;
  } catch (err) {
    if (isTimeoutError(err)) {
      return { _timedOut: true };
    }
    return null;
  } finally {
    cancelTimer();
  }
}
