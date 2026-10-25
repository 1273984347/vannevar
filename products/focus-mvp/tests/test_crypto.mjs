// tests/test_crypto.mjs — Phase 4.4 crypto.js 单元测试
//
// 运行：node tests/test_crypto.mjs
//
// 测试 crypto.js 的核心契约：
//   1. enableCrypto → encryptData → decryptData 往返还原明文
//   2. isCryptoEnabled / isCryptoReady 状态在 enable / lock / disable 后正确切换
//   3. unlockCrypto 用正确 passphrase 解锁、错误 passphrase 返回 false
//   4. isEncryptedData 识别密文容器 vs 旧明文
//   5. 禁用后 salt + verification 被清除，key 不可用
//
// 依赖：Node 18+ 内置 globalThis.crypto（Crypto API，无需手动注入）

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

// Node 18+ 已有 globalThis.crypto（含 subtle / getRandomValues），无需注入

// mock chrome.storage.local（crypto.js 仅用 get / set / remove）
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
};

// mock btoa / atob（crypto.js 用于 base64；Node 没有全局 btoa/atob 直到 v16+，但保险起见显式提供）
if (typeof globalThis.btoa !== 'function') {
  globalThis.btoa = (str) => Buffer.from(str, 'binary').toString('base64');
}
if (typeof globalThis.atob !== 'function') {
  globalThis.atob = (b64) => Buffer.from(b64, 'base64').toString('binary');
}

// 动态加载被测模块（crypto.js 是 ES module）
const cryptoModulePath = pathToFileURL(
  path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\//, '')), '..', 'crypto.js')
).href;
const {
  isCryptoEnabled,
  isCryptoReady,
  getCryptoState,
  enableCrypto,
  disableCrypto,
  unlockCrypto,
  lockCrypto,
  encryptData,
  decryptData,
  isEncryptedData,
} = await import(cryptoModulePath);

let pass = 0;
let fail = 0;
function test(name, fn) {
  return Promise.resolve(fn()).then(
    () => { pass++; console.log(`  ✓ ${name}`); },
    (err) => { fail++; console.error(`  ✗ ${name}\n    ${err.stack}`); }
  );
}

console.log('Phase 4.4 crypto.js 单元测试');
console.log('---------------------------');

// 清理 storage 防止测试间污染
function resetStorage() { _storage.clear(); }

await test('初始状态：未启用、未就绪', async () => {
  resetStorage();
  assert.equal(await isCryptoEnabled(), false, 'isCryptoEnabled 应为 false');
  assert.equal(isCryptoReady(), false, 'isCryptoReady 应为 false');
  const state = await getCryptoState();
  assert.equal(state.enabled, false);
  assert.equal(state.ready, false);
});

await test('enableCrypto 派生 key 并装载到内存', async () => {
  resetStorage();
  const result = await enableCrypto('test-passphrase-123');
  assert.equal(result.enabled, true);
  assert.ok(result.saltFingerprint, '应返回 salt 指纹');
  assert.match(result.saltFingerprint, /^[0-9a-f]{8}$/, 'salt 指纹为 4 字节 hex');
  assert.equal(await isCryptoEnabled(), true, '启用后 isCryptoEnabled=true');
  assert.equal(isCryptoReady(), true, '启用后 isCryptoReady=true');
});

await test('encryptData → decryptData 往返还原明文', async () => {
  resetStorage();
  await enableCrypto('roundtrip-pass-123');
  const plaintext = {
    title: '测试快照',
    tabs: [{ url: 'https://example.com', title: '示例' }],
    createdAt: 1718000000000,
  };
  const encrypted = await encryptData(plaintext);
  assert.equal(encrypted.encrypted, true);
  assert.equal(encrypted.alg, 'AES-GCM-256');
  assert.equal(encrypted.kdf, 'PBKDF2-SHA256-100000');
  assert.equal(encrypted.v, 1);
  assert.ok(encrypted.ciphertext, 'ciphertext 应非空');
  assert.ok(encrypted.nonce, 'nonce 应非空');
  // 密文与明文不应相等
  assert.notEqual(encrypted.ciphertext, JSON.stringify(plaintext));
  const decrypted = await decryptData(encrypted);
  assert.deepEqual(decrypted, plaintext, '解密后应与原明文一致');
});

await test('isEncryptedData 识别密文容器 vs 旧明文', () => {
  assert.equal(isEncryptedData({ encrypted: true, ciphertext: 'x', nonce: 'y' }), true);
  assert.equal(isEncryptedData({ title: '明文快照', tabs: [] }), false);
  assert.equal(isEncryptedData(null), false);
  assert.equal(isEncryptedData(undefined), false);
  assert.equal(isEncryptedData('string'), false);
  assert.equal(isEncryptedData({ encrypted: false }), false);
});

await test('lockCrypto 清除内存 key 但保留 salt + verification', async () => {
  resetStorage();
  await enableCrypto('lock-test-pass-123');
  assert.equal(isCryptoReady(), true);
  lockCrypto();
  assert.equal(isCryptoReady(), false, 'lock 后 isCryptoReady=false');
  // 仍处于启用状态（salt 仍在 storage）
  assert.equal(await isCryptoEnabled(), true, 'lock 后 isCryptoEnabled 应仍为 true');
  const state = await getCryptoState();
  assert.equal(state.enabled, true);
  assert.equal(state.ready, false);
  assert.ok(state.saltFingerprint, 'salt 指纹应保留');
});

await test('unlockCrypto 用正确 passphrase 重新装载 key', async () => {
  resetStorage();
  await enableCrypto('unlock-correct-pass-123');
  lockCrypto();
  assert.equal(isCryptoReady(), false);
  const ok = await unlockCrypto('unlock-correct-pass-123');
  assert.equal(ok, true, '正确 passphrase 应解锁成功');
  assert.equal(isCryptoReady(), true, '解锁后 isCryptoReady=true');
});

await test('unlockCrypto 用错误 passphrase 返回 false', async () => {
  resetStorage();
  await enableCrypto('correct-pass-123');
  lockCrypto();
  const ok = await unlockCrypto('wrong-pass');
  assert.equal(ok, false, '错误 passphrase 应返回 false');
  assert.equal(isCryptoReady(), false, '错误 passphrase 后 key 不应装载');
});

await test('encryptData 未就绪时抛错', async () => {
  resetStorage();
  await enableCrypto('err-test-pass-123');
  lockCrypto();
  await assert.rejects(
    () => encryptData({ foo: 'bar' }),
    /未就绪|未解锁/,
    '加密未就绪时应抛错'
  );
});

await test('decryptData 对旧明文返回 null', async () => {
  resetStorage();
  await enableCrypto('plain-fallback-pass-123');
  const result = await decryptData({ title: '旧明文', tabs: [] });
  assert.equal(result, null, '对没有 encrypted 标记的 data 应返回 null（调用方自行处理）');
});

await test('disableCrypto 验证 passphrase 后清除 storage + 内存 key', async () => {
  resetStorage();
  await enableCrypto('disable-pass-123');
  assert.equal(await isCryptoEnabled(), true);
  await disableCrypto('disable-pass-123');
  assert.equal(await isCryptoEnabled(), false, '禁用后 isCryptoEnabled=false');
  assert.equal(isCryptoReady(), false, '禁用后 isCryptoReady=false');
  const state = await getCryptoState();
  assert.equal(state.enabled, false);
});

await test('disableCrypto 用错误 passphrase 抛错', async () => {
  resetStorage();
  await enableCrypto('real-pass-123');
  await assert.rejects(
    () => disableCrypto('wrong-pass'),
    /passphrase 错误/,
    '错误 passphrase 应抛错'
  );
  // 仍处于启用状态
  assert.equal(await isCryptoEnabled(), true, '错误 passphrase 后加密不应被禁用');
});

await test('enableCrypto 拒绝短 passphrase', async () => {
  resetStorage();
  await assert.rejects(
    () => enableCrypto('short'),
    /至少 8/,
    '短 passphrase 应被拒绝'
  );
});

await test('enableCrypto 重复启用抛错', async () => {
  resetStorage();
  await enableCrypto('first-pass-123');
  await assert.rejects(
    () => enableCrypto('second-pass-123'),
    /已启用|已启用/,
    '已启用时再次启用应抛错'
  );
});

await test('加密格式：每次加密 nonce 不同（随机性）', async () => {
  resetStorage();
  await enableCrypto('random-nonce-pass-123');
  const data = { same: 'payload' };
  const a = await encryptData(data);
  const b = await encryptData(data);
  assert.notEqual(a.nonce, b.nonce, '相同明文两次加密 nonce 应不同');
  assert.notEqual(a.ciphertext, b.ciphertext, '相同明文两次加密 ciphertext 应不同（因 nonce 不同）');
  // 但都能解密回原明文
  assert.deepEqual(await decryptData(a), data);
  assert.deepEqual(await decryptData(b), data);
});

// B-P1-2 修复：损坏密文（GCM tag mismatch）解密路径测试
// AES-GCM 自带完整性校验（auth tag），篡改 ciphertext 或 nonce 应导致解密抛错（不是返回 null）
await test('B-P1-2: 损坏密文 GCM tag mismatch 解密抛错', async () => {
  resetStorage();
  await enableCrypto('tamper-test-pass-123');
  const plaintext = { secret: 'sensitive-data' };
  const encrypted = await encryptData(plaintext);
  // 篡改 ciphertext 最后 1 字节（翻转 bit）
  const tamperedCiphertext = _flipLastBase64Char(encrypted.ciphertext);
  await assert.rejects(
    () => decryptData({ ...encrypted, ciphertext: tamperedCiphertext }),
    '篡改 ciphertext 后解密应抛错（GCM tag 校验失败）'
  );
  // 篡改 nonce
  const tamperedNonce = _flipLastBase64Char(encrypted.nonce);
  await assert.rejects(
    () => decryptData({ ...encrypted, nonce: tamperedNonce }),
    '篡改 nonce 后解密应抛错（GCM tag 校验失败）'
  );
  // 原始密文仍能正常解密
  assert.deepEqual(await decryptData(encrypted), plaintext, '原始密文应正常解密');
});

// B-P2-2 修复：非 ASCII passphrase（中文 / emoji）测试
// 验证 PBKDF2 + AES-GCM 对 Unicode passphrase 的支持
await test('B-P2-2: 非 ASCII passphrase（中文/emoji）正常工作', async () => {
  resetStorage();
  const unicodePassphrases = [
    '中文密码短语-123',           // 中文字符
    '密码🔑安全-456',             // 中文字符 + emoji
    '日本語パスワード-789',       // 日文字符
    '한국어비밀번호-012',         // 韩文字符
  ];
  for (const pass of unicodePassphrases) {
    resetStorage();
    await enableCrypto(pass);
    assert.equal(isCryptoReady(), true, `passphrase "${pass}" 启用后应就绪`);
    const data = { test: `数据 for ${pass}`, ts: Date.now() };
    const encrypted = await encryptData(data);
    assert.equal(encrypted.encrypted, true);
    // 锁定后用相同 Unicode passphrase 解锁
    lockCrypto();
    assert.equal(isCryptoReady(), false);
    const ok = await unlockCrypto(pass);
    assert.equal(ok, true, `Unicode passphrase "${pass}" 解锁应成功`);
    // 解密验证
    const decrypted = await decryptData(encrypted);
    assert.deepEqual(decrypted, data, `Unicode passphrase "${pass}" 加解密往返应一致`);
    // 错误的 Unicode passphrase 应返回 false
    lockCrypto();
    const wrongOk = await unlockCrypto(pass + '错');
    assert.equal(wrongOk, false, '错误的 Unicode passphrase 应返回 false');
  }
});

// 辅助函数：翻转 base64 字符串最后一个字符的某一位（模拟密文篡改）
function _flipLastBase64Char(b64) {
  const chars = b64.split('');
  const lastIdx = chars.length - 1;
  const lastChar = chars[lastIdx];
  // 翻转最后一个字符为不同的 base64 字符（保证改了至少 1 bit）
  const base64Chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const idx = base64Chars.indexOf(lastChar);
  const newIdx = (idx + 1) % base64Chars.length;
  chars[lastIdx] = base64Chars[newIdx];
  return chars.join('');
}

console.log('---------------------------');
console.log(`结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
