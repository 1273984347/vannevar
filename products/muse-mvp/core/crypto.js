// core/crypto.js — Muse 端到端加密（薄封装）
//
// 核心实现见 shared/vannevar-crypto.js（共享层唯一真源），
// 运行时副本位于 products/muse-mvp/shared/vannevar-crypto.js。
// 本文件只做两件事：
//   1. 绑定 Muse 专属的 storeKey 与 verification 明文（与 Focus 隔离，避免同机互相污染）
//   2. 以统一签名 re-export，供 sync.js / auto-sync.js / options.js 使用
//
// 加密格式（写入 SyncItem.data）：
//   { encrypted: true, alg: "AES-GCM-256", kdf: "PBKDF2-SHA256-100000",
//     ciphertext: "<base64>", nonce: "<base64 12 bytes>", v: 1 }
//
// 存储位置：chrome.storage.local['vannevar_muse_crypto']
//   { enabled, salt, verification, verificationNonce, iterations }
//
// passphrase 永不落盘、永不上云；salt 是 PBKDF2 公开盐值，可上云供跨设备派生同一密钥。

import { createCryptoModule } from '../shared/vannevar-crypto.js';

const CRYPTO_STORE_KEY = 'vannevar_muse_crypto';
// 跨产品统一的 verification 明文：同一 Bridge 账号下的 Focus 与 Muse
// 共享同一份加密档案（同一 salt、同一密钥），因此才能互解对方推送的密文。
// 若各产品用不同明文，后启用的那个产品会拿自己的明文去反验对方的 verification，
// 必然失败并被误报为「口令不一致」。
const VERIFICATION_PLAINTEXT = 'vannevar-e2e-verification-v1';

const _crypto = createCryptoModule({
  storeKey: CRYPTO_STORE_KEY,
  verificationPlaintext: VERIFICATION_PLAINTEXT,
});

export const isCryptoEnabled = _crypto.isCryptoEnabled;
export const isCryptoReady = _crypto.isCryptoReady;
export const getCryptoState = _crypto.getCryptoState;
export const enableCrypto = _crypto.enableCrypto;
export const disableCrypto = _crypto.disableCrypto;
export const unlockCrypto = _crypto.unlockCrypto;
export const lockCrypto = _crypto.lockCrypto;
export const encryptData = _crypto.encryptData;
export const decryptData = _crypto.decryptData;
export const isEncryptedData = _crypto.isEncryptedData;

// 跨设备解密支持：档案含公开 salt 与 verification 密文
export const getLocalProfile = _crypto.getLocalProfile;
export const adoptRemoteProfile = _crypto.adoptRemoteProfile;
