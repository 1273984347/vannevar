// tests/loader-hooks.mjs — ESM resolve hook for Muse media session tests
// 把 muse.js 内部的 './db.js' 重定向到 tests/mocks/db.js，
// 避免 db.js 加载 SQLite WASM（Node 环境不支持 self.initSqlJs UMD 挂载）。
//
// 用法：node --import ./tests/loader.mjs tests/test_media_session.mjs

export async function resolve(specifier, context, nextResolve) {
  const parentURL = context.parentURL || '';
  // 仅重定向 muse.js 的 import（避免影响其他模块）
  // Windows 路径可能用 \ 或 /，统一检查
  const isMuseJs = parentURL.includes('muse-mvp/core/muse.js') || parentURL.includes('muse-mvp\\core\\muse.js');
  if (isMuseJs && specifier === './db.js') {
    // 重定向到 tests/mocks/db.js
    const mockSpec = `./mocks/db.js`;
    return nextResolve(mockSpec, { ...context, parentURL: import.meta.url });
  }
  return nextResolve(specifier, context);
}
