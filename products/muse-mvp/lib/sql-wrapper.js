// sql-wrapper.js — 包装 sql.js UMD 构建，导出全局 initSqlJs
import './sql-wasm.js';
export default self.initSqlJs;
