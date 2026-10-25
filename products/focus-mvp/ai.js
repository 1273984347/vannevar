// ai.js — AI 增强模块（可选）
// 无 API key 时所有生成函数返回 null，调用方降级到规则引擎。
// 通过 chrome.storage.local 存储 API key，不硬编码任何凭据。

const STORAGE_KEY_API = 'vannevar_ai_config';
const STORAGE_KEY_DEMO = 'demoMode';

// AI 请求超时时间（F-PERF-01）：30s 后中止并降级到规则引擎
const AI_REQUEST_TIMEOUT_MS = 30000;

// 演示模式预设结果：无需 API Key 即可展示 AI 增强效果
const DEMO_SUMMARY = {
  scene: '跨项目深度工作',
  summary: '这是一组高强度的跨项目工作上下文，包含文档撰写、邮件沟通和设计评审，建议优先恢复。',
  keywords: ['文档撰写', '邮件沟通', '设计评审', '跨项目'],
  nextSteps: ['优先恢复文档撰写进度，确认未提交段落', '梳理待回复邮件清单，按紧急程度排序', '回顾设计评审遗留问题并记录结论'],
  contextTags: ['深度专注', '紧急', '创造性'],
};

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

// 调用 AI 生成 session 摘要
// 输入：标签页信息数组
// 输出：{ scene, summary, keywords, nextSteps }；未配置或非超时失败返回 null；
//       超时返回 { _timedOut: true }，调用方据此显示超时提示并降级到规则引擎
export async function generateAISummary(tabs) {
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
    const tabInfo = tabs.slice(0, 30).map(t => ({
      title: t.title,
      url: t.url
    }));

    const prompt = `分析以下浏览器标签页，生成工作上下文摘要。
标签页列表：
${JSON.stringify(tabInfo, null, 2)}

请用 JSON 格式返回：
{
  "scene": "工作场景（如：前端开发、文档阅读、设计调研等）",
  "summary": "2-3 句话概述当前工作内容",
  "keywords": ["关键词1", "关键词2"],
  "nextSteps": ["建议下一步1", "建议下一步2"],
  "contextTags": ["上下文标签：1-3 个，从以下维度选取——工作强度（如：深度专注/多线并行）、紧急程度（如：需尽快处理/可暂缓）、协作状态（如：独立工作/待协作）、认知类型（如：创造性/审查性/学习性）"]
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
      // F-PERF-01：超时返回特殊标志，调用方降级到规则引擎并 toast 提示
// P2: 静默处理非关键扩展错误
      return { _timedOut: true };
    }
// P2: 静默处理非关键扩展错误
    return null;
  } finally {
    cancelTimer();
  }
}

// 阶段2.7: 流式生成 session 摘要（SSE 风格逐字输出）
// 输入：标签页信息数组 + onChunk 回调
// 输出：完整摘要文本（流结束后返回）；未配置/失败返回 null，调用方降级到规则引擎
// onChunk(text): 每收到一个文本增量调用一次，供 UI 实时显示
export async function generateAISummaryStream(tabs, onChunk) {
  // 演示模式：返回预设结果并按段切片模拟流式
  if (await getDemoMode()) {
    const full = DEMO_SUMMARY.summary;
    const chunkSize = 10;
    for (let i = 0; i < full.length; i += chunkSize) {
      onChunk(full.slice(i, i + chunkSize));
      // 模拟网络延迟
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
    const tabInfo = tabs.slice(0, 30).map(t => ({ title: t.title, url: t.url }));
    const prompt = `分析以下浏览器标签页，生成工作上下文摘要。
标签页列表：
${JSON.stringify(tabInfo, null, 2)}

请用纯文本输出 2-3 句话概述当前工作内容，不要 JSON 包装。`;

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
