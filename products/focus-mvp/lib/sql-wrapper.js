// sql-wrapper.js — 包装 sql.js UMD 构建，导出全局 initSqlJs
import './sql-wasm.js';

// 守卫：若 sql-wasm.js 未正确挂载 initSqlJs 到 self，导出抛错的函数，
// 避免后续调用以更隐蔽的方式失败（如 "initSqlJs is not a function"）
function initSqlJsGuard() {
  throw new Error('[sql-wrapper] initSqlJs 未定义：sql-wasm.js 加载失败');
}

const initSqlJs = typeof self.initSqlJs === 'function' ? self.initSqlJs : initSqlJsGuard;

export default initSqlJs;
