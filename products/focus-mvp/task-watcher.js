// task-watcher.js — Phase 4.1 Bridge → Focus 任务状态变更通知
// 每 5 分钟轮询 Bridge 的 /api/auth/me/recent-task-updates 端点，
// 用水位线（lastSeenTaskUpdatedAtIso）过滤已通知过的任务，
// 对新任务通过 chrome.notifications + badge 通知用户。
// 全部静默执行：未登录/未启用/出错只 console.warn，不干扰主流程。

import { getBridgeUrl, getAuthToken, clearAuthToken, getBridgeHeaders } from './auth.js';

const ALARM_NAME = 'vannevar-focus-task-watcher';
const ALARM_INTERVAL_MIN = 5; // 每 5 分钟检查一次
const STORAGE_KEY_LAST_SEEN = 'vannevar_focus_task_watcher_last_seen';
const STORAGE_KEY_ENABLED = 'vannevar_focus_task_watcher_enabled';
const MAX_NOTIFS_PER_TICK = 3; // 每个 alarm 周期最多通知 3 条，避免刷屏

const STATUS_LABELS = {
  todo: '待办',
  in_progress: '进行中',
  done: '已完成',
};

// Bridge URL 同步缓存（避免每次 alarm 都重新读 storage）
let _cachedBridgeUrl = '';

async function _refreshBridgeUrlCache() {
  try {
    _cachedBridgeUrl = await getBridgeUrl();
  } catch (_) {
    _cachedBridgeUrl = '';
  }
}

// SW 启动时拿一次，避免首次 alarm 时 _cachedBridgeUrl 为空
function getBridgeUrlSync() {
  return _cachedBridgeUrl || 'http://localhost:8000';
}

// ---------- 开关 ----------

export async function getTaskWatcherEnabled() {
  try {
    const result = await chrome.storage.local.get(STORAGE_KEY_ENABLED);
    // 默认启用（仅当显式 false 时关闭）
    return result[STORAGE_KEY_ENABLED] !== false;
  } catch (_) {
    return true;
  }
}

export async function setTaskWatcherEnabled(enabled) {
  await chrome.storage.local.set({ [STORAGE_KEY_ENABLED]: !!enabled });
}

// ---------- 登录态 ----------

async function isLoggedIn() {
  try {
    return !!(await getAuthToken());
  } catch (_) {
    return false;
  }
}

// ---------- 水位线 ----------

async function getLastSeen() {
  try {
    const result = await chrome.storage.local.get(STORAGE_KEY_LAST_SEEN);
    return result[STORAGE_KEY_LAST_SEEN] || '';
  } catch (_) {
    return '';
  }
}

async function setLastSeen(isoStr) {
  try {
    await chrome.storage.local.set({ [STORAGE_KEY_LAST_SEEN]: isoStr });
  } catch (e) {
    console.warn('[TaskWatcher] 保存水位线失败:', e.message);
  }
}

// ---------- 主逻辑 ----------

async function checkTaskUpdates() {
  if (!(await getTaskWatcherEnabled())) return;
  if (!(await isLoggedIn())) return;

  const headers = await getBridgeHeaders();
  if (!headers) return; // 未登录

  const bridgeUrl = getBridgeUrlSync();
  let res;
  try {
    res = await fetch(`${bridgeUrl}/api/auth/me/recent-task-updates?limit=10`, {
      method: 'GET',
      headers,
    });
  } catch (err) {
    console.warn('[TaskWatcher] 网络请求失败:', err.message);
    return;
  }

  // 401：token 过期，清登录态（与 auto-sync 模式一致）
  if (res.status === 401) {
    try { await clearAuthToken(); } catch (_) {}
    console.warn('[TaskWatcher] token 过期，已清登录态');
    return;
  }
  if (!res.ok) {
    console.warn('[TaskWatcher] 非 200 响应:', res.status);
    return;
  }

  const tasks = await res.json().catch(() => []);
  if (!Array.isArray(tasks) || tasks.length === 0) {
    return;
  }

  // tasks 已按 updated_at 倒序，取首条作为本次水位线候选
  const latestIso = tasks[0]?.updated_at_iso || '';
  const lastSeen = await getLastSeen();

  // 首次运行：仅设置水位线，不通知（避免历史任务全推）
  if (!lastSeen) {
    await setLastSeen(latestIso);
    console.log('[TaskWatcher] 首次运行，已初始化水位线:', latestIso);
    return;
  }

  // 过滤出"新于水位线"的任务
  const newTasks = tasks.filter(t => t.updated_at_iso && t.updated_at_iso > lastSeen);
  if (newTasks.length === 0) {
    return;
  }

  // 最多通知 MAX_NOTIFS_PER_TICK 条（按时间倒序优先最新的）
  const toNotify = newTasks.slice(0, MAX_NOTIFS_PER_TICK);
  for (const task of toNotify) {
    _notifyTaskUpdate(task);
  }

  // 更新水位线为本次最新
  await setLastSeen(latestIso);

  // 更新 badge 显示新任务数（5 秒后清除，与 agent.js 的 badge 风格不同以区分）
  try {
    chrome.action.setBadgeText({ text: String(newTasks.length > 99 ? '99+' : newTasks.length) });
    chrome.action.setBadgeBackgroundColor({ color: '#3b82f6' }); // 蓝色，区别于 Agent 的琥珀色
    setTimeout(() => {
      try { chrome.action.setBadgeText({ text: '' }); } catch (_) {}
    }, 5000);
  } catch (_) { /* SW 中可能不可用 */ }
}

function _notifyTaskUpdate(task) {
  if (!chrome.notifications) return;
  try {
    const statusLabel = STATUS_LABELS[task.status] || task.status || '更新';
    const notifId = `vannevar-focus-task-${task.task_id}-${Date.now()}`;
    chrome.notifications.create(notifId, {
      type: 'basic',
      iconUrl: 'icons/icon128.png',
      title: `任务状态变更：${statusLabel}`,
      message: `项目「${task.project_name || ''}」任务「${task.title || ''}」已变更为 ${statusLabel}`,
      priority: 0,
      isClickable: true,
    });
  } catch (err) {
    console.warn('[TaskWatcher] 通知创建失败:', err.message);
  }
}

// ---------- 导出 API ----------

// 注册 alarm + 通知点击监听（background.js 启动时调用一次，幂等）
export function initTaskWatcher() {
  if (!chrome.alarms) return;
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: ALARM_INTERVAL_MIN });

  // 通知点击：打开 Bridge 项目详情页
  if (chrome.notifications?.onClicked) {
    chrome.notifications.onClicked.addListener((notifId) => {
      if (!notifId.startsWith('vannevar-focus-task-')) return;
      // notifId 格式：vannevar-focus-task-{taskId}-{ts}，无法直接拿到 project_id
      // 改为打开 Bridge 首页（用户可自行定位项目）
      try {
        const url = `${getBridgeUrlSync()}/`;
        chrome.tabs?.create({ url }).catch(() => {});
      } catch (_) {}
      try { chrome.notifications.clear(notifId); } catch (_) {}
    });
  }
}

// alarm 触发处理（在 background.js 的 onAlarm 监听器中调用）
export async function handleTaskWatcherAlarm(alarm) {
  if (!alarm || alarm.name !== ALARM_NAME) return;
  await _refreshBridgeUrlCache();
  try {
    await checkTaskUpdates();
  } catch (err) {
    console.warn('[TaskWatcher] check 失败:', err.message);
  }
}
