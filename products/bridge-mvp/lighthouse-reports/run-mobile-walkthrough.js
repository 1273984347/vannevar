// Phase 6.3 移动端视觉走查 — iPhone 12 Pro 390x844
const { chromium, devices } = require('playwright');
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
  { name: '11_admin_index_mobile', url: '/', desc: 'admin 首页（项目列表）' },
  { name: '12_demo_mobile', url: '/demo', desc: 'demo 演示 入口' },
  { name: '13_project_detail_mobile', url: '/admin/projects/1', desc: '项目详情 6 大模块' },
  { name: '14_admin_stats_mobile', url: '/admin/stats', desc: '统计仪表盘 9 KPI' },
];

(async () => {
  const browser = await chromium.launch({ headless: true });

  // iPhone 12 Pro: 390x844, deviceScaleFactor 3, isMobile true, hasTouch true
  const iphone = devices['iPhone 12 Pro'];
  const context = await browser.newContext({
    ...iphone,
    extraHTTPHeaders: {
      'Authorization': AUTH,
    },
  });

  for (const page of pages) {
    console.log(`\n=== ${page.name}: ${page.url} — ${page.desc} ===`);
    const p = await context.newPage();
    try {
      const resp = await p.goto(`${BASE}${page.url}`, { waitUntil: 'networkidle', timeout: 20000 });
      console.log(`  Status: ${resp ? resp.status() : 'N/A'}`);
      await p.waitForTimeout(2500);

      const shotPath = path.join(OUT_DIR, `${page.name}.png`);
      await p.screenshot({ path: shotPath, fullPage: true });
      const stats = fs.statSync(shotPath);
      console.log(`  Screenshot: ${shotPath} (${(stats.size / 1024).toFixed(1)} KB)`);

      const title = await p.title();
      console.log(`  Title: ${title}`);

      // 提取首屏可见文本 + 检查横向溢出
      const info = await p.evaluate(() => {
        const body = document.body;
        if (!body) return { bodyText: '', overflow: 0 };
        const text = (body.innerText || '').slice(0, 1200);
        // 检测横向溢出
        const docWidth = document.documentElement.clientWidth;
        const scrollWidth = document.documentElement.scrollWidth;
        const overflow = scrollWidth - docWidth;
        // 检测触控目标 < 44px 的按钮
        const btns = Array.from(document.querySelectorAll('button, a.btn, .primary-btn, .secondary-btn, [role="button"]'));
        const small = btns.filter(b => {
          const r = b.getBoundingClientRect();
          return r.height > 0 && r.height < 44;
        }).length;
        return {
          bodyText: text,
          overflow: overflow,
          totalBtns: btns.length,
          smallBtns: small,
        };
      });
      console.log(`  Horizontal overflow: ${info.overflow}px`);
      console.log(`  Buttons: ${info.totalBtns} total, ${info.smallBtns} < 44px height`);
      console.log(`  First-screen text (1200 chars):`);
      console.log('  ---');
      console.log(info.bodyText.split('\n').map(l => '    ' + l).join('\n').slice(0, 1500));
      console.log('  ---');
    } catch (e) {
      console.error(`  ERROR: ${e.message}`);
    } finally {
      await p.close();
    }
  }

  await browser.close();
  console.log('\n=== Mobile walkthrough complete ===');
})();
