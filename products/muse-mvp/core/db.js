// core/db.js — SQLite 本地存储封装（MV3 Service Worker 兼容）
// 所有数据以 SQLite 数据库二进制形式存于 chrome.storage.local

import initSqlJs from '../lib/sql-wrapper.js';

const DB_KEY = 'vannevar_muse_db';
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
    // 注意：locateFile 的基准是 Service Worker 脚本 URL。
    // 本文件位于 core/background.js，wasm 在 lib/，所以用 ../lib/
    locateFile: (file) => `../lib/${file}`
  });

  const stored = await chrome.storage.local.get(DB_KEY);
  if (stored[DB_KEY] && typeof stored[DB_KEY] === 'string' && stored[DB_KEY].length > 0) {
    // Base64 格式（新版）
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

  // 初始化时即创建 schema 与索引，确保数据库可用
  db.run(`
    CREATE TABLE IF NOT EXISTS inspirations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      url TEXT NOT NULL,
      note TEXT NOT NULL DEFAULT '',
      mood TEXT NOT NULL DEFAULT '',
      selected_text TEXT NOT NULL DEFAULT '',
      created_at INTEGER NOT NULL
    )
  `);
  db.run('CREATE INDEX IF NOT EXISTS idx_inspirations_created_at ON inspirations(created_at DESC)');

  // 旧库兼容：inspirations 表加 pinned 列（已存在则忽略）
  try {
    db.run('ALTER TABLE inspirations ADD COLUMN pinned INTEGER DEFAULT 0');
  } catch (e) {
    // 列已存在时 sql.js 抛 "duplicate column name" 错误，安全忽略
  }

  // R5-FE-1: inspirations 表加 updated_at 列，追踪修改时间（LWW 同步需要）
  try {
    db.run('ALTER TABLE inspirations ADD COLUMN updated_at INTEGER');
  } catch (e) {
    // 列已存在时忽略
  }

  // Phase 4.2: 媒体会话集成 — 加 4 个可选媒体元数据列（与 muse.js ensureSchema 保持一致）
  for (const col of [
    'media_type TEXT',
    'media_artist TEXT',
    'media_album TEXT',
    'media_artwork_url TEXT',
  ]) {
    try {
      db.run(`ALTER TABLE inspirations ADD COLUMN ${col}`);
    } catch (e) {
      // 列已存在时忽略
    }
  }

  // 氛围标签表（用户可自定义增删改）
  db.run(`
    CREATE TABLE IF NOT EXISTS moods (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      color TEXT NOT NULL,
      sort_order INTEGER DEFAULT 0
    )
  `);

  // 如果 moods 表为空且从未 seed 过，插入默认 6 个标签
  // 用 moods_seeded 标记避免用户删除所有氛围后被重新插入
  const moodCountResult = db.exec('SELECT COUNT(*) FROM moods');
  const moodCount = moodCountResult.length > 0 ? moodCountResult[0].values[0][0] : 0;
  if (moodCount === 0) {
    const seededFlag = await chrome.storage.local.get('moods_seeded');
    if (!seededFlag.moods_seeded) {
      const defaults = [
        ['日常', '#c2410c', 0],
        ['夜晚', '#44403c', 1],
        ['兴奋', '#f59e0b', 2],
        ['忧郁', '#78716c', 3],
        ['专注', '#10b981', 4],
        ['放松', '#d97706', 5]
      ];
      for (const [name, color, sortOrder] of defaults) {
        db.run(
          'INSERT INTO moods (name, color, sort_order) VALUES (?, ?, ?)',
          [name, color, sortOrder]
        );
      }
      await chrome.storage.local.set({ moods_seeded: true });
    }
  }

  initialized = true;
}

export async function persistDb() {
  if (!db) throw new Error('数据库未初始化');
  return enqueueWrite(async () => {
    try {
      const data = db.export();
      const b64 = uint8ArrayToBase64(data);
      await chrome.storage.local.set({ [DB_KEY]: b64 });
    } catch (err) {
      console.error('[DB] 持久化失败:', err);
      throw err;
    }
  });
}

export function run(sql, params = []) {
  if (!db) throw new Error('数据库未初始化');
  db.run(sql, params);
}

export function query(sql, params = []) {
  if (!db) throw new Error('数据库未初始化');
  const stmt = db.prepare(sql);
  const rows = [];
  try {
    stmt.bind(params);
    while (stmt.step()) {
      rows.push(stmt.getAsObject());
    }
  } finally {
    stmt.free();
  }
  return rows;
}
