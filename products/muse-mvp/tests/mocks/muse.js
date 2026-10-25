// tests/mocks/muse.js — Muse sync 加密路径测试用的 muse.js mock
//
// core/sync.js 从 './muse.js' 导入 exportInspirations / importInspirations /
// getMoods / addMood。本 mock 提供内存版实现，避免真实 muse.js 走 SQLite WASM。
//
// 契约（与真实 core/muse.js 对齐）：
//   exportInspirations() → { inspirations: [...], moods: [...] }
//   importInspirations({ inspirations, moods }) → 新增条数
//   getMoods() → moods 数组
//   addMood(name, color) → 新建的 mood 对象；重名返回 null

const _state = {
  inspirations: [],
  moods: [],
  _nextId: 1,
};

export async function exportInspirations() {
  return {
    inspirations: _state.inspirations.map((r) => ({ ...r })),
    moods: _state.moods.map((m) => ({ ...m })),
  };
}

export async function importInspirations({ inspirations = [], moods = [] } = {}) {
  let count = 0;
  for (const insp of inspirations) {
    _state.inspirations.push({ ...insp, id: _state._nextId++ });
    count++;
  }
  for (const mood of moods) {
    _state.moods.push({ ...mood, id: _state._nextId++ });
  }
  return count;
}

export async function getMoods() {
  return _state.moods.map((m) => ({ ...m }));
}

export async function addMood(name, color) {
  if (_state.moods.some((m) => m.name === name)) return null;
  const mood = {
    id: _state._nextId++,
    name,
    color,
    sortOrder: _state.moods.length,
    createdAt: Date.now(),
  };
  _state.moods.push(mood);
  return mood;
}

// 测试辅助：重置 mock 状态
export function _resetMuseState() {
  _state.inspirations = [];
  _state.moods = [];
  _state._nextId = 1;
}

// 测试辅助：预置灵感
export function _seedInspirations(rows) {
  for (const row of rows) {
    _state.inspirations.push({ id: _state._nextId++, ...row });
  }
}

// 测试辅助：预置氛围
export function _seedMoods(rows) {
  for (const row of rows) {
    _state.moods.push({ id: _state._nextId++, ...row });
  }
}

// 测试辅助：读取灵感
export function _getInspirations() {
  return _state.inspirations.map((r) => ({ ...r }));
}
