// tests/test_sync_crypto.mjs — Muse sync.js 加密路径单元测试
//
// 运行：node --import ./tests/loader.mjs tests/test_sync_crypto.mjs
//
// 测试 Muse 同步的加密分支 + 网络故障路径：
//   - push：启用加密后 data 为密文容器（灵感 + 氛围都加密）
//   - pull：密文解密后导入；密文损坏时跳过该条；旧明文向后兼容
//   - 写端点必须携带 X-CSRF-Token（Muse 侧 CSRF 修复验证）
//   - push 成功后自动把加密档案同步到 Bridge（跨设备解密的前提）
//
// 依赖：
//   - loader.mjs + loader-hooks.mjs：把 sync.js 的 ./muse.js / ./auth.js 重定向到 mocks/
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
let _fetchMock = async () => new Response(JSON.stringify({ created: 0, updated: 0, skipped: 0 }), {
  status: 200,
  headers: { 'Content-Type': 'application/json' },
});
globalThis.fetch = (url, opts) => _fetchMock(url, opts);

// mock btoa/atob（Node 18+ 已有，但保险）
if (typeof globalThis.btoa !== 'function') {
  globalThis.btoa = (str) => Buffer.from(str, 'binary').toString('base64');
}
if (typeof globalThis.atob !== 'function') {
  globalThis.atob = (b64) => Buffer.from(b64, 'base64').toString('binary');
}

// ---------- 3. 加载被测模块 ----------
const baseDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const syncUrl = pathToFileURL(path.join(baseDir, 'core', 'sync.js')).href;
const cryptoUrl = pathToFileURL(path.join(baseDir, 'core', 'crypto.js')).href;
const mocksAuthUrl = pathToFileURL(path.join(baseDir, 'tests', 'mocks', 'auth.js')).href;
const mocksMuseUrl = pathToFileURL(path.join(baseDir, 'tests', 'mocks', 'muse.js')).href;

const { pushSync, pullSync } = await import(syncUrl);
const {
  enableCrypto,
  lockCrypto,
  isCryptoReady,
  encryptData,
  getLocalProfile,
} = await import(cryptoUrl);
const authMock = await import(mocksAuthUrl);
const museMock = await import(mocksMuseUrl);

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

// 记录加密档案端点的调用（验证跨设备同步链路）
let _profileCalls = [];
let _profilePutBody = null;

function _defaultFetch(url, opts) {
  if (String(url).includes('/api/auth/me/crypto-profile')) {
    _profileCalls.push({ method: opts.method, url: String(url) });
    if (opts.method === 'PUT') {
      _profilePutBody = JSON.parse(opts.body);
      return new Response(JSON.stringify(_profilePutBody), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    // GET：默认云端没有档案
    return new Response(JSON.stringify({ detail: '尚未设置加密档案' }), {
      status: 404, headers: { 'Content-Type': 'application/json' },
    });
  }
  return new Response(JSON.stringify({ created: 0, updated: 0, skipped: 0 }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  });
}

function resetAll() {
  _storage.clear();
  authMock._resetAuthState();
  museMock._resetMuseState();
  _profileCalls = [];
  _profilePutBody = null;
  _fetchMock = _defaultFetch;
}

console.log('Muse sync.js 加密路径单元测试');
console.log('-----------------------------------');

// ---------- push 加密路径 ----------

await test('push 启用加密+已解锁 → fetch body 中 data 是密文容器', async () => {
  resetAll();
  authMock._simulateLogin();
  museMock._seedInspirations([
    { title: '测试灵感', url: 'https://example.com', note: '笔记', mood: '专注', selectedText: '选中文字', createdAt: 1718000000000, updatedAt: 1718000000000 },
  ]);
  await enableCrypto('sync-test-pass-123');

  let capturedBody = null;
  _fetchMock = async (url, opts) => {
    if (String(url).includes('/api/auth/me/crypto-profile')) return _defaultFetch(url, opts);
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
  assert.equal(item.app, 'muse');
  assert.equal(item.item_type, 'inspiration');
  assert.ok(item.client_uuid, '应有 client_uuid');
  // 关键断言：data 应为密文容器
  assert.equal(item.data.encrypted, true, 'data.encrypted 应为 true');
  assert.equal(item.data.alg, 'AES-GCM-256');
  assert.equal(item.data.kdf, 'PBKDF2-SHA256-100000');
  assert.ok(item.data.ciphertext, '应有 ciphertext');
  assert.ok(item.data.nonce, '应有 nonce');
  assert.equal(item.data.v, 1);
  // 密文不应包含明文内容
  const dumped = JSON.stringify(item.data);
  assert.ok(!dumped.includes('测试灵感'), '密文不应包含明文标题');
  assert.ok(!dumped.includes('选中文字'), '密文不应包含明文选中片段');
  assert.ok(!dumped.includes('笔记'), '密文不应包含明文笔记');
});

await test('push 启用加密 → 氛围标签（mood）同样加密', async () => {
  resetAll();
  authMock._simulateLogin();
  museMock._seedMoods([
    { name: '专注', color: '#10b981', sortOrder: 0, createdAt: 1718000000000, updatedAt: 1718000000000 },
  ]);
  await enableCrypto('mood-crypto-pass-123');

  let capturedBody = null;
  _fetchMock = async (url, opts) => {
    if (String(url).includes('/api/auth/me/crypto-profile')) return _defaultFetch(url, opts);
    capturedBody = JSON.parse(opts.body);
    return new Response(JSON.stringify({ created: 1, updated: 0, skipped: 0 }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };

  await pushSync();
  assert.ok(capturedBody);
  const moodItem = capturedBody.items.find((i) => i.item_type === 'mood');
  assert.ok(moodItem, '应有 mood 条目');
  assert.equal(moodItem.data.encrypted, true, 'mood 的 data 也应是密文容器');
  assert.ok(!JSON.stringify(moodItem.data).includes('专注'), '密文不应包含明文氛围名');
});

await test('push 未启用加密 → fetch body 中 data 是明文', async () => {
  resetAll();
  authMock._simulateLogin();
  museMock._seedInspirations([
    { title: '明文灵感', url: '', note: '', mood: '', selectedText: '', createdAt: 1718000000000, updatedAt: 1718000000000 },
  ]);

  let capturedBody = null;
  _fetchMock = async (url, opts) => {
    if (String(url).includes('/api/auth/me/crypto-profile')) return _defaultFetch(url, opts);
    capturedBody = JSON.parse(opts.body);
    return new Response(JSON.stringify({ created: 1, updated: 0, skipped: 0 }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };

  await pushSync();
  const item = capturedBody.items.find((i) => i.item_type === 'inspiration');
  assert.equal(item.data.encrypted, undefined, '未启用加密时 data.encrypted 应不存在');
  assert.equal(item.data.title, '明文灵感');
});

await test('push 启用但未解锁 → 抛"E2E 加密已启用但未解锁"', async () => {
  resetAll();
  authMock._simulateLogin();
  museMock._seedInspirations([
    { title: '灵感', url: '', note: '', mood: '', selectedText: '', createdAt: 1718000000000 },
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

await test('push 成功后把加密档案同步到 Bridge（跨设备前提）', async () => {
  resetAll();
  authMock._simulateLogin();
  museMock._seedInspirations([
    { title: '灵感', url: '', note: '', mood: '', selectedText: '', createdAt: 1718000000000 },
  ]);
  await enableCrypto('profile-push-pass-123');

  _fetchMock = async (url, opts) => _defaultFetch(url, opts);
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

// ---------- pull 解密路径 ----------

await test('pull 返回密文+已解锁 → 解密后导入本地', async () => {
  resetAll();
  authMock._simulateLogin();
  await enableCrypto('pull-test-pass-123');

  const plaintext = { title: '云端灵感', url: 'https://example.com', note: '远端笔记', mood: '夜晚', selectedText: '远端选中', createdAt: 1718000000000 };
  const encryptedData = await encryptData(plaintext);

  _fetchMock = async (url, opts) => {
    if (String(url).includes('/api/auth/me/crypto-profile')) return _defaultFetch(url, opts);
    return new Response(JSON.stringify({
      items: [{
        app: 'muse',
        item_type: 'inspiration',
        client_uuid: 'remote-uuid-1',
        data: encryptedData,
        client_created_at: 1718000000000,
        client_updated_at: 1718000000000,
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  const result = await pullSync();
  assert.equal(result.pulled, 1);
  assert.equal(result.importedInspirations, 1, '应导入 1 条');
  const rows = museMock._getInspirations();
  assert.equal(rows.length, 1);
  assert.equal(rows[0].title, '云端灵感', '导入的标题应是解密后的明文');
  assert.equal(rows[0].note, '远端笔记');
  assert.equal(rows[0].selectedText, '远端选中');
});

await test('pull 返回密文+未解锁 → 抛"云端存在加密灵感"', async () => {
  resetAll();
  authMock._simulateLogin();
  await enableCrypto('lock-pull-test-pass');
  const encryptedData = await encryptData({ title: '密文', note: '', mood: '', selectedText: '', url: '', createdAt: 1718000000000 });
  lockCrypto(); // 模拟未解锁

  _fetchMock = async (url) => {
    if (String(url).includes('/api/auth/me/crypto-profile')) return _defaultFetch(url, {});
    return new Response(JSON.stringify({
      items: [{ client_uuid: 'u1', data: encryptedData, client_created_at: 1718000000000 }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  await assert.rejects(
    () => pullSync(),
    /云端存在加密灵感/,
    '云端有密文但未解锁时应抛友好错误'
  );
});

await test('pull 返回密文+解密失败（篡改）→ 跳过该条不整体失败', async () => {
  resetAll();
  authMock._simulateLogin();
  await enableCrypto('tamper-pull-pass-123');
  const encryptedData = await encryptData({ title: '原始', url: '', note: '', mood: '', selectedText: '', createdAt: 1718000000000 });
  const tampered = {
    ...encryptedData,
    ciphertext: encryptedData.ciphertext.slice(0, -1) + (
      encryptedData.ciphertext.slice(-1) === 'A' ? 'B' : 'A'
    ),
  };

  _fetchMock = async (url) => {
    if (String(url).includes('/api/auth/me/crypto-profile')) return _defaultFetch(url, {});
    return new Response(JSON.stringify({
      items: [
        { app: 'muse', item_type: 'inspiration', client_uuid: 'tampered-uuid', data: tampered, client_created_at: 1718000000000 },
        // 加一条正常明文，验证不影响其他条目
        { app: 'muse', item_type: 'inspiration', client_uuid: 'plain-uuid', data: { title: '明文灵感', url: '', note: '', mood: '', selectedText: '', createdAt: 1718000000001 }, client_created_at: 1718000000001 },
      ],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  const result = await pullSync();
  assert.equal(result.pulled, 2, 'pulled 应为 2（服务端返回 2 条）');
  assert.equal(result.importedInspirations, 1, '篡改的密文应被跳过，只导入 1 条');
  const rows = museMock._getInspirations();
  assert.equal(rows.length, 1);
  assert.equal(rows[0].title, '明文灵感');
});

await test('pull 返回旧明文 → 正常导入（向后兼容，不丢历史数据）', async () => {
  resetAll();
  authMock._simulateLogin();
  await enableCrypto('legacy-plain-pass-123');

  _fetchMock = async (url) => {
    if (String(url).includes('/api/auth/me/crypto-profile')) return _defaultFetch(url, {});
    return new Response(JSON.stringify({
      items: [{
        app: 'muse',
        item_type: 'inspiration',
        client_uuid: 'legacy-uuid',
        data: { title: '历史明文灵感', url: 'https://old.example.com', note: '旧笔记', mood: '日常', selectedText: '', createdAt: 1717000000000 },
        client_created_at: 1717000000000,
        client_updated_at: 1717000000000,
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  const result = await pullSync();
  assert.equal(result.importedInspirations, 1, '旧明文数据应正常导入');
  const rows = museMock._getInspirations();
  assert.equal(rows[0].title, '历史明文灵感');
});

// ---------- 网络故障路径 ----------

await test('push fetch reject → 抛"无法连接到 Bridge 服务器"', async () => {
  resetAll();
  authMock._simulateLogin();
  museMock._seedInspirations([
    { title: '灵感', url: '', note: '', mood: '', selectedText: '', createdAt: 1718000000000 },
  ]);

  _fetchMock = async () => { throw new Error('network error'); };

  await assert.rejects(
    () => pushSync(),
    /无法连接到 Bridge 服务器/,
    'fetch reject 时应抛友好错误'
  );
});

await test('pull fetch reject → 抛"无法连接到 Bridge 服务器"', async () => {
  resetAll();
  authMock._simulateLogin();

  _fetchMock = async () => { throw new Error('network error'); };

  await assert.rejects(
    () => pullSync(),
    /无法连接到 Bridge 服务器/,
    'fetch reject 时应抛友好错误'
  );
});

await test('push 返回 401 → 清 token + 抛"登录已过期"', async () => {
  resetAll();
  authMock._simulateLogin();
  museMock._seedInspirations([
    { title: '灵感', url: '', note: '', mood: '', selectedText: '', createdAt: 1718000000000 },
  ]);

  _fetchMock = async () => new Response('{"detail":"token expired"}', {
    status: 401, headers: { 'Content-Type': 'application/json' },
  });

  await assert.rejects(
    () => pushSync(),
    /登录已过期/,
    '401 时应抛登录过期错误'
  );
  const token = await authMock.getAuthToken();
  assert.equal(token, null, '401 后应清除 token');
});

await test('push 返回 500 → 抛"推送失败（500）"', async () => {
  resetAll();
  authMock._simulateLogin();
  museMock._seedInspirations([
    { title: '灵感', url: '', note: '', mood: '', selectedText: '', createdAt: 1718000000000 },
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

await test('push 时 fetch headers 含 X-CSRF-Token（Muse CSRF 修复验证）', async () => {
  resetAll();
  authMock._simulateLogin();
  museMock._seedInspirations([
    { title: '灵感', url: '', note: '', mood: '', selectedText: '', createdAt: 1718000000000 },
  ]);

  let capturedHeaders = null;
  _fetchMock = async (url, opts) => {
    if (String(url).includes('/api/auth/me/crypto-profile')) return _defaultFetch(url, opts);
    capturedHeaders = opts.headers;
    return new Response(JSON.stringify({ created: 1, updated: 0, skipped: 0 }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };

  await pushSync();
  assert.ok(capturedHeaders);
  assert.ok(capturedHeaders['Authorization']?.startsWith('Bearer '), '应有 Authorization Bearer 头');
  assert.ok(capturedHeaders['X-CSRF-Token'], '应有 X-CSRF-Token 头（写端点强制校验，缺失会 403）');
  assert.equal(capturedHeaders['Content-Type'], 'application/json');
});

await test('云端状态未知（GET 500）→ 不上传档案（避免误覆盖另一台设备的档案）', async () => {
  resetAll();
  authMock._simulateLogin();
  museMock._seedInspirations([
    { title: '灵感', url: '', note: '', mood: '', selectedText: '', createdAt: 1718000000000 },
  ]);
  await enableCrypto('unknown-state-pass-123');

  let putCount = 0;
  _fetchMock = async (url, opts) => {
    if (String(url).includes('/api/auth/me/crypto-profile')) {
      if (opts.method === 'PUT') {
        putCount++;
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      // GET 失败：云端状态**未知**（不能当作「云端没有档案」）
      return new Response('{"detail":"server error"}', {
        status: 500, headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(JSON.stringify({ created: 1, updated: 0, skipped: 0 }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };

  await pushSync();
  assert.equal(putCount, 0, '云端状态未知时不得上传档案（否则会覆盖另一台设备的 salt，历史密文永久不可解）');
});

await test('云端已有其它设备的档案（salt 不同）→ 不覆盖，保留远端', async () => {
  resetAll();
  authMock._simulateLogin();
  museMock._seedInspirations([
    { title: '灵感', url: '', note: '', mood: '', selectedText: '', createdAt: 1718000000000 },
  ]);
  await enableCrypto('mismatch-remote-pass-123');
  const local = await getLocalProfile();
  assert.ok(local, '本地应有档案');

  let putCount = 0;
  _fetchMock = async (url, opts) => {
    if (String(url).includes('/api/auth/me/crypto-profile')) {
      if (opts.method === 'PUT') {
        putCount++;
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      // GET 返回**另一台设备**的档案（salt 不同）
      return new Response(JSON.stringify({
        salt: 'YW5vdGhlci1kZXZpY2Utc2FsdC12YWx1ZQ==',
        iterations: 100000,
        kdf: 'PBKDF2-SHA256-100000',
        salt_fingerprint: 'ffffffff',
        verification: { ciphertext: 'eA==', nonce: 'eQ==' },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(JSON.stringify({ created: 1, updated: 0, skipped: 0 }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };

  await pushSync();
  assert.equal(putCount, 0, '不得覆盖其它设备的档案');
});

console.log('-----------------------------------');
console.log(`结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
