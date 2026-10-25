// background.js — Service Worker
// 可在此处理快捷键、上下文菜单等后台逻辑

import { captureCurrentContext, restoreSnapshot } from './snapshot.js';
import { saveSnapshot, getSnapshots, deleteSnapshot, clearAllSnapshots, getSnapshotById, initDatabase, exportSnapshots, importSnapshots, updateSnapshotTitle, searchSnapshots } from './db.js';
import { generateResume } from './resume.js';
import { schedulePushSync, syncOnStartup, schedulePeriodicPull, handleAlarm, resumePendingPush, resetCryptoLockedNotification } from './auto-sync.js';
import { removeUuid } from './sync.js';
import { initAgent, handleAgentAlarm } from './agent.js';
import { initTaskWatcher, handleTaskWatcherAlarm } from './task-watcher.js';

// 创建右键菜单（contextMenus 权限可选；缺失时不影响核心功能）
if (chrome.contextMenus) {
  chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.removeAll(() => {
      // R6-FE-9: 对齐 Muse 实现，removeAll 的 lastError 也 console.error，便于调试
      if (chrome.runtime.lastError) {
        console.error('[BG] contextMenus.removeAll 失败:', chrome.runtime.lastError.message);
        return;
      }
      chrome.contextMenus.create({
        id: 'vannevar-save-context',
        title: '保存当前上下文到 Vannevar',
        contexts: ['page', 'action']
      }, () => {
        if (chrome.runtime.lastError) {
          console.error('[BG] contextMenus.create 失败:', chrome.runtime.lastError.message);
        }
      });
    });
  });

  chrome.contextMenus.onClicked.addListener(async (info) => {
    if (info.menuItemId === 'vannevar-save-context') {
      await saveCurrentContext();
    }
  });
} else {
// P2: 静默处理非关键扩展错误
}

// 快捷键命令处理
chrome.commands?.onCommand.addListener(async (command) => {
  try {
    if (command === 'save-context') {
      const ctx = await captureCurrentContext();
      await saveSnapshot(ctx.title, ctx.tabs);
      schedulePushSync(); // 阶段1.6: 快捷键保存后去抖自动推送
    } else if (command === 'restore-last') {
      const rows = await getSnapshots(1);
      if (rows.length === 0) {
// P2: 静默处理非关键扩展错误
        return;
      }
      await restoreSnapshot(rows[0]);
    }
  } catch (err) {
    console.error('[Vannevar] 快捷键操作失败:', command, err);
  }
});

async function saveCurrentContext() {
  try {
    const ctx = await captureCurrentContext();
    await saveSnapshot(ctx.title, ctx.tabs);
    schedulePushSync(); // 阶段1.6: 右键菜单保存后去抖自动推送
  } catch (err) {
    console.error('[Vannevar] 保存失败:', err);
  }
}

// 供 popup 调用的消息接口
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  // F-1: 纵深防御 — 仅信任本扩展发的消息（popup / options / 本扩展注入的 content script）
  // sender.id 对于本扩展自身页面与本扩展注入的 content script 均等于 chrome.runtime.id
  if (sender.id !== chrome.runtime.id) {
    return false;
  }
  if (!request || typeof request.action !== 'string') {
    sendResponse({ ok: false, error: '无效请求' });
    return false;
  }
  (async () => {
    try {
      if (request.action === 'SAVE_CONTEXT') {
        const ctx = await captureCurrentContext();
        if (!ctx.tabs || ctx.tabs.length === 0) {
          sendResponse({ ok: false, error: '当前窗口没有可保存的标签页' });
          return;
        }
        await saveSnapshot(ctx.title, ctx.tabs);
        schedulePushSync(); // 阶段1.6: 数据变化后去抖自动推送
        sendResponse({ ok: true, title: ctx.title, count: ctx.tabs.length });
      } else if (request.action === 'GET_SNAPSHOTS') {
        // 支持分页：popup 传入 limit/offset，缺省取前 50 条
        const limit = Number.isInteger(request.limit) && request.limit > 0 ? request.limit : 50;
        const offset = Number.isInteger(request.offset) && request.offset >= 0 ? request.offset : 0;
        const rows = await getSnapshots(limit, offset);
        sendResponse({ ok: true, snapshots: rows });
      } else if (request.action === 'RESTORE_SNAPSHOT') {
        // P1-3: 废弃直接传入 snapshot 对象恢复，改为必须提供 id，防止恢复未经验证的标签页。
        const snapshotId = request.snapshot?.id ?? request.id;
        if (typeof snapshotId !== 'number' || !Number.isInteger(snapshotId)) {
          sendResponse({ ok: false, error: '无效的快照 ID' });
          return;
        }
        const snapshot = await getSnapshotById(snapshotId);
        if (!snapshot) {
          sendResponse({ ok: false, error: '找不到该上下文' });
          return;
        }
        await restoreSnapshot(snapshot);
        sendResponse({ ok: true });
      } else if (request.action === 'RESTORE_SNAPSHOT_BY_ID') {
        if (typeof request.id !== 'number' || !Number.isInteger(request.id)) {
          sendResponse({ ok: false, error: '无效的快照 ID' });
          return;
        }
        const snapshot = await getSnapshotById(request.id);
        if (!snapshot) {
          sendResponse({ ok: false, error: '找不到该上下文' });
          return;
        }
        const result = await restoreSnapshot(snapshot);
        sendResponse({ ok: true, restored: result.restored, total: result.total });
      } else if (request.action === 'DELETE_SNAPSHOT') {
        if (typeof request.id !== 'number' || !Number.isInteger(request.id)) {
          sendResponse({ ok: false, error: '无效的快照 ID' });
          return;
        }
        await deleteSnapshot(request.id);
        // R2-V2 修复：删除本地记录后清理 client_uuid 映射，避免映射表无限增长
        try {
          await removeUuid(request.id);
        } catch (e) {
          console.warn('[BG] removeUuid 失败:', e);
        }
        sendResponse({ ok: true });
      } else if (request.action === 'CLEAR_ALL_DATA') {
        const removed = await clearAllSnapshots();
        sendResponse({ ok: true, removed: Number(removed) || 0 });
      } else if (request.action === 'EXPORT_SNAPSHOTS') {
        const data = await exportSnapshots();
        sendResponse({ ok: true, data });
      } else if (request.action === 'IMPORT_SNAPSHOTS') {
        const imported = await importSnapshots(request.data);
        schedulePushSync(); // 阶段1.6: 导入后去抖自动推送
        sendResponse({ ok: true, imported });
      } else if (request.action === 'UPDATE_TITLE') {
        if (typeof request.id !== 'number' || !Number.isInteger(request.id)) {
          sendResponse({ ok: false, error: '无效的快照 ID' });
          return;
        }
        if (typeof request.title !== 'string' || !request.title.trim()) {
          sendResponse({ ok: false, error: '标题不能为空' });
          return;
        }
        await updateSnapshotTitle(request.id, request.title);
        schedulePushSync(); // 阶段1.6: 标题修改后去抖自动推送
        sendResponse({ ok: true });
      } else if (request.action === 'SEARCH_SNAPSHOTS') {
        if (typeof request.keyword !== 'string') {
          sendResponse({ ok: false, error: '搜索关键词无效' });
          return;
        }
        const rows = await searchSnapshots(request.keyword);
        sendResponse({ ok: true, snapshots: rows });
      } else if (request.action === 'GENERATE_RESUME') {
        if (typeof request.id !== 'number' || !Number.isInteger(request.id)) {
          sendResponse({ ok: false, error: '无效的快照 ID' });
          return;
        }
        const snapshot = await getSnapshotById(request.id);
        if (!snapshot) {
          sendResponse({ ok: false, error: '找不到该上下文' });
          return;
        }
        const resume = await generateResume(snapshot);
        sendResponse({ ok: true, resume });
      } else if (request.action === 'CRYPTO_UNLOCK') {
        // Phase 4.4: options 页将 passphrase 转发给 SW，让 SW 的 crypto.js 模块也派生 key 装载到内存。
        // 这样 auto-sync 调用 sync.js 时 isCryptoReady() 在 SW 上下文也返回 true。
        // passphrase 仅在扩展内部消息通道传输，不持久化、不出扩展边界。
        try {
          const { unlockCrypto } = await import('./crypto.js');
          const ok = await unlockCrypto(request.passphrase);
          if (ok) {
            resetCryptoLockedNotification();
            // 解锁后补推：SW 重启期间因「未解锁」被挂起的 push（pendingPush=true）
            // 必须在这里补上，否则用户解锁后未变更的数据永远不会被推送。
            resumePendingPush().catch((e) => console.warn('[BG] 解锁后补推失败:', e.message));
          }
          sendResponse({ ok, unlocked: ok });
        } catch (e) {
          sendResponse({ ok: false, error: String(e && e.message || e) });
        }
      } else if (request.action === 'CRYPTO_LOCK') {
        // Phase 4.4: options 页锁定时同步锁定 SW 的 crypto.js 模块
        try {
          const { lockCrypto } = await import('./crypto.js');
          lockCrypto();
          sendResponse({ ok: true });
        } catch (e) {
          sendResponse({ ok: false, error: String(e && e.message || e) });
        }
      } else if (request.action === 'CRYPTO_DISABLE_SW') {
        // Phase 4.4: options 页禁用加密时同步清理 SW 内存 key
        try {
          const { lockCrypto } = await import('./crypto.js');
          lockCrypto();
          resetCryptoLockedNotification();
          sendResponse({ ok: true });
        } catch (e) {
          sendResponse({ ok: false, error: String(e && e.message || e) });
        }
      } else {
        sendResponse({ ok: false, error: '未知操作' });
      }
    } catch (err) {
      console.error('[BG] 处理消息失败:', request.action, err);
      if (err && err.message === 'STORAGE_QUOTA_EXCEEDED') {
        sendResponse({ ok: false, error: '存储空间已满，请删除旧快照后重试' });
      } else {
        sendResponse({ ok: false, error: (err && err.message) || '操作失败，请重试' });
      }
    }
  })().catch(err => console.error('[BG] 消息处理异常:', err));
  return true; // 保持消息通道开启
});

// 浏览器启动时预热数据库（双保险：与模块顶层 initDatabase() 互补，确保 SW 被休眠后
// 浏览器重启时也能在 onStartup 时机提前加载 SQLite WASM，避免首次消息延迟）
chrome.runtime.onStartup?.addListener(() => {
  initDatabase().catch(err => console.error('[BG] onStartup 数据库预热失败:', err));
  // R3-V6 修复：移除 onStartup 中的 resumePendingPush 调用——模块顶层已在本 SW 启动时
  // 执行过 resumePendingPush（浏览器启动 = SW 首次加载 = 模块顶层执行），重复调用会导致
  // 同一批 pending push 被并发恢复两次。onAlarm 中的调用保留（定时同步前需恢复 SW 休眠期间
  // 累积的 pending push）。
  // 阶段1.6: 启动时自动拉取云端数据 + 注册定时 pull
  syncOnStartup().catch(err => console.warn('[BG] onStartup 自动同步失败:', err));
  schedulePeriodicPull();
});

// 阶段1.6: 定时 alarm 触发自动 pull + 阶段2.1: Agent alarm 触发主动建议
// R2-V1 修复：alarm 触发时先恢复未完成的 push，再执行定时 pull（push 优先）
chrome.alarms?.onAlarm.addListener(async (alarm) => {
  // R5-FE-9: resumePendingPush 非阻塞，让 handleAlarm/handleAgentAlarm 立即执行
  resumePendingPush().catch(e => console.warn('[BG] alarm resumePendingPush 失败:', e));
  handleAlarm(alarm).catch(err => console.warn('[BG] alarm 同步失败:', err));
  handleAgentAlarm(alarm).catch(err => console.warn('[BG] alarm Agent 失败:', err));
  // Phase 4.1: Bridge → Focus 任务状态变更通知
  handleTaskWatcherAlarm(alarm).catch(err => console.warn('[BG] alarm TaskWatcher 失败:', err));
});

// 预初始化数据库，避免第一次消息时等待 sql.js 加载
// MV3 SW 生命周期兼容：SW 被休眠后重新唤醒时，模块会重新执行本调用，
// initDatabase() 内部的 initialized 标志会重置为 false（模块级变量随 SW 重启丢失），
// 从而触发 doInitDatabase() 从 chrome.storage.local 重新加载 Base64 数据重建 SQLite 内存数据库。
initDatabase().catch(err => console.error('[BG] 数据库预初始化失败:', err));
// R2-V1 修复：SW 唤醒时恢复未完成的 push（模块顶层执行 = SW 重启）
resumePendingPush().catch(err => console.warn('[BG] SW 唤醒 resumePendingPush 失败:', err));
// 阶段1.6: SW 唤醒时也注册定时 pull（alarm 会被浏览器持久化，但 create 幂等）
schedulePeriodicPull();
// 阶段2.1: 初始化 Agent（注册 alarm + 通知监听 + 刷新 badge）
initAgent();
// Phase 4.1: 初始化 Task Watcher（注册 alarm + 通知点击监听）
initTaskWatcher();
