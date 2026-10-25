// tests/mocks/auth.js — Muse sync 加密路径测试用的 auth mock
// 不依赖 chrome.storage.local，状态保存在模块内存中，方便测试用例控制
//
// 与 Focus 侧 tests/mocks/auth.js 同构（两个产品的 auth.js 导出签名一致）。

const _state = {
  token: null,           // { token, email, userId, expiresAt }
  bridgeUrl: 'http://localhost:8000',
  csrfToken: 'test-csrf-token',
};

export async function getAuthToken() {
  return _state.token;
}

export async function setAuthToken(data) {
  _state.token = data;
}

export async function clearAuthToken() {
  _state.token = null;
}

export async function getBridgeUrl() {
  return _state.bridgeUrl;
}

export async function setBridgeUrl(url) {
  _state.bridgeUrl = url;
}

// 模拟 getBridgeHeaders：返回 Authorization + X-CSRF-Token
// （真实实现从 JWT 的 csrf_jti 解出；mock 直接给固定值即可）
export async function getBridgeHeaders() {
  if (!_state.token) return null;
  return {
    'Authorization': `Bearer ${_state.token.token}`,
    'X-CSRF-Token': _state.csrfToken,
  };
}

// 测试辅助：重置 mock 状态
export function _resetAuthState() {
  _state.token = null;
  _state.bridgeUrl = 'http://localhost:8000';
}

// 测试辅助：模拟登录
export function _simulateLogin(token = 'test-jwt-token', email = 'test@example.com') {
  _state.token = { token, email, userId: 1, expiresAt: Date.now() + 7 * 24 * 60 * 60 * 1000 };
}
