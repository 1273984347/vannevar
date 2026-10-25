// Extract Lighthouse scores and Core Web Vitals from all reports
//
// Phase 6.6（2026-07-21）改造：
//   - reports 数组改自动扫描：glob 当前目录下所有 *.report.json，不再硬编码
//   - 新增页面无需手动同步此文件
// DRL R2 修复（2026-07-24）：P2 无报告时友好提示 + JSON.parse try/catch 跳过坏文件
const fs = require('fs');
const path = require('path');

const OUT_DIR = __dirname;

// Phase 6.6: 自动扫描当前目录下所有 *.report.json（排除 *-mobile-*.report.json 由 run-lighthouse-mobile.js 单独处理，
// 但若 mobile 报告存在也一并提取——只是把名字保留完整以区分）
const reports = fs.readdirSync(OUT_DIR)
  .filter(f => f.endsWith('.report.json'))
  .map(f => f.replace(/\.report\.json$/, ''))
  .sort();

// P2 修复（DRL R2）：无报告时友好提示，避免陌生人无法区分"无报告"与"报告全部正常"
if (reports.length === 0) {
  console.error('[WARN] 当前目录未找到任何 *.report.json 文件。');
  console.error('        用法: 先运行 run-lighthouse.js 生成报告，再运行本脚本提取分数。');
  process.exit(1);
}

const results = [];

for (const name of reports) {
  const file = path.join(OUT_DIR, `${name}.report.json`);
  if (!fs.existsSync(file)) {
    console.log(`${name}: NO REPORT`);
    continue;
  }
  // P2 修复（DRL R2）：JSON.parse 加 try/catch，坏文件跳过而非终止整个脚本
  let j;
  try {
    j = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    console.error(`${name}: JSON 解析失败，跳过 — ${e.message}`);
    results.push({ name, error: `JSON parse error: ${e.message}` });
    continue;
  }
  if (j.runtimeError && j.runtimeError.code !== 'NO_ERROR') {
    console.log(`${name}: runtimeError ${j.runtimeError.code}`);
    results.push({ name, error: j.runtimeError.message });
    continue;
  }
  const cats = j.categories || {};
  const audits = j.audits || {};
  const score = (c) => (cats[c] && typeof cats[c].score === 'number') ? Math.round(cats[c].score * 100) : null;
  const dv = (a) => audits[a] ? audits[a].displayValue : null;
  const nv = (a) => audits[a] && typeof audits[a].numericValue === 'number' ? audits[a].numericValue : null;

  const perf = score('performance');
  const a11y = score('accessibility');
  const bp = score('best-practices');
  const seo = score('seo');

  const lcp = dv('largest-contentful-paint');
  const lcpMs = nv('largest-contentful-paint');
  const fcp = dv('first-contentful-paint');
  const fcpMs = nv('first-contentful-paint');
  const tbt = dv('total-blocking-time');
  const tbtMs = nv('total-blocking-time');
  const cls = dv('cumulative-layout-shift');
  const clsNum = nv('cumulative-layout-shift');
  const inp = dv('interaction-to-next-paint');
  const inpMs = nv('interaction-to-next-paint');
  const ttfb = dv('server-response-time');
  const ttfbMs = nv('server-response-time');
  const si = dv('speed-index');
  const siMs = nv('speed-index');
  const fmp = dv('first-meaningful-paint');
  const tti = dv('interactive');
  const ttiMs = nv('interactive');

  // Network / DOM audits
  const domSize = dv('dom-size');
  const domSizeNum = nv('dom-size');
  const totalBytes = dv('total-byte-weight');
  const totalBytesNum = nv('total-byte-weight');
  const numReq = dv('network-requests');
  const numReqNum = audits['network-requests'] && audits['network-requests'].details ? audits['network-requests'].details.items.length : null;
  const unusedCss = dv('unused-css-rules');
  const unusedJs = dv('unused-javascript');
  const renderBlocks = dv('render-blocking-resources');
  const renderBlocksNum = nv('render-blocking-resources');
  const usesOptImages = dv('uses-optimized-images');
  const modernImgFmt = dv('modern-image-formats');
  const efficientBp = dv('efficient-animated-content');
  const longTasks = dv('long-tasks');
  const longTasksNum = nv('long-tasks');
  const mainThread = dv('mainthread-work-breakdown');
  const mainThreadNum = nv('mainthread-work-breakdown');
  const bootupTime = dv('bootup-time');
  const bootupTimeNum = nv('bootup-time');

  // Top failing audits (score < 1)
  const failing = [];
  for (const catKey of Object.keys(cats)) {
    const cat = cats[catKey];
    if (!cat.auditRefs) continue;
    for (const ref of cat.auditRefs) {
      const audit = audits[ref.id];
      if (!audit) continue;
      if (audit.score !== null && audit.score < 0.9 && audit.score !== 1) {
        failing.push({
          id: ref.id,
          title: audit.title,
          score: audit.score,
          displayValue: audit.displayValue,
          category: catKey,
        });
      }
    }
  }
  failing.sort((a, b) => (a.score || 0) - (b.score || 0));
  const topFailing = failing.slice(0, 12);

  console.log(`\n=== ${name} ===`);
  console.log(`  Scores: Perf=${perf} A11y=${a11y} BP=${bp} SEO=${seo}`);
  console.log(`  CWV:    LCP=${lcp} FCP=${fcp} TBT=${tbt} CLS=${cls} INP=${inp} TTFB=${ttfb}`);
  console.log(`  Other:  SI=${si} TTI=${tti} FMP=${fmp}`);
  console.log(`  Network: reqs=${numReqNum} totalBytes=${totalBytes} DOM=${domSize}`);
  console.log(`  Long tasks=${longTasks} Main thread=${mainThread} JS bootup=${bootupTime}`);
  console.log(`  Render-block=${renderBlocks} UnusedCSS=${unusedCss} UnusedJS=${unusedJs}`);
  console.log(`  Top failing audits:`);
  for (const f of topFailing) {
    console.log(`    [${f.score.toFixed(2)}] ${f.id}: ${f.title} (${f.displayValue || '-'}) [${f.category}]`);
  }

  results.push({
    name,
    scores: { perf, a11y, bp, seo },
    cwv: {
      lcp: { display: lcp, ms: lcpMs },
      fcp: { display: fcp, ms: fcpMs },
      tbt: { display: tbt, ms: tbtMs },
      cls: { display: cls, num: clsNum },
      inp: { display: inp, ms: inpMs },
      ttfb: { display: ttfb, ms: ttfbMs },
      si: { display: si, ms: siMs },
      tti: { display: tti, ms: ttiMs },
    },
    network: {
      requests: numReqNum,
      totalBytes: totalBytesNum,
      domSize: domSizeNum,
      renderBlockMs: renderBlocksNum,
      longTasks: longTasksNum,
      mainThreadMs: mainThreadNum,
      bootupMs: bootupTimeNum,
    },
    topFailing,
  });
}

fs.writeFileSync(path.join(OUT_DIR, 'lighthouse-summary.json'), JSON.stringify(results, null, 2));
console.log('\n=== Summary saved to lighthouse-summary.json ===');
