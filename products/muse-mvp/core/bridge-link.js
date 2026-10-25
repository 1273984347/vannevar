// bridge-link.js — Phase 4.1 跨产品联动（Muse → Bridge 项目档案）
// 复用 auth.js 的 token / Bridge URL 管理，不重复实现。
// 提供灵感推送 / 列表 / 删除能力，将 Muse 灵感附加到 Bridge 项目档案。

import { getBridgeUrl, setBridgeUrl, clearAuthToken, getBridgeHeaders } from './auth.js';

const STORAGE_KEY_BRIDGE_LINK = 'vannevar_muse_bridge_link';

// 读取 Bridge 关联配置
// bridgeBaseUrl 复用 auth.js 的源（单一数据源），selectedProjectId 为本模块私有
// 返回 { bridgeBaseUrl: string, selectedProjectId: string|null }
export async function getBridgeLinkConfig() {
  const result = await chrome.storage.local.get(STORAGE_KEY_BRIDGE_LINK);
  const cfg = result[STORAGE_KEY_BRIDGE_LINK] || {};
  const bridgeBaseUrl = await getBridgeUrl();
  return {
    bridgeBaseUrl,
    selectedProjectId: cfg.selectedProjectId || null,
  };
}

// 保存 Bridge 关联配置
// config: { bridgeBaseUrl?, selectedProjectId? }
// bridgeBaseUrl 若提供则同步到 auth.js（保持单一数据源），selectedProjectId 存本模块私有键
export async function setBridgeLinkConfig(config) {
  const cfg = config || {};
  if (cfg.bridgeBaseUrl !== undefined) {
    await setBridgeUrl(cfg.bridgeBaseUrl);
  }
  const result = await chrome.storage.local.get(STORAGE_KEY_BRIDGE_LINK);
  const current = result[STORAGE_KEY_BRIDGE_LINK] || {};
  const next = { ...current };
  if (cfg.selectedProjectId !== undefined) {
    next.selectedProjectId = cfg.selectedProjectId || null;
  }
  await chrome.storage.local.set({ [STORAGE_KEY_BRIDGE_LINK]: next });
}

// 统一构造请求头（含 Authorization + X-CSRF-Token），未登录抛错
async function _requireBridgeHeaders(withBody) {
  const headers = await getBridgeHeaders();
  if (!headers) {
    const err = new Error('未登录，请先登录 Bridge 账号');
    err.code = 'NOT_LOGGED_IN';
    throw err;
  }
  if (withBody) {
    headers['Content-Type'] = 'application/json';
  }
  return headers;
}

// 统一调用 Bridge API：
// - 网络异常 → 友好错误
// - 401 → 清 token + 标记 authExpired，提示重新登录
// - 403 → 权限不足
// - 409 → 重复推送
// - 其他非 2xx → detail 或状态码
// 返回 { success, error?, data?, status }
async function _callBridge(method, path, { body } = {}) {
  let headers;
  try {
    headers = await _requireBridgeHeaders(body !== undefined);
  } catch (err) {
    return { success: false, status: 0, error: err.message };
  }
  const bridgeUrl = await getBridgeUrl();

  let res;
  try {
    res = await fetch(`${bridgeUrl}${path}`, {
      method,
      headers,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  } catch (err) {
    return {
      success: false,
      status: 0,
      error: '无法连接到 Bridge 服务器，请确认后端已启动',
    };
  }

  // 401：token 过期，清除并提示重新登录（与 sync.js 一致）
  if (res.status === 401) {
    await clearAuthToken();
    await chrome.storage.local.set({ authExpired: true });
    return {
      success: false,
      status: 401,
      error: '登录已过期，请重新登录后再操作',
    };
  }

  // 403：权限不足
  if (res.status === 403) {
    const body403 = await res.json().catch(() => ({}));
    return {
      success: false,
      status: 403,
      error: body403.detail || '权限不足，无法访问该项目',
    };
  }

  // 409：重复推送
  if (res.status === 409) {
    const body409 = await res.json().catch(() => ({}));
    return {
      success: false,
      status: 409,
      error: body409.detail || '该灵感已推送过，请勿重复推送',
    };
  }

  // 其他非 2xx
  if (!res.ok) {
    const bodyErr = await res.json().catch(() => ({}));
    return {
      success: false,
      status: res.status,
      error: bodyErr.detail || `请求失败（${res.status}）`,
    };
  }

  // 204 No Content 或空响应
  if (res.status === 204) {
    return { success: true, status: 204, data: null };
  }

  let data;
  try {
    data = await res.json();
  } catch (_) {
    data = null;
  }
  return { success: true, status: res.status, data };
}

// 列出当前用户在 Bridge 的项目列表
// 返回 { success, error?, data?: projects[] }
export async function listBridgeProjects() {
  return _callBridge('GET', '/api/projects');
}

// 校验并转换灵感为后端 schema
// inspiration: { uuid, title, url, note, mood, createdAt }
// → { inspiration_uuid, title, url, note, mood, created_at }
function _buildInspirationPayload(inspiration) {
  const insp = inspiration || {};
  const inspirationUuid = (insp.uuid != null ? String(insp.uuid) : '');
  const title = insp.title != null ? String(insp.title) : '';
  const url = insp.url != null ? String(insp.url) : '';
  const note = insp.note != null ? String(insp.note) : '';
  const mood = insp.mood != null ? String(insp.mood) : '';
  const createdAt = Number(insp.createdAt);

  if (!inspirationUuid || inspirationUuid.length > 64) {
    throw new Error('inspiration_uuid 无效（需 1-64 字符）');
  }
  if (!title || title.length > 200) {
    throw new Error('title 无效（需 1-200 字符）');
  }
  if (!url || url.length > 2048) {
    throw new Error('url 无效（需 1-2048 字符）');
  }
  if (note.length > 4000) {
    throw new Error('note 过长（最大 4000 字符）');
  }
  if (mood.length > 60) {
    throw new Error('mood 过长（最大 60 字符）');
  }
  if (!Number.isFinite(createdAt) || createdAt < 0) {
    throw new Error('createdAt 无效（需 >= 0 的毫秒时间戳）');
  }

  return {
    inspiration_uuid: inspirationUuid,
    title,
    url,
    note,
    mood,
    created_at: createdAt,
  };
}

// 推送灵感到 Bridge 项目档案
// inspiration: { uuid, title, url, note, mood, createdAt }
// projectId 省略时使用 getBridgeLinkConfig().selectedProjectId
// 返回 { success: boolean, error?: string, data?: responseJson }
export async function pushInspirationToBridge(inspiration, projectId) {
  let pid = projectId;
  if (pid === undefined || pid === null) {
    const cfg = await getBridgeLinkConfig();
    pid = cfg.selectedProjectId;
  }
  if (!pid) {
    return {
      success: false,
      error: '未选择 Bridge 项目，请先在设置中关联项目',
    };
  }

  let payload;
  try {
    payload = _buildInspirationPayload(inspiration);
  } catch (err) {
    return { success: false, error: err.message };
  }

  return _callBridge(
    'POST',
    `/api/projects/${encodeURIComponent(pid)}/context-inspirations`,
    { body: payload }
  );
}

// 列出项目已推送的灵感
// 返回 { success, error?, data?: inspirations[] }
export async function listProjectInspirations(projectId) {
  if (!projectId) {
    return { success: false, error: 'projectId 不能为空' };
  }
  return _callBridge(
    'GET',
    `/api/projects/${encodeURIComponent(projectId)}/context-inspirations`
  );
}

// 删除项目下指定灵感
// 返回 { success, error?, data?: null }
export async function removeProjectInspiration(projectId, inspirationId) {
  if (!projectId || !inspirationId) {
    return { success: false, error: 'projectId 与 inspirationId 均不能为空' };
  }
  return _callBridge(
    'DELETE',
    `/api/projects/${encodeURIComponent(projectId)}/context-inspirations/${encodeURIComponent(inspirationId)}`
  );
}
