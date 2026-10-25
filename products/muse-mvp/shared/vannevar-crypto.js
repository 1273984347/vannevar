// vannevar-crypto.js — Vannevar 三产品共享的端到端加密核心
//
// 单一真源（single source of truth）：
//   shared/vannevar-crypto.js  ← 本文件（源）
//   products/focus-mvp/lib/vannevar-crypto.js   ← 副本（Focus 扩展内）
//   products/muse-mvp/shared/vannevar-crypto.js ← 副本（Muse 扩展内）
// 副本一致性由 scripts/check_shared_sync.py 门禁保证。
//
// 设计目标：
//   - Bridge 服务器永远只能看到密文（data.ciphertext），无法解密内容
//   - 密钥派生自用户 passphrase（PBKDF2-SHA256），passphrase 绝不持久化、不上云
//   - 派生出的 AES-GCM-256 CryptoKey 保持在内存中，非 extractable
//   - SW 重启后 key 丢失，需要用户重新输入 passphrase（lock 状态）
//   - 旧明文数据向后兼容：pull 时通过 data.encrypted 字段识别
//   - 跨设备：PBKDF2 salt（公开参数，非密钥）可上云，供其它设备派生同一密钥
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
// 加密档案（跨设备用，可上云）：
//   {
//     salt: "<base64 16 bytes>",
//     iterations: 100000,
//     kdf: "PBKDF2-SHA256-100000",
//     salt_fingerprint: "<8 hex>",
//     verification: { ciphertext: "<base64>", nonce: "<base64>" }
//   }
//   注意：salt 是 PBKDF2 的公开盐值，不是密钥；passphrase 永不出现在档案里。
//
// 注意：
//   - crypto.subtle 在 MV3 SW / options / popup 中均可用（Chrome 37+）
//   - 不依赖任何第三方库，纯 WebCrypto API
//   - AES-GCM 自带完整性校验（GCM tag），无需额外 HMAC

export const DEFAULT_PBKDF2_ITERATIONS = 100_000;
const SALT_BYTES = 16;
const NONCE_BYTES = 12;
const KEY_BITS = 256;
const ALG_NAME = 'AES-GCM-256';
const FORMAT_VERSION = 1;

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
 * 创建加密模块实例。
 *
 * 每个产品用自己的 storeKey 与 verification 明文，避免两个扩展的
 * 本地状态互相污染（同一台机器可能同时装 Focus 与 Muse）。
 *
 * @param {object} opts
 * @param {string} opts.storeKey             chrome.storage.local 键名
 * @param {string} opts.verificationPlaintext 用于验证 passphrase 正确性的固定明文
 * @param {number} [opts.iterations]          PBKDF2 迭代次数，默认 100000
 * @returns {object} 加密模块（Focus / Muse 共用同一套 API）
 */
export function createCryptoModule({ storeKey, verificationPlaintext, iterations = DEFAULT_PBKDF2_ITERATIONS }) {
  if (!storeKey || typeof storeKey !== 'string') {
    throw new Error('createCryptoModule: storeKey 必须是非空字符串');
  }
  if (!verificationPlaintext || typeof verificationPlaintext !== 'string') {
    throw new Error('createCryptoModule: verificationPlaintext 必须是非空字符串');
  }

  const KDF_NAME = `PBKDF2-SHA256-${iterations}`;

  // 内存中的 CryptoKey（非持久化，SW 重启后丢失）
  let _inMemoryKey = null;

  // ---------- 内部：派生 / 加解密 ----------

  async function _deriveKey(passphrase, saltBytes, iterationsOverride) {
    const enc = new TextEncoder();
    const baseKey = await crypto.subtle.importKey(
      'raw',
      enc.encode(passphrase),
      { name: 'PBKDF2' },
      false,
      ['deriveKey'],
    );
    // non-extractable: 派生后无法 exportKey，密钥永不离开 WebCrypto
    // iterationsOverride：派生参数必须与「产生该 salt 时」完全一致，
    // 否则跨设备派生出的是不同的密钥（PBKDF2 的迭代次数是密钥的一部分）。
    return crypto.subtle.deriveKey(
      {
        name: 'PBKDF2',
        salt: saltBytes,
        iterations: iterationsOverride || iterations,
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

  // ---------- 本地存储 ----------

  async function _readConfig() {
    const data = await chrome.storage.local.get(storeKey);
    return data[storeKey] || null;
  }

  async function _writeConfig(cfg) {
    await chrome.storage.local.set({ [storeKey]: cfg });
  }

  // ---------- 公开 API ----------

  /**
   * 检查 E2E 加密是否已启用（用户设置过 passphrase）。
   * 注意：启用 ≠ 可用。启用后 SW 重启需要重新输入 passphrase 才能解密。
   */
  async function isCryptoEnabled() {
    const cfg = await _readConfig();
    return !!(cfg && cfg.enabled);
  }

  /**
   * 检查 E2E 加密是否就绪（key 在内存中，可立即加解密）。
   * SW 重启后此函数返回 false，需调用 unlockCrypto(passphrase)。
   */
  function isCryptoReady() {
    return _inMemoryKey !== null;
  }

  /**
   * 返回加密状态信息（用于 UI 展示，不含敏感数据）。
   */
  async function getCryptoState() {
    const cfg = await _readConfig();
    if (!cfg || !cfg.enabled) {
      return { enabled: false, ready: false };
    }
    return {
      enabled: true,
      ready: isCryptoReady(),
      iterations: cfg.iterations || iterations,
      // salt 指纹（前 4 字节 hex），让用户识别这是哪台设备设置的
      saltFingerprint: _saltFingerprint(cfg.salt),
    };
  }

  /**
   * 启用 E2E 加密：设置 passphrase，派生 key，存 salt + verification。
   * 调用后 key 自动加载到内存（ready=true）。
   *
   * @param {string} passphrase 用户输入的 passphrase（≥ 8 字符）
   * @param {object} [opts]
   * @param {object} [opts.remoteProfile] 远端加密档案（跨设备场景）。
   *        提供时复用其 salt，使本设备与其它设备派生同一密钥；
   *        passphrase 不一致会抛错。
   * @returns {Promise<{enabled: true, saltFingerprint: string, reusedRemoteSalt: boolean}>}
   * @throws {Error} passphrase 太短 / 已启用 / 与远端 passphrase 不一致
   */
  async function enableCrypto(passphrase, opts = {}) {
    if (typeof passphrase !== 'string' || passphrase.length < 8) {
      throw new Error('passphrase 至少 8 个字符');
    }
    const existing = await _readConfig();
    if (existing && existing.enabled) {
      throw new Error('E2E 加密已启用，如需更换 passphrase 请先禁用');
    }

    const remoteProfile = opts.remoteProfile || null;
    if (remoteProfile) {
      // 跨设备：复用远端 salt，passphrase 必须与远端一致
      const derived = await _deriveKeyWithRemoteProfile(passphrase, remoteProfile);
      if (!derived.ok) {
        throw new Error('passphrase 与其它设备不一致，无法用同一密钥解密历史数据');
      }
      const verification = await _encryptWithKey(derived.key, verificationPlaintext);
      await _writeConfig({
        enabled: true,
        salt: remoteProfile.salt,
        verification: verification.ciphertext,
        verificationNonce: verification.nonce,
        iterations: remoteProfile.iterations || iterations,
        adoptedRemote: true,
      });
      _inMemoryKey = derived.key;
      return {
        enabled: true,
        saltFingerprint: remoteProfile.salt_fingerprint || _saltFingerprint(remoteProfile.salt),
        reusedRemoteSalt: true,
      };
    }

    const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
    const key = await _deriveKey(passphrase, salt);
    // 用 key 加密固定明文作为 verification token
    const verification = await _encryptWithKey(key, verificationPlaintext);
    await _writeConfig({
      enabled: true,
      salt: _bytesToBase64(salt),
      verification: verification.ciphertext,        // 仅密文
      verificationNonce: verification.nonce,
      iterations,
    });
    _inMemoryKey = key;
    return {
      enabled: true,
      saltFingerprint: _saltFingerprint(_bytesToBase64(salt)),
      reusedRemoteSalt: false,
    };
  }

  /**
   * 禁用 E2E 加密：清除 salt + verification + 内存 key。
   * 警告：禁用后云端已有的密文数据将无法解密（每次启用生成新 salt）。
   *
   * @param {string} passphrase 验证 passphrase，避免误操作
   * @returns {Promise<void>}
   * @throws {Error} passphrase 错误或未启用
   */
  async function disableCrypto(passphrase) {
    const cfg = await _readConfig();
    if (!cfg || !cfg.enabled) {
      throw new Error('E2E 加密未启用');
    }
    // 验证 passphrase 正确性
    const ok = await unlockCrypto(passphrase);
    if (!ok) {
      throw new Error('passphrase 错误，无法禁用');
    }
    await chrome.storage.local.remove(storeKey);
    _inMemoryKey = null;
  }

  /**
   * 解锁：SW 重启后用 passphrase 重新派生 key 装载到内存。
   *
   * @param {string} passphrase
   * @returns {Promise<boolean>} true=解锁成功，false=passphrase 错误
   */
  async function unlockCrypto(passphrase) {
    const cfg = await _readConfig();
    if (!cfg || !cfg.enabled) {
      return false;
    }
    const salt = _base64ToBytes(cfg.salt);
    // 用本地记录的 iterations 派生（可能与当前默认值不同，必须以档案为准）
    const key = await _deriveKey(passphrase, salt, cfg.iterations);
    // 用 verification token 验证 passphrase 是否正确
    try {
      const decrypted = await _decryptWithKey(key, cfg.verification, cfg.verificationNonce);
      if (decrypted !== verificationPlaintext) {
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
  function lockCrypto() {
    _inMemoryKey = null;
  }

  /**
   * 加密任意 JSON-able 对象。
   *
   * @param {object} data 明文对象
   * @returns {Promise<object>} 密文容器 {encrypted, alg, kdf, ciphertext, nonce, v}
   * @throws {Error} 加密未就绪（未启用或未解锁）
   */
  async function encryptData(data) {
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
  async function decryptData(encryptedObj) {
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
  function isEncryptedData(data) {
    return !!(data && typeof data === 'object' && data.encrypted === true);
  }

  /**
   * 读取本地加密档案（用于上云）。
   * @returns {Promise<object|null>} 档案对象；未启用时返回 null
   */
  async function getLocalProfile() {
    const cfg = await _readConfig();
    if (!cfg || !cfg.enabled) return null;
    return {
      salt: cfg.salt,
      iterations: cfg.iterations || iterations,
      kdf: `PBKDF2-SHA256-${cfg.iterations || iterations}`,
      salt_fingerprint: _saltFingerprint(cfg.salt),
      verification: {
        ciphertext: cfg.verification,
        nonce: cfg.verificationNonce,
      },
    };
  }

  /**
   * 采用远端档案（跨设备解锁）：用远端 salt 派生密钥并反验 passphrase。
   * 成功后把远端 salt 写入本地（若无本地配置则创建 enabled 配置），
   * 使本设备与其它设备共用同一密钥，可解密历史密文。
   *
   * @param {string} passphrase
   * @param {object} remoteProfile 远端档案
   * @returns {Promise<{ok: boolean, saltFingerprint?: string, reason?: string}>}
   */
  async function adoptRemoteProfile(passphrase, remoteProfile) {
    if (!remoteProfile || !remoteProfile.salt) {
      return { ok: false, reason: 'no-remote-profile' };
    }
    const derived = await _deriveKeyWithRemoteProfile(passphrase, remoteProfile);
    if (!derived.ok) {
      return { ok: false, reason: 'passphrase-mismatch' };
    }
    const verification = await _encryptWithKey(derived.key, verificationPlaintext);
    await _writeConfig({
      enabled: true,
      salt: remoteProfile.salt,
      verification: verification.ciphertext,
      verificationNonce: verification.nonce,
      iterations: remoteProfile.iterations || iterations,
      adoptedRemote: true,
    });
    _inMemoryKey = derived.key;
    return {
      ok: true,
      saltFingerprint: remoteProfile.salt_fingerprint || _saltFingerprint(remoteProfile.salt),
    };
  }

  /**
   * 用远端档案派生密钥并反验 passphrase。
   * @private
   */
  async function _deriveKeyWithRemoteProfile(passphrase, remoteProfile) {
    if (typeof passphrase !== 'string' || passphrase.length < 8) {
      return { ok: false };
    }
    let key;
    try {
      // 必须使用档案里记录的 iterations —— 它是产生该 salt 时的派生参数
      key = await _deriveKey(passphrase, _base64ToBytes(remoteProfile.salt), remoteProfile.iterations);
    } catch (_) {
      return { ok: false };
    }
    const verification = remoteProfile.verification;
    if (verification && verification.ciphertext && verification.nonce) {
      try {
        const decrypted = await _decryptWithKey(key, verification.ciphertext, verification.nonce);
        if (decrypted !== verificationPlaintext) {
          return { ok: false };
        }
      } catch (_) {
        return { ok: false };
      }
    }
    return { ok: true, key };
  }

  return {
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
  };
}

// ---------- 加密档案的网络同步层（依赖注入，避免绑定产品路径） ----------

/**
 * 创建加密档案同步器：把本地档案推到 Bridge，并在需要时拉取远端档案。
 *
 * 依赖由各产品注入（Focus 用 './auth.js'，Muse 用 '../core/auth.js'），
 * 这样共享层不绑定任何产品路径。
 *
 * @param {object} deps
 * @param {function(): Promise<{token:string}|null>} deps.getAuthToken
 * @param {function(): Promise<string>} deps.getBridgeUrl
 * @param {function(): Promise<object>} deps.getBridgeHeaders
 * @param {object} deps.crypto createCryptoModule 的返回值
 * @param {string} [deps.profilePath] 档案端点路径
 * @returns {{fetchRemoteProfile: Function, pushRemoteProfile: Function, ensureRemoteProfile: Function}}
 */
export function createCryptoProfileSync({
  getAuthToken,
  getBridgeUrl,
  getBridgeHeaders,
  crypto,
  profilePath = '/api/auth/me/crypto-profile',
}) {
  /**
   * 拉取远端加密档案，并**区分三种状态**。
   *
   * 三态区分是安全关键：把「网络/鉴权失败」当成「云端没有档案」，
   * 会导致随后的一次 PUT 覆盖掉另一台设备的档案，那台设备的历史密文就永久解不开了。
   *
   * @returns {Promise<{status: 'ok', profile: object} | {status: 'absent'} | {status: 'error', reason: string}>}
   */
  async function fetchRemoteProfile() {
    const authData = await getAuthToken();
    if (!authData || !authData.token) {
      return { status: 'error', reason: 'not-authenticated' };
    }
    const baseUrl = await getBridgeUrl();
    const headers = await getBridgeHeaders();
    let res;
    try {
      res = await fetch(`${baseUrl}${profilePath}`, {
        method: 'GET',
        headers,
      });
    } catch (_) {
      return { status: 'error', reason: 'network' };
    }
    if (res.status === 404) {
      return { status: 'absent' };
    }
    if (!res.ok) {
      return { status: 'error', reason: `http-${res.status}` };
    }
    let body;
    try {
      body = await res.json();
    } catch (_) {
      return { status: 'error', reason: 'bad-json' };
    }
    if (!isValidProfile(body)) {
      return { status: 'error', reason: 'invalid-profile' };
    }
    return { status: 'ok', profile: body };
  }

  /**
   * 把本地档案推到 Bridge。
   *
   * **只在确认云端确实没有档案（404）时才写入**：
   * - 云端状态未知（网络/鉴权/服务端错误）→ 拒绝写入（宁可不同步，也不能覆盖他人档案）
   * - 云端已有档案且 salt 相同 → 视为已同步，无需写入
   * - 云端已有档案但 salt 不同 → 拒绝写入（那是另一台设备的档案，覆盖会毁掉它的历史密文）
   *
   * @returns {Promise<{synced: boolean, reason?: string}>}
   */
  async function pushRemoteProfile() {
    const authData = await getAuthToken();
    if (!authData || !authData.token) {
      return { synced: false, reason: 'not-authenticated' };
    }
    const profile = await crypto.getLocalProfile();
    if (!profile) {
      return { synced: false, reason: 'not-enabled' };
    }

    const state = await fetchRemoteProfile();
    if (state.status === 'error') {
      // 云端状态未知 —— 不写入，避免误覆盖另一台设备的档案
      return { synced: false, reason: 'remote-unknown' };
    }
    if (state.status === 'ok') {
      if (state.profile.salt === profile.salt) {
        return { synced: true, reason: 'already-synced' };
      }
      return { synced: false, reason: 'salt-mismatch' };
    }

    // status === 'absent'：云端确实没有档案，安全写入
    const baseUrl = await getBridgeUrl();
    const headers = await getBridgeHeaders();
    try {
      const res = await fetch(`${baseUrl}${profilePath}`, {
        method: 'PUT',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify(profile),
      });
      if (!res.ok) {
        return { synced: false, reason: `http-${res.status}` };
      }
      return { synced: true };
    } catch (_) {
      return { synced: false, reason: 'network' };
    }
  }

  /**
   * 确保云端存在与本地一致的档案：
   * - 云端无档案（确认 404）→ 上传本地档案（补齐老用户）
   * - 云端有档案但 salt 与本地不同 → 不覆盖（避免毁掉另一台设备的历史）
   * - 云端有档案且 salt 一致 → 视为已同步
   * - 云端状态未知 → 不写入，置「未同步」
   * @returns {Promise<{synced: boolean, reason?: string}>}
   */
  async function ensureRemoteProfile() {
    const local = await crypto.getLocalProfile();
    if (!local) return { synced: false, reason: 'not-enabled' };
    const state = await fetchRemoteProfile();
    if (state.status === 'error') {
      return { synced: false, reason: 'remote-unknown' };
    }
    if (state.status === 'ok') {
      if (state.profile.salt !== local.salt) {
        // 云端已有另一台设备的档案 —— 不覆盖，保留远端（跨设备共用它）
        return { synced: false, reason: 'salt-mismatch' };
      }
      return { synced: true, reason: 'already-synced' };
    }
    const result = await pushRemoteProfile();
    return result.synced ? { synced: true } : { synced: false, reason: result.reason || 'push-failed' };
  }

  return { fetchRemoteProfile, pushRemoteProfile, ensureRemoteProfile };
}

/**
 * 校验远端档案结构是否完整可用。
 * @param {object} profile
 * @returns {boolean}
 */
export function isValidProfile(profile) {
  return !!(
    profile &&
    typeof profile === 'object' &&
    typeof profile.salt === 'string' &&
    profile.salt.length > 0 &&
    profile.verification &&
    typeof profile.verification.ciphertext === 'string' &&
    typeof profile.verification.nonce === 'string'
  );
}
