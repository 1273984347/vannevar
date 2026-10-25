// tests/loader-hooks.mjs — ESM resolve hook
// 把 sync.js 内部的 './auth.js' / './db.js' 重定向到 tests/mocks/ 下的 mock 模块
// crypto.js 保持原样加载（真实模块）

export async function resolve(specifier, context, nextResolve) {
  const parentURL = context.parentURL || '';
  // 仅重定向 sync.js 的 import（避免影响其他模块）
  // Windows 路径可能用 \ 或 /，统一检查
  const isSyncJs = parentURL.includes('focus-mvp/sync.js') || parentURL.includes('focus-mvp\\sync.js');
  if (isSyncJs && (specifier === './auth.js' || specifier === './db.js')) {
    // 重定向到 tests/mocks/auth.js 或 tests/mocks/db.js
    // parentURL 改为 loader-hooks.mjs 所在目录，让 nextResolve 相对它解析
    const mockSpec = `./mocks/${specifier.slice(2)}`;
    return nextResolve(mockSpec, { ...context, parentURL: import.meta.url });
  }
  return nextResolve(specifier, context);
}
