// tests/test_logout_revoke.mjs — T-08：logout 服务端撤销 + salt-mismatch 用户可见提示
//
// 运行：node tests/test_logout_revoke.mjs
// （不依赖 db.js / SQLite WASM，无需 loader；chrome.storage / notifications / fetch 均为 globalThis mock）
//
// 覆盖：
//   T-08① auth.js logout()：先 POST /api/auth/logout（Bearer + X-CSRF-Token）再清本地；
//           服务端不可达时仍完成本地登出；未登录时不发起请求。
//   T-08② crypto-profile.js handleProfileSyncResult()：salt-mismatch → 通知 + 24h 节流；
//           synced:true → 清除标记；其余 reason 不打扰用户。

import assert from 'node:assert/strict';

// ---------- mock globalThis.chrome ----------
const _storage = new Map();
const _notifications = [];
globalThis.chrome = {
  storage: {
    local: {
      async get(key) {
        if (typeof key === 'string') {
          return _storage.has(key) ? { [key]: _storage.get(key) } : {};
        }
        return {};
      },
      async set(obj) {
        for (const [k, v] of Object.entries(obj)) _storage.set(k, v);
      },
      async remove(key) {
        if (typeof key === 'string') _storage.delete(key);
      },
    },
  },
  notifications: {
    create(id, opts) {
      _notifications.push({ id, opts });
      return id;
    },
  },
};

// ---------- mock globalThis.fetch ----------
let _fetchCalls = [];
let _fetchImpl = async () => new Response(JSON.stringify({ message: '已登出' }), { status: 200 });
globalThis.fetch = async (url, opts) => {
  _fetchCalls.push({ url, opts });
  return _fetchImpl(url, opts);
};

// ---------- 构造带 csrf_jti 的假 JWT ----------
const b64u = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
const JWT = ['e30', b64u({ sub: '1', iat: Math.floor(Date.now() / 1000), csrf_jti: 'csrf-test-123' }), 'sig'].join('.');

const { logout, setAuthToken, getAuthToken } = await import('../auth.js');
const { handleProfileSyncResult } = await import('../crypto-profile.js');

let pass = 0, fail = 0;
async function t(name, fn) {
  try {
    _storage.clear(); _notifications.length = 0; _fetchCalls.length = 0;
    _fetchImpl = async () => new Response(JSON.stringify({ message: '已登出' }), { status: 200 });
    await fn();
    pass++; console.log('  ✓ ' + name);
  } catch (err) {
    fail++; console.log('  ✗ ' + name + '\n    ' + (err && err.message || err));
  }
}

await t('① logout 已登录：POST /api/auth/logout 带 Bearer + X-CSRF-Token，随后清除本地 token', async () => {
  await setAuthToken({ token: JWT, email: 't@example.com', userId: 1, expiresAt: Date.now() + 3600e3 });
  await logout();
  assert.equal(_fetchCalls.length, 1, '应恰好发起一次撤销请求');
  const { url, opts } = _fetchCalls[0];
  assert.equal(url, 'http://localhost:8000/api/auth/logout');
  assert.equal(opts.method, 'POST');
  assert.equal(opts.headers['Authorization'], `Bearer ${JWT}`);
  assert.equal(opts.headers['X-CSRF-Token'], 'csrf-test-123', 'JWT 路径写端点强制 csrf_jti');
  assert.equal(await getAuthToken(), null, '本地 token 应已清除');
});

await t('① logout 服务端不可达：不抛错，本地 token 仍清除', async () => {
  await setAuthToken({ token: JWT, email: 't@example.com', userId: 1, expiresAt: Date.now() + 3600e3 });
  _fetchImpl = async () => { throw new Error('network down'); };
  await logout(); // 不得抛出
  assert.equal(await getAuthToken(), null);
});

await t('① logout 未登录：不发起请求，不抛错', async () => {
  await logout();
  assert.equal(_fetchCalls.length, 0);
  assert.equal(await getAuthToken(), null);
});

await t('② salt-mismatch 首次：系统通知一次 + 写入节流标记', async () => {
  await handleProfileSyncResult({ synced: false, reason: 'salt-mismatch' });
  assert.equal(_notifications.length, 1);
  assert.equal(_notifications[0].id, 'vannevar-salt-mismatch');
  assert.match(_notifications[0].opts.message, /不一致/);
  const flag = (await chrome.storage.local.get('vannevar_e2e_salt_mismatch_at'))['vannevar_e2e_salt_mismatch_at'];
  assert.ok(flag > 0, '节流标记应已写入');
});

await t('② salt-mismatch 24h 内重复：不重复通知', async () => {
  await handleProfileSyncResult({ synced: false, reason: 'salt-mismatch' });
  await handleProfileSyncResult({ synced: false, reason: 'salt-mismatch' });
  assert.equal(_notifications.length, 1, '24h 节流内不得重复弹通知');
});

await t('② 同步成功后清除标记，再次 mismatch 可重新提醒', async () => {
  await handleProfileSyncResult({ synced: false, reason: 'salt-mismatch' });
  assert.equal(_notifications.length, 1);
  await handleProfileSyncResult({ synced: true, reason: 'already-synced' });
  const flag = (await chrome.storage.local.get('vannevar_e2e_salt_mismatch_at'))['vannevar_e2e_salt_mismatch_at'];
  assert.equal(flag, undefined, '一致后标记应清除');
  await handleProfileSyncResult({ synced: false, reason: 'salt-mismatch' });
  assert.equal(_notifications.length, 2, '标记清除后应可再次提醒');
});

await t('② 其余 reason（not-enabled 等）不打扰用户', async () => {
  await handleProfileSyncResult({ synced: false, reason: 'not-enabled' });
  await handleProfileSyncResult({ synced: false, reason: 'remote-unknown' });
  await handleProfileSyncResult(null);
  assert.equal(_notifications.length, 0);
  assert.equal((await chrome.storage.local.get('vannevar_e2e_salt_mismatch_at'))['vannevar_e2e_salt_mismatch_at'], undefined);
});

console.log('-----------------------------------');
console.log(`结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
