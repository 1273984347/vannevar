// snapshot.js — 浏览器标签页上下文捕获与恢复

// 白名单方案：仅允许 http/https，更安全
function isAllowedUrl(url) {
  if (!url || typeof url !== 'string') return false;
  const trimmed = url.trim().toLowerCase();
  return trimmed.startsWith('http://') || trimmed.startsWith('https://');
}

// 检测浏览器新标签页 URL（兼容 Chrome / Edge / about:newtab）
function isNewTabUrl(url) {
  if (!url || typeof url !== 'string') return false;
  const lower = url.trim().toLowerCase();
  return (
    lower.startsWith('chrome://newtab') ||
    lower.startsWith('edge://newtab') ||
    lower.startsWith('about:newtab')
  );
}

const MAX_RESTORE_TABS = 50;

export async function captureCurrentContext() {
  const window = await chrome.windows.getCurrent({ populate: true });
  const tabs = (window.tabs || [])
    .filter((tab) => isAllowedUrl(tab.url))
    .map((tab) => ({
      url: tab.url,
      title: tab.title || tab.url,
      pinned: tab.pinned || false,
      active: tab.active || false
    }));

  const activeTab = tabs.find((t) => t.active) || tabs[0];
  const title = activeTab ? activeTab.title : '未命名上下文';

  return {
    title: truncateTitle(title),
    tabs
  };
}

async function safeCreateTab(windowId, url, active = false) {
  try {
    return await chrome.tabs.create({ windowId, url, active });
  } catch (err) {
// P2: 静默处理非关键扩展错误
    return null;
  }
}

export async function restoreSnapshot(snapshot, onProgress) {
  if (!snapshot || !snapshot.tabs || snapshot.tabs.length === 0) {
    throw new Error('没有可恢复的标签页');
  }

  // 只恢复允许 scheme 的 URL（防止旧数据或异常数据导致整批失败）
  const urls = snapshot.tabs
    .filter((t) => t && typeof t.url === 'string' && isAllowedUrl(t.url))
    .map((t) => t.url);
  if (urls.length === 0) {
    throw new Error('没有可恢复的有效标签页');
  }

  // 限制恢复数量，防止浏览器卡顿
  const urlsToRestore = urls.slice(0, MAX_RESTORE_TABS);

  const total = urlsToRestore.length;
  let restored = 0;
  const report = (done) => {
    if (typeof onProgress === 'function') {
      try { onProgress({ done, total }); } catch (e) { /* 忽略进度回调错误 */ }
    }
  };

  const window = await chrome.windows.getCurrent();
  const currentTabs = await chrome.tabs.query({ windowId: window.id });
  const onlyNewTab = currentTabs.length === 1 && currentTabs[0].url && isNewTabUrl(currentTabs[0].url);

  if (onlyNewTab) {
    // 替换空白标签页
    let firstOpened = true;
    try {
      await chrome.tabs.update(currentTabs[0].id, { url: urlsToRestore[0] });
      restored++;
    } catch (err) {
      firstOpened = !!(await safeCreateTab(window.id, urlsToRestore[0], true));
      if (firstOpened) restored++;
    }
    if (firstOpened) report(1);
    for (let i = 1; i < urlsToRestore.length; i++) {
      const created = await safeCreateTab(window.id, urlsToRestore[i], false);
      if (created) { restored++; report(i + 1); }
    }
  } else {
    // 打开新窗口
    try {
      await chrome.windows.create({ url: urlsToRestore });
      restored = total;
      report(total);
    } catch (err) {
      // 如果批量创建窗口失败，逐个在当前窗口创建
      for (let i = 0; i < urlsToRestore.length; i++) {
        const created = await safeCreateTab(window.id, urlsToRestore[i], false);
        if (created) { restored++; report(i + 1); }
      }
    }
  }

  return { restored, total: urlsToRestore.length };
}

function truncateTitle(title, maxLen = 40) {
  if (!title) return '未命名上下文';
  return title.length > maxLen ? title.slice(0, maxLen) + '…' : title;
}
