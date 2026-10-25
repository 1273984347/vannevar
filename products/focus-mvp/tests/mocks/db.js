// tests/mocks/db.js — sync.js 加密路径测试用的 db mock
// 不依赖 SQLite WASM，状态保存在模块内存中

const _snapshots = [];

// 模拟 exportSnapshots：返回所有快照
export async function exportSnapshots() {
  return { snapshots: _snapshots.slice() };
}

// 模拟 importSnapshots：将新快照加入内存，返回插入数
export async function importSnapshots(data) {
  const newSnaps = data?.snapshots || [];
  for (const snap of newSnaps) {
    _snapshots.push({ ...snap, id: _snapshots.length + 1 });
  }
  return newSnaps.length;
}

// 测试辅助：重置 mock 状态
export function _resetDbState() {
  _snapshots.length = 0;
}

// 测试辅助：预置快照
export function _seedSnapshots(snaps) {
  _snapshots.length = 0;
  for (const s of snaps) {
    _snapshots.push({ ...s });
  }
}
