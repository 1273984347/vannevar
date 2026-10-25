// core/background.js — Muse Service Worker

import { initDatabase } from './db.js';
import {
  captureInspiration,
  saveInspiration,
  getInspirations,
  getAllInspirationsForStats,
  getInspirationById,
  deleteInspiration,
  getMoods,
  addMood,
  deleteMood,
  updateMood,
  togglePin,
  exportInspirations,
  importInspirations,
  updateInspiration,
  searchInspirations
} from './muse.js';
import { generateInspirationResume } from './resume.js';
import { schedulePushSync, syncOnStartup, schedulePeriodicPull, handleAlarm, resumePendingPush } from './auto-sync.js';
import { removeInspirationUuid, removeMoodUuid } from './sync.js';
import { initAgent, handleAgentAlarm, refreshAgentBadge } from './agent.js';

// 捕获成功后给扩展图标一个短暂徽章反馈（右键菜单 / 快捷键均无 popup 自动弹出，需可视化反馈）
// 阶段2.1: 闪烁后恢复 Agent 角标（避免清空 Agent 未读数）
function flashCaptureBadge(text = '✓') {
  try {
    chrome.action.setBadgeText({ text });
    chrome.action.setBadgeBackgroundColor({ color: '#10b981' });
    // R5-FE-5: 用 alarms 替代 setTimeout，避免 SW 终止后 badge 残留
    // R6-FE-12: delayInMinutes 改为 1（packed extension 中 2/60 分钟会被限流到 1 分钟）
    chrome.alarms.create('muse-flash-badge', { delayInMinutes: 1 });
  } catch (err) {
    // 部分环境下 action API 可能不可用，徽章失败不影响主流程
// P2: 静默处理非关键扩展错误
  }
}

// 捕获失败时也给出可视化反馈，便于用户感知
function flashErrorBadge(text = '!') {
  try {
    chrome.action.setBadgeText({ text });
    chrome.action.setBadgeBackgroundColor({ color: '#ef4444' });
    // R5-FE-5: 用 alarms 替代 setTimeout，避免 SW 终止后 badge 残留
    // R6-FE-12: delayInMinutes 改为 1（packed extension 中 2/60 分钟会被限流到 1 分钟）
    chrome.alarms.create('muse-flash-badge', { delayInMinutes: 1 });
  } catch (err) {
// P2: 静默处理非关键扩展错误
  }
}

// 右键菜单：快速保存当前页面为灵感
if (chrome.contextMenus) {
  chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.removeAll(() => {
      // R6-FE-9: 与 Focus 对称，removeAll 的 lastError 用 console.error
      if (chrome.runtime.lastError) {
        console.error('[BG] contextMenus.removeAll 失败:', chrome.runtime.lastError.message);
        return;
      }
      chrome.contextMenus.create({
        id: 'vannevar-muse-capture',
        title: '保存到 Muse 灵感库',
        contexts: ['page', 'action']
      }, () => {
        if (chrome.runtime.lastError) {
          console.error('[BG] contextMenus.create 失败:', chrome.runtime.lastError.message);
        }
      });
    });
  });

  chrome.contextMenus.onClicked.addListener(async (info) => {
    if (info.menuItemId === 'vannevar-muse-capture') {
      try {
        const item = await captureInspiration();
        // Phase 4.2: 透传 captureMediaMetadata 捕获的媒体会话信息（与 popup 路径保持一致）
        // media 字段为 null 时各项均 undefined，saveInspiration 内部默认为 null
        await saveInspiration({
          title: item.title,
          url: item.url,
          selectedText: item.selectedText,
          mediaType: item.media?.source ?? null,
          mediaArtist: item.media?.artist ?? null,
          mediaAlbum: item.media?.album ?? null,
          mediaArtworkUrl: item.media?.artworkUrl ?? null,
        });
        flashCaptureBadge();
        schedulePushSync(); // 阶段1.6: 右键保存后去抖自动推送
      } catch (err) {
        console.error('[Muse] 保存失败:', err);
        flashErrorBadge();
      }
    }
  });
} else {
// P2: 静默处理非关键扩展错误
}

// 快捷键命令处理：一键捕获当前页面 + 选中文本
chrome.commands?.onCommand.addListener(async (command) => {
  if (command !== 'capture-inspiration') return;
  try {
    const item = await captureInspiration();
    // Phase 4.2: 透传 captureMediaMetadata 捕获的媒体会话信息（与 popup 路径保持一致）
    await saveInspiration({
      title: item.title,
      url: item.url,
      note: '快捷捕获',
      mood: '',
      selectedText: item.selectedText,
      mediaType: item.media?.source ?? null,
      mediaArtist: item.media?.artist ?? null,
      mediaAlbum: item.media?.album ?? null,
      mediaArtworkUrl: item.media?.artworkUrl ?? null,
    });
    flashCaptureBadge();
    schedulePushSync(); // 阶段1.6: 快捷键保存后去抖自动推送
  } catch (err) {
    console.error('[Muse] 快捷键捕获失败:', err);
    flashErrorBadge();
  }
});

// 消息路由器
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  // 仅接受来自本扩展的消息，防止外部扩展或内容脚本伪造请求
  if (!sender || sender.id !== chrome.runtime.id) {
    sendResponse({ ok: false, error: '未授权的发送方' });
    return false;
  }
  if (!request || typeof request.action !== 'string') {
    sendResponse({ ok: false, error: '无效请求' });
    return false;
  }
  (async () => {
    try {
      if (request.action === 'PING') {
        sendResponse({ ok: true, message: 'pong' });
      } else if (request.action === 'CAPTURE_INSPIRATION') {
        const item = await captureInspiration();
        sendResponse({ ok: true, item });
      } else if (request.action === 'SAVE_INSPIRATION') {
        await saveInspiration(request.data);
        schedulePushSync(); // 阶段1.6: 保存灵感后去抖自动推送
        sendResponse({ ok: true });
      } else if (request.action === 'GET_INSPIRATIONS') {
        // 支持可选 limit 透传（默认 200，popup 历史列表传 5）
        const limit = Number.isFinite(request.limit) && request.limit > 0 && request.limit <= 1000 ? request.limit : 200;
        const rows = await getInspirations(limit);
        sendResponse({ ok: true, inspirations: rows });
      } else if (request.action === 'GET_INSPIRATION_BY_ID') {
        if (typeof request.id !== 'number' || !Number.isInteger(request.id)) {
          sendResponse({ ok: false, error: '无效的记录 ID' });
          return;
        }
        const row = await getInspirationById(request.id);
        if (row === null) {
          sendResponse({ ok: false, error: '记录不存在' });
        } else {
          sendResponse({ ok: true, inspiration: row });
        }
      } else if (request.action === 'DELETE_INSPIRATION') {
        if (typeof request.id !== 'number' || !Number.isInteger(request.id)) {
          sendResponse({ ok: false, error: '无效的记录 ID' });
          return;
        }
        await deleteInspiration(request.id);
        // R2-V2 修复：删除灵感后清理 client_uuid 映射，避免映射表无限增长
        try {
          await removeInspirationUuid(request.id);
        } catch (e) {
          console.warn('[BG] removeInspirationUuid 失败:', e);
        }
        sendResponse({ ok: true });
      } else if (request.action === 'GET_MOODS') {
        const moods = await getMoods();
        sendResponse({ ok: true, moods });
      } else if (request.action === 'ADD_MOOD') {
        const mood = await addMood(request.name, request.color);
        schedulePushSync(); // 阶段1.6: 新增氛围后去抖自动推送
        sendResponse({ ok: true, mood });
      } else if (request.action === 'DELETE_MOOD') {
        if (typeof request.id !== 'number' || !Number.isInteger(request.id)) {
          sendResponse({ ok: false, error: '无效的记录 ID' });
          return;
        }
        await deleteMood(request.id);
        // R2-V2 修复：删除氛围标签后清理 client_uuid 映射
        try {
          await removeMoodUuid(request.id);
        } catch (e) {
          console.warn('[BG] removeMoodUuid 失败:', e);
        }
        sendResponse({ ok: true });
      } else if (request.action === 'UPDATE_MOOD') {
        if (typeof request.id !== 'number' || !Number.isInteger(request.id)) {
          sendResponse({ ok: false, error: '无效的记录 ID' });
          return;
        }
        await updateMood(request.id, request.data?.name, request.data?.color);
        schedulePushSync(); // 阶段1.6: 更新氛围后去抖自动推送
        sendResponse({ ok: true });
      } else if (request.action === 'TOGGLE_PIN') {
        if (typeof request.id !== 'number' || !Number.isInteger(request.id)) {
          sendResponse({ ok: false, error: '无效的记录 ID' });
          return;
        }
        const pinned = await togglePin(request.id);
        schedulePushSync(); // 阶段1.6: 置顶切换后去抖自动推送
        sendResponse({ ok: true, pinned });
      } else if (request.action === 'UPDATE_INSPIRATION') {
        if (typeof request.id !== 'number' || !Number.isInteger(request.id)) {
          sendResponse({ ok: false, error: '无效的记录 ID' });
          return;
        }
        await updateInspiration(request.id, request.data || {});
        schedulePushSync(); // 阶段1.6: 更新灵感后去抖自动推送
        sendResponse({ ok: true });
      } else if (request.action === 'SEARCH_INSPIRATIONS') {
        if (typeof request.keyword !== 'string') {
          sendResponse({ ok: false, error: '搜索关键词无效' });
          return;
        }
        const keyword = request.keyword.trim();
        if (keyword.length > 100) {
          sendResponse({ ok: false, error: '搜索关键词过长' });
          return;
        }
        const rows = await searchInspirations(keyword);
        sendResponse({ ok: true, inspirations: rows });
      } else if (request.action === 'GENERATE_RESUME') {
        // 基于全部灵感生成 Resume 摘要（供 dashboard 顶部回顾面板使用）
        // 使用 getAllInspirationsForStats 而非 getInspirations，避免 200 条 LIMIT 截断统计
        const rows = await getAllInspirationsForStats();
        const resume = await generateInspirationResume(rows);
        sendResponse({ ok: true, resume });
      } else if (request.action === 'EXPORT_INSPIRATIONS') {
        const data = await exportInspirations();
        sendResponse({ ok: true, data });
      } else if (request.action === 'IMPORT_INSPIRATIONS') {
        const imported = await importInspirations(request.data);
        schedulePushSync(); // 阶段1.6: 导入后去抖自动推送
        sendResponse({ ok: true, imported });
      } else {
        sendResponse({ ok: false, error: '未知操作' });
      }
    } catch (err) {
      console.error('[BG] 处理消息失败:', request.action, err);
      sendResponse({ ok: false, error: err.message });
    }
  })().catch(err => console.error('[BG] 消息处理异常:', err));
  return true;
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
  // R5-FE-5: 闪烁徽章超时清除（替代 setTimeout，避免 SW 终止后 badge 残留）
  if (alarm.name === 'muse-flash-badge') {
    try {
      chrome.action.setBadgeText({ text: '' });
      refreshAgentBadge();
    } catch (e) {
// P2: 静默处理非关键扩展错误
    }
    return;
  }
  // R5-FE-9: resumePendingPush 非阻塞，让 handleAlarm/handleAgentAlarm 立即执行
  resumePendingPush().catch(e => console.warn('[BG] alarm resumePendingPush 失败:', e));
  handleAlarm(alarm).catch(err => console.warn('[BG] alarm 同步失败:', err));
  handleAgentAlarm(alarm).catch(err => console.warn('[BG] alarm Agent 失败:', err));
});

// 预初始化数据库
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
