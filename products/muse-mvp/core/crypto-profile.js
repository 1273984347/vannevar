// core/crypto-profile.js — Muse 加密档案与 Bridge 的同步（跨设备解密支持）
//
// 职责边界：
//   core/crypto.js         纯加密（无网络依赖，便于单测）
//   core/crypto-profile.js 档案同步（网络层，依赖 core/auth.js）
//
// 跨设备模型：
//   - PBKDF2 salt 是公开参数（不是密钥），可以安全上云；
//   - passphrase 永不落盘、永不上云；
//   - verification 只存密文，用于在另一台设备上反验 passphrase 是否一致；
//   - 设备 B 拉取档案后用「同一 passphrase + 同一 salt」派生同一密钥，
//     因而能解密设备 A 推送的密文。
//
// 失败语义：档案同步失败**不阻断**加解密，仅置「未同步」状态，可稍后重试。

import { createCryptoProfileSync } from '../shared/vannevar-crypto.js';
import { getAuthToken, getBridgeUrl, getBridgeHeaders } from './auth.js';
import { getLocalProfile } from './crypto.js';

export const cryptoProfileSync = createCryptoProfileSync({
  getAuthToken,
  getBridgeUrl,
  getBridgeHeaders,
  crypto: { getLocalProfile },
});

export const { fetchRemoteProfile, pushRemoteProfile, ensureRemoteProfile } = cryptoProfileSync;
