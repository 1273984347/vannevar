// tests/test_sync_crypto.mjs — Phase 4.4 sync.js 加密路径单元测试
//
// 运行：node --import ./tests/loader.mjs tests/test_sync_crypto.mjs
//
// 测试 sync.js 的加密分支 + 网络故障路径：
//   B-P1-1: sync.js push/pull 加密路径单元测试
//   B-P1-3: sync.js push/pull 网络故障测试
//
// 依赖：
//   - Node 20.6+（module.register）
//   - loader.mjs + loader-hooks.mjs：把 sync.js 的 ./auth.js / ./db.js 重定向到 mocks/
//   - crypto.js 真实加载（测试加密功能）
//   - chrome.storage.local / fetch 用 globalThis mock

import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// ---------- 1. mock globalThis.chrome.storage.local ----------
const _storage = new Map();
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
  // crypto.randomUUID 用于 sync.js ensureUuids
  randomUUID() {
    return 'test-uuid-' + Math.random().toString(36).slice(2, 10);
  },
};

// ---------- 2. mock globalThis.fetch ----------
// _fetchMock 可在测试用例中配置返回值或抛错
let _fetchMock = async (url, opts) => {
  return new Response(JSON.stringify({ created: 0, updated: 0, skipped: 0 }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
};
// Phase 4.4 收尾：push/pull 成功后会自动同步加密档案（跨设备解密的前提）。
// 档案端点单独分流，避免污染同步请求的 body/header 捕获变量。
let _profileCalls = [];
let _profilePutBody = null;
globalThis.fetch = (url, opts) => {
  if (String(url).includes('/api/auth/me/crypto-profile')) {
    _profileCalls.push({ method: opts.method, url: String(url) });
    if (opts.method === 'PUT') {
      _profilePutBody = JSON.parse(opts.body);
      return Promise.resolve(new Response(JSON.stringify(_profilePutBody), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }));
    }
    return Promise.resolve(new Response(JSON.stringify({ detail: '尚未设置加密档案' }), {
      status: 404, headers: { 'Content-Type': 'application/json' },
    }));
  }
  return _fetchMock(url, opts);
};

// mock btoa/atob（Node 18+ 已有，但保险）
if (typeof globalThis.btoa !== 'function') {
  globalThis.btoa = (str) => Buffer.from(str, 'binary').toString('base64');
}
if (typeof globalThis.atob !== 'function') {
  globalThis.atob = (b64) => Buffer.from(b64, 'base64').toString('binary');
}

// ---------- 3. 加载被测模块 ----------
// loader.mjs 已通过 --import 注册 resolver，sync.js 的 ./auth.js / ./db.js 会重定向到 mocks/
// 跨平台：用 fileURLToPath(import.meta.url) 还原模块所在目录（不要用
// `new URL(import.meta.url).pathname`——那是 Windows-only 写法，剥掉前导
// / 后在 Linux 上会变成相对路径并被 path.resolve 按 cwd 二次拼接）。
const baseDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const syncUrl = pathToFileURL(path.join(baseDir, 'sync.js')).href;
const cryptoUrl = pathToFileURL(path.join(baseDir, 'crypto.js')).href;
const mocksAuthUrl = pathToFileURL(path.join(baseDir, 'tests', 'mocks', 'auth.js')).href;
const mocksDbUrl = pathToFileURL(path.join(baseDir, 'tests', 'mocks', 'db.js')).href;

const { pushSync, pullSync } = await import(syncUrl);
const {
  enableCrypto,
  unlockCrypto,
  lockCrypto,
  isCryptoReady,
  isCryptoEnabled,
  encryptData,
  decryptData,
} = await import(cryptoUrl);
const authMock = await import(mocksAuthUrl);
const dbMock = await import(mocksDbUrl);

// ---------- 4. 测试框架 ----------
let pass = 0;
let fail = 0;
async function test(name, fn) {
  try {
    await fn();
    pass++;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    fail++;
    console.error(`  ✗ ${name}\n    ${err.stack}`);
  }
}

// 重置所有 mock 状态
function resetAll() {
  _storage.clear();
  authMock._resetAuthState();
  dbMock._resetDbState();
  _profileCalls = [];
  _profilePutBody = null;
  _fetchMock = async () => new Response(JSON.stringify({ created: 0, updated: 0, skipped: 0 }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  });
}

console.log('Phase 4.4 sync.js 加密路径单元测试');
console.log('-----------------------------------');

// ---------- B-P1-1: sync.js push/pull 加密路径 ----------

await test('B-P1-1-a: push 启用加密+已解锁 → fetch body 中 data 是密文容器', async () => {
  resetAll();
  authMock._simulateLogin();
  dbMock._seedSnapshots([
    { id: 1, title: '测试快照', tabs: [{ url: 'https://example.com' }], createdAt: 1718000000000, updatedAt: 1718000000000 },
  ]);
  await enableCrypto('sync-test-pass-123');

  let capturedBody = null;
  _fetchMock = async (url, opts) => {
    capturedBody = JSON.parse(opts.body);
    return new Response(JSON.stringify({ created: 1, updated: 0, skipped: 0 }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };

  const result = await pushSync();
  assert.equal(result.created, 1);
  assert.ok(capturedBody, '应捕获 fetch body');
  assert.equal(capturedBody.items.length, 1);
  const item = capturedBody.items[0];
  assert.equal(item.app, 'focus');
  assert.equal(item.item_type, 'snapshot');
  assert.ok(item.client_uuid, '应有 client_uuid');
  // 关键断言：data 应为密文容器
  assert.equal(item.data.encrypted, true, 'data.encrypted 应为 true');
  assert.equal(item.data.alg, 'AES-GCM-256');
  assert.equal(item.data.kdf, 'PBKDF2-SHA256-100000');
  assert.ok(item.data.ciphertext, '应有 ciphertext');
  assert.ok(item.data.nonce, '应有 nonce');
  assert.equal(item.data.v, 1);
  // 密文不应包含明文标题
  assert.ok(!JSON.stringify(item.data).includes('测试快照'), '密文不应包含明文标题');
});

await test('B-P1-1-b: push 未启用加密 → fetch body 中 data 是明文', async () => {
  resetAll();
  authMock._simulateLogin();
  dbMock._seedSnapshots([
    { id: 1, title: '明文快照', tabs: [], createdAt: 1718000000000, updatedAt: 1718000000000 },
  ]);
  // 不调用 enableCrypto，加密未启用

  let capturedBody = null;
  _fetchMock = async (url, opts) => {
    capturedBody = JSON.parse(opts.body);
    return new Response(JSON.stringify({ created: 1, updated: 0, skipped: 0 }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };

  await pushSync();
  assert.ok(capturedBody);
  const item = capturedBody.items[0];
  // data 应为明文对象（无 encrypted 字段）
  assert.equal(item.data.encrypted, undefined, '未启用加密时 data.encrypted 应不存在');
  assert.equal(item.data.title, '明文快照');
});

await test('B-P1-1-c: push 启用但未解锁 → 抛"E2E 加密已启用但未解锁"', async () => {
  resetAll();
  authMock._simulateLogin();
  dbMock._seedSnapshots([
    { id: 1, title: '快照', tabs: [], createdAt: 1718000000000 },
  ]);
  await enableCrypto('lock-test-pass-123');
  lockCrypto(); // 模拟 SW 重启后 key 丢失
  assert.equal(isCryptoReady(), false);

  await assert.rejects(
    () => pushSync(),
    /E2E 加密已启用但未解锁/,
    '启用但未解锁时应抛友好错误'
  );
});

await test('B-P1-1-d: pull 返回密文+已解锁 → 解密后 importSnapshots', async () => {
  resetAll();
  authMock._simulateLogin();
  await enableCrypto('pull-test-pass-123');

  // 先加密一条快照，作为 pull 的返回数据
  const plaintext = { title: '云端快照', tabs: [{ url: 'https://example.com' }], createdAt: 1718000000000 };
  const encryptedData = await encryptData(plaintext);

  _fetchMock = async (url, opts) => {
    return new Response(JSON.stringify({
      items: [{
        app: 'focus',
        item_type: 'snapshot',
        client_uuid: 'remote-uuid-1',
        data: encryptedData,
        client_created_at: 1718000000000,
        client_updated_at: 1718000000000,
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  const result = await pullSync();
  assert.equal(result.pulled, 1);
  assert.equal(result.imported, 1, '应导入 1 条');
  // 验证 importSnapshots 收到的是解密后的明文
  const snaps = (await dbMock.exportSnapshots()).snapshots;
  assert.equal(snaps.length, 1);
  assert.equal(snaps[0].title, '云端快照', '导入的快照标题应是解密后的明文');
});

await test('B-P1-1-e: pull 返回密文+未解锁 → 抛"云端存在加密快照"', async () => {
  resetAll();
  authMock._simulateLogin();
  await enableCrypto('lock-pull-test-pass');
  const encryptedData = await encryptData({ title: '密文', tabs: [] });
  lockCrypto(); // 模拟未解锁

  _fetchMock = async () => new Response(JSON.stringify({
    items: [{ client_uuid: 'u1', data: encryptedData, client_created_at: 1718000000000 }],
  }), { status: 200, headers: { 'Content-Type': 'application/json' } });

  await assert.rejects(
    () => pullSync(),
    /云端存在加密快照/,
    '云端有密文但未解锁时应抛友好错误'
  );
});

await test('B-P1-1-f: pull 返回密文+解密失败（篡改）→ 跳过该条不整体失败', async () => {
  resetAll();
  authMock._simulateLogin();
  await enableCrypto('tamper-pull-pass-123');
  const encryptedData = await encryptData({ title: '原始', tabs: [] });
  // 篡改 ciphertext 最后 1 字节
  const tampered = {
    ...encryptedData,
    ciphertext: encryptedData.ciphertext.slice(0, -1) + (
      encryptedData.ciphertext.slice(-1) === 'A' ? 'B' : 'A'
    ),
  };

  _fetchMock = async () => new Response(JSON.stringify({
    items: [
      { client_uuid: 'tampered-uuid', data: tampered, client_created_at: 1718000000000 },
      // 加一条正常明文，验证不影响其他条目
      { client_uuid: 'plain-uuid', data: { title: '明文快照', tabs: [], createdAt: 1718000000001 }, client_created_at: 1718000000001 },
    ],
  }), { status: 200, headers: { 'Content-Type': 'application/json' } });

  const result = await pullSync();
  assert.equal(result.pulled, 2, 'pulled 应为 2（服务端返回 2 条）');
  // 篡改的条目被跳过，明文条目被导入
  assert.equal(result.imported, 1, '篡改的密文应被跳过，只导入 1 条');
  const snaps = (await dbMock.exportSnapshots()).snapshots;
  assert.equal(snaps.length, 1);
  assert.equal(snaps[0].title, '明文快照');
});

// ---------- B-P1-3: sync.js push/pull 网络故障 ----------

await test('B-P1-3-a: push fetch reject → 抛"无法连接到 Bridge 服务器"', async () => {
  resetAll();
  authMock._simulateLogin();
  dbMock._seedSnapshots([
    { id: 1, title: '快照', tabs: [], createdAt: 1718000000000 },
  ]);

  _fetchMock = async () => { throw new Error('network error'); };

  await assert.rejects(
    () => pushSync(),
    /无法连接到 Bridge 服务器/,
    'fetch reject 时应抛友好错误'
  );
});

await test('B-P1-3-b: pull fetch reject → 抛"无法连接到 Bridge 服务器"', async () => {
  resetAll();
  authMock._simulateLogin();

  _fetchMock = async () => { throw new Error('network error'); };

  await assert.rejects(
    () => pullSync(),
    /无法连接到 Bridge 服务器/,
    'fetch reject 时应抛友好错误'
  );
});

await test('B-P1-3-c: push 返回 401 → 清 token + 抛"登录已过期"', async () => {
  resetAll();
  authMock._simulateLogin();
  dbMock._seedSnapshots([
    { id: 1, title: '快照', tabs: [], createdAt: 1718000000000 },
  ]);

  _fetchMock = async () => new Response('{"detail":"token expired"}', {
    status: 401, headers: { 'Content-Type': 'application/json' },
  });

  await assert.rejects(
    () => pushSync(),
    /登录已过期/,
    '401 时应抛登录过期错误'
  );
  // 验证 token 已清除
  const token = await authMock.getAuthToken();
  assert.equal(token, null, '401 后应清除 token');
});

await test('B-P1-3-d: push 返回 500 → 抛"推送失败（500）"', async () => {
  resetAll();
  authMock._simulateLogin();
  dbMock._seedSnapshots([
    { id: 1, title: '快照', tabs: [], createdAt: 1718000000000 },
  ]);

  // mock 返回空 body（detail 字段缺失），让 sync.js 走 fallback `推送失败（500）`
  _fetchMock = async () => new Response('{}', {
    status: 500, headers: { 'Content-Type': 'application/json' },
  });

  await assert.rejects(
    () => pushSync(),
    /推送失败/,
    '500 时应抛推送失败错误'
  );
});

await test('B-P1-3-e: push 时 fetch headers 含 X-CSRF-Token（CSRF 头修复验证）', async () => {
  resetAll();
  authMock._simulateLogin();
  dbMock._seedSnapshots([
    { id: 1, title: '快照', tabs: [], createdAt: 1718000000000 },
  ]);

  let capturedHeaders = null;
  _fetchMock = async (url, opts) => {
    capturedHeaders = opts.headers;
    return new Response(JSON.stringify({ created: 1, updated: 0, skipped: 0 }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };

  await pushSync();
  assert.ok(capturedHeaders);
  assert.ok(capturedHeaders['Authorization']?.startsWith('Bearer '), '应有 Authorization Bearer 头');
  assert.ok(capturedHeaders['X-CSRF-Token'], '应有 X-CSRF-Token 头（CSRF 修复）');
  assert.equal(capturedHeaders['Content-Type'], 'application/json');
});

await test('B-P1-4: push 成功后把加密档案同步到 Bridge（跨设备前提）', async () => {
  resetAll();
  authMock._simulateLogin();
  dbMock._seedSnapshots([
    { id: 1, title: '快照', tabs: [], createdAt: 1718000000000 },
  ]);
  await enableCrypto('profile-push-pass-123');

  await pushSync();

  const putCalls = _profileCalls.filter((c) => c.method === 'PUT');
  assert.equal(putCalls.length, 1, '应向 /api/auth/me/crypto-profile 发起 1 次 PUT');
  assert.ok(_profilePutBody, '应捕获档案内容');
  assert.equal(typeof _profilePutBody.salt, 'string');
  assert.equal(_profilePutBody.iterations, 100000);
  assert.ok(_profilePutBody.verification.ciphertext, '档案应含 verification 密文');
  // 档案里绝不能出现 passphrase 明文
  assert.ok(!JSON.stringify(_profilePutBody).includes('profile-push-pass-123'), '档案不得包含 passphrase');
});

await test('B-P1-5: 云端状态未知（GET 500）→ 不上传档案（避免误覆盖另一台设备的档案）', async () => {
  resetAll();
  authMock._simulateLogin();
  dbMock._seedSnapshots([
    { id: 1, title: '快照', tabs: [], createdAt: 1718000000000 },
  ]);
  await enableCrypto('unknown-state-pass-123');

  let putCount = 0;
  const origFetch = globalThis.fetch;
  globalThis.fetch = (url, opts) => {
    if (String(url).includes('/api/auth/me/crypto-profile') && opts.method === 'PUT') {
      putCount++;
      return Promise.resolve(new Response('{}', {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }));
    }
    if (String(url).includes('/api/auth/me/crypto-profile')) {
      // GET 失败：云端状态**未知**（不能当作「云端没有档案」）
      return Promise.resolve(new Response('{"detail":"server error"}', {
        status: 500, headers: { 'Content-Type': 'application/json' },
      }));
    }
    return origFetch(url, opts);
  };

  try {
    await pushSync();
  } finally {
    globalThis.fetch = origFetch;
  }
  assert.equal(putCount, 0, '云端状态未知时不得上传档案（否则会覆盖另一台设备的 salt，历史密文永久不可解）');
});

await test('B-P1-6: 云端已有其它设备的档案（salt 不同）→ 不覆盖，保留远端', async () => {
  resetAll();
  authMock._simulateLogin();
  dbMock._seedSnapshots([
    { id: 1, title: '快照', tabs: [], createdAt: 1718000000000 },
  ]);
  await enableCrypto('mismatch-remote-pass-123');

  let putCount = 0;
  const origFetch = globalThis.fetch;
  globalThis.fetch = (url, opts) => {
    if (String(url).includes('/api/auth/me/crypto-profile') && opts.method === 'PUT') {
      putCount++;
      return Promise.resolve(new Response('{}', {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }));
    }
    if (String(url).includes('/api/auth/me/crypto-profile')) {
      // GET 返回**另一台设备**的档案（salt 不同）
      return Promise.resolve(new Response(JSON.stringify({
        salt: 'YW5vdGhlci1kZXZpY2Utc2FsdC12YWx1ZQ==',
        iterations: 100000,
        kdf: 'PBKDF2-SHA256-100000',
        salt_fingerprint: 'ffffffff',
        verification: { ciphertext: 'eA==', nonce: 'eQ==' },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    return origFetch(url, opts);
  };

  try {
    await pushSync();
  } finally {
    globalThis.fetch = origFetch;
  }
  assert.equal(putCount, 0, '不得覆盖其它设备的档案');
});

console.log('-----------------------------------');
console.log(`结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
