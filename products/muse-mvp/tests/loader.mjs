// tests/loader.mjs — Muse 媒体会话测试的 ESM resolver
// 用法：node --import ./tests/loader.mjs tests/test_media_session.mjs
//
// 作用：把 muse.js 的静态 import './db.js' 重定向到 tests/mocks/db.js，
//       避免 db.js 加载 SQLite WASM（Node 环境不支持 self.initSqlJs UMD 挂载）。
import { register } from 'node:module';
import { pathToFileURL } from 'node:url';
import { dirname, resolve as pathResolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const mocksDir = pathToFileURL(pathResolve(__dirname, 'mocks')).href;

register('./loader-hooks.mjs', import.meta.url);
