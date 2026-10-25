// auth.js — Vannevar 统一账号体系（阶段1.4）
// 可选登录：未登录时本地模式，登录后开启云同步。
// token 通过 chrome.storage.local 存储，与 ai.js 的配置存储模式一致。
// 不硬编码任何凭据，Bridge URL 可配置（默认 localhost:8000）。

const STORAGE_KEY_AUTH = 'vannevar_auth_token';
const STORAGE_KEY_BRIDGE_URL = 'vannevar_bridge_url';
const DEFAULT_BRIDGE_URL = 'http://localhost:8000';

// 读取 Bridge 后端 URL（默认 localhost:8000）
export async function getBridgeUrl() {
  const result = await chrome.storage.local.get(STORAGE_KEY_BRIDGE_URL);
  const url = result[STORAGE_KEY_BRIDGE_URL];
  return url || DEFAULT_BRIDGE_URL;
}

// 设置 Bridge 后端 URL
// V19 修复：校验 URL scheme，仅允许 http/https，防止 javascript:/data: 等危险协议
export async function setBridgeUrl(url) {
  const trimmed = (url || '').trim().replace(/\/+$/, '');
  if (trimmed && trimmed !== DEFAULT_BRIDGE_URL) {
    try {
      const parsed = new URL(trimmed);
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
        throw new Error(`仅支持 http/https 协议，收到 ${parsed.protocol}`);
      }
    } catch (err) {
      throw new Error('Bridge URL 无效：' + err.message);
    }
  }
  await chrome.storage.local.set({ [STORAGE_KEY_BRIDGE_URL]: trimmed || DEFAULT_BRIDGE_URL });
}

// 读取 token；过期返回 null
export async function getAuthToken() {
  const result = await chrome.storage.local.get(STORAGE_KEY_AUTH);
  const data = result[STORAGE_KEY_AUTH];
  if (!data || !data.token) return null;
  // 检查过期时间（JWT 7 天，expiresAt 为 ms 时间戳）
  if (data.expiresAt && Date.now() > data.expiresAt) {
    await clearAuthToken();
    return null;
  }
  return data;
}

// 写入 token（含 email / userId / expiresAt）
export async function setAuthToken(data) {
  await chrome.storage.local.set({ [STORAGE_KEY_AUTH]: data });
}

// 清除 token
export async function clearAuthToken() {
  await chrome.storage.local.remove(STORAGE_KEY_AUTH);
}

// V8 修复：解码 JWT payload 的 exp claim，返回毫秒时间戳；失败返回 0
// JWT 结构：header.payload.signature，payload 为 base64url 编码的 JSON
function _decodeJwtExp(token) {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return 0;
    // base64url → base64
    let payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    while (payload.length % 4) payload += '='; // 补齐 padding
    const decoded = JSON.parse(atob(payload));
    if (typeof decoded.exp === 'number') {
      return decoded.exp * 1000; // 秒 → 毫秒
    }
    return 0;
  } catch (_) {
    return 0;
  }
}

// 是否已登录（有有效 token）
export async function hasAuthToken() {
  const data = await getAuthToken();
  return !!data;
}

// 获取认证状态（供 UI 显示）
export async function getAuthState() {
  const data = await getAuthToken();
  if (!data) return { loggedIn: false, email: null, userId: null };
  return { loggedIn: true, email: data.email, userId: data.userId };
}

// 登录：调用 Bridge /api/auth/login
// 成功返回 { token, email, userId }，失败抛 Error（含状态码或消息）
export async function login(email, password) {
  const bridgeUrl = await getBridgeUrl();
  let res;
  try {
    res = await fetch(`${bridgeUrl}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
  } catch (err) {
    throw new Error('无法连接到 Bridge 服务器，请确认后端已启动');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const msg = body.detail || `登录失败（${res.status}）`;
    throw new Error(msg);
  }
  const data = await res.json();
  // V8 修复：优先使用 JWT exp claim 设置过期时间，解码失败回退到 7 天默认值
  const jwtExp = _decodeJwtExp(data.access_token);
  const expiresAt = jwtExp > 0 ? jwtExp : Date.now() + 7 * 24 * 60 * 60 * 1000;
  const tokenData = {
    token: data.access_token,
    email: data.user.email,
    userId: data.user.id,
    expiresAt,
  };
  await setAuthToken(tokenData);
  return tokenData;
}

// 注册：调用 Bridge /api/auth/register（注册即登录）
// 成功返回 { token, email, userId }，失败抛 Error
export async function register(email, password, displayName = '') {
  const bridgeUrl = await getBridgeUrl();
  let res;
  try {
    res = await fetch(`${bridgeUrl}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, display_name: displayName }),
    });
  } catch (err) {
    throw new Error('无法连接到 Bridge 服务器，请确认后端已启动');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const msg = body.detail || `注册失败（${res.status}）`;
    throw new Error(msg);
  }
  const data = await res.json();
  // V8 修复：优先使用 JWT exp claim 设置过期时间，解码失败回退到 7 天默认值
  const jwtExp = _decodeJwtExp(data.access_token);
  const expiresAt = jwtExp > 0 ? jwtExp : Date.now() + 7 * 24 * 60 * 60 * 1000;
  const tokenData = {
    token: data.access_token,
    email: data.user.email,
    userId: data.user.id,
    expiresAt,
  };
  await setAuthToken(tokenData);
  return tokenData;
}

// 登出：清除本地 token
export async function logout() {
  await clearAuthToken();
}

// 获取 Authorization 头（供其他模块调用 Bridge API 时使用）
// 未登录返回 null
export async function getAuthHeader() {
  const data = await getAuthToken();
  if (!data) return null;
  return `Bearer ${data.token}`;
}

// Phase 4.1：从 JWT 中提取 csrf_jti（写端点需要 X-CSRF-Token 头 == 此值）
// 复用 _decodeJwtExp 的 base64url 解码逻辑；未登录或解码失败返回空字符串
export async function getCsrfToken() {
  const data = await getAuthToken();
  if (!data || !data.token) return '';
  try {
    const parts = data.token.split('.');
    if (parts.length !== 3) return '';
    let payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    while (payload.length % 4) payload += '=';
    const decoded = JSON.parse(atob(payload));
    return typeof decoded.csrf_jti === 'string' ? decoded.csrf_jti : '';
  } catch (_) {
    return '';
  }
}

// 构造 Bridge API 请求头（含 Authorization + X-CSRF-Token）
// 未登录返回 null；写端点必用此函数确保 CSRF 头注入
export async function getBridgeHeaders() {
  const data = await getAuthToken();
  if (!data) return null;
  const headers = { 'Authorization': `Bearer ${data.token}` };
  try {
    const parts = data.token.split('.');
    if (parts.length === 3) {
      let payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      while (payload.length % 4) payload += '=';
      const decoded = JSON.parse(atob(payload));
      if (typeof decoded.csrf_jti === 'string' && decoded.csrf_jti) {
        headers['X-CSRF-Token'] = decoded.csrf_jti;
      }
    }
  } catch (_) { /* 解码失败忽略，仅缺 CSRF 头 */ }
  return headers;
}
