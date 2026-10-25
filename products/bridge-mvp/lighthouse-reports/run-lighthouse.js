// Lighthouse runner with Basic Auth header (new credentials).
// Calls Lighthouse CLI directly via node, passing args array (no shell).
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

// Phase 7.3: dev 模式 admin 密码（每次重启服务会变，从 server 启动 log 读取后填入）
// P0 修复（DRL R2）：移除硬编码密码默认值，缺失即 fail-fast（与 dogfood-qc.js 一致）
const ADMIN_PASS = process.env.BRIDGE_ADMIN_PASS;
if (!ADMIN_PASS) {
  console.error('[FATAL] BRIDGE_ADMIN_PASS 环境变量未设置。');
  console.error('        用法: BRIDGE_ADMIN_PASS=<your-pass> node ' + path.basename(process.argv[1]));
  process.exit(2);
}
const AUTH = 'Basic ' + Buffer.from(`admin:${ADMIN_PASS}`).toString('base64');
const BASE = 'http://localhost:8000';
const OUT_DIR = path.join(__dirname);

// Direct CLI entry point (avoids .cmd spawn EINVAL on Node 18.17+)
// 开源发布改造（2026-10-25）：不再硬编码本机 npm 全局路径，改为动态探测
const { resolveLighthouseCli, missingDependency } = require('./lh-resolve');
const LH_CLI = resolveLighthouseCli();
if (!LH_CLI) {
  missingDependency('lighthouse CLI', [
    '全局安装：npm install -g lighthouse',
    '或设置 LIGHTHOUSE_CLI 指向 lighthouse/cli/index.js',
    '或设置 LIGHTHOUSE_HOME 指向 lighthouse 包目录',
  ]);
}

// Phase 7.3: 增加 /admin/stats 页面（Phase 4.3 仪表盘，关键演示路径）
const pages = [
  { name: 'index', url: '/' },
  { name: 'demo', url: '/demo' },
  { name: 'project-detail', url: '/admin/projects/1' },
  { name: 'admin-stats', url: '/admin/stats' },
];

const headersJson = JSON.stringify({ Authorization: AUTH });

function runOne(page) {
  return new Promise((resolve) => {
    const outPath = path.join(OUT_DIR, page.name + '-p7');
    console.log(`\n=== Scanning ${page.name}: ${page.url} ===`);
    const args = [
      LH_CLI,
      `${BASE}${page.url}`,
      '--output', 'json',
      '--output', 'html',
      '--output-path', outPath,
      '--chrome-flags=--headless=new --no-sandbox --disable-gpu',
      '--extra-headers', headersJson,
      '--only-categories=performance,accessibility,best-practices,seo',
      '--quiet',
    ];
    const child = spawn(process.execPath, args, { stdio: 'inherit', shell: false });
    child.on('close', (code) => {
      if (code !== 0) console.error(`[WARN] ${page.name} exited with status ${code}`);
      resolve();
    });
    child.on('error', (e) => {
      console.error(`[ERROR] ${page.name}:`, e.message);
      resolve();
    });
  });
}

(async () => {
  for (const page of pages) {
    await runOne(page);
  }
  console.log('\n=== All scans complete ===');
})();
