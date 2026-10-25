// auto-sync.js — 阶段1.6 Context Card 账号级自动同步（Muse 版）
// 数据变化时去抖 push（5s 批量），启动时 + 定时（30min）自动 pull。
// 全部静默执行：未登录时跳过，出错只 console.warn，不干扰主流程。
import { getAuthToken } from './auth.js';
import { pushSync, pullSync } from './sync.js';

const ALARM_NAME = 'vannevar-muse-auto-sync';
const ALARM_INTERVAL_MIN = 30; // 每 30 分钟定时 pull
const PUSH_DEBOUNCE_MS = 5000; // 数据变化后 5s 去抖 push
// V4 修复：推送失败指数退避重试配置
const MAX_PUSH_RETRIES = 3;
const PUSH_RETRY_BASE_MS = 30000; // 30s 起步，按 2^count 递增

let pushTimer = null;
let isSyncing = false; // 防止 push/pull 并发冲突
let _pushRetryCount = 0; // V4 修复：当前 push 重试次数
// R6-FE-3: syncOnStartup 重试次数上限，避免 isSyncing 一直繁忙时无限重试
let _startupRetryCount = 0;
const MAX_STARTUP_RETRIES = 30;

// 是否已登录（静默检查，不抛错）
async function isLoggedIn() {
  try {
    return !!(await getAuthToken());
  } catch (_) {
    return false;
  }
}

// 去抖推送：数据变化后延迟 5s 执行，期间多次变化合并为一次
export function schedulePushSync() {
  if (pushTimer) {
    clearTimeout(pushTimer);
  }
  pushTimer = setTimeout(() => {
    pushTimer = null;
    doPushSync().catch(err => console.warn('[AutoSync] push 失败:', err.message));
  }, PUSH_DEBOUNCE_MS);
  // V14 修复：标记 pendingPush，SW 终止后重启时可恢复未完成的 push
  // R5-FE-8: 添加 .catch 避免未处理 rejection
  chrome.storage.local.set({ pendingPush: true }).catch(e => console.error('[AutoSync] set pendingPush failed', e));
}

// V14 修复：SW 重启后检查 pendingPush 标志，若有未完成的 push 则立即触发
// 应在 background.js 的 chrome.runtime.onStartup 监听中调用此函数
// R2-V5 修复：await doPushSync 确保 push 优先于 pull，避免与 handleAlarm 竞态
export async function resumePendingPush() {
  try {
    const result = await chrome.storage.local.get('pendingPush');
    if (result.pendingPush) {
      console.log('[AutoSync] 检测到未完成的 push，立即恢复');
      await doPushSync();
    }
  } catch (err) {
    console.warn('[AutoSync] 恢复 push 失败:', err.message);
  }
}

// 实际执行推送（静默）
async function doPushSync() {
  if (isSyncing) return;
  isSyncing = true; // R5-FE-2: 先设置标志，避免 await isLoggedIn 间隙并发进入
  try {
    if (!(await isLoggedIn())) return; // 未登录跳过
    const result = await pushSync();
    console.log(`[AutoSync] push 完成：新增 ${result.created}，更新 ${result.updated}，跳过 ${result.skipped}`);
    // V4 修复：成功后重置重试计数
    _pushRetryCount = 0;
    // V14 修复：清除 pendingPush 标志
    // R6-FE-4: 添加 .catch 避免未处理 rejection
    chrome.storage.local.set({ pendingPush: false }).catch(e => console.warn('[auto-sync] clear pendingPush', e));
  } catch (err) {
    // V4 修复：推送失败后指数退避重试，最多 3 次（30s / 60s / 120s）
    if (_pushRetryCount < MAX_PUSH_RETRIES) {
      const delay = PUSH_RETRY_BASE_MS * Math.pow(2, _pushRetryCount);
      _pushRetryCount++;
      console.warn(`[AutoSync] push 失败，${delay / 1000}s 后重试（第 ${_pushRetryCount}/${MAX_PUSH_RETRIES} 次）:`, err.message);
      setTimeout(() => {
        doPushSync().catch(e => console.warn('[AutoSync] push 重试失败:', e.message));
      }, delay);
    } else {
      console.warn('[AutoSync] push 重试已达上限，放弃:', err.message);
      _pushRetryCount = 0;
      // R2-V4 修复：达上限后清除 pendingPush 标志，避免 SW 重启时反复触发注定失败的 push
      // R6-FE-4: 添加 .catch 避免未处理 rejection
    chrome.storage.local.set({ pendingPush: false }).catch(e => console.warn('[auto-sync] clear pendingPush', e));
    }
  } finally {
    isSyncing = false;
  }
}

// 启动时拉取（静默）
export async function syncOnStartup() {
  // R5-FE-3: 正在同步时延迟重试，避免与 doPushSync 竞态
  // R6-FE-3: 重试次数上限，达到后放弃，避免无限重试
  if (isSyncing) {
    if (_startupRetryCount >= MAX_STARTUP_RETRIES) return;
    _startupRetryCount++;
    setTimeout(syncOnStartup, 1000);
    return;
  }
  _startupRetryCount = 0;
  // R6-FE-1: 检查 isSyncing 后立即设置标志，防止 await isLoggedIn() 间隙
  // handleAlarm/doPushSync 并发进入导致 pullSync 重复执行
  isSyncing = true;
  try {
    if (!(await isLoggedIn())) return;
    const result = await pullSync();
    if (result.importedInspirations > 0 || result.importedMoods > 0) {
      console.log(`[AutoSync] 启动 pull 完成：灵感 ${result.importedInspirations} 条，氛围 ${result.importedMoods} 个`);
    }
  } catch (err) {
    console.warn('[AutoSync] 启动 pull 失败:', err.message);
  } finally {
    isSyncing = false;
  }
}

// 注册定时 alarm（幂等：重复调用安全）
export function schedulePeriodicPull() {
  if (!chrome.alarms) return;
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: ALARM_INTERVAL_MIN });
}

// alarm 触发处理（静默 pull）
export async function handleAlarm(alarm) {
  if (!alarm || alarm.name !== ALARM_NAME) return;
  if (isSyncing) return;
  isSyncing = true; // R5-FE-2: 先设置标志，避免 await isLoggedIn 间隙并发进入
  try {
    if (!(await isLoggedIn())) return;
    const result = await pullSync();
    if (result.importedInspirations > 0 || result.importedMoods > 0) {
      console.log(`[AutoSync] 定时 pull 完成：灵感 ${result.importedInspirations} 条，氛围 ${result.importedMoods} 个`);
    }
  } catch (err) {
    console.warn('[AutoSync] 定时 pull 失败:', err.message);
  } finally {
    isSyncing = false;
  }
}
