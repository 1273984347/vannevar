// agent.js — 阶段2.1 Focus 端侧 Agent
// 定时（15min）检查触发条件，生成主动建议，通过 notifications + badge 双通道提示。
// 规则引擎优先（<100ms），AI 可选增强（SW 生命周期内完成，超时降级）。
// 全部静默执行，出错只 console.warn，不干扰主流程。

import { exportSnapshots } from './db.js';

const AGENT_ALARM_NAME = 'vannevar-focus-agent';
const AGENT_INTERVAL_MIN = 15;
const STATE_KEY = 'vannevar_focus_agent_state';
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
    // 同日跨段，如 2:00-8:00
    return hour >= start && hour < end;
  }
  // 跨午夜，如 22:00-8:00
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
  // 清理过期的 dismissedTypes 条目（>24h）
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
    action,  // 如 'restore_snapshot:123'，popup 解析后执行
  };
}

// 规则 1：停滞检测 — 最近快照 >3 天未恢复
function checkStaleSnapshot(snapshots) {
  if (snapshots.length === 0) return null;
  const latest = snapshots[0]; // exportSnapshots 按 created_at DESC 排序
  const daysSince = (Date.now() - latest.createdAt) / (1000 * 60 * 60 * 24);
  if (daysSince >= 3) {
    return makeSuggestion(
      'restore_reminder',
      '快照停滞提醒',
      `你的「${latest.title || '未命名快照'}」已 ${Math.floor(daysSince)} 天未使用，要恢复吗？`,
      `restore_snapshot:${latest.id}`,
    );
  }
  return null;
}

// 规则 2：超载检测 — 当前标签页 >30 个
async function checkTabOverload() {
  try {
    const tabs = await chrome.tabs.query({});
    if (tabs.length > 30) {
      return makeSuggestion(
        'tab_overload',
        '标签页超载提醒',
        `当前打开了 ${tabs.length} 个标签页，建议保存快照释放内存`,
        'save_context',
      );
    }
  } catch (_) { /* SW 中可能无 tabs 权限，静默跳过 */ }
  return null;
}

// 规则 3：时段回顾 — 每天 18:00 后首次触发
function checkDailyReview(state, snapshots) {
  const today = todayKey();
  if (state.dailyFlags[today]?.dailyReviewDone) return null;
  const hour = new Date().getHours();
  if (hour < 18) return null;
  // 今天保存的快照数
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayCount = snapshots.filter(s => s.createdAt >= todayStart.getTime()).length;
  if (todayCount >= 2) {
    return makeSuggestion(
      'daily_review',
      '今日回顾',
      `今天保存了 ${todayCount} 个快照，要生成日报吗？`,
      'generate_resume',
    );
  }
  return null;
}

// ---------- 通知双通道 ----------

function notify(suggestion) {
  // 1. 系统通知（需 notifications 权限）
  try {
    const notifId = `vannevar-focus-agent-${suggestion.id}`;
    chrome.notifications.create(notifId, {
      type: 'basic',
      iconUrl: 'icons/icon128.png',
      title: suggestion.title,
      message: suggestion.body,
      priority: 1, // 低优先级
      isClickable: true,
    });
  } catch (_) { /* notifications 不可用时静默降级到 badge */ }

  // 2. 角标提示
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

// 通知点击：尝试打开 popup，降级到 options
function setupNotificationListener() {
  if (!chrome.notifications?.onClicked) return;
  chrome.notifications.onClicked.addListener((notifId) => {
    if (!notifId.startsWith('vannevar-focus-agent-')) return;
    chrome.notifications.clear(notifId);
    // MV3: openPopup 支持有限，降级到打开 options
    if (chrome.action.openPopup) {
      try { chrome.action.openPopup(); return; } catch (_) { /* 降级 */ }
    }
    chrome.tabs.create({ url: chrome.runtime.getURL('options.html') }).catch(() => {});
  });
}

// ---------- Agent 主流程 ----------

async function runAgentCycle() {
  const state = await getState();
  state.lastRunAt = Date.now();

  // 免打扰时段跳过
  if (isQuietHour()) {
    await setState(state);
    return;
  }

  // 每日上限检查
  if (getDailyCount(state) >= DAILY_LIMIT) {
    await setState(state);
    return;
  }

  // 读取本地数据
  const exportData = await exportSnapshots();
  const snapshots = exportData.snapshots || [];

  // 按优先级检查触发规则（每个周期最多生成 1 条）
  const rules = [
    { fn: () => checkStaleSnapshot(snapshots), dedupKey: (s) => `restore_reminder:${s.action?.split(':')[1] || ''}` },
    { fn: () => checkTabOverload(), dedupKey: () => 'tab_overload' },
    { fn: () => checkDailyReview(state, snapshots), dedupKey: () => `daily_review:${todayKey()}` },
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

    // 命中：添加到建议列表 + 去重标记 + 通知
    state.suggestions.unshift(suggestion);
    // 清理超出的旧建议（优先清理已读的）
    if (state.suggestions.length > MAX_SUGGESTIONS) {
      const kept = state.suggestions.slice(0, MAX_SUGGESTIONS);
      state.suggestions = kept;
    }
    markDeduped(state, dedupKey);

    // daily_review 标记当日已完成
    if (suggestion.type === 'daily_review') {
      const today = todayKey();
      state.dailyFlags[today] = { ...(state.dailyFlags[today] || {}), dailyReviewDone: true };
    }

    await setState(state);
    notify(suggestion);
    return; // 每个周期最多 1 条
  }

  // 补充-P2-4: 清理过期 dailyFlags（仅保留今天 + 昨天，防止无限增长）
  cleanupDailyFlags(state);

  await setState(state);
}

// ---------- 导出 API ----------

// 注册定时 alarm（幂等）
export function scheduleAgentAlarm() {
  if (!chrome.alarms) return;
  chrome.alarms.create(AGENT_ALARM_NAME, { periodInMinutes: AGENT_INTERVAL_MIN });
}

// alarm 触发处理
export async function handleAgentAlarm(alarm) {
  if (!alarm || alarm.name !== AGENT_ALARM_NAME) return;
  try {
    await runAgentCycle();
  } catch (err) {
    console.warn('[Agent] 周期执行失败:', err.message);
  }
}

// 获取当前 Agent 状态（供 popup 展示）
export async function getAgentState() {
  const state = await getState();
  return {
    unreadCount: state.suggestions.filter(s => !s.read).length,
    suggestions: state.suggestions.slice(0, 10), // 最近 10 条
    lastRunAt: state.lastRunAt,
  };
}

// 标记建议为已读（popup 展示后调用）
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

// 标记建议已采纳
export async function markSuggestionActed(suggestionId) {
  const state = await getState();
  const s = state.suggestions.find(x => x.id === suggestionId);
  if (s && !s.acted) {
    s.acted = true;
    await setState(state);
  }
}

// 忽略建议（用户点击「忽略」）
export async function dismissSuggestion(suggestionId) {
  const state = await getState();
  const idx = state.suggestions.findIndex(x => x.id === suggestionId);
  if (idx >= 0) {
    state.suggestions.splice(idx, 1);
    await setState(state);
    await updateBadge();
  }
}

// 初始化通知监听（background.js 启动时调用一次）
export function initAgent() {
  scheduleAgentAlarm();
  setupNotificationListener();
  updateBadge();
}
