// tests/mocks/db.js — Muse 媒体会话测试用的 db mock
// 不依赖 SQLite WASM，状态保存在模块内存中
//
// 模拟 core/db.js 的 export：initDatabase / run / query / persistDb
// run() 拦截 SQL 并写入 _rows（按表名分桶），query() 拦截 SELECT 返回匹配行
// 仅实现 muse.js 媒体字段测试所需的最小 SQL 子集（INSERT / SELECT / ALTER / CREATE）

const _tables = new Map(); // tableName -> Array<rowObject>
const _schema = new Map(); // tableName -> Set<columnName>

function _parseTableName(sql, kind) {
  // 简化解析：INSERT INTO <t> / SELECT ... FROM <t> / CREATE TABLE IF NOT EXISTS <t> / ALTER TABLE <t>
  const upper = sql.toUpperCase();
  if (kind === 'insert') {
    const m = sql.match(/INSERT\s+INTO\s+(\w+)/i);
    return m ? m[1] : null;
  }
  if (kind === 'select') {
    const m = sql.match(/FROM\s+(\w+)/i);
    return m ? m[1] : null;
  }
  if (kind === 'create') {
    const m = sql.match(/CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)/i);
    return m ? m[1] : null;
  }
  if (kind === 'alter') {
    const m = sql.match(/ALTER\s+TABLE\s+(\w+)/i);
    return m ? m[1] : null;
  }
  return null;
}

function _ensureTable(name) {
  if (!_tables.has(name)) _tables.set(name, []);
  if (!_schema.has(name)) _schema.set(name, new Set());
  return _tables.get(name);
}

function _applyColumnsFromSchema(name, columns) {
  const set = _schema.get(name) || new Set();
  for (const c of columns) set.add(c);
  _schema.set(name, set);
}

export async function initDatabase() {
  // mock：不做任何事，状态在模块加载时已就绪
}

export async function persistDb() {
  // mock：内存状态即是持久化状态
}

export function run(sql, params = []) {
  const upper = sql.toUpperCase();
  if (upper.startsWith('INSERT')) {
    const table = _parseTableName(sql, 'insert');
    if (!table) return;
    _ensureTable(table);
    // 从 SQL 提取列名：(col1, col2, ...) VALUES (?, ?, 0, ?, ...)
    const colMatch = sql.match(/\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)/i);
    let columns = [];
    let valueTokens = [];
    if (colMatch) {
      columns = colMatch[1].split(',').map((s) => s.trim());
      valueTokens = colMatch[2].split(',').map((s) => s.trim());
    }
    // 按 token 类型映射：'?' 取下一个 param，字面量（0/'admin'/等）直接 parse
    const row = {};
    let paramIdx = 0;
    columns.forEach((col, i) => {
      const token = valueTokens[i];
      if (token === '?') {
        row[col] = paramIdx < params.length ? params[paramIdx] : null;
        paramIdx++;
      } else {
        // 字面量：尝试 parse 为 number，否则去掉引号当字符串
        const num = Number(token);
        if (!Number.isNaN(num) && token !== '') {
          row[col] = num;
        } else {
          // 去掉单引号
          row[col] = token.replace(/^'(.*)'$/, '$1');
        }
      }
    });
    _applyColumnsFromSchema(table, columns);
    _tables.get(table).push(row);
    return;
  }
  if (upper.startsWith('CREATE TABLE')) {
    const table = _parseTableName(sql, 'create');
    if (table) _ensureTable(table);
    return;
  }
  if (upper.startsWith('ALTER TABLE')) {
    const table = _parseTableName(sql, 'alter');
    if (!table) return;
    _ensureTable(table);
    // ALTER TABLE <t> ADD COLUMN <col> <type>
    const colMatch = sql.match(/ADD\s+COLUMN\s+(\w+)/i);
    if (colMatch) {
      _schema.get(table).add(colMatch[1]);
    }
    return;
  }
  if (upper.startsWith('DELETE')) {
    const table = _parseTableName(sql, 'select');
    if (table && _tables.has(table)) {
      const m = sql.match(/WHERE\s+id\s*=\s*\?/i);
      if (m) {
        const idVal = params[0];
        const rows = _tables.get(table);
        const idx = rows.findIndex((r) => r.id === idVal);
        if (idx >= 0) rows.splice(idx, 1);
      }
    }
    return;
  }
  if (upper.startsWith('UPDATE')) {
    const m = sql.match(/UPDATE\s+(\w+)\s+SET\s+(.+?)\s+WHERE/i);
    if (m) {
      const table = m[1];
      const setClause = m[2];
      const setCols = setClause.split(',').map((s) => s.split('=')[0].trim());
      if (_tables.has(table)) {
        const whereIdMatch = sql.match(/WHERE\s+id\s*=\s*\?/i);
        const idVal = whereIdMatch ? params[params.length - 1] : null;
        for (const row of _tables.get(table)) {
          if (whereIdMatch && row.id !== idVal) continue;
          setCols.forEach((col, i) => {
            row[col] = params[i] !== undefined ? params[i] : row[col];
          });
        }
      }
    }
    return;
  }
  // 其他 SQL 静默忽略
}

export function query(sql, params = []) {
  const upper = sql.toUpperCase();
  if (!upper.startsWith('SELECT')) return [];
  const table = _parseTableName(sql, 'select');
  if (!table || !_tables.has(table)) return [];
  let rows = _tables.get(table).slice();
  // 简化 WHERE id = ? 支持
  const whereIdMatch = sql.match(/WHERE\s+id\s*=\s*\?/i);
  if (whereIdMatch) {
    const idVal = params[0];
    rows = rows.filter((r) => r.id === idVal);
  }
  // ORDER BY created_at DESC（默认 muse.js 调用）
  if (/ORDER\s+BY\s+created_at\s+DESC/i.test(sql) || /ORDER\s+BY\s+pinned\s+DESC,\s*created_at\s+DESC/i.test(sql)) {
    rows.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  }
  // LIMIT ?
  const limitMatch = sql.match(/LIMIT\s+\?/i);
  if (limitMatch) {
    const limit = params[params.length - 1];
    if (typeof limit === 'number') rows = rows.slice(0, limit);
  }
  // 返回深拷贝避免外部修改污染
  return rows.map((r) => ({ ...r }));
}

// 测试辅助：重置 mock 状态
export function _resetDbState() {
  _tables.clear();
  _schema.clear();
}

// 测试辅助：预置表数据
export function _seedRows(tableName, rows) {
  _ensureTable(tableName);
  _tables.set(tableName, rows.map((r) => ({ ...r })));
  _applyColumnsFromSchema(tableName, Object.keys(rows[0] || {}));
}

// 测试辅助：读取表数据
export function _getRows(tableName) {
  return _tables.has(tableName) ? _tables.get(tableName).slice() : [];
}
