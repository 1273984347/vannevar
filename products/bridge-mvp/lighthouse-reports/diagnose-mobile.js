// 诊断脚本：定位横向溢出元素 + < 44px 按钮
const { chromium, devices } = require('playwright');

// P0 修复（DRL R2）：移除硬编码密码默认值，缺失即 fail-fast（与 dogfood-qc.js 一致）
const ADMIN_PASS = process.env.BRIDGE_ADMIN_PASS;
if (!ADMIN_PASS) {
  console.error('[FATAL] BRIDGE_ADMIN_PASS 环境变量未设置。');
  console.error('        用法: BRIDGE_ADMIN_PASS=<your-pass> node ' + path.basename(process.argv[1]));
  process.exit(2);
}
const AUTH = 'Basic ' + Buffer.from(`admin:${ADMIN_PASS}`).toString('base64');
const BASE = 'http://localhost:8000';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const iphone = devices['iPhone 12 Pro'];
  const context = await browser.newContext({
    ...iphone,
    extraHTTPHeaders: { 'Authorization': AUTH },
  });

  const targets = [
    { name: 'demo', url: '/demo' },
    { name: 'project-detail', url: '/admin/projects/1' },
  ];

  for (const t of targets) {
    console.log(`\n=== ${t.name} ===`);
    const p = await context.newPage();
    await p.goto(`${BASE}${t.url}`, { waitUntil: 'networkidle', timeout: 20000 });
    await p.waitForTimeout(2000);

    // 1. 横向溢出元素
    const overflow = await p.evaluate(() => {
      const docWidth = document.documentElement.clientWidth;
      const scrollWidth = document.documentElement.scrollWidth;
      const overflow = scrollWidth - docWidth;
      if (overflow <= 0) return { overflow: 0, offenders: [] };
      const offenders = [];
      const all = document.querySelectorAll('*');
      for (const el of all) {
        const r = el.getBoundingClientRect();
        if (r.right > docWidth + 1) {
          offenders.push({
            tag: el.tagName.toLowerCase(),
            cls: el.className || '',
            id: el.id || '',
            right: Math.round(r.right),
            width: Math.round(r.width),
            text: (el.innerText || '').slice(0, 40).replace(/\n/g, ' '),
          });
        }
      }
      return { overflow, offenders: offenders.slice(0, 10) };
    });
    console.log(`  Overflow: ${overflow.overflow}px`);
    overflow.offenders.forEach(o => {
      console.log(`    - ${o.tag}.${o.cls}#${o.id} right=${o.right} width=${o.width} text="${o.text}"`);
    });

    // 2. < 44px 按钮清单
    const smallBtns = await p.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button, a.btn, .primary-btn, .secondary-btn, [role="button"], a'));
      const small = [];
      for (const b of btns) {
        const r = b.getBoundingClientRect();
        if (r.height > 0 && r.height < 44 && r.width > 30) {
          small.push({
            tag: b.tagName.toLowerCase(),
            cls: (b.className || '').slice(0, 60),
            href: b.getAttribute('href') || '',
            h: Math.round(r.height),
            w: Math.round(r.width),
            text: (b.innerText || b.textContent || '').trim().slice(0, 30),
          });
        }
      }
      return small.slice(0, 10);
    });
    console.log(`  Small buttons (< 44px):`);
    smallBtns.forEach(b => {
      console.log(`    - ${b.tag}.${b.cls} h=${b.h} w=${b.w} text="${b.text}" href="${b.href}"`);
    });

    await p.close();
  }

  await browser.close();
})();
