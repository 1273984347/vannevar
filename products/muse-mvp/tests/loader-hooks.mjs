// tests/loader-hooks.mjs — ESM resolve hook for Muse 测试
// 把 muse.js 内部的 './db.js' 重定向到 tests/mocks/db.js，
// 避免 db.js 加载 SQLite WASM（Node 环境不支持 self.initSqlJs UMD 挂载）。
//
// Phase 4.4 收尾：同步加密路径测试还需要重定向
//   - core/sync.js 的 './muse.js'        → tests/mocks/muse.js（内存版灵感读写）
//   - core/sync.js 的 './auth.js'        → tests/mocks/auth.js（可控登录态）
//   - core/crypto-profile.js 的 './auth.js' → tests/mocks/auth.js
//
// 用法：node --import ./tests/loader.mjs tests/test_media_session.mjs
//      node --import ./tests/loader.mjs tests/test_sync_crypto.mjs

export async function resolve(specifier, context, nextResolve) {
  const parentURL = context.parentURL || '';
  // Windows 路径可能用 \ 或 /，统一检查
  const isMuseJs = parentURL.includes('muse-mvp/core/muse.js') || parentURL.includes('muse-mvp\\core\\muse.js');
  const isSyncJs = parentURL.includes('muse-mvp/core/sync.js') || parentURL.includes('muse-mvp\\core\\sync.js');
  const isCryptoProfileJs = parentURL.includes('muse-mvp/core/crypto-profile.js')
    || parentURL.includes('muse-mvp\\core\\crypto-profile.js');

  // 仅重定向 muse.js 的 import（避免影响其他模块）
  if (isMuseJs && specifier === './db.js') {
    // 重定向到 tests/mocks/db.js
    const mockSpec = `./mocks/db.js`;
    return nextResolve(mockSpec, { ...context, parentURL: import.meta.url });
  }
  // sync.js 的灵感读写走内存 mock，不加载真实 muse.js
  if (isSyncJs && specifier === './muse.js') {
    return nextResolve('./mocks/muse.js', { ...context, parentURL: import.meta.url });
  }
  // sync.js / crypto-profile.js 的认证态走 mock，便于测试控制登录与 CSRF 头
  if ((isSyncJs || isCryptoProfileJs) && specifier === './auth.js') {
    return nextResolve('./mocks/auth.js', { ...context, parentURL: import.meta.url });
  }
  return nextResolve(specifier, context);
}
