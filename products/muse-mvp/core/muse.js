// core/muse.js — Muse 灵感捕获与归档
// 依赖 core/db.js

import { initDatabase, run, query, persistDb } from './db.js';

const MOODS = ['日常', '夜晚', '兴奋', '忧郁', '专注', '放松'];

// 默认氛围颜色映射（用于 ensureSchema 时 moods 表初始化失败兜底，以及
// 兼容旧 inspirations 中已存在但未在 moods 表登记的 mood 文本）
const DEFAULT_MOOD_COLORS = {
  '日常': '#c2410c',
  '夜晚': '#44403c',
  '兴奋': '#f59e0b',
  '忧郁': '#78716c',
  '专注': '#10b981',
  '放松': '#d97706'
};

// URL 安全校验：白名单模式 — 仅允许 http: 与 https: scheme。
// 显式排除 chrome*/edge/view-source/about/file/data/blob/devtools/javascript 等内部或危险 scheme。
function isAllowedUrl(url) {
  if (!url || typeof url !== 'string') return false;
  const lower = url.trim().toLowerCase();
  // 显式排除浏览器内部 scheme 与危险 scheme（防御层，便于阅读与审计）
  const blockedPrefixes = [
    'chrome:',
    'chrome-search:',
    'chrome-extension:',
    'edge:',
    'view-source:',
    'about:',
    'file:',
    'data:',
    'blob:',
    'devtools:',
    'javascript:'
  ];
  if (blockedPrefixes.some((p) => lower.startsWith(p))) return false;
  // 白名单：仅允许 http: / https:（通过 URL 解析确保 scheme 规范）
  let parsed;
  try {
    parsed = new URL(url);
  } catch (_) {
    return false;
  }
  return parsed.protocol === 'http:' || parsed.protocol === 'https:';
}

let ensuredSchema = false;
async function ensureSchema() {
  if (ensuredSchema) return;
  await initDatabase();
  run(`
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
  run('CREATE INDEX IF NOT EXISTS idx_inspirations_created_at ON inspirations(created_at DESC)');

  // 旧库兼容：inspirations 表加 pinned 列（已存在则忽略）
  try {
    run('ALTER TABLE inspirations ADD COLUMN pinned INTEGER DEFAULT 0');
  } catch (e) {
    // 列已存在时 sql.js 抛 "duplicate column name" 错误，安全忽略
  }

  // R5-FE-1: inspirations 表加 updated_at 列，追踪修改时间（LWW 同步需要）
  try {
    run('ALTER TABLE inspirations ADD COLUMN updated_at INTEGER');
  } catch (e) {
    // 列已存在时忽略
  }

  // Phase 4.2: 媒体会话集成 — 加 4 个可选媒体元数据列
  // media_type: music/podcast/video，NULL 表示非媒体灵感
  // media_artist / media_album / media_artwork_url: 媒体元数据，NULL 表示未提供
  for (const col of [
    'media_type TEXT',
    'media_artist TEXT',
    'media_album TEXT',
    'media_artwork_url TEXT',
  ]) {
    try {
      run(`ALTER TABLE inspirations ADD COLUMN ${col}`);
    } catch (e) {
      // 列已存在时忽略
    }
  }

  // 氛围标签表（兜底创建；db.js initDb 中也会创建并 seed 默认值）
  run(`
    CREATE TABLE IF NOT EXISTS moods (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      color TEXT NOT NULL,
      sort_order INTEGER DEFAULT 0
    )
  `);
  // R5-FE-1: moods 表加 updated_at 列，追踪氛围修改时间
  try {
    run('ALTER TABLE moods ADD COLUMN updated_at INTEGER');
  } catch (e) {
    // 列已存在时忽略
  }
  // R6-FE-8: moods 表加 created_at 列，区分创建与修改时间（client_created_at 不应随 updated_at 变化）
  try {
    run('ALTER TABLE moods ADD COLUMN created_at INTEGER');
  } catch (e) {
    // 列已存在时忽略
  }
  // R7-FE-1: backfill 种子 moods 的 NULL 时间戳为 0（稳定值）
  // db.js seed 的 6 个默认 moods 插入时不含 created_at/updated_at，ALTER TABLE 添加列后为 NULL，
  // sync.js 会回退到 Date.now()，导致每次 push 都被 LWW 标记为"已更新"。用 0 作为稳定值对齐兜底 MOODS。
  try {
    run('UPDATE moods SET created_at = 0 WHERE created_at IS NULL');
    run('UPDATE moods SET updated_at = 0 WHERE updated_at IS NULL');
  } catch (e) {
    console.warn('[muse] backfill moods timestamps failed', e);
  }
  ensuredSchema = true;
}

// 从 moods 表读取所有氛围（按 sort_order, id 排序）
// 失败时兜底返回硬编码 MOODS（带默认颜色）
export async function getMoods() {
  await ensureSchema();
  try {
    const rows = query('SELECT id, name, color, sort_order, updated_at, created_at FROM moods ORDER BY sort_order ASC, id ASC');
    if (rows.length > 0) {
      return rows.map((row) => ({
        id: row.id,
        name: row.name,
        color: row.color,
        sortOrder: row.sort_order,
        // R7-FE-2: 用 ?? 而非 ||，避免将 0（稳定兜底值）转为 null，与 sync.js 的 != null 判断一致
        updatedAt: row.updated_at ?? null,
        createdAt: row.created_at ?? null
      }));
    }
  } catch (err) {
// P2: 静默处理非关键扩展错误
  }
  // 兜底：硬编码 MOODS
  // R6-FE-7: 兜底对象添加 createdAt: 0，保证 moodTs 稳定，避免每次 push 标记 mood 为"已更新"
  // R8-FE-1: 补齐 updatedAt: 0，与 DB 读取分支对象形状一致
  return MOODS.map((name, idx) => ({
    id: idx + 1,
    name,
    color: DEFAULT_MOOD_COLORS[name] || '#c2410c',
    sortOrder: idx,
    createdAt: 0,
    updatedAt: 0
  }));
}

// 添加自定义氛围（name 唯一）
export async function addMood(name, color) {
  if (!name || typeof name !== 'string' || !name.trim()) {
    throw new Error('氛围名称不能为空');
  }
  const trimmed = name.trim();
  if (trimmed.length > 20) {
    throw new Error('氛围名称过长（最多 20 字）');
  }
  if (!color || typeof color !== 'string' || !/^#[0-9a-fA-F]{3,8}$/.test(color)) {
    throw new Error('颜色格式无效（需为 #RRGGBB）');
  }
  await ensureSchema();
  // 计算新 sort_order：当前最大值 + 1
  const rows = query('SELECT MAX(sort_order) as max_order FROM moods');
  const nextOrder = (rows.length > 0 && Number.isFinite(rows[0].max_order)) ? rows[0].max_order + 1 : 0;
  try {
    const now = Date.now();
    // R6-FE-8: 写入 created_at，区分创建与修改时间
    run('INSERT INTO moods (name, color, sort_order, updated_at, created_at) VALUES (?, ?, ?, ?, ?)', [trimmed, color, nextOrder, now, now]);
  } catch (err) {
    if (String(err.message || '').includes('UNIQUE')) {
      throw new Error('氛围名称已存在');
    }
    throw err;
  }
  await persistDb();
  // 返回新插入的氛围
  const inserted = query('SELECT id, name, color, sort_order, updated_at, created_at FROM moods WHERE name = ?', [trimmed]);
  return inserted.length > 0
    ? { id: inserted[0].id, name: inserted[0].name, color: inserted[0].color, sortOrder: inserted[0].sort_order, updatedAt: inserted[0].updated_at ?? null, createdAt: inserted[0].created_at ?? null }
    : null;
}

// 删除氛围（关联灵感的 mood 字段保留为文本，不强制清空）
export async function deleteMood(id) {
  if (!Number.isFinite(id)) {
    throw new Error('无效的氛围 ID');
  }
  await ensureSchema();
  run('DELETE FROM moods WHERE id = ?', [id]);
  await persistDb();
}

// 编辑氛围（name / color）
// 若 name 发生变化，会同步更新 inspirations 表的 mood 字段，避免关联断裂
export async function updateMood(id, name, color) {
  if (!Number.isFinite(id)) {
    throw new Error('无效的氛围 ID');
  }
  const fields = [];
  const params = [];
  let newName = null;
  if (name !== undefined) {
    if (typeof name !== 'string' || !name.trim()) {
      throw new Error('氛围名称不能为空');
    }
    const trimmed = name.trim();
    if (trimmed.length > 20) {
      throw new Error('氛围名称过长（最多 20 字）');
    }
    fields.push('name = ?');
    params.push(trimmed);
    newName = trimmed;
  }
  if (color !== undefined) {
    if (typeof color !== 'string' || !/^#[0-9a-fA-F]{3,8}$/.test(color)) {
      throw new Error('颜色格式无效（需为 #RRGGBB）');
    }
    fields.push('color = ?');
    params.push(color);
  }
  if (fields.length === 0) return;

  // 先获取旧名称，便于同步 inspirations 表的 mood 字段
  let oldName = null;
  try {
    const existing = query('SELECT name FROM moods WHERE id = ?', [id]);
    oldName = existing[0]?.name || null;
  } catch (err) {
// P2: 静默处理非关键扩展错误
  }

  // R5-FE-1: 更新时写入 updated_at，LWW 同步才能识别变更
  fields.push('updated_at = ?');
  params.push(Date.now());
  params.push(id);
  try {
    run(`UPDATE moods SET ${fields.join(', ')} WHERE id = ?`, params);
  } catch (err) {
    if (String(err.message || '').includes('UNIQUE')) {
      throw new Error('氛围名称已存在');
    }
    throw err;
  }

  // 同步更新 inspirations 表的 mood 字段
  if (oldName && newName && oldName !== newName) {
    try {
      run('UPDATE inspirations SET mood = ? WHERE mood = ?', [newName, oldName]);
    } catch (err) {
// P2: 静默处理非关键扩展错误
    }
  }
  await persistDb();
}

// 校验 mood 名称是否在 moods 表中（用于保存/更新灵感时校验）
async function isValidMoodName(name) {
  if (!name) return true; // 空字符串允许
  const moods = await getMoods();
  return moods.some((m) => m.name === name);
}

export async function captureInspiration() {
  // 优先获取最后聚焦窗口的当前标签页；若失败，则遍历所有普通窗口兜底
  let tab = null;
  try {
    const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    tab = tabs[0];
  } catch (err) {
// P2: 静默处理非关键扩展错误
  }

  // 兜底 1：如果活动标签是扩展页/chrome 页（例如测试环境直接打开 popup），
  // 就在同一窗口里找第一个可保存的普通标签页。
  // F-2: 删除全局 chrome.tabs.query({}) 兜底，避免跨窗口注入非活动 tab
  if (tab && !isAllowedUrl(tab.url) && tab.windowId) {
    try {
      const tabs = await chrome.tabs.query({ windowId: tab.windowId });
      const fallback = tabs.find((t) => isAllowedUrl(t.url));
      if (fallback) tab = fallback;
    } catch (err) {
// P2: 静默处理非关键扩展错误
    }
  }

  // F-2: 不再遍历所有窗口，活动 tab 不可注入时直接报错
  if (!tab || !isAllowedUrl(tab.url)) {
    throw new Error('当前页面无法被保存');
  }

  // 尝试获取用户选中的文字（需要 activeTab + scripting 权限）
  let selectedText = '';
  try {
    if (chrome.scripting) {
      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => window.getSelection().toString().trim().slice(0, 1000)
      });
      selectedText = results[0]?.result || '';
    }
  } catch (err) {
// P2: 静默处理非关键扩展错误
  }

  // Phase 4.2: 尝试读取页面 Media Session 元数据（一次性快照）
  const media = await captureMediaMetadata(tab.id);

  return {
    title: tab.title || tab.url,
    url: tab.url,
    selectedText,
    media
  };
}

// Phase 4.2: 读取当前 tab 的 Media Session 元数据
// 通过 chrome.scripting.executeScript 一次性读取 navigator.mediaSession.metadata
// 返回 null 表示无媒体会话或读取失败（页面未实现 Media Session API）
// 返回对象结构：{ title, artist, album, artworkUrl, source }
// source 用于推断 media_type: 'music' / 'podcast' / 'video'（基于 URL 启发式判断）
export async function captureMediaMetadata(tabId) {
  if (!chrome.scripting || !tabId) return null;
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        try {
          const md = navigator.mediaSession?.metadata;
          if (!md) return null;
          // artwork 数组按 size 降序取最大那张的 src
          let artworkUrl = '';
          if (Array.isArray(md.artwork) && md.artwork.length > 0) {
            const sorted = [...md.artwork].sort((a, b) => {
              const sa = parseInt(a.sizes?.split('x')[0] || '0', 10) || 0;
              const sb = parseInt(b.sizes?.split('x')[0] || '0', 10) || 0;
              return sb - sa;
            });
            artworkUrl = sorted[0]?.src || '';
          }
          return {
            title: md.title || '',
            artist: md.artist || '',
            album: md.album || '',
            artworkUrl,
          };
        } catch (_) {
          return null;
        }
      },
    });
    const result = results[0]?.result;
    if (!result || !result.title) return null;
    // 根据 URL 启发式推断 media_type
    const url = (await chrome.tabs.get(tabId).catch(() => null))?.url || '';
    let source = 'music';
    if (/youtube\.com|bilibili\.com|vimeo\.com|netflix\.com/i.test(url)) {
      source = 'video';
    } else if (/podcast|播客|喜马拉雅|ximalaya|小宇宙|xiaoyuzhou/i.test(url)) {
      source = 'podcast';
    }
    return { ...result, source };
  } catch (err) {
    // 页面无权注入或 Media Session API 不可用，静默返回 null
    return null;
  }
}

export async function saveInspiration({ title, url, note = '', mood = '', selectedText = '', mediaType = null, mediaArtist = null, mediaAlbum = null, mediaArtworkUrl = null }) {
  if (!title || typeof title !== 'string' || !title.trim()) {
    throw new Error('标题不能为空');
  }
  if (!isAllowedUrl(url)) {
    throw new Error('不允许保存的 URL');
  }
  if (mood && !(await isValidMoodName(mood))) {
    throw new Error('无效的氛围标签');
  }
  await ensureSchema();
  const now = Date.now();
  run(
    'INSERT INTO inspirations (title, url, note, mood, selected_text, created_at, pinned, media_type, media_artist, media_album, media_artwork_url) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)',
    [title, url, note, mood, selectedText, now, mediaType, mediaArtist, mediaAlbum, mediaArtworkUrl]
  );
  await persistDb();
}

export async function getInspirations(limit = 200) {
  await ensureSchema();
  const rows = query(
    `SELECT id, title, url, note, mood, selected_text, created_at, updated_at, COALESCE(pinned, 0) as pinned,
            media_type, media_artist, media_album, media_artwork_url
     FROM inspirations
     ORDER BY pinned DESC, created_at DESC LIMIT ?`,
    [limit]
  );
  return rows.map((row) => ({
    id: row.id,
    title: row.title,
    url: row.url,
    note: row.note,
    mood: row.mood,
    selectedText: row.selected_text,
    createdAt: row.created_at,
    updatedAt: row.updated_at || null,
    pinned: row.pinned ? true : false,
    mediaType: row.media_type || null,
    mediaArtist: row.media_artist || null,
    mediaAlbum: row.media_album || null,
    mediaArtworkUrl: row.media_artwork_url || null,
  }));
}

// 用于 Resume 统计的全量灵感（不受 200 条 LIMIT 限制）
// 设置较大上限（10000）作为安全兜底，避免极端数据量下内存暴涨
export async function getAllInspirationsForStats(limit = 10000) {
  await ensureSchema();
  const rows = query(
    `SELECT id, title, url, note, mood, selected_text, created_at, COALESCE(pinned, 0) as pinned
     FROM inspirations
     ORDER BY created_at DESC LIMIT ?`,
    [limit]
  );
  return rows.map((row) => ({
    id: row.id,
    title: row.title,
    url: row.url,
    note: row.note,
    mood: row.mood,
    selectedText: row.selected_text,
    createdAt: row.created_at,
    pinned: row.pinned ? true : false
  }));
}

export async function getInspirationById(id) {
  await ensureSchema();
  const rows = query('SELECT * FROM inspirations WHERE id = ?', [id]);
  if (rows.length === 0) return null;
  const row = rows[0];
  return {
    id: row.id,
    title: row.title,
    url: row.url,
    note: row.note,
    mood: row.mood,
    selectedText: row.selected_text,
    createdAt: row.created_at,
    updatedAt: row.updated_at || null,
    pinned: row.pinned ? true : false,
    mediaType: row.media_type || null,
    mediaArtist: row.media_artist || null,
    mediaAlbum: row.media_album || null,
    mediaArtworkUrl: row.media_artwork_url || null,
  };
}

export async function deleteInspiration(id) {
  await ensureSchema();
  run('DELETE FROM inspirations WHERE id = ?', [id]);
  await persistDb();
}

// 切换灵感置顶状态
export async function togglePin(id) {
  if (!Number.isFinite(id)) {
    throw new Error('无效的灵感 ID');
  }
  await ensureSchema();
  run('UPDATE inspirations SET pinned = CASE WHEN COALESCE(pinned, 0) = 1 THEN 0 ELSE 1 END, updated_at = ? WHERE id = ?', [Date.now(), id]);
  await persistDb();
  // 返回最新状态
  const rows = query('SELECT COALESCE(pinned, 0) as pinned FROM inspirations WHERE id = ?', [id]);
  return rows.length > 0 ? (rows[0].pinned ? true : false) : false;
}

// 导出全部灵感为 JSON（数据可控原则）
// 同时导出 moods 表，便于在新设备完整恢复氛围标签
export async function exportInspirations() {
  await ensureSchema();
  const rows = query(
    `SELECT id, title, url, note, mood, selected_text, created_at, updated_at, COALESCE(pinned, 0) as pinned,
            media_type, media_artist, media_album, media_artwork_url
     FROM inspirations ORDER BY pinned DESC, created_at DESC`
  );
  const inspirations = rows.map((row) => ({
    id: row.id,
    title: row.title,
    url: row.url,
    note: row.note,
    mood: row.mood,
    selectedText: row.selected_text,
    createdAt: row.created_at,
    updatedAt: row.updated_at || null,
    pinned: row.pinned ? true : false,
    mediaType: row.media_type || null,
    mediaArtist: row.media_artist || null,
    mediaAlbum: row.media_album || null,
    mediaArtworkUrl: row.media_artwork_url || null,
  }));
  const moods = await getMoods();
  return {
    version: 1,
    exportedAt: Date.now(),
    count: inspirations.length,
    moods,
    inspirations
  };
}

// 从 JSON 导入灵感（合并模式：作为新记录写入，保留原 createdAt 与 pinned）
// 若 data.moods 存在，先导入氛围标签（重复名称自动跳过），再导入灵感
export async function importInspirations(data) {
  await ensureSchema();
  if (!data || !Array.isArray(data.inspirations)) {
    throw new Error('导入数据格式无效');
  }
  if (data.inspirations.length > 5000) {
    throw new Error('导入数据过多（最多 5000 条）');
  }
  // 先导入 moods（如果存在），重复名称会被 addMood 跳过
  if (Array.isArray(data.moods)) {
    for (const mood of data.moods) {
      if (!mood || typeof mood.name !== 'string' || !mood.name.trim()) continue;
      if (typeof mood.color !== 'string' || !/^#[0-9a-fA-F]{3,8}$/.test(mood.color)) continue;
      try {
        await addMood(mood.name, mood.color);
      } catch (e) {
        // 跳过重复名称或其它单条错误，继续处理后续条目
      }
    }
  }
  // 预加载当前 mood 名称集合，过滤无效 mood
  const validMoodNames = (await getMoods()).map((m) => m.name);
  // Phase 4.2: 合法媒体类型白名单
  const VALID_MEDIA_TYPES = ['music', 'podcast', 'video'];
  let imported = 0;
  for (const item of data.inspirations) {
    if (!item.title || typeof item.title !== 'string' || !item.title.trim()) continue;
    if (!item.url || !isAllowedUrl(item.url)) continue;
    const mood = typeof item.mood === 'string' && validMoodNames.includes(item.mood) ? item.mood : '';
    const note = typeof item.note === 'string' ? item.note : '';
    const selectedText = typeof item.selectedText === 'string' ? item.selectedText : '';
    const createdAt = Number.isFinite(item.createdAt) ? item.createdAt : Date.now();
    const pinned = item.pinned ? 1 : 0;
    const mediaType = typeof item.mediaType === 'string' && VALID_MEDIA_TYPES.includes(item.mediaType) ? item.mediaType : null;
    const mediaArtist = typeof item.mediaArtist === 'string' && item.mediaArtist ? item.mediaArtist : null;
    const mediaAlbum = typeof item.mediaAlbum === 'string' && item.mediaAlbum ? item.mediaAlbum : null;
    const mediaArtworkUrl = typeof item.mediaArtworkUrl === 'string' && item.mediaArtworkUrl ? item.mediaArtworkUrl : null;
    run(
      'INSERT INTO inspirations (title, url, note, mood, selected_text, created_at, pinned, media_type, media_artist, media_album, media_artwork_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [item.title, item.url, note, mood, selectedText, createdAt, pinned, mediaType, mediaArtist, mediaAlbum, mediaArtworkUrl]
    );
    imported++;
  }
  if (imported > 0) await persistDb();
  return imported;
}

// 编辑灵感：支持部分更新 title / note / mood
export async function updateInspiration(id, { title, note, mood } = {}) {
  await ensureSchema();
  if (title !== undefined) {
    if (typeof title !== 'string' || !title.trim()) {
      throw new Error('标题不能为空');
    }
  }
  if (mood !== undefined && mood !== '' && !(await isValidMoodName(mood))) {
    throw new Error('无效的氛围标签');
  }
  const fields = [];
  const params = [];
  if (title !== undefined) {
    fields.push('title = ?');
    params.push(title.trim());
  }
  if (note !== undefined) {
    params.push(typeof note === 'string' ? note : '');
    fields.push('note = ?');
  }
  if (mood !== undefined) {
    params.push(mood);
    fields.push('mood = ?');
  }
  if (fields.length === 0) return;
  // R5-FE-1: 更新时写入 updated_at，LWW 同步才能识别变更
  fields.push('updated_at = ?');
  params.push(Date.now());
  params.push(id);
  run(`UPDATE inspirations SET ${fields.join(', ')} WHERE id = ?`, params);
  await persistDb();
}

// 搜索灵感：title / note / selected_text / mood 模糊匹配
export async function searchInspirations(keyword) {
  await ensureSchema();
  const kw = typeof keyword === 'string' ? keyword.trim() : '';
  if (!kw) return [];
  // 转义 LIKE 通配符 % _ \，避免用户输入干扰模式匹配
  const escaped = kw.replace(/[%_\\]/g, '\\$&');
  const like = `%${escaped}%`;
  const rows = query(
    `SELECT id, title, url, note, mood, selected_text, created_at, updated_at, COALESCE(pinned, 0) as pinned
     FROM inspirations
     WHERE title LIKE ? ESCAPE '\\' OR note LIKE ? ESCAPE '\\' OR selected_text LIKE ? ESCAPE '\\' OR mood LIKE ? ESCAPE '\\'
     ORDER BY pinned DESC, created_at DESC LIMIT 200`,
    [like, like, like, like]
  );
  return rows.map((row) => ({
    id: row.id,
    title: row.title,
    url: row.url,
    note: row.note,
    mood: row.mood,
    selectedText: row.selected_text,
    createdAt: row.created_at,
    updatedAt: row.updated_at || null,
    pinned: row.pinned ? true : false
  }));
}
