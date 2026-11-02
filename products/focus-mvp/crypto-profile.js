// crypto-profile.js — Focus 加密档案与 Bridge 的同步（跨设备解密支持）
//
// 职责边界：
//   crypto.js           纯加密（无网络依赖，便于单测）
//   crypto-profile.js   档案同步（网络层，依赖 auth.js）
//
// 跨设备模型：
//   - PBKDF2 salt 是公开参数（不是密钥），可以安全上云；
//   - passphrase 永不落盘、永不上云；
//   - verification 只存密文，用于在另一台设备上反验 passphrase 是否一致；
//   - 设备 B 拉取档案后用「同一 passphrase + 同一 salt」派生同一密钥，
//     因而能解密设备 A 推送的密文。
//
// 失败语义：档案同步失败**不阻断**加解密，仅置「未同步」状态，可稍后重试。

import { createCryptoProfileSync } from './lib/vannevar-crypto.js';
import { getAuthToken, getBridgeUrl, getBridgeHeaders } from './auth.js';
import { getLocalProfile } from './crypto.js';

export const cryptoProfileSync = createCryptoProfileSync({
  getAuthToken,
  getBridgeUrl,
  getBridgeHeaders,
  crypto: { getLocalProfile },
});

export const { fetchRemoteProfile, pushRemoteProfile, ensureRemoteProfile } = cryptoProfileSync;

// ---------- salt-mismatch 用户可见提示（T-08 清账②） ----------
//
// 背景：ensureRemoteProfile() 遇到「云端已有另一台设备的档案（salt 不同）」时返回
// { synced: false, reason: 'salt-mismatch' }，行为安全（不覆盖远端），但调用点若不
// 消费返回值，用户对「为什么另一台设备解不开」毫无感知（T-02 P2-1）。
//
// handleProfileSyncResult：在 sync.js 的自动同步路径统一消费返回值 ——
//   - salt-mismatch → 系统通知 + 本地标记（24h 节流，避免每次同步都弹）；
//   - 同步成功（synced: true）→ 清除标记（一致后不再提示）；
//   - 其余原因（not-enabled / remote-unknown / push-failed）→ 不打扰用户。
const KEY_SALT_NOTICE = 'vannevar_e2e_salt_mismatch_at';
const SALT_NOTICE_THROTTLE_MS = 24 * 60 * 60 * 1000;

export async function handleProfileSyncResult(result) {
  if (!result || typeof result !== 'object') return;
  if (result.reason === 'salt-mismatch') {
    try {
      const stored = await chrome.storage.local.get(KEY_SALT_NOTICE);
      const last = stored[KEY_SALT_NOTICE] || 0;
      if (Date.now() - last > SALT_NOTICE_THROTTLE_MS) {
        await chrome.storage.local.set({ [KEY_SALT_NOTICE]: Date.now() });
        try {
          chrome.notifications.create('vannevar-salt-mismatch', {
            type: 'basic',
            iconUrl: 'icons/icon48.png',
            title: 'Vannevar 端到端加密',
            message: 'Bridge 上的加密档案与本机口令不一致，本次未覆盖云端档案。' +
              '请到设置页用正确口令解锁，或采用云端档案。',
          });
        } catch (_) { /* 通知失败不影响同步 */ }
      }
    } catch (_) { /* 存储异常时静默，与「不阻断同步」的失败语义一致 */ }
    return;
  }
  if (result.synced) {
    try {
      await chrome.storage.local.remove(KEY_SALT_NOTICE);
    } catch (_) { /* 忽略 */ }
  }
}
