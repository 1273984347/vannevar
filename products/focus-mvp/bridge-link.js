// bridge-link.js — Phase 4.1 跨产品联动（Focus → Bridge 项目档案）
// 把 Focus 快照推送到 Bridge 项目的 context-snapshots 端点，
// 让 Bridge 项目档案可以引用 Focus 当时的浏览上下文。
// 复用 auth.js 的 token / Bridge URL 管理，不重复实现登录逻辑。

import { getBridgeUrl, clearAuthToken, getBridgeHeaders } from './auth.js';

// chrome.storage.local 中存放 Bridge 关联配置的 key
const STORAGE_KEY_BRIDGE_LINK = 'vannevar_focus_bridge_link';

// 默认配置：bridgeBaseUrl 留空时回退到 auth.js 的 getBridgeUrl()
const DEFAULT_BRIDGE_LINK_CONFIG = {
  bridgeBaseUrl: '',          // 用户配置的 Bridge URL（留空则使用 auth.js 的默认值）
  selectedProjectId: '',      // 当前选中的目标项目 ID
};

// 读取 Bridge 关联配置（bridgeBaseUrl, selectedProjectId）
export async function getBridgeLinkConfig() {
  const result = await chrome.storage.local.get(STORAGE_KEY_BRIDGE_LINK);
  return {
    ...DEFAULT_BRIDGE_LINK_CONFIG,
    ...(result[STORAGE_KEY_BRIDGE_LINK] || {}),
  };
}

// 保存 Bridge 关联配置到 chrome.storage.local
// config: { bridgeBaseUrl?, selectedProjectId? }
export async function setBridgeLinkConfig(config) {
  const merged = {
    ...DEFAULT_BRIDGE_LINK_CONFIG,
    ...(config || {}),
  };
  await chrome.storage.local.set({ [STORAGE_KEY_BRIDGE_LINK]: merged });
  return merged;
}

// 列出当前用户在 Bridge 上的项目列表
// 需 JWT，复用 auth.js 的 getBridgeHeaders
// 返回项目数组 [{ id, name, ... }]，失败抛 Error（含友好消息）
export async function listBridgeProjects() {
  const headers = await getBridgeHeaders();
  if (!headers) {
    throw new Error('未登录，请先登录后再获取项目列表');
  }

  const bridgeUrl = await getBridgeUrl();
  let res;
  try {
    res = await fetch(`${bridgeUrl}/api/projects`, {
      method: 'GET',
      headers,
    });
  } catch (err) {
    throw new Error('无法连接到 Bridge 服务器，请确认后端已启动');
  }

  // 401 token 过期：清 token + 标记 authExpired 供 UI 提示重新登录
  if (res.status === 401) {
    await clearAuthToken();
    await chrome.storage.local.set({ authExpired: true });
    throw new Error('登录已过期，请重新登录');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `获取项目列表失败（${res.status}）`);
  }

  const data = await res.json();
  // 兼容后端可能的返回格式：数组 / {items:[...]} / {projects:[...]}
  return Array.isArray(data) ? data : (data.items || data.projects || []);
}

// 推送 Focus 快照到 Bridge 项目档案
// snapshot: { uuid, title, summary, urls, tabCount, createdAt, projectId? }
//   - projectId 可选，未传时使用 getBridgeLinkConfig().selectedProjectId
// 返回 { success: boolean, error?: string, data?: responseJson }
export async function pushSnapshotToBridge(snapshot) {
  // 基本参数校验
  if (!snapshot || !snapshot.uuid || !snapshot.title) {
    return { success: false, error: '快照参数缺失：uuid 和 title 必填' };
  }

  // 解析目标项目 ID：优先使用 snapshot.projectId，其次用配置中的 selectedProjectId
  const config = await getBridgeLinkConfig();
  const projectId = snapshot.projectId || config.selectedProjectId;
  if (!projectId) {
    return { success: false, error: '尚未选择 Bridge 项目，请先在设置中选择目标项目' };
  }

  // 转换为后端 schema（camelCase → snake_case）
  // 顺便按 schema 限制做裁剪，避免后端 422
  const payload = {
    snapshot_uuid: String(snapshot.uuid).slice(0, 64),
    title: String(snapshot.title).slice(0, 200),
    summary: snapshot.summary ? String(snapshot.summary).slice(0, 4000) : '',
    urls: Array.isArray(snapshot.urls)
      ? snapshot.urls.map(u => String(u).slice(0, 2048)).slice(0, 100)
      : [],
    tab_count: Number.isFinite(snapshot.tabCount)
      ? Math.max(0, Math.min(1000, Math.floor(snapshot.tabCount)))
      : 0,
    created_at: snapshot.createdAt || Date.now(),
  };

  const bridgeUrl = await getBridgeUrl();
  const headers = await getBridgeHeaders();
  if (!headers) {
    return { success: false, error: '未登录，请先登录后再推送快照' };
  }
  headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(
      `${bridgeUrl}/api/projects/${encodeURIComponent(projectId)}/context-snapshots`,
      {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      }
    );
  } catch (err) {
    return { success: false, error: '无法连接到 Bridge 服务器，请确认后端已启动' };
  }

  // 401 token 过期：清 token + 标记 authExpired，提示重新登录
  if (res.status === 401) {
    await clearAuthToken();
    await chrome.storage.local.set({ authExpired: true });
    return { success: false, error: '登录已过期，请重新登录后再推送快照' };
  }
  // 403 权限不足：项目不属于当前用户
  if (res.status === 403) {
    const body = await res.json().catch(() => ({}));
    return { success: false, error: body.detail || '无权限访问该项目，请确认项目归属' };
  }
  // 409 重复推送：同 snapshot_uuid 已存在
  if (res.status === 409) {
    const body = await res.json().catch(() => ({}));
    return { success: false, error: body.detail || '该快照已推送过，请勿重复推送' };
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return { success: false, error: body.detail || `推送失败（${res.status}）` };
  }

  const data = await res.json().catch(() => ({}));
  return { success: true, data };
}

// 列出指定项目下已推送的 Focus 快照
// 返回快照数组 [{ id, snapshot_uuid, title, ... }]，失败抛 Error
export async function listProjectSnapshots(projectId) {
  if (!projectId) {
    throw new Error('projectId 不能为空');
  }
  const headers = await getBridgeHeaders();
  if (!headers) {
    throw new Error('未登录，请先登录后再列出快照');
  }

  const bridgeUrl = await getBridgeUrl();
  let res;
  try {
    res = await fetch(
      `${bridgeUrl}/api/projects/${encodeURIComponent(projectId)}/context-snapshots`,
      {
        method: 'GET',
        headers,
      }
    );
  } catch (err) {
    throw new Error('无法连接到 Bridge 服务器，请确认后端已启动');
  }

  if (res.status === 401) {
    await clearAuthToken();
    await chrome.storage.local.set({ authExpired: true });
    throw new Error('登录已过期，请重新登录');
  }
  if (res.status === 403) {
    throw new Error('无权限访问该项目，请确认项目归属');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `获取快照列表失败（${res.status}）`);
  }

  const data = await res.json();
  return Array.isArray(data) ? data : (data.items || data.snapshots || []);
}

// 删除指定项目下的一条 Focus 快照
// 成功返回 true，失败抛 Error（含友好消息）
export async function removeProjectSnapshot(projectId, snapshotId) {
  if (!projectId || !snapshotId) {
    throw new Error('projectId 和 snapshotId 都不能为空');
  }
  const bridgeUrl = await getBridgeUrl();
  const headers = await getBridgeHeaders();
  if (!headers) {
    throw new Error('未登录，请先登录后再删除快照');
  }
  let res;
  try {
    res = await fetch(
      `${bridgeUrl}/api/projects/${encodeURIComponent(projectId)}/context-snapshots/${encodeURIComponent(snapshotId)}`,
      {
        method: 'DELETE',
        headers,
      }
    );
  } catch (err) {
    throw new Error('无法连接到 Bridge 服务器，请确认后端已启动');
  }

  if (res.status === 401) {
    await clearAuthToken();
    await chrome.storage.local.set({ authExpired: true });
    throw new Error('登录已过期，请重新登录');
  }
  if (res.status === 403) {
    throw new Error('无权限删除该快照，请确认项目归属');
  }
  if (res.status === 404) {
    throw new Error('快照不存在或已被删除');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `删除快照失败（${res.status}）`);
  }

  return true;
}
