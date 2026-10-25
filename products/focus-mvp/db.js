// db.js — SQLite 本地存储封装（基于 sql.js）
// 数据库二进制持久化到 chrome.storage.local（Base64 编码）
//
// P2-28 决策：保留 console.error / console.warn 用于错误诊断，移除生产环境非必要 console.log。
// 原因：① MV3 SW 中 console 输出是用户反馈问题时唯一的诊断通道
//      （chrome://extensions SW 日志仅在 DevTools 打开时保留）；
//      ② 成功/初始化日志在日常使用中产生噪音，移除可提升产品质感；
//      ③ 该扩展无敏感数据，错误日志中不输出标签页 URL 等用户内容。

import initSqlJs from './lib/sql-wrapper.js';

const DB_KEY = 'vannevar_focus_db';
let SQL = null;
let db = null;
let initialized = false;
let initPromise = null;

// 写操作串行化队列，防止并发写入导致数据丢失
// P2-17 决策：不引入 persistDb 防抖。串行队列已保证写操作不并发，
// 防抖会延迟落盘，存在 SW 被提前终止（MV3 30s 空闲回收）时丢失数据的风险。
// 当前每条写操作立即持久化，正确性优先于吞吐。
let writeQueue = Promise.resolve();

function enqueueWrite(fn) {
  const result = writeQueue.then(fn, fn);
  writeQueue = result.catch(() => {});
  return result;
}

export async function initDatabase() {
  if (initialized) return;
  if (initPromise) return initPromise;

  initPromise = doInitDatabase();
  try {
    await initPromise;
  } catch (err) {
    initPromise = null;
    throw err;
  }
}

async function doInitDatabase() {
  SQL = await initSqlJs({
    locateFile: (file) => `./lib/${file}`
  });

  const stored = await chrome.storage.local.get(DB_KEY);
  if (stored[DB_KEY] && typeof stored[DB_KEY] === 'string' && stored[DB_KEY].length > 0) {
    try {
      const buffer = base64ToUint8Array(stored[DB_KEY]);
      db = new SQL.Database(buffer);
    } catch (err) {
      console.error('[DB] 存储数据损坏，重建数据库:', err);
      // 损坏时自动备份原数据，避免用户数据丢失
      await backupCorruptData(stored[DB_KEY]);
      db = new SQL.Database();
    }
  } else if (stored[DB_KEY] && Array.isArray(stored[DB_KEY]) && stored[DB_KEY].length > 0) {
    // 兼容旧版 Array.from 格式
    try {
      const buffer = new Uint8Array(stored[DB_KEY]);
      db = new SQL.Database(buffer);
    } catch (err) {
      console.error('[DB] 旧格式数据损坏，重建数据库:', err);
      await backupCorruptData(stored[DB_KEY]);
      db = new SQL.Database();
    }
  } else {
    db = new SQL.Database();
  }

  db.run(`
    CREATE TABLE IF NOT EXISTS snapshots (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      tab_count INTEGER NOT NULL,
      tabs TEXT NOT NULL
    )
  `);
  db.run(`CREATE INDEX IF NOT EXISTS idx_snapshots_created_at ON snapshots(created_at DESC)`);
  // R5-FE-1: 添加 updated_at 列用于追踪标题修改时间，LWW 同步需要区分 created/updated
  try {
    db.run('ALTER TABLE snapshots ADD COLUMN updated_at INTEGER');
  } catch (e) {
    // 列已存在时 sql.js 抛 "duplicate column name" 错误，安全忽略
  }
  await persistDb();

  initialized = true;
}

// 损坏数据自动备份到独立 key，便于后续恢复
async function backupCorruptData(rawData) {
  try {
    const backupKey = `${DB_KEY}_corrupt_${Date.now()}`;
    await chrome.storage.local.set({ [backupKey]: rawData });
// P2: 静默处理非关键扩展错误
  } catch (e) {
    console.error('[DB] 备份损坏数据失败:', e);
  }
}

function parseTabs(json) {
  try {
    const arr = JSON.parse(json);
    return Array.isArray(arr) ? arr : [];
  } catch (err) {
    console.error('[DB] tabs 字段解析失败:', err);
    return [];
  }
}

// 当 parseTabs 解析失败（返回空数组）但 tabCount > 0 时，修正 tabCount 并标记错误
function normalizeTabsResult(tabCount, tabs) {
  if (tabs.length === 0 && tabCount > 0) {
    return { tabCount: 0, tabs, tabsError: true };
  }
  return { tabCount, tabs };
}

// P2-18 决策：保留当前 String.fromCharCode.apply 分块实现。
// 数据库体积通常 <1MB，分块（chunk=8192）已规避 apply 栈长度限制，
// 实测在 chrome.storage.local 配额（约 10MB）内无性能瓶颈。
// 替代方案 btoa(String.fromCharCode(...bytes)) 在大数组上会触发 "Maximum call stack
// size exceeded"；Blob + FileReader 是异步且依赖 DOM，不适合 SW 环境。
function uint8ArrayToBase64(bytes) {
  let binary = '';
  const chunk = 8192;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function base64ToUint8Array(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

async function persistDb() {
  if (!db) return;
  const data = db.export();
  const b64 = uint8ArrayToBase64(data);
  try {
    await chrome.storage.local.set({ [DB_KEY]: b64 });
  } catch (err) {
    const msg = (err && (err.message || String(err))) || '';
    if (/quota/i.test(msg)) {
      throw new Error('STORAGE_QUOTA_EXCEEDED');
    }
    throw err;
  }
}

export async function saveSnapshot(title, tabs) {
  await initDatabase();
  if (typeof title !== 'string' || !title.trim()) title = '未命名上下文';
  if (!Array.isArray(tabs)) throw new Error('tabs 必须为数组');

  return enqueueWrite(async () => {
    const stmt = db.prepare(`
      INSERT INTO snapshots (title, created_at, tab_count, tabs)
      VALUES (?, ?, ?, ?)
    `);
    const now = Date.now();
    try {
      stmt.run([title, now, tabs.length, JSON.stringify(tabs)]);
    } finally {
      stmt.free();
    }
    await persistDb();
    return now;
  });
}

export async function getSnapshots(limit = 50, offset = 0) {
  await initDatabase();
  // 防御：limit/offset 必须为非负整数，避免 SQL 异常
  const safeLimit = Number.isInteger(limit) && limit > 0 ? limit : 50;
  const safeOffset = Number.isInteger(offset) && offset >= 0 ? offset : 0;
  const stmt = db.prepare(`
    SELECT id, title, created_at, updated_at, tab_count, tabs
    FROM snapshots
    ORDER BY created_at DESC
    LIMIT ? OFFSET ?
  `);
  const rows = [];
  try {
    stmt.bind([safeLimit, safeOffset]);
    while (stmt.step()) {
      const row = stmt.getAsObject();
      const tabs = parseTabs(row.tabs);
      const normalized = normalizeTabsResult(row.tab_count, tabs);
      rows.push({
        id: row.id,
        title: row.title,
        createdAt: row.created_at,
        updatedAt: row.updated_at || null,
        tabCount: normalized.tabCount,
        tabs: normalized.tabs,
        ...(normalized.tabsError ? { tabsError: true } : {})
      });
    }
  } finally {
    stmt.free();
  }
  return rows;
}

export async function getSnapshotById(id) {
  await initDatabase();
  const stmt = db.prepare('SELECT * FROM snapshots WHERE id = ?');
  let result = null;
  try {
    stmt.bind([id]);
    if (stmt.step()) {
      const row = stmt.getAsObject();
      const tabs = parseTabs(row.tabs);
      const normalized = normalizeTabsResult(row.tab_count, tabs);
      result = {
        id: row.id,
        title: row.title,
        createdAt: row.created_at,
        updatedAt: row.updated_at || null,
        tabCount: normalized.tabCount,
        tabs: normalized.tabs,
        ...(normalized.tabsError ? { tabsError: true } : {})
      };
    }
  } finally {
    stmt.free();
  }
  return result;
}

export async function deleteSnapshot(id) {
  await initDatabase();
  return enqueueWrite(async () => {
    db.run('DELETE FROM snapshots WHERE id = ?', [id]);
    await persistDb();
  });
}

// 清除所有快照数据（F-SEC-03：用户主动清空，便于在公用设备上彻底移除未加密数据）
export async function clearAllSnapshots() {
  await initDatabase();
  return enqueueWrite(async () => {
    db.run('DELETE FROM snapshots');
    await persistDb();
    return db.getRowsModified();
  });
}

// 导出全部快照为 JSON（数据可控原则）
export async function exportSnapshots() {
  await initDatabase();
  const stmt = db.prepare('SELECT id, title, created_at, updated_at, tab_count, tabs FROM snapshots ORDER BY created_at DESC');
  const rows = [];
  try {
    while (stmt.step()) {
      const row = stmt.getAsObject();
      rows.push({
        id: row.id,
        title: row.title,
        createdAt: row.created_at,
        updatedAt: row.updated_at || null,
        tabCount: row.tab_count,
        tabs: parseTabs(row.tabs)
      });
    }
  } finally {
    stmt.free();
  }
  return {
    version: 1,
    exportedAt: Date.now(),
    count: rows.length,
    snapshots: rows
  };
}

// 从 JSON 导入快照（追加模式：不检查 id 冲突，直接插入新记录）
export async function importSnapshots(data) {
  await initDatabase();
  if (!data || !Array.isArray(data.snapshots)) {
    throw new Error('导入数据格式无效');
  }
  if (data.snapshots.length > 5000) {
    throw new Error('IMPORT_TOO_MANY: 单次导入不能超过 5000 条');
  }
  // 逐条校验，发现任何非法记录立即拒绝整个导入
  for (const snap of data.snapshots) {
    if (typeof snap.title !== 'string') {
      throw new Error('IMPORT_INVALID: 快照标题必须为字符串');
    }
    if (!Array.isArray(snap.tabs) || !snap.tabs.every(t => t && typeof t.url === 'string')) {
      throw new Error('IMPORT_INVALID: 快照 tabs 字段无效');
    }
    if (typeof snap.createdAt !== 'number') {
      throw new Error('IMPORT_INVALID: 快照 createdAt 必须为数字');
    }
  }
  return enqueueWrite(async () => {
    let imported = 0;
    db.run("BEGIN");
    try {
      // 同一批次内若 createdAt 相同会导致排序顺序不稳定；
      // 这里保证唯一性：原值缺失用 Date.now()+i 兜底，原值重复则递增直到不冲突
      const seenTimestamps = new Set();
      data.snapshots.forEach((snap, i) => {
        let ts = snap.createdAt || (Date.now() + i);
        while (seenTimestamps.has(ts)) ts += 1;
        snap.createdAt = ts;
        seenTimestamps.add(ts);
      });
      for (const snap of data.snapshots) {
        const stmt = db.prepare(`
          INSERT INTO snapshots (title, created_at, tab_count, tabs)
          VALUES (?, ?, ?, ?)
        `);
        try {
          stmt.run([snap.title, snap.createdAt, snap.tabs.length, JSON.stringify(snap.tabs)]);
          imported++;
        } finally {
          stmt.free();
        }
      }
      db.run("COMMIT");
    } catch (e) {
      db.run("ROLLBACK");
      throw e;
    }
    await persistDb();
    return imported;
  });
}

// 更新快照标题
export async function updateSnapshotTitle(id, title) {
  await initDatabase();
  if (typeof title !== 'string' || !title.trim()) {
    throw new Error('标题不能为空');
  }
  return enqueueWrite(async () => {
    db.run('UPDATE snapshots SET title = ?, updated_at = ? WHERE id = ?', [title.trim(), Date.now(), id]);
    if (db.getRowsModified() === 0) {
      throw new Error('找不到该快照');
    }
    await persistDb();
  });
}

// 搜索快照（标题模糊匹配）
export async function searchSnapshots(keyword) {
  await initDatabase();
  // 空 keyword 防御
  if (typeof keyword !== 'string' || keyword.length === 0) {
    return [];
  }
  // 转义 LIKE 特殊字符 % _ \，避免注入与误匹配
  const escaped = keyword.replace(/[%_\\]/g, '\\$&');
  const stmt = db.prepare(`
    SELECT id, title, created_at, updated_at, tab_count, tabs FROM snapshots
    WHERE title LIKE ? ESCAPE '\\'
    ORDER BY created_at DESC
    LIMIT 50
  `);
  const rows = [];
  try {
    stmt.bind([`%${escaped}%`]);
    while (stmt.step()) {
      const row = stmt.getAsObject();
      const tabs = parseTabs(row.tabs);
      const normalized = normalizeTabsResult(row.tab_count, tabs);
      rows.push({
        id: row.id,
        title: row.title,
        createdAt: row.created_at,
        updatedAt: row.updated_at || null,
        tabCount: normalized.tabCount,
        tabs: normalized.tabs
      });
    }
  } finally {
    stmt.free();
  }
  return rows;
}
