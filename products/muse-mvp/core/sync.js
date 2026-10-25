// sync.js — 阶段1.5 Muse 数据同步（push/pull to Bridge）
// 需先登录（auth.js），未登录时抛错。
// 同步两类数据：inspiration + mood，各维护 client_uuid 映射表。
import { getAuthToken, getBridgeUrl, getBridgeHeaders, clearAuthToken } from './auth.js';
import { exportInspirations, importInspirations, getMoods, addMood } from './muse.js';
// Phase 4.4 收尾：Muse 同步端到端加密（与 Focus 共用 shared/vannevar-crypto.js）
import {
  isCryptoEnabled,
  isCryptoReady,
  encryptData,
  decryptData,
  isEncryptedData,
} from './crypto.js';
import { ensureRemoteProfile } from './crypto-profile.js';

const SYNC_APP = 'muse';
const UUID_MAP_INSPIRATION_KEY = 'vannevar_muse_sync_uuids_insp';
const UUID_MAP_MOOD_KEY = 'vannevar_muse_sync_uuids_mood';
const SYNC_META_KEY = 'vannevar_muse_sync_meta';

// R5-FE-10: 写锁 Promise 队列，防止 ensureUuids/removeUuid 并发写入丢失
let _uuidMapWriteChain = Promise.resolve();
function withUuidMapWrite(fn) {
  const run = _uuidMapWriteChain.then(fn);
  _uuidMapWriteChain = run.catch(() => {});
  return run;
}

// 读取映射表
async function getUuidMap(key) {
  const result = await chrome.storage.local.get(key);
  return result[key] || {};
}

// 写入映射表
async function setUuidMap(key, map) {
  await chrome.storage.local.set({ [key]: map });
}

// 为本地记录生成或读取 client_uuid
async function ensureUuids(storageKey, items, idField = 'id') {
  // R5-FE-10: 用写锁串行化，防止并发写入丢失
  return withUuidMapWrite(async () => {
    const map = await getUuidMap(storageKey);
    let changed = false;
    for (const item of items) {
      const key = String(item[idField]);
      if (!map[key]) {
        map[key] = crypto.randomUUID();
        changed = true;
      }
    }
    if (changed) {
      await setUuidMap(storageKey, map);
    }
    return map;
  });
}

// Phase 4.1：读取或生成单条灵感的 UUID（供 bridge-link.js 跨产品推送复用）
// 与 ensureUuids 共享同一 UUID 映射表，保证同一 Muse 灵感在 sync / bridge-link 中 UUID 一致
export async function getInspirationUuid(localId) {
  const id = String(localId);
  const map = await ensureUuids(UUID_MAP_INSPIRATION_KEY, [{ id }]);
  return map[id] || '';
}

// 读取/写入上次同步时间
async function getLastSyncAt() {
  const result = await chrome.storage.local.get(SYNC_META_KEY);
  return result[SYNC_META_KEY]?.lastSyncAt || 0;
}

async function setLastSyncAt(ts) {
  const result = await chrome.storage.local.get(SYNC_META_KEY);
  const meta = result[SYNC_META_KEY] || {};
  meta.lastSyncAt = ts;
  await chrome.storage.local.set({ [SYNC_META_KEY]: meta });
}

// 推送本地数据到 Bridge（inspirations + moods）
// 返回 { pushed, created, updated, skipped }
export async function pushSync() {
  const authData = await getAuthToken();
  if (!authData) {
    throw new Error('未登录，请先登录后再同步');
  }

  // Phase 4.4 收尾：加密已启用但未解锁（SW 重启后 key 丢失）→ 抛友好错误，
  // 避免明文与密文混推到服务端。
  const cryptoEnabled = await isCryptoEnabled();
  if (cryptoEnabled && !isCryptoReady()) {
    throw new Error('E2E 加密已启用但未解锁，请到设置页输入 passphrase 解锁后再同步');
  }

  const exportData = await exportInspirations();
  const inspirations = exportData.inspirations || [];
  const moods = exportData.moods || [];

  // 确保每条都有 client_uuid
  const inspUuidMap = await ensureUuids(UUID_MAP_INSPIRATION_KEY, inspirations);
  const moodUuidMap = await ensureUuids(UUID_MAP_MOOD_KEY, moods);

  // 构造推送 items
  const items = [];

  for (const insp of inspirations) {
    // Phase 4.4 收尾：明文先构造为独立对象，加密启用时整体加密为密文容器
    const plainData = {
      title: insp.title,
      url: insp.url,
      note: insp.note,
      mood: insp.mood,
      selectedText: insp.selectedText,
      createdAt: insp.createdAt,
      pinned: insp.pinned,
      // Phase 4.2: 媒体元数据参与云同步（向后兼容：旧客户端忽略这些字段）
      mediaType: insp.mediaType || null,
      mediaArtist: insp.mediaArtist || null,
      mediaAlbum: insp.mediaAlbum || null,
      mediaArtworkUrl: insp.mediaArtworkUrl || null,
    };
    items.push({
      app: SYNC_APP,
      item_type: 'inspiration',
      client_uuid: inspUuidMap[String(insp.id)],
      data: cryptoEnabled ? await encryptData(plainData) : plainData,
      client_created_at: insp.createdAt,
      client_updated_at: insp.updatedAt || insp.createdAt, // R5-FE-1: 修改后 updatedAt 变化，LWW 才会同步
    });
  }

  for (const mood of moods) {
    // R6-FE-8: client_created_at 用 mood.createdAt，client_updated_at 用 mood.updatedAt
    // R5-FE-7: createdAt/updatedAt 缺失时用 Date.now() 兜底
    // 注意：0 是合法的稳定时间戳（兜底 MOODS），需用 != null 判断而非 ||
    const moodCreated = mood.createdAt != null ? mood.createdAt : Date.now();
    const moodUpdated = mood.updatedAt != null ? mood.updatedAt : moodCreated;
    const plainMoodData = {
      name: mood.name,
      color: mood.color,
      sortOrder: mood.sortOrder,
    };
    items.push({
      app: SYNC_APP,
      item_type: 'mood',
      client_uuid: moodUuidMap[String(mood.id)],
      data: cryptoEnabled ? await encryptData(plainMoodData) : plainMoodData,
      client_created_at: moodCreated,
      client_updated_at: moodUpdated,
    });
  }

  if (items.length === 0) {
    // 即使没有数据也要同步加密档案：老版本已在本地启用加密、但云端尚无档案时，
    // 这里（及正常推送路径）是自动补齐的时机 —— 否则没有数据变化，档案永远补不上。
    if (cryptoEnabled) {
      try {
        await ensureRemoteProfile();
      } catch (_) { /* 档案同步失败不影响同步结果 */ }
    }
    return { pushed: 0, created: 0, updated: 0, skipped: 0 };
  }

  const bridgeUrl = await getBridgeUrl();
  // Phase 4.4 收尾修复：Bridge 的写端点强制校验 X-CSRF-Token（须等于 JWT.csrf_jti），
  // 缺失会直接 403。统一走 getBridgeHeaders() 注入（与 Focus 侧保持一致）。
  const headers = await getBridgeHeaders();
  if (!headers) {
    throw new Error('未登录，请先登录后再同步');
  }
  let res;
  try {
    res = await fetch(`${bridgeUrl}/api/sync/push`, {
      method: 'POST',
      headers: {
        ...headers,
        'Content-Type': 'application/json',
      },
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

// Phase 4.4 收尾：解密单条同步条目的 data。
// 旧明文数据原样返回；密文解密失败（passphrase 错误 / 数据损坏）返回 null，
// 由调用方跳过该条而非让整次拉取失败。
async function decryptItemData(item) {
  if (!isEncryptedData(item.data)) return item.data;
  try {
    const plain = await decryptData(item.data);
    return plain || null;
  } catch (err) {
    console.warn('[Sync] 跳过无法解密的记录 uuid=', item.client_uuid, err.message);
    return null;
  }
}

// 从 Bridge 拉取数据并合并到本地
// 返回 { pulled, importedInspirations, importedMoods }
export async function pullSync() {
  const authData = await getAuthToken();
  if (!authData) {
    throw new Error('未登录，请先登录后再同步');
  }

  const bridgeUrl = await getBridgeUrl();
  // Phase 4.4 收尾修复：统一用 getBridgeHeaders()（含 Authorization + X-CSRF-Token）
  const headers = await getBridgeHeaders();
  if (!headers) {
    throw new Error('未登录，请先登录后再同步');
  }
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
    return { pulled: 0, importedInspirations: 0, importedMoods: 0 };
  }

  // Phase 4.4 收尾：云端存在密文条目而本地未解锁 → 抛友好错误
  const hasEncrypted = remoteItems.some(item => isEncryptedData(item.data));
  if (hasEncrypted && !isCryptoReady()) {
    throw new Error('云端存在加密灵感，请到设置页启用/解锁加密（使用与其它设备相同的 passphrase）后再拉取');
  }

  // 分离 inspiration 和 mood
  const remoteInspirations = remoteItems.filter(i => i.item_type === 'inspiration');
  const remoteMoods = remoteItems.filter(i => i.item_type === 'mood');

  // 合并 moods（重复名称自动跳过）
  let importedMoods = 0;
  if (remoteMoods.length > 0) {
    const localMoods = await getMoods();
    const localMoodNames = new Set(localMoods.map(m => m.name));
    // R6-FE-2: 包裹在 withUuidMapWrite 写锁内，防止与 ensureUuids/removeMoodUuid 并发写入时 uuid 丢失
    await withUuidMapWrite(async () => {
      const moodUuidMap = await getUuidMap(UUID_MAP_MOOD_KEY);
      let moodMapChanged = false;
      for (const item of remoteMoods) {
        // Phase 4.4 收尾：氛围标签同样可能是密文
        const plain = await decryptItemData(item);
        if (!plain) continue;
        const name = plain.name;
        const color = plain.color;
        if (!name || localMoodNames.has(name)) continue;
        try {
          const inserted = await addMood(name, color || '#c2410c');
          if (inserted) {
            importedMoods++;
            localMoodNames.add(name);
            // 补充 uuid 映射，避免下次 push 被当作新记录重复推送
            moodUuidMap[String(inserted.id)] = item.client_uuid;
            moodMapChanged = true;
          }
        } catch (e) {
          // 跳过单条错误
        }
      }
      if (moodMapChanged) {
        await setUuidMap(UUID_MAP_MOOD_KEY, moodUuidMap);
      }
    });
  }

  // 合并 inspirations（通过 client_uuid 去重）
  let importedInspirations = 0;
  if (remoteInspirations.length > 0) {
    const inspUuidMap = await getUuidMap(UUID_MAP_INSPIRATION_KEY);
    const reverseMap = {};
    for (const [localId, uuid] of Object.entries(inspUuidMap)) {
      reverseMap[uuid] = localId;
    }

    const newItems = [];
    // Phase 4.4 收尾：记录解密后的 createdAt ↔ client_uuid 对应关系，
    // 供下方补充 uuid 映射时使用（密文条目的 item.data.createdAt 不可直读）。
    const createdAtByUuid = new Map();
    for (const item of remoteInspirations) {
      if (!reverseMap[item.client_uuid]) {
        const plain = await decryptItemData(item);
        if (!plain) continue;
        const createdAt = plain.createdAt || item.client_created_at || Date.now();
        createdAtByUuid.set(item.client_uuid, createdAt);
        newItems.push({
          title: plain.title || '未命名灵感',
          url: plain.url || '',  // V10 修复：默认值改为空字符串，避免 example.com 误导
          note: plain.note || '',
          mood: plain.mood || '',
          selectedText: plain.selectedText || '',
          createdAt,
          pinned: plain.pinned || false,
          // Phase 4.2: 拉取时也带回媒体元数据
          mediaType: plain.mediaType || null,
          mediaArtist: plain.mediaArtist || null,
          mediaAlbum: plain.mediaAlbum || null,
          mediaArtworkUrl: plain.mediaArtworkUrl || null,
        });
      }
    }

    if (newItems.length > 0) {
      importedInspirations = await importInspirations({ inspirations: newItems, moods: [] });
      // 为新导入的记录补充 client_uuid 映射
      const updatedExport = await exportInspirations();
      // R6-FE-2: 包裹在 withUuidMapWrite 写锁内，防止与 ensureUuids/removeInspirationUuid 并发写入时 uuid 丢失
      await withUuidMapWrite(async () => {
        const updatedMap = await getUuidMap(UUID_MAP_INSPIRATION_KEY);
        // V11 限制：通过 createdAt 匹配新导入的记录以补充 client_uuid 映射。
        // 风险：同一秒内创建多条记录时可能映射错乱。彻底修复需改造 importInspirations 返回插入 ID，
        // 或改用服务端返回的 client_uuid 作为本地记录的主键关联。本次不改（改动太大）。
        const newCreatedAts = new Set(newItems.map(i => i.createdAt));
        for (const insp of updatedExport.inspirations || []) {
          if (newCreatedAts.has(insp.createdAt) && !updatedMap[String(insp.id)]) {
            // Phase 4.4 收尾：密文条目的 item.data.createdAt 不可直读，
            // 改用解密阶段建立的 createdAt ↔ client_uuid 映射。
            let remoteUuid = null;
            for (const [uuid, ca] of createdAtByUuid) {
              if (ca === insp.createdAt) { remoteUuid = uuid; break; }
            }
            if (remoteUuid) {
              updatedMap[String(insp.id)] = remoteUuid;
            }
          }
        }
        await setUuidMap(UUID_MAP_INSPIRATION_KEY, updatedMap);
      });
    }
  }

  await setLastSyncAt(Date.now());
  // 跨设备支持：确保云端存在与本地一致的加密档案
  if (await isCryptoEnabled()) {
    try {
      await ensureRemoteProfile();
    } catch (_) { /* 档案同步失败不影响同步结果 */ }
  }
  return {
    pulled: remoteItems.length,
    importedInspirations,
    importedMoods,
  };
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
// 应在 background.js 的 DELETE_INSPIRATION 消息处理中调用：await removeInspirationUuid(deletedId)
export async function removeInspirationUuid(id) {
  // R5-FE-10: 用写锁串行化，防止并发写入丢失
  return withUuidMapWrite(async () => {
    const map = await getUuidMap(UUID_MAP_INSPIRATION_KEY);
    const key = String(id);
    if (map[key]) {
      delete map[key];
      await setUuidMap(UUID_MAP_INSPIRATION_KEY, map);
    }
  });
}

// V15 修复：删除氛围标签时调用（如未来支持删除 mood）
export async function removeMoodUuid(id) {
  // R5-FE-10: 用写锁串行化，防止并发写入丢失
  return withUuidMapWrite(async () => {
    const map = await getUuidMap(UUID_MAP_MOOD_KEY);
    const key = String(id);
    if (map[key]) {
      delete map[key];
      await setUuidMap(UUID_MAP_MOOD_KEY, map);
    }
  });
}
