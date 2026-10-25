// Phase 7.5 现场演练 — 模拟用户 30 秒首屏走查（v2: setExtraHTTPHeaders）
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
  { name: '01_demo', url: '/demo', desc: 'demo 演示 入口' },
  { name: '02_project_detail', url: '/admin/projects/1', desc: '项目详情 + AI 助手 + Context Cards' },
  { name: '03_admin_stats', url: '/admin/stats', desc: '统计仪表盘 + 数据可视化' },
  { name: '04_admin_index', url: '/', desc: '项目列表 admin 首页' },
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    extraHTTPHeaders: {
      'Authorization': AUTH,
    },
  });

  for (const page of pages) {
    console.log(`\n=== ${page.name}: ${page.url} — ${page.desc} ===`);
    const p = await context.newPage();
    try {
      const resp = await p.goto(`${BASE}${page.url}`, { waitUntil: 'networkidle', timeout: 15000 });
      console.log(`  Status: ${resp ? resp.status() : 'N/A'}`);

      await p.waitForTimeout(2000);

      const shotPath = path.join(OUT_DIR, `${page.name}_judge_walkthrough.png`);
      await p.screenshot({ path: shotPath, fullPage: false });
      console.log(`  Screenshot: ${shotPath}`);

      const title = await p.title();
      console.log(`  Title: ${title}`);

      const bodyText = await p.evaluate(() => {
        const body = document.body;
        if (!body) return '';
        const text = body.innerText || '';
        return text.slice(0, 2000);
      });
      console.log(`  First-screen text (2000 chars):`);
      console.log('  ---');
      console.log(bodyText.split('\n').map(l => '    ' + l).join('\n'));
      console.log('  ---');

      const ctas = await p.evaluate(() => {
        const els = Array.from(document.querySelectorAll('a, button, [role="button"]'));
        const results = [];
        for (const el of els.slice(0, 15)) {
          const text = (el.innerText || el.textContent || '').trim().slice(0, 50);
          const href = el.getAttribute('href') || '';
          if (text) results.push(`${text}${href ? ' -> ' + href : ''}`);
        }
        return results;
      });
      console.log(`  Top CTAs/links (first 15):`);
      ctas.forEach(c => console.log(`    - ${c}`));

      const headings = await p.evaluate(() => {
        const els = Array.from(document.querySelectorAll('h1, h2, h3'));
        return els.slice(0, 10).map(el => `${el.tagName}: ${el.innerText.trim().slice(0, 80)}`);
      });
      console.log(`  Headings (first 10):`);
      headings.forEach(h => console.log(`    - ${h}`));

    } catch (e) {
      console.error(`  ERROR: ${e.message}`);
    } finally {
      await p.close();
    }
  }

  await browser.close();
  console.log('\n=== Walkthrough complete ===');
})();
