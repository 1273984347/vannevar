// crypto.js — Phase 4.4 Focus E2E 加密同步
//
// 设计目标：
//   - Bridge 服务器永远只能看到密文（data.ciphertext），无法解密快照内容
//   - 密钥派生自用户 passphrase（PBKDF2-SHA256），passphrase 绝不持久化
//   - 派生出的 AES-GCM-256 CryptoKey 保持在内存中，非 extractable
//   - SW 重启后 key 丢失，需要用户重新输入 passphrase（lock 状态）
//   - 旧明文数据向后兼容：pull 时通过 data.encrypted 字段识别
//
// 加密格式（写入 SyncItem.data）：
//   {
//     encrypted: true,
//     alg: "AES-GCM-256",
//     kdf: "PBKDF2-SHA256-100000",
//     ciphertext: "<base64>",          // AES-GCM 密文（含 auth tag）
//     nonce: "<base64 12 bytes>",       // AES-GCM IV
//     v: 1                              // 格式版本号
//   }
//
// 存储位置：chrome.storage.local[CRYPTO_STORE_KEY]
//   {
//     enabled: true,
//     salt: "<base64 16 bytes>",        // PBKDF2 salt（启用时随机生成，固定不变）
//     verification: "<base64>",         // 加密后的固定明文，用于验证 passphrase 正确性
//     iterations: 100000
//   }
//
// 注意：
//   - crypto.subtle 在 MV3 SW / options / popup 中均可用（Chrome 37+）
//   - 不依赖任何第三方库，纯 WebCrypto API
//   - AES-GCM 自带完整性校验（GCM tag），无需额外 HMAC

const CRYPTO_STORE_KEY = 'vannevar_focus_crypto';
const PBKDF2_ITERATIONS = 100_000;
const SALT_BYTES = 16;
const NONCE_BYTES = 12;
const KEY_BITS = 256;
const VERIFICATION_PLAINTEXT = 'vannevar-focus-e2e-verification-v1';
const ALG_NAME = 'AES-GCM-256';
const KDF_NAME = `PBKDF2-SHA256-${PBKDF2_ITERATIONS}`;
const FORMAT_VERSION = 1;

// 内存中的 CryptoKey（非持久化，SW 重启后丢失）
let _inMemoryKey = null;

// ---------- Base64 工具（兼容 Uint8Array 与 SW 环境） ----------

function _bytesToBase64(bytes) {
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

function _base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

// ---------- 内部：从 passphrase 派生 AES-GCM key ----------

async function _deriveKey(passphrase, saltBytes) {
  const enc = new TextEncoder();
  const baseKey = await crypto.subtle.importKey(
    'raw',
    enc.encode(passphrase),
    { name: 'PBKDF2' },
    false,
    ['deriveKey'],
  );
  // non-extractable: 派生后无法 exportKey，密钥永不离开 WebCrypto
  return crypto.subtle.deriveKey(
    {
      name: 'PBKDF2',
      salt: saltBytes,
      iterations: PBKDF2_ITERATIONS,
      hash: 'SHA-256',
    },
    baseKey,
    { name: 'AES-GCM', length: KEY_BITS },
    false,            // extractable=false
    ['encrypt', 'decrypt'],
  );
}

async function _encryptWithKey(key, plaintextStr) {
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
  const enc = new TextEncoder();
  const cipherBuf = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: nonce },
    key,
    enc.encode(plaintextStr),
  );
  return {
    ciphertext: _bytesToBase64(new Uint8Array(cipherBuf)),
    nonce: _bytesToBase64(nonce),
  };
}

async function _decryptWithKey(key, ciphertextB64, nonceB64) {
  const cipherBytes = _base64ToBytes(ciphertextB64);
  const nonceBytes = _base64ToBytes(nonceB64);
  const plainBuf = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: nonceBytes },
    key,
    cipherBytes,
  );
  const dec = new TextDecoder();
  return dec.decode(plainBuf);
}

// ---------- 公开 API ----------

/**
 * 检查 E2E 加密是否已启用（用户设置过 passphrase）。
 * 注意：启用 ≠ 可用。启用后 SW 重启需要重新输入 passphrase 才能解密。
 */
export async function isCryptoEnabled() {
  const data = await chrome.storage.local.get(CRYPTO_STORE_KEY);
  return !!(data[CRYPTO_STORE_KEY] && data[CRYPTO_STORE_KEY].enabled);
}

/**
 * 检查 E2E 加密是否就绪（key 在内存中，可立即加解密）。
 * SW 重启后此函数返回 false，需调用 unlockCrypto(passphrase)。
 */
export function isCryptoReady() {
  return _inMemoryKey !== null;
}

/**
 * 返回加密状态信息（用于 UI 展示，不含敏感数据）。
 */
export async function getCryptoState() {
  const data = await chrome.storage.local.get(CRYPTO_STORE_KEY);
  const cfg = data[CRYPTO_STORE_KEY];
  if (!cfg || !cfg.enabled) {
    return { enabled: false, ready: false };
  }
  return {
    enabled: true,
    ready: isCryptoReady(),
    iterations: cfg.iterations,
    // salt 指纹（前 4 字节 hex），让用户识别这是哪台设备设置的
    saltFingerprint: _saltFingerprint(cfg.salt),
  };
}

function _saltFingerprint(saltB64) {
  try {
    const bytes = _base64ToBytes(saltB64);
    let hex = '';
    for (let i = 0; i < Math.min(4, bytes.length); i++) {
      hex += bytes[i].toString(16).padStart(2, '0');
    }
    return hex;
  } catch (_) {
    return '';
  }
}

/**
 * 启用 E2E 加密：设置 passphrase，派生 key，存 salt + verification。
 * 调用后 key 自动加载到内存（ready=true）。
 *
 * @param {string} passphrase 用户输入的 passphrase（≥ 8 字符）
 * @returns {Promise<{enabled: true, saltFingerprint: string}>}
 * @throws {Error} passphrase 太短或已启用
 */
export async function enableCrypto(passphrase) {
  if (typeof passphrase !== 'string' || passphrase.length < 8) {
    throw new Error('passphrase 至少 8 个字符');
  }
  const existing = await chrome.storage.local.get(CRYPTO_STORE_KEY);
  if (existing[CRYPTO_STORE_KEY] && existing[CRYPTO_STORE_KEY].enabled) {
    throw new Error('E2E 加密已启用，如需更换 passphrase 请先禁用');
  }
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const key = await _deriveKey(passphrase, salt);
  // 用 key 加密固定明文作为 verification token
  const verification = await _encryptWithKey(key, VERIFICATION_PLAINTEXT);
  await chrome.storage.local.set({
    [CRYPTO_STORE_KEY]: {
      enabled: true,
      salt: _bytesToBase64(salt),
      verification: verification.ciphertext,        // 仅密文
      verificationNonce: verification.nonce,
      iterations: PBKDF2_ITERATIONS,
    },
  });
  _inMemoryKey = key;
  return { enabled: true, saltFingerprint: _saltFingerprint(_bytesToBase64(salt)) };
}

/**
 * 禁用 E2E 加密：清除 salt + verification + 内存 key。
 * 警告：禁用后云端已有的密文数据将无法解密（需保留 passphrase 或重新启用相同 passphrase）。
 *
 * @param {string} passphrase 验证 passphrase，避免误操作
 * @returns {Promise<void>}
 * @throws {Error} passphrase 错误或未启用
 */
export async function disableCrypto(passphrase) {
  const data = await chrome.storage.local.get(CRYPTO_STORE_KEY);
  const cfg = data[CRYPTO_STORE_KEY];
  if (!cfg || !cfg.enabled) {
    throw new Error('E2E 加密未启用');
  }
  // 验证 passphrase 正确性
  const ok = await unlockCrypto(passphrase);
  if (!ok) {
    throw new Error('passphrase 错误，无法禁用');
  }
  await chrome.storage.local.remove(CRYPTO_STORE_KEY);
  _inMemoryKey = null;
}

/**
 * 解锁：SW 重启后用 passphrase 重新派生 key 装载到内存。
 *
 * @param {string} passphrase
 * @returns {Promise<boolean>} true=解锁成功，false=passphrase 错误
 */
export async function unlockCrypto(passphrase) {
  const data = await chrome.storage.local.get(CRYPTO_STORE_KEY);
  const cfg = data[CRYPTO_STORE_KEY];
  if (!cfg || !cfg.enabled) {
    return false;
  }
  const salt = _base64ToBytes(cfg.salt);
  const key = await _deriveKey(passphrase, salt);
  // 用 verification token 验证 passphrase 是否正确
  try {
    const decrypted = await _decryptWithKey(key, cfg.verification, cfg.verificationNonce);
    if (decrypted !== VERIFICATION_PLAINTEXT) {
      return false;
    }
  } catch (_) {
    // GCM tag 校验失败 → passphrase 错误
    return false;
  }
  _inMemoryKey = key;
  return true;
}

/**
 * 锁定：清除内存 key（保留 salt + verification）。
 * 调用后 ready=false，需重新 unlockCrypto 才能加解密。
 */
export function lockCrypto() {
  _inMemoryKey = null;
}

/**
 * 加密任意 JSON-able 对象。
 *
 * @param {object} data 明文对象（如 {title, tabs, createdAt}）
 * @returns {Promise<object>} 密文容器 {encrypted, alg, kdf, ciphertext, nonce, v}
 * @throws {Error} 加密未就绪（未启用或未解锁）
 */
export async function encryptData(data) {
  if (!isCryptoReady()) {
    throw new Error('E2E 加密未就绪，请先输入 passphrase 解锁');
  }
  const plaintext = JSON.stringify(data);
  const { ciphertext, nonce } = await _encryptWithKey(_inMemoryKey, plaintext);
  return {
    encrypted: true,
    alg: ALG_NAME,
    kdf: KDF_NAME,
    ciphertext,
    nonce,
    v: FORMAT_VERSION,
  };
}

/**
 * 解密密文容器。
 *
 * @param {object} encryptedObj {encrypted:true, ciphertext, nonce, ...}
 * @returns {Promise<object|null>} 明文对象；无法解密（未启用/未解锁/旧数据）返回 null
 */
export async function decryptData(encryptedObj) {
  if (!encryptedObj || typeof encryptedObj !== 'object') return null;
  if (!encryptedObj.encrypted) return null;  // 旧明文数据，调用方应自行处理
  if (!isCryptoReady()) {
    throw new Error('E2E 加密未就绪，请先输入 passphrase 解锁');
  }
  const plaintext = await _decryptWithKey(
    _inMemoryKey,
    encryptedObj.ciphertext,
    encryptedObj.nonce,
  );
  try {
    return JSON.parse(plaintext);
  } catch (_) {
    return null;
  }
}

/**
 * 判断 data 对象是否为加密格式。
 * 用于 pull 时区分密文容器 vs 旧明文数据。
 */
export function isEncryptedData(data) {
  return !!(data && typeof data === 'object' && data.encrypted === true);
}
