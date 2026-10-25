// Puppeteer-based performance measurement (Lighthouse fallback).
// Captures Core Web Vitals (LCP/FCP/CLS/TBT/INP/TTFB) by:
// 1. Launching headless Chrome with puppeteer
// 2. Authenticating via page.authenticate() (Basic Auth)
// 3. Injecting performance observers BEFORE navigation
// 4. Collecting metrics after page load + 5s quiescence
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

// Performance observer script - injected via evaluateOnNewDocument
// Captures: FCP, LCP, CLS, TBT (long tasks), TTFB, INP
const PERF_OBSERVER_SCRIPT = `
(function() {
  window.__perfMetrics = {
    fcp: null,
    lcp: null,
    cls: 0,
    tbt: 0,
    ttfb: null,
    inp: null,
    longTasks: [],
    layoutShifts: [],
    resourceTimings: [],
    domNodes: 0,
    jsHeapSize: null,
  };

  // FCP + TTFB via PerformanceNavigationTiming
  const navEntries = performance.getEntriesByType('navigation');
  if (navEntries.length > 0) {
    const nav = navEntries[0];
    window.__perfMetrics.ttfb = nav.responseStart;
    window.__perfMetrics.domContentLoaded = nav.domContentLoadedEventEnd;
    window.__perfMetrics.loadEventEnd = nav.loadEventEnd;
    window.__perfMetrics.transferSize = nav.transferSize;
    window.__perfMetrics.domInteractive = nav.domInteractive;
  }

  // Paint entries (FCP)
  const paintEntries = performance.getEntriesByType('paint');
  paintEntries.forEach(p => {
    if (p.name === 'first-contentful-paint') {
      window.__perfMetrics.fcp = p.startTime;
    }
  });

  // LCP via PerformanceObserver
  try {
    new PerformanceObserver(list => {
      const entries = list.getEntries();
      if (entries.length > 0) {
        window.__perfMetrics.lcp = entries[entries.length - 1].startTime;
      }
    }).observe({ type: 'largest-contentful-paint', buffered: true });
  } catch (e) {}

  // CLS via PerformanceObserver
  try {
    new PerformanceObserver(list => {
      list.getEntries().forEach(entry => {
        if (!entry.hadRecentInput) {
          window.__perfMetrics.cls += entry.value;
          window.__perfMetrics.layoutShifts.push({ time: entry.startTime, value: entry.value });
        }
      });
    }).observe({ type: 'layout-shift', buffered: true });
  } catch (e) {}

  // TBT via LongTask observer
  try {
    new PerformanceObserver(list => {
      list.getEntries().forEach(entry => {
        window.__perfMetrics.longTasks.push({ startTime: entry.startTime, duration: entry.duration });
        // TBT = sum of (duration - 50) for tasks > 50ms
        if (entry.duration > 50) {
          window.__perfMetrics.tbt += entry.duration - 50;
        }
      });
    }).observe({ type: 'longtask', buffered: true });
  } catch (e) {}

  // INP via PerformanceObserver (event timing)
  try {
    let maxInp = 0;
    new PerformanceObserver(list => {
      list.getEntries().forEach(entry => {
        if (entry.interactionId > 0) {
          const duration = entry.duration;
          if (duration > maxInp) {
            maxInp = duration;
            window.__perfMetrics.inp = duration;
          }
        }
      });
    }).observe({ type: 'event', buffered: true });
  } catch (e) {}

  // DOM node count
  function countDOM() {
    window.__perfMetrics.domNodes = document.querySelectorAll('*').length;
  }
  // Count after load
  if (document.readyState === 'complete') {
    countDOM();
  } else {
    window.addEventListener('load', () => setTimeout(countDOM, 0));
  }
})();
`;

async function measurePage(chrome, page) {
  const url = `${BASE}${page.url}`;
  console.log(`\n=== Measuring ${page.name}: ${page.url} ===`);

  const p = await chrome.newPage();
  // Use setExtraHTTPHeaders to send Authorization on EVERY request,
  // avoiding the 401 challenge cycle entirely (more reliable than authenticate()).
  await p.setExtraHTTPHeaders({
    Authorization: 'Basic ' + Buffer.from(`${USERNAME}:${PASSWORD}`).toString('base64'),
  });
  // Also call authenticate() as a fallback for any sub-resources that might challenge
  await p.authenticate({ username: USERNAME, password: PASSWORD }).catch(() => {});

  // Set mobile emulation for realistic Core Web Vitals
  await p.emulate({
    viewport: { width: 412, height: 823, isMobile: true, hasTouch: true, deviceScaleFactor: 2.625 },
    userAgent: 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
  });

  // Inject perf observer BEFORE navigation
  await p.evaluateOnNewDocument(PERF_OBSERVER_SCRIPT);

  // Enable CDP domains for additional metrics
  const client = await p.target().createCDPSession();
  await client.send('Performance.enable');

  // Collect network requests
  const requests = [];
  const responses = [];
  p.on('requestfinished', req => {
    requests.push({
      url: req.url(),
      method: req.method(),
      resourceType: req.resourceType(),
      transferSize: req.headers()['content-length'] || null,
    });
  });
  p.on('response', res => {
    responses.push({
      url: res.url(),
      status: res.status(),
      contentType: res.headers()['content-type'] || '',
    });
  });

  // Navigate and wait for load
  const startMs = Date.now();
  let response;
  try {
    response = await p.goto(url, { waitUntil: 'networkidle0', timeout: 30000 });
  } catch (e) {
    console.error(`  [nav error] ${e.message}`);
    await p.close();
    return null;
  }
  const navEndMs = Date.now();

  // Wait additional 3s for late-arriving LCP/layout shifts
  await new Promise(r => setTimeout(r, 3000));

  // Collect metrics
  const metrics = await p.evaluate(() => window.__perfMetrics);

  // Get Performance metrics via CDP
  const perfMetrics = await client.send('Performance.getMetrics');
  let heapMetrics = null;
  try {
    heapMetrics = await client.send('Runtime.getHeapUsage');
  } catch (e) {
    // Runtime.getHeapUsage may not be available in all Chrome versions
  }

  // Get DOM size, JS heap, etc.
  const domSize = await p.evaluate(() => document.querySelectorAll('*').length);
  const htmlSize = await p.content();
  const htmlBytes = Buffer.byteLength(htmlSize, 'utf8');

  // Compute resource summary
  const totalResources = requests.length;
  const totalTransferBytes = requests.reduce((s, r) => s + (parseInt(r.transferSize) || 0), 0);

  // JS bootup time (rough estimate from long tasks)
  const totalLongTasks = metrics.longTasks.length;
  const totalBlockingTime = Math.round(metrics.tbt);

  const result = {
    name: page.name,
    url: url,
    httpStatus: response ? response.status() : null,
    // Core Web Vitals (target: LCP<2.5s, INP<200ms, CLS<0.1)
    lcp: metrics.lcp ? metrics.lcp / 1000 : null,
    inp: metrics.inp ? metrics.inp : null,
    cls: metrics.cls,
    // Other metrics
    ttfb: metrics.ttfb ? metrics.ttfb / 1000 : null,
    fcp: metrics.fcp ? metrics.fcp / 1000 : null,
    tbt: totalBlockingTime,
    // Load times
    domContentLoaded: metrics.domContentLoaded ? metrics.domContentLoaded / 1000 : null,
    loadEventEnd: metrics.loadEventEnd ? metrics.loadEventEnd / 1000 : null,
    domInteractive: metrics.domInteractive ? metrics.domInteractive / 1000 : null,
    // Resources
    totalResources,
    totalTransferKB: Math.round(totalTransferBytes / 1024),
    htmlSizeKB: Math.round(htmlBytes / 1024),
    domSize,
    // Long tasks
    totalLongTasks,
    longTasksList: metrics.longTasks,
    // Heap
    jsHeapUsedMB: heapMetrics ? Math.round(heapMetrics.usedSize / 1024 / 1024) : null,
    // Navigation
    navTimeMs: navEndMs - startMs,
  };

  console.log(`  HTTP ${result.httpStatus} | TTFB=${result.ttfb !== null ? result.ttfb + 's' : 'null'} | FCP=${result.fcp !== null ? result.fcp + 's' : 'null'} | LCP=${result.lcp !== null ? result.lcp + 's' : 'null'}`);
  console.log(`  CLS=${result.cls} | TBT=${result.tbt}ms | INP=${result.inp}ms | LongTasks=${result.totalLongTasks}`);
  console.log(`  DOM=${result.domSize} | HTML=${result.htmlSizeKB}KB | Transfer=${result.totalTransferKB}KB | Resources=${result.totalResources} | Heap=${result.jsHeapUsedMB}MB`);

  await p.close();
  return result;
}

async function run() {
  // 开源发布改造（2026-10-25）：不再硬编码本机 npm 全局路径，改为动态探测
  const { resolvePuppeteerCore, resolveChromePath, missingDependency } = require('./lh-resolve');
  const PUPPETEER_DIR = resolvePuppeteerCore();
  if (!PUPPETEER_DIR) {
    missingDependency('puppeteer-core', [
      '全局安装：npm install -g puppeteer-core',
      '或设置 PUPPETEER_CORE 环境变量',
    ]);
  }
  const puppeteer = require(PUPPETEER_DIR);

  const chrome = await puppeteer.launch({
    executablePath: resolveChromePath() || undefined,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--remote-debugging-port=9223'],
  });

  const allResults = [];
  try {
    for (const page of pages) {
      const result = await measurePage(chrome, page);
      if (result) allResults.push(result);
    }
  } finally {
    await chrome.close();
  }

  // Save full JSON
  fs.writeFileSync(path.join(OUT_DIR, 'perf-metrics.json'), JSON.stringify(allResults, null, 2));
  console.log('\n=== Measurement complete ===');
  console.log('Results saved to perf-metrics.json');
}

run().catch(e => {
  console.error('Fatal:', e);
  process.exit(1);
});
