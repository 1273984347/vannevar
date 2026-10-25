// Programmatic Lighthouse runner with reliable Basic Auth.
// Strategy: launch Chrome with puppeteer, set up Basic Auth at the BROWSER
// level by listening for every new target (page) and calling authenticate()
// on each. This ensures Lighthouse's gatherer page also gets the credentials.
const fs = require('fs');
const path = require('path');

const USERNAME = process.env.BRIDGE_ADMIN_USER || 'admin';
// P0 修复（DRL R1b class-level）：移除硬编码密码，缺失即 fail-fast（与 dogfood-qc.js 一致）
const PASSWORD = process.env.BRIDGE_ADMIN_PASS;
if (!PASSWORD) {
  console.error('[FATAL] BRIDGE_ADMIN_PASS 环境变量未设置。');
  console.error('        用法: BRIDGE_ADMIN_PASS=<your-pass> node ' + path.basename(process.argv[1]));
  process.exit(2);
}
const BASE = 'http://localhost:8000';
const OUT_DIR = __dirname;

const pages = [
  { name: 'index', url: '/' },
  { name: 'demo', url: '/demo' },
  { name: 'project-detail', url: '/admin/projects/1' },
  { name: 'api-projects-1', url: '/api/projects/1' },
];

async function runLighthouse() {
  // 开源发布改造（2026-10-25）：不再硬编码本机 npm 全局路径，改为动态探测
  const { resolveLighthouseHome, resolvePuppeteerCore, resolveChromePath, missingDependency } = require('./lh-resolve');
  const LH_HOME = resolveLighthouseHome();
  const PUPPETEER_DIR = resolvePuppeteerCore();
  const CHROME_BIN = resolveChromePath();
  if (!LH_HOME || !PUPPETEER_DIR) {
    missingDependency('lighthouse / puppeteer-core', [
      '全局安装：npm install -g lighthouse puppeteer-core',
      '或设置 LIGHTHOUSE_HOME / PUPPETEER_CORE 环境变量',
    ]);
  }
  const lighthouseMod = require(LH_HOME);
  const lighthouse = lighthouseMod.default || lighthouseMod;
  const puppeteer = require(PUPPETEER_DIR);

  const LH_PORT = 9222;

  const chrome = await puppeteer.launch({
    executablePath: CHROME_BIN || undefined,
    headless: 'new',
    args: [
      '--no-sandbox',
      '--disable-gpu',
      '--disable-dev-shm-usage',
      `--remote-debugging-port=${LH_PORT}`,
    ],
  });

  // CRITICAL: Listen for every new page (target) and register Basic Auth on it.
  // Lighthouse creates its own page when it runs, so we need to authenticate
  // that page too, not just our own.
  chrome.on('targetcreated', async (target) => {
    if (target.type() === 'page') {
      try {
        const newPage = await target.page();
        if (newPage) {
          await newPage.authenticate({ username: USERNAME, password: PASSWORD });
        }
      } catch (e) {
        // ignore
      }
    }
  });

  // Also authenticate the initial about:blank page
  const pages_arr = await chrome.pages();
  for (const p of pages_arr) {
    await p.authenticate({ username: USERNAME, password: PASSWORD });
  }

  try {
    for (const page of pages) {
      console.log(`\n=== Scanning ${page.name}: ${page.url} ===`);
      try {
        const options = {
          logLevel: 'error',
          output: ['json', 'html'],
          outputPath: path.join(OUT_DIR, page.name),
          onlyCategories: ['performance', 'accessibility', 'best-practices', 'seo'],
          port: LH_PORT,
        };
        const result = await lighthouse(`${BASE}${page.url}`, options);
        if (result && result.lhr) {
          if (result.lhr.runtimeError) {
            console.error(`  [runtimeError] ${result.lhr.runtimeError.code}: ${result.lhr.runtimeError.message}`);
          }
          const cats = result.lhr.categories;
          const perf = Math.round((cats.performance?.score ?? 0) * 100);
          const a11y = Math.round((cats.accessibility?.score ?? 0) * 100);
          const bp = Math.round((cats['best-practices']?.score ?? 0) * 100);
          const seo = Math.round((cats.seo?.score ?? 0) * 100);
          const audits = result.lhr.audits;
          const lcp = audits['largest-contentful-paint']?.displayValue;
          const fcp = audits['first-contentful-paint']?.displayValue;
          const tbt = audits['total-blocking-time']?.displayValue;
          const cls = audits['cumulative-layout-shift']?.displayValue;
          const inp = audits['interaction-to-next-paint']?.displayValue;
          const ttfb = audits['server-response-time']?.displayValue;
          const si = audits['speed-index']?.displayValue;
          console.log(`  Performance=${perf} Accessibility=${a11y} BestPractices=${bp} SEO=${seo}`);
          console.log(`  LCP=${lcp} | FCP=${fcp} | TBT=${tbt} | CLS=${cls} | INP=${inp} | TTFB=${ttfb} | SI=${si}`);
        }
      } catch (e) {
        console.error(`[ERROR] ${page.name}: ${e.message}`);
      }
    }
  } finally {
    await chrome.close();
  }
  console.log('\n=== All scans complete ===');
}

runLighthouse().catch(e => {
  console.error('Fatal:', e);
  process.exit(1);
});
