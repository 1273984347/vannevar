/**
 * lh-resolve.js — 解析 Lighthouse / puppeteer-core / Chrome 的本地路径
 *
 * 开源发布改造（2026-10-25）：原脚本把开发者本机的 npm 全局安装路径与
 * Chrome 安装路径写死在代码里，克隆仓库后必然失效（ENOENT）。
 * 这里改为逐级探测，任一环节都可由环境变量覆盖：
 *
 *   LIGHTHOUSE_CLI     → lighthouse CLI 入口（cli/index.js）
 *   LIGHTHOUSE_HOME    → lighthouse 包目录（其下有 cli/ 与 node_modules/）
 *   PUPPETEER_CORE     → puppeteer-core 包目录
 *   CHROME_PATH        → Chrome / Chromium 可执行文件
 *
 * 探测顺序：环境变量 → `npm root -g` → 本地 node_modules → 常见安装位置。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

/** 取得全局 node_modules 根目录；失败返回 null。 */
function globalNodeModules() {
  if (process.env.NODE_PATH) {
    const first = process.env.NODE_PATH.split(path.delimiter)[0];
    if (first && fs.existsSync(first)) return first;
  }
  try {
    const root = execSync('npm root -g', { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    if (root && fs.existsSync(root)) return root;
  } catch (e) {
    // npm 不在 PATH —— 继续走本地与常见位置探测
  }
  return null;
}

/** 候选路径列表（去重、保持顺序）。 */
function candidates(rel) {
  const list = [];
  const local = path.resolve(__dirname, '..', '..', '..', 'node_modules');
  const globalRoot = globalNodeModules();
  if (globalRoot) list.push(path.join(globalRoot, rel));
  list.push(path.join(local, rel));
  return list;
}

/** 在候选列表中找第一个存在的路径。 */
function firstExisting(paths) {
  for (const p of paths) {
    try {
      if (fs.existsSync(p)) return p;
    } catch (e) { /* 忽略不可访问项 */ }
  }
  return null;
}

/** lighthouse CLI 入口（cli/index.js）绝对路径；找不到返回 null。 */
function resolveLighthouseCli() {
  if (process.env.LIGHTHOUSE_CLI && fs.existsSync(process.env.LIGHTHOUSE_CLI)) {
    return process.env.LIGHTHOUSE_CLI;
  }
  const rels = [];
  if (process.env.LIGHTHOUSE_HOME) {
    rels.push(path.join(process.env.LIGHTHOUSE_HOME, 'cli', 'index.js'));
  }
  rels.push(path.join('lighthouse', 'cli', 'index.js'));
  const found = firstExisting(rels.map((r) => (path.isAbsolute(r) ? r : firstExisting(candidates(r)) || r)));
  if (found) return found;
  for (const r of rels) {
    if (!path.isAbsolute(r)) {
      const hit = firstExisting(candidates(r));
      if (hit) return hit;
    }
  }
  return null;
}

/** lighthouse 包目录绝对路径；找不到返回 null。 */
function resolveLighthouseHome() {
  if (process.env.LIGHTHOUSE_HOME && fs.existsSync(process.env.LIGHTHOUSE_HOME)) {
    return process.env.LIGHTHOUSE_HOME;
  }
  return firstExisting(candidates(path.join('lighthouse')));
}

/** puppeteer-core 包目录绝对路径；找不到返回 null。 */
function resolvePuppeteerCore() {
  if (process.env.PUPPETEER_CORE && fs.existsSync(process.env.PUPPETEER_CORE)) {
    return process.env.PUPPETEER_CORE;
  }
  const home = resolveLighthouseHome();
  if (home) {
    const nested = path.join(home, 'node_modules', 'puppeteer-core');
    if (fs.existsSync(nested)) return nested;
  }
  return firstExisting(candidates('puppeteer-core'));
}

/** Chrome / Chromium 可执行文件绝对路径；找不到返回 null。 */
function resolveChromePath() {
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) {
    return process.env.CHROME_PATH;
  }
  const isWin = process.platform === 'win32';
  const guesses = isWin
    ? [
        'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
        path.join(process.env.LOCALAPPDATA || '', 'Google', 'Chrome', 'Application', 'chrome.exe'),
        'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
      ]
    : [
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      ];
  for (const g of guesses) {
    if (g && fs.existsSync(g)) return g;
  }
  return null;
}

/** 找不到依赖时给出可操作的报错信息。 */
function missingDependency(what, hints) {
  console.error(
    `\n[${what}] 未能自动解析到依赖。\n` +
    hints.map((h) => `  - ${h}`).join('\n') +
    `\n\n提示：先全局安装 lighthouse 与 puppeteer-core，或用环境变量指定路径。\n`
  );
  process.exit(1);
}

module.exports = {
  globalNodeModules,
  resolveLighthouseCli,
  resolveLighthouseHome,
  resolvePuppeteerCore,
  resolveChromePath,
  missingDependency,
};
