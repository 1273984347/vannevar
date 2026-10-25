// tests/test_crypto.mjs — Muse 端到端加密单元测试
//
// 运行：node tests/test_crypto.mjs
//
// 覆盖：
//   1. 加密核心契约（与 Focus 同构）：enable → encrypt → decrypt 往返、状态切换、
//      错误 passphrase、损坏密文、非 ASCII passphrase
//   2. Muse 专属 storeKey 隔离（与 Focus 互不污染）
//   3. 跨设备解密（A2）：设备 B 用云端档案里的 salt + 同一 passphrase 派生同一把密钥，
//      能解密设备 A 推送的密文
//
// 依赖：Node 18+ 内置 globalThis.crypto（Crypto API，无需手动注入）
// 不依赖 loader.mjs —— core/crypto.js 不触碰 SQLite WASM。

import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

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

// mock btoa / atob（base64；Node 16+ 有全局实现，保险起见显式提供）
if (typeof globalThis.btoa !== 'function') {
  globalThis.btoa = (str) => Buffer.from(str, 'binary').toString('base64');
}
if (typeof globalThis.atob !== 'function') {
  globalThis.atob = (b64) => Buffer.from(b64, 'base64').toString('binary');
}

// 动态加载被测模块（core/crypto.js 是 ES module）
const cryptoModulePath = pathToFileURL(
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'core', 'crypto.js')
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
  getLocalProfile,
  adoptRemoteProfile,
} = await import(cryptoModulePath);

// 共享层工厂：用于模拟「第二台设备」（独立 storeKey + 独立内存 key）
const sharedPath = pathToFileURL(
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'shared', 'vannevar-crypto.js')
).href;
const { createCryptoModule } = await import(sharedPath);

let pass = 0;
let fail = 0;
function test(name, fn) {
  return Promise.resolve(fn()).then(
    () => { pass++; console.log(`  ✓ ${name}`); },
    (err) => { fail++; console.error(`  ✗ ${name}\n    ${err.stack}`); }
  );
}

console.log('Muse 端到端加密单元测试');
console.log('---------------------------');

function resetStorage() { _storage.clear(); }

// 辅助：翻转 base64 字符串最后一个字符的某一位（模拟密文篡改）
function _flipLastBase64Char(b64) {
  const chars = b64.split('');
  const lastIdx = chars.length - 1;
  const lastChar = chars[lastIdx];
  const base64Chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const idx = base64Chars.indexOf(lastChar);
  const newIdx = (idx + 1) % base64Chars.length;
  chars[lastIdx] = base64Chars[newIdx];
  return chars.join('');
}

// ============ 加密核心契约 ============

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
    title: '测试灵感',
    url: 'https://example.com',
    note: '一段笔记',
    selectedText: '选中的文字',
    createdAt: 1718000000000,
  };
  const encrypted = await encryptData(plaintext);
  assert.equal(encrypted.encrypted, true);
  assert.equal(encrypted.alg, 'AES-GCM-256');
  assert.equal(encrypted.kdf, 'PBKDF2-SHA256-100000');
  assert.equal(encrypted.v, 1);
  assert.ok(encrypted.ciphertext, 'ciphertext 应非空');
  assert.ok(encrypted.nonce, 'nonce 应非空');
  assert.notEqual(encrypted.ciphertext, JSON.stringify(plaintext));
  const decrypted = await decryptData(encrypted);
  assert.deepEqual(decrypted, plaintext, '解密后应与原明文一致');
});

await test('isEncryptedData 识别密文容器 vs 旧明文', () => {
  assert.equal(isEncryptedData({ encrypted: true, ciphertext: 'x', nonce: 'y' }), true);
  assert.equal(isEncryptedData({ title: '明文灵感' }), false);
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
  assert.equal(await unlockCrypto('unlock-correct-pass-123'), true, '正确 passphrase 应解锁成功');
  assert.equal(isCryptoReady(), true, '解锁后 isCryptoReady=true');
});

await test('unlockCrypto 用错误 passphrase 返回 false', async () => {
  resetStorage();
  await enableCrypto('correct-pass-123');
  lockCrypto();
  assert.equal(await unlockCrypto('wrong-pass'), false, '错误 passphrase 应返回 false');
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
  assert.equal(await decryptData({ title: '旧明文' }), null, '对没有 encrypted 标记的 data 应返回 null');
});

await test('disableCrypto 验证 passphrase 后清除 storage + 内存 key', async () => {
  resetStorage();
  await enableCrypto('disable-pass-123');
  assert.equal(await isCryptoEnabled(), true);
  await disableCrypto('disable-pass-123');
  assert.equal(await isCryptoEnabled(), false, '禁用后 isCryptoEnabled=false');
  assert.equal(isCryptoReady(), false, '禁用后 isCryptoReady=false');
  assert.equal((await getCryptoState()).enabled, false);
});

await test('disableCrypto 用错误 passphrase 抛错', async () => {
  resetStorage();
  await enableCrypto('real-pass-123');
  await assert.rejects(
    () => disableCrypto('wrong-pass'),
    /passphrase 错误/,
    '错误 passphrase 应抛错'
  );
  assert.equal(await isCryptoEnabled(), true, '错误 passphrase 后加密不应被禁用');
});

await test('enableCrypto 拒绝短 passphrase', async () => {
  resetStorage();
  await assert.rejects(() => enableCrypto('short'), /至少 8/, '短 passphrase 应被拒绝');
});

await test('enableCrypto 重复启用抛错', async () => {
  resetStorage();
  await enableCrypto('first-pass-123');
  await assert.rejects(() => enableCrypto('second-pass-123'), /已启用/, '已启用时再次启用应抛错');
});

await test('加密格式：每次加密 nonce 不同（随机性）', async () => {
  resetStorage();
  await enableCrypto('random-nonce-pass-123');
  const data = { same: 'payload' };
  const a = await encryptData(data);
  const b = await encryptData(data);
  assert.notEqual(a.nonce, b.nonce, '相同明文两次加密 nonce 应不同');
  assert.notEqual(a.ciphertext, b.ciphertext, '相同明文两次加密 ciphertext 应不同');
  assert.deepEqual(await decryptData(a), data);
  assert.deepEqual(await decryptData(b), data);
});

await test('损坏密文（GCM tag mismatch）解密抛错', async () => {
  resetStorage();
  await enableCrypto('tamper-test-pass-123');
  const plaintext = { secret: 'sensitive-data' };
  const encrypted = await encryptData(plaintext);
  await assert.rejects(
    () => decryptData({ ...encrypted, ciphertext: _flipLastBase64Char(encrypted.ciphertext) }),
    '篡改 ciphertext 后解密应抛错（GCM tag 校验失败）'
  );
  await assert.rejects(
    () => decryptData({ ...encrypted, nonce: _flipLastBase64Char(encrypted.nonce) }),
    '篡改 nonce 后解密应抛错（GCM tag 校验失败）'
  );
  assert.deepEqual(await decryptData(encrypted), plaintext, '原始密文应正常解密');
});

await test('非 ASCII passphrase（中文/emoji）正常工作', async () => {
  const unicodePassphrases = ['中文密码短语-123', '密码🔑安全-456', '日本語パスワード-789'];
  for (const pass of unicodePassphrases) {
    resetStorage();
    await enableCrypto(pass);
    assert.equal(isCryptoReady(), true, `passphrase "${pass}" 启用后应就绪`);
    const data = { test: `数据 for ${pass}`, ts: Date.now() };
    const encrypted = await encryptData(data);
    lockCrypto();
    assert.equal(await unlockCrypto(pass), true, `Unicode passphrase "${pass}" 解锁应成功`);
    assert.deepEqual(await decryptData(encrypted), data, `Unicode passphrase "${pass}" 加解密往返应一致`);
    lockCrypto();
    assert.equal(await unlockCrypto(pass + '错'), false, '错误的 Unicode passphrase 应返回 false');
  }
});

// ============ Muse 专属 storeKey 隔离 ============

await test('Muse 使用独立 storeKey，与 Focus 互不污染', async () => {
  resetStorage();
  await enableCrypto('isolation-pass-123');
  const raw = await chrome.storage.local.get('vannevar_muse_crypto');
  assert.ok(raw['vannevar_muse_crypto'], '应写入 vannevar_muse_crypto');
  assert.equal(raw['vannevar_focus_crypto'], undefined, '不应写入 Focus 的 storeKey');
});

// ============ A2：加密档案与跨设备解密 ============

await test('getLocalProfile 返回可上云的档案（salt + verification 密文）', async () => {
  resetStorage();
  await enableCrypto('profile-pass-123');
  const profile = await getLocalProfile();
  assert.ok(profile, '启用后应有档案');
  assert.equal(typeof profile.salt, 'string');
  assert.ok(profile.salt.length > 0, 'salt 非空');
  assert.equal(profile.iterations, 100000, 'PBKDF2 迭代次数');
  assert.equal(profile.kdf, 'PBKDF2-SHA256-100000');
  assert.match(profile.salt_fingerprint, /^[0-9a-f]{8}$/);
  assert.ok(profile.verification.ciphertext, 'verification 密文非空');
  assert.ok(profile.verification.nonce, 'verification nonce 非空');
  // 档案里绝不能出现 passphrase 明文
  assert.ok(!JSON.stringify(profile).includes('profile-pass-123'), '档案不得包含 passphrase');
});

await test('未启用加密时 getLocalProfile 返回 null', async () => {
  resetStorage();
  assert.equal(await getLocalProfile(), null);
});

await test('A2 跨设备：设备 B 用云端 salt 派生同一密钥，可解密设备 A 的密文', async () => {
  resetStorage();

  // —— 设备 A：启用加密，产出加密档案（salt + verification 密文）——
  const deviceA = createCryptoModule({
    storeKey: 'vannevar_muse_crypto',
    verificationPlaintext: 'vannevar-e2e-verification-v1',
  });
  await deviceA.enableCrypto('shared-device-pass-123');
  const profile = await deviceA.getLocalProfile();
  assert.ok(profile, '设备 A 应产出档案');

  // —— 设备 B：全新设备（独立 storeKey + 独立内存 key），本地尚无档案 ——
  const deviceB = createCryptoModule({
    storeKey: 'vannevar_muse_crypto_device_b',
    verificationPlaintext: 'vannevar-e2e-verification-v1',
  });
  assert.equal(await deviceB.isCryptoEnabled(), false, '设备 B 初始未启用');

  // 设备 B 用同一 passphrase + 云端 salt 采用档案
  const adopted = await deviceB.adoptRemoteProfile('shared-device-pass-123', profile);
  assert.equal(adopted.ok, true, '设备 B 应能采用云端档案');
  assert.equal(await deviceB.isCryptoEnabled(), true, '采用后设备 B 处于启用状态');
  assert.equal(adopted.saltFingerprint, profile.salt_fingerprint, 'salt 指纹应一致（同一 salt）');

  // 设备 B 用设备 A 的密钥加密 → 设备 A 能解密（同一密钥）
  const cipherFromB = await deviceB.encryptData({ title: '设备 B 加密的内容' });
  const plainOnA = await deviceA.decryptData(cipherFromB);
  assert.deepEqual(plainOnA, { title: '设备 B 加密的内容' }, '设备 A 应能解密设备 B 的密文');

  // 设备 A 加密 → 设备 B 能解密
  const cipherFromA = await deviceA.encryptData({ title: '设备 A 加密的内容' });
  const plainOnB = await deviceB.decryptData(cipherFromA);
  assert.deepEqual(plainOnB, { title: '设备 A 加密的内容' }, '设备 B 应能解密设备 A 的密文');
});

await test('A2 跨设备：passphrase 不一致时采用档案失败并给出明确原因', async () => {
  resetStorage();
  const deviceA = createCryptoModule({
    storeKey: 'vannevar_muse_crypto',
    verificationPlaintext: 'vannevar-e2e-verification-v1',
  });
  await deviceA.enableCrypto('right-device-pass-123');
  const profile = await deviceA.getLocalProfile();

  const deviceB = createCryptoModule({
    storeKey: 'vannevar_muse_crypto_device_b2',
    verificationPlaintext: 'vannevar-e2e-verification-v1',
  });
  const adopted = await deviceB.adoptRemoteProfile('wrong-device-pass-999', profile);
  assert.equal(adopted.ok, false, '错误 passphrase 不应采用成功');
  assert.equal(adopted.reason, 'passphrase-mismatch', '应给出 passphrase-mismatch 原因');
  assert.equal(await deviceB.isCryptoEnabled(), false, '失败后不应处于启用状态');
});

await test('A2 跨设备：enableCrypto 复用远端 salt（reusedRemoteSalt=true）', async () => {
  resetStorage();
  const deviceA = createCryptoModule({
    storeKey: 'vannevar_muse_crypto',
    verificationPlaintext: 'vannevar-e2e-verification-v1',
  });
  await deviceA.enableCrypto('reuse-salt-pass-123');
  const profile = await deviceA.getLocalProfile();

  const deviceB = createCryptoModule({
    storeKey: 'vannevar_muse_crypto_device_b3',
    verificationPlaintext: 'vannevar-e2e-verification-v1',
  });
  const result = await deviceB.enableCrypto('reuse-salt-pass-123', { remoteProfile: profile });
  assert.equal(result.reusedRemoteSalt, true, '应复用远端 salt');
  assert.equal(result.saltFingerprint, profile.salt_fingerprint, 'salt 指纹应与远端一致');
});

await test('A2 跨设备：enableCrypto 复用远端 salt 时 passphrase 不一致抛错', async () => {
  resetStorage();
  const deviceA = createCryptoModule({
    storeKey: 'vannevar_muse_crypto',
    verificationPlaintext: 'vannevar-e2e-verification-v1',
  });
  await deviceA.enableCrypto('consistent-pass-123');
  const profile = await deviceA.getLocalProfile();

  const deviceB = createCryptoModule({
    storeKey: 'vannevar_muse_crypto_device_b4',
    verificationPlaintext: 'vannevar-e2e-verification-v1',
  });
  await assert.rejects(
    () => deviceB.enableCrypto('mismatch-pass-456', { remoteProfile: profile }),
    /与其它设备不一致/,
    'passphrase 与远端不一致时应抛错'
  );
  assert.equal(await deviceB.isCryptoEnabled(), false, '抛错后不应处于启用状态');
});

await test('A2 跨设备：adoptRemoteProfile 对空档案返回 no-remote-profile', async () => {
  resetStorage();
  const deviceB = createCryptoModule({
    storeKey: 'vannevar_muse_crypto_device_b5',
    verificationPlaintext: 'vannevar-e2e-verification-v1',
  });
  const adopted = await deviceB.adoptRemoteProfile('any-pass-123', null);
  assert.equal(adopted.ok, false);
  assert.equal(adopted.reason, 'no-remote-profile');
});

console.log('---------------------------');
console.log(`结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
