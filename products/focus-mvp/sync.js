// sync.js — 阶段1.5 Focus 数据同步（push/pull to Bridge）
// 需先登录（auth.js），未登录时抛错。
// client_uuid 映射表：{ [localId]: uuid }，存在 chrome.storage.local
// Phase 4.4: push 时加密 data 字段，pull 时按 encrypted 标记自动解密；
//           启用但未解锁时抛友好错误提示用户输入 passphrase。
import { getAuthToken, getBridgeUrl, getBridgeHeaders, clearAuthToken } from './auth.js';
import { exportSnapshots, importSnapshots } from './db.js';
import {
  isCryptoEnabled,
  isCryptoReady,
  encryptData,
  decryptData,
  isEncryptedData,
} from './crypto.js';
import { ensureRemoteProfile } from './crypto-profile.js';

const SYNC_APP = 'focus';
const SYNC_ITEM_TYPE = 'snapshot';
const UUID_MAP_KEY = 'vannevar_focus_sync_uuids';
const SYNC_META_KEY = 'vannevar_focus_sync_meta';

// R5-FE-10: 写锁 Promise 队列，防止 ensureUuids/removeUuid 并发写入丢失
let _uuidMapWriteChain = Promise.resolve();
function withUuidMapWrite(fn) {
  const run = _uuidMapWriteChain.then(fn);
  _uuidMapWriteChain = run.catch(() => {});
  return run;
}

// 读取本地 id → client_uuid 映射表
async function getUuidMap() {
  const result = await chrome.storage.local.get(UUID_MAP_KEY);
  return result[UUID_MAP_KEY] || {};
}

// 写入映射表
async function setUuidMap(map) {
  await chrome.storage.local.set({ [UUID_MAP_KEY]: map });
}

// 为本地记录生成或读取 client_uuid
async function ensureUuids(snapshots) {
  // R5-FE-10: 用写锁串行化，防止并发写入丢失
  return withUuidMapWrite(async () => {
    const map = await getUuidMap();
    let changed = false;
    for (const snap of snapshots) {
      const key = String(snap.id);
      if (!map[key]) {
        map[key] = crypto.randomUUID();
        changed = true;
      }
    }
    if (changed) {
      await setUuidMap(map);
    }
    return map;
  });
}

// Phase 4.1：读取或生成单条快照的 UUID（供 bridge-link.js 跨产品推送复用）
// 与 ensureUuids 共享同一 UUID 映射表，保证同一 Focus 快照在 sync / bridge-link 中 UUID 一致
export async function getSnapshotUuid(localId) {
  const id = String(localId);
  const map = await ensureUuids([{ id }]);
  return map[id] || '';
}

// 读取上次同步时间戳
async function getLastSyncAt() {
  const result = await chrome.storage.local.get(SYNC_META_KEY);
  return result[SYNC_META_KEY]?.lastSyncAt || 0;
}

// 写入上次同步时间戳
async function setLastSyncAt(ts) {
  const result = await chrome.storage.local.get(SYNC_META_KEY);
  const meta = result[SYNC_META_KEY] || {};
  meta.lastSyncAt = ts;
  await chrome.storage.local.set({ [SYNC_META_KEY]: meta });
}

// 推送本地数据到 Bridge
// 返回 { pushed, created, updated, skipped }
// Phase 4.4: 启用 E2E 加密后，data 字段以 AES-GCM-256 密文形式上传，Bridge 无法解密。
//            启用但未解锁时抛友好错误，避免误推明文造成数据明密混杂。
export async function pushSync() {
  const authData = await getAuthToken();
  if (!authData) {
    throw new Error('未登录，请先登录后再同步');
  }

  // Phase 4.4: 启用加密但未解锁（SW 重启后 key 丢失）→ 抛友好错误
  const cryptoEnabled = await isCryptoEnabled();
  if (cryptoEnabled && !isCryptoReady()) {
    throw new Error('E2E 加密已启用但未解锁，请到设置页输入 passphrase 解锁后再同步');
  }

  // 读取本地全部快照
  const exportData = await exportSnapshots();
  const snapshots = exportData.snapshots || [];
  if (snapshots.length === 0) {
    // 即使没有数据也要同步加密档案：老版本已在本地启用加密、但云端尚无档案时，
    // 这里（及正常推送路径）是自动补齐的时机 —— 否则没有数据变化，档案永远补不上。
    if (cryptoEnabled) {
      try {
        await ensureRemoteProfile();
      } catch (_) { /* 档案同步失败不影响同步结果 */ }
    }
    return { pushed: 0, created: 0, updated: 0, skipped: 0 };
  }

  // 确保每条都有 client_uuid
  const uuidMap = await ensureUuids(snapshots);

  // 构造推送 items（Phase 4.4: 启用加密时 data 字段替换为密文容器）
  const items = [];
  for (const snap of snapshots) {
    const plaintext = {
      title: snap.title,
      tabs: snap.tabs,
      createdAt: snap.createdAt,
    };
    const data = cryptoEnabled ? await encryptData(plaintext) : plaintext;
    items.push({
      app: SYNC_APP,
      item_type: SYNC_ITEM_TYPE,
      client_uuid: uuidMap[String(snap.id)],
      data,
      client_created_at: snap.createdAt,
      client_updated_at: snap.updatedAt || snap.createdAt, // R5-FE-1: 标题修改后 updatedAt 变化，LWW 才会同步
    });
  }

  // Phase 4.4 修复：使用 getBridgeHeaders() 注入 X-CSRF-Token 头（写端点必需）
  const headers = await getBridgeHeaders();
  if (!headers) {
    throw new Error('未登录，请先登录后再同步');
  }
  headers['Content-Type'] = 'application/json';

  const bridgeUrl = await getBridgeUrl();
  let res;
  try {
    res = await fetch(`${bridgeUrl}/api/sync/push`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ items }),
    });
  } catch (err) {
    throw new Error('无法连接到 Bridge 服务器，请确认后端已启动');
  }

  if (res.status === 401) {
    // V2 修复：401 时清除过期 token 并通知 UI 显示重新登录提示
    await clearAuthToken();
    await chrome.storage.local.set({ authExpired: true });
    throw new Error('登录已过期，请重新登录后再同步');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    // 服务端的 detail 可能是字符串，也可能是 {code, message} 对象
    // （如 csrf_token_missing）；直接 new Error(object) 会显示成 [object Object]
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || detail.code || `推送失败（${res.status}）`)
      : (detail || `推送失败（${res.status}）`);
    throw new Error(msg);
  }

  const result = await res.json();
  await setLastSyncAt(Date.now());
  // 跨设备支持：确保云端存在与本地一致的加密档案（老用户首次同步时自动补齐）。
  // 失败不阻断本次同步——加解密本身不依赖这一步。
  if (cryptoEnabled) {
    try {
      await ensureRemoteProfile();
    } catch (_) { /* 档案同步失败不影响同步结果 */ }
  }
  return result;
}

// 从 Bridge 拉取数据并合并到本地
// 返回 { pulled, imported }（pulled = 服务端条数，imported = 新增到本地的条数）
// Phase 4.4: 通过 isEncryptedData() 区分密文容器 vs 旧明文；密文需先解锁才能解密。
export async function pullSync() {
  const authData = await getAuthToken();
  if (!authData) {
    throw new Error('未登录，请先登录后再同步');
  }

  // Phase 4.4 修复：使用 getBridgeHeaders() 统一注入 CSRF 头（GET 不强制但保持一致）
  const headers = await getBridgeHeaders();
  if (!headers) {
    throw new Error('未登录，请先登录后再同步');
  }

  const bridgeUrl = await getBridgeUrl();
  let res;
  try {
    res = await fetch(`${bridgeUrl}/api/sync/pull?app=${SYNC_APP}`, {
      method: 'GET',
      headers,
    });
  } catch (err) {
    throw new Error('无法连接到 Bridge 服务器，请确认后端已启动');
  }

  if (res.status === 401) {
    // V2 修复：401 时清除过期 token 并通知 UI 显示重新登录提示
    await clearAuthToken();
    await chrome.storage.local.set({ authExpired: true });
    throw new Error('登录已过期，请重新登录后再同步');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    // 服务端的 detail 可能是字符串，也可能是 {code, message} 对象
    const msg = (detail && typeof detail === 'object')
      ? (detail.message || detail.code || `拉取失败（${res.status}）`)
      : (detail || `拉取失败（${res.status}）`);
    throw new Error(msg);
  }

  const result = await res.json();
  const remoteItems = result.items || [];
  if (remoteItems.length === 0) {
    return { pulled: 0, imported: 0 };
  }

  // Phase 4.4: 检查是否有密文条目；如启用但未解锁 → 抛友好错误
  const hasEncrypted = remoteItems.some(item => isEncryptedData(item.data));
  if (hasEncrypted && !isCryptoReady()) {
    throw new Error('云端存在加密快照，请到设置页启用/解锁加密（使用与其它设备相同的 passphrase）后再拉取');
  }

  // 读取本地数据，建立 client_uuid → local id 的反向映射
  const uuidMap = await getUuidMap();
  const reverseMap = {}; // uuid → localId
  for (const [localId, uuid] of Object.entries(uuidMap)) {
    reverseMap[uuid] = localId;
  }

  // 筛选出本地没有的记录（client_uuid 不在反向映射中）
  // Phase 4.4: 解密密文 data 后取 title/tabs/createdAt
  const newItems = [];
  for (const item of remoteItems) {
    if (reverseMap[item.client_uuid]) continue;
    let plainData = item.data;
    if (isEncryptedData(item.data)) {
      try {
        plainData = await decryptData(item.data);
      } catch (err) {
        // 解密失败（passphrase 错误 / 数据损坏）：跳过本条而非整体失败
        console.warn('[Sync] 跳过无法解密的快照 uuid=', item.client_uuid, err.message);
        continue;
      }
      if (!plainData) continue;
    }
    newItems.push({
      title: plainData.title || '未命名快照',
      tabs: Array.isArray(plainData.tabs) ? plainData.tabs : [],
      createdAt: plainData.createdAt || item.client_created_at || Date.now(),
    });
  }

  let imported = 0;
  if (newItems.length > 0) {
    imported = await importSnapshots({ snapshots: newItems });
    // 为新导入的记录补充 client_uuid 映射
    const updatedLocal = await exportSnapshots();
    // R6-FE-2: 包裹在 withUuidMapWrite 写锁内，防止与 ensureUuids/removeUuid 并发写入时 uuid 丢失
    await withUuidMapWrite(async () => {
      const updatedMap = await getUuidMap();
      // V11 限制：通过 createdAt 匹配新导入的记录以补充 client_uuid 映射。
      // 风险：同一秒内创建多条记录时可能映射错乱。彻底修复需改造 importSnapshots 返回插入 ID，
      // 或改用服务端返回的 client_uuid 作为本地记录的主键关联。本次不改（改动太大）。
      const newCreatedAts = new Set(newItems.map(i => i.createdAt));
      for (const snap of updatedLocal.snapshots || []) {
        if (newCreatedAts.has(snap.createdAt) && !updatedMap[String(snap.id)]) {
          const remoteItem = remoteItems.find(ri => {
            // Phase 4.4: 密文 data 没有 createdAt 字段，需用 plainData 中的值匹配
            // 但 remoteItems 已经消费过 plainData，此处用 client_created_at 近似匹配（密文场景）
            // 明文场景：ri.data.createdAt 直接可用
            if (isEncryptedData(ri.data)) {
              return ri.client_created_at === snap.createdAt;
            }
            return (ri.data.createdAt || ri.client_created_at) === snap.createdAt;
          });
          if (remoteItem) {
            updatedMap[String(snap.id)] = remoteItem.client_uuid;
          }
        }
      }
      await setUuidMap(updatedMap);
    });
  }

  await setLastSyncAt(Date.now());
  // 跨设备支持：确保云端存在与本地一致的加密档案
  if (await isCryptoEnabled()) {
    try {
      await ensureRemoteProfile();
    } catch (_) { /* 档案同步失败不影响同步结果 */ }
  }
  return { pulled: remoteItems.length, imported };
}

// 获取同步状态
export async function getSyncState() {
  const authData = await getAuthToken();
  const lastSyncAt = await getLastSyncAt();
  return {
    loggedIn: !!authData,
    lastSyncAt,
  };
}

// V15 修复：删除本地记录时调用，清理对应的 client_uuid 映射，避免映射表无限增长
// 应在 background.js 的 DELETE_SNAPSHOT 消息处理中调用：await removeUuid(deletedId)
export async function removeUuid(id) {
  // R5-FE-10: 用写锁串行化，防止并发写入丢失
  return withUuidMapWrite(async () => {
    const map = await getUuidMap();
    const key = String(id);
    if (map[key]) {
      delete map[key];
      await setUuidMap(map);
    }
  });
}
