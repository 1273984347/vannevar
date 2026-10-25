// Phase 6.3 回归验证：桌面视口 1440x900 渲染 + 性能指标
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

// P0 修复（DRL R2 class-level）：移除硬编码密码默认值，缺失即 fail-fast（与 dogfood-qc.js 一致）
const ADMIN_PASS = process.env.BRIDGE_ADMIN_PASS;
if (!ADMIN_PASS) {
  console.error('[FATAL] BRIDGE_ADMIN_PASS 环境变量未设置。');
  console.error('        用法: BRIDGE_ADMIN_PASS=<your-pass> node ' + path.basename(process.argv[1]));
  process.exit(2);
}
const AUTH = 'Basic ' + Buffer.from(`admin:${ADMIN_PASS}`).toString('base64');
const BASE = 'http://localhost:8000';
const OUT_DIR = path.join(__dirname, '..', 'dogfood_shots');

const pages = [
  { name: '21_admin_index_desktop', url: '/' },
  { name: '22_demo_desktop', url: '/demo' },
  { name: '23_project_detail_desktop', url: '/admin/projects/1' },
  { name: '24_admin_stats_desktop', url: '/admin/stats' },
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    extraHTTPHeaders: { 'Authorization': AUTH },
  });

  for (const page of pages) {
    console.log(`\n=== ${page.name}: ${page.url} ===`);
    const p = await context.newPage();
    try {
      const resp = await p.goto(`${BASE}${page.url}`, { waitUntil: 'networkidle', timeout: 20000 });
      console.log(`  Status: ${resp ? resp.status() : 'N/A'}`);

      // 收集性能指标
      const perf = await p.evaluate(() => {
        const nav = performance.getEntriesByType('navigation')[0] || {};
        const paints = performance.getEntriesByType('paint');
        const fcp = paints.find(e => e.name === 'first-contentful-paint');
        const lcpEntries = performance.getEntriesByType('largest-contentful-paint');
        const lcp = lcpEntries[lcpEntries.length - 1];
        return {
          domContentLoaded: Math.round(nav.domContentLoadedEventEnd || 0),
          loadEvent: Math.round(nav.loadEventEnd || 0),
          transferSize: nav.transferSize || 0,
          fcp: fcp ? Math.round(fcp.startTime) : null,
          lcp: lcp ? Math.round(lcp.startTime) : null,
          domNodes: document.querySelectorAll('*').length,
        };
      });
      console.log(`  FCP: ${perf.fcp}ms, LCP: ${perf.lcp}ms, DOM nodes: ${perf.domNodes}`);
      console.log(`  Transfer: ${perf.transferSize} bytes, Load: ${perf.loadEvent}ms`);

      // 横向溢出检查
      const overflow = await p.evaluate(() => {
        return document.documentElement.scrollWidth - document.documentElement.clientWidth;
      });
      console.log(`  Horizontal overflow: ${overflow}px`);

      const shotPath = path.join(OUT_DIR, `${page.name}.png`);
      await p.screenshot({ path: shotPath, fullPage: false });
      console.log(`  Screenshot: ${shotPath}`);
    } catch (e) {
      console.error(`  ERROR: ${e.message}`);
    } finally {
      await p.close();
    }
  }

  await browser.close();
  console.log('\n=== Desktop regression check complete ===');
})();
