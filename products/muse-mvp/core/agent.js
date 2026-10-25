// agent.js — 阶段2.1 Muse 端侧 Agent
// 定时（15min）检查触发条件，生成主动建议，通过 notifications + badge 双通道提示。
// 规则引擎优先（<100ms），AI 可选增强（SW 生命周期内完成，超时降级）。
// 全部静默执行，出错只 console.warn，不干扰主流程。

import { exportInspirations } from './muse.js';

const AGENT_ALARM_NAME = 'vannevar-muse-agent';
const AGENT_INTERVAL_MIN = 15;
const STATE_KEY = 'vannevar_muse_agent_state';
const MAX_SUGGESTIONS = 50;
const DAILY_LIMIT = 5;
const DEDUP_WINDOW_MS = 24 * 60 * 60 * 1000; // 24h
const QUIET_HOURS = { start: 22, end: 8 }; // 默认免打扰 22:00-08:00

// ---------- 状态持久化 ----------

async function getState() {
  const result = await chrome.storage.local.get(STATE_KEY);
  return result[STATE_KEY] || {
    lastRunAt: 0,
    suggestions: [],
    dailyFlags: {},
    dismissedTypes: {},
  };
}

async function setState(state) {
  await chrome.storage.local.set({ [STATE_KEY]: state });
}

function todayKey() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// 补充-P2-4: 清理过期 dailyFlags，仅保留今天 + 昨天，防止对象无限增长
function todayKeyOffset(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function cleanupDailyFlags(state) {
  if (!state.dailyFlags || typeof state.dailyFlags !== 'object') return;
  const today = todayKey();
  const yesterday = todayKeyOffset(-1);
  for (const key of Object.keys(state.dailyFlags)) {
    if (key !== today && key !== yesterday) {
      delete state.dailyFlags[key];
    }
  }
}

// ---------- 免打扰时段检查 ----------

function isQuietHour(now = new Date()) {
  const hour = now.getHours();
  const { start, end } = QUIET_HOURS;
  if (start < end) {
    return hour >= start && hour < end;
  }
  return hour >= start || hour < end;
}

// ---------- 去重检查 ----------

function isDeduped(state, dedupKey) {
  if (!dedupKey) return false;
  const lastShown = state.dismissedTypes[dedupKey];
  if (!lastShown) return false;
  return Date.now() - lastShown < DEDUP_WINDOW_MS;
}

function markDeduped(state, dedupKey) {
  if (!dedupKey) return;
  state.dismissedTypes[dedupKey] = Date.now();
  const cutoff = Date.now() - DEDUP_WINDOW_MS;
  for (const [k, v] of Object.entries(state.dismissedTypes)) {
    if (v < cutoff) delete state.dismissedTypes[k];
  }
}

function getDailyCount(state) {
  const today = todayKey();
  return state.suggestions.filter(s => {
    const d = new Date(s.createdAt);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    return key === today;
  }).length;
}

// ---------- 建议生成 ----------

function makeSuggestion(type, title, body, action = null) {
  return {
    id: crypto.randomUUID(),
    type,
    title,
    body,
    createdAt: Date.now(),
    read: false,
    acted: false,
    action,
  };
}

// 从 URL 提取域名
function extractDomain(url) {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

// 规则 1：氛围聚集 — 本周某氛围 ≥3 条
function checkMoodCluster(inspirations) {
  if (inspirations.length === 0) return null;
  const weekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const moodCount = new Map();
  for (const insp of inspirations) {
    if (!insp.mood || insp.createdAt < weekAgo) continue;
    moodCount.set(insp.mood, (moodCount.get(insp.mood) || 0) + 1);
  }
  // 找到第一个 >=3 的氛围
  for (const [mood, count] of moodCount) {
    if (count >= 3) {
      return makeSuggestion(
        'moodboard_suggest',
        '氛围聚集提醒',
        `本周「${mood}」已 ${count} 条灵感，要整理 moodboard 吗？`,
        `filter_mood:${mood}`,
      );
    }
  }
  return null;
}

// 规则 2：来源重复 — 同一域名 ≥3 条灵感
function checkSourceCluster(inspirations) {
  if (inspirations.length === 0) return null;
  const domainCount = new Map();
  for (const insp of inspirations) {
    const domain = extractDomain(insp.url);
    if (!domain) continue;
    domainCount.set(domain, (domainCount.get(domain) || 0) + 1);
  }
  for (const [domain, count] of domainCount) {
    if (count >= 3) {
      return makeSuggestion(
        'source_cluster',
        '来源集中提醒',
        `你在 ${domain} 上收藏了 ${count} 条灵感，要集中阅读吗？`,
        `filter_source:${domain}`,
      );
    }
  }
  return null;
}

// 规则 3：时段回顾 — 每天 21:00 后首次触发
function checkDailyReview(state, inspirations) {
  const today = todayKey();
  if (state.dailyFlags[today]?.dailyReviewDone) return null;
  const hour = new Date().getHours();
  if (hour < 21) return null;
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayCount = inspirations.filter(s => s.createdAt >= todayStart.getTime()).length;
  if (todayCount >= 2) {
    return makeSuggestion(
      'daily_review',
      '今日灵感回顾',
      `今天捕获了 ${todayCount} 条灵感，要生成今日回顾吗？`,
      'generate_resume',
    );
  }
  return null;
}

// 规则 4：灵感停滞 — >5 天无新灵感
function checkInspirationStale(inspirations) {
  if (inspirations.length === 0) return null;
  const latest = inspirations[0]; // exportInspirations 按 created_at DESC
  const daysSince = (Date.now() - latest.createdAt) / (1000 * 60 * 60 * 24);
  if (daysSince >= 5) {
    return makeSuggestion(
      'inspiration_reminder',
      '灵感停滞提醒',
      `已 ${Math.floor(daysSince)} 天未捕获新灵感，去看看你保存的来源？`,
      'open_dashboard',
    );
  }
  return null;
}

// ---------- 通知双通道 ----------

function notify(suggestion) {
  try {
    const notifId = `vannevar-muse-agent-${suggestion.id}`;
    chrome.notifications.create(notifId, {
      type: 'basic',
      iconUrl: 'icons/icon128.png',
      title: suggestion.title,
      message: suggestion.body,
      priority: 1,
      isClickable: true,
    });
  } catch (_) { /* 静默降级到 badge */ }
  updateBadge();
}

async function updateBadge() {
  try {
    const state = await getState();
    const unread = state.suggestions.filter(s => !s.read).length;
    if (unread > 0) {
      chrome.action.setBadgeText({ text: String(unread > 99 ? '99+' : unread) });
      chrome.action.setBadgeBackgroundColor({ color: '#f59e0b' }); // 琥珀色
    } else {
      chrome.action.setBadgeText({ text: '' });
    }
  } catch (_) { /* 静默 */ }
}

function setupNotificationListener() {
  if (!chrome.notifications?.onClicked) return;
  chrome.notifications.onClicked.addListener((notifId) => {
    if (!notifId.startsWith('vannevar-muse-agent-')) return;
    chrome.notifications.clear(notifId);
    if (chrome.action.openPopup) {
      try { chrome.action.openPopup(); return; } catch (_) { /* 降级 */ }
    }
    chrome.tabs.create({ url: chrome.runtime.getURL('dashboard/dashboard.html') }).catch(() => {});
  });
}

// ---------- Agent 主流程 ----------

async function runAgentCycle() {
  const state = await getState();
  state.lastRunAt = Date.now();

  if (isQuietHour()) {
    await setState(state);
    return;
  }

  if (getDailyCount(state) >= DAILY_LIMIT) {
    await setState(state);
    return;
  }

  // 读取本地数据
  const exportData = await exportInspirations();
  const inspirations = exportData.inspirations || [];

  // 按优先级检查触发规则
  const rules = [
    { fn: () => checkMoodCluster(inspirations), dedupKey: (s) => `moodboard:${s.action?.split(':')[1] || ''}` },
    { fn: () => checkSourceCluster(inspirations), dedupKey: (s) => `source:${s.action?.split(':')[1] || ''}` },
    { fn: () => checkDailyReview(state, inspirations), dedupKey: () => `daily_review:${todayKey()}` },
    { fn: () => checkInspirationStale(inspirations), dedupKey: () => 'inspiration_stale' },
  ];

  for (const rule of rules) {
    let suggestion = null;
    try {
      suggestion = rule.fn();
    } catch (err) {
      console.warn('[Agent] 规则执行失败:', err.message);
      continue;
    }
    if (!suggestion) continue;

    const dedupKey = rule.dedupKey(suggestion);
    if (isDeduped(state, dedupKey)) continue;

    state.suggestions.unshift(suggestion);
    if (state.suggestions.length > MAX_SUGGESTIONS) {
      state.suggestions = state.suggestions.slice(0, MAX_SUGGESTIONS);
    }
    markDeduped(state, dedupKey);

    if (suggestion.type === 'daily_review') {
      const today = todayKey();
      state.dailyFlags[today] = { ...(state.dailyFlags[today] || {}), dailyReviewDone: true };
    }

    await setState(state);
    notify(suggestion);
    return;
  }

  // 补充-P2-4: 清理过期 dailyFlags（仅保留今天 + 昨天，防止无限增长）
  cleanupDailyFlags(state);

  await setState(state);
}

// ---------- 导出 API ----------

export function scheduleAgentAlarm() {
  if (!chrome.alarms) return;
  chrome.alarms.create(AGENT_ALARM_NAME, { periodInMinutes: AGENT_INTERVAL_MIN });
}

export async function handleAgentAlarm(alarm) {
  if (!alarm || alarm.name !== AGENT_ALARM_NAME) return;
  try {
    await runAgentCycle();
  } catch (err) {
    console.warn('[Agent] 周期执行失败:', err.message);
  }
}

export async function getAgentState() {
  const state = await getState();
  return {
    unreadCount: state.suggestions.filter(s => !s.read).length,
    suggestions: state.suggestions.slice(0, 10),
    lastRunAt: state.lastRunAt,
  };
}

export async function markSuggestionsRead() {
  const state = await getState();
  let changed = false;
  for (const s of state.suggestions) {
    if (!s.read) { s.read = true; changed = true; }
  }
  if (changed) {
    await setState(state);
    await updateBadge();
  }
}

export async function markSuggestionActed(suggestionId) {
  const state = await getState();
  const s = state.suggestions.find(x => x.id === suggestionId);
  if (s && !s.acted) {
    s.acted = true;
    await setState(state);
  }
}

export async function dismissSuggestion(suggestionId) {
  const state = await getState();
  const idx = state.suggestions.findIndex(x => x.id === suggestionId);
  if (idx >= 0) {
    state.suggestions.splice(idx, 1);
    await setState(state);
    await updateBadge();
  }
}

export function initAgent() {
  scheduleAgentAlarm();
  setupNotificationListener();
  updateBadge();
}

// 供 background.js 在 flashCaptureBadge 闪烁后调用，恢复 Agent 未读角标
export async function refreshAgentBadge() {
  await updateBadge();
}
