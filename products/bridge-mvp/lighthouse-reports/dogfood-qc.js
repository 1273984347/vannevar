// Phase 6.5 自动化测试：模拟用户完整 demo 路径 QA
// 覆盖：/demo 加载 → /admin/stats 仪表盘 → /demo/story 故事模式 → /admin/projects/1 项目详情 → AI 助手 + Context Cards
// Phase 6.4 扩展：多角色视角切换（admin / owner / volunteer）
//
// 使用说明：
//   1. 启动 Bridge 服务：cd products/bridge-mvp && python -m uvicorn main:app --port 8000
//   2. 确保 demo 数据已 seed（python scripts/seed_demo_data.py）
//   3. 安装依赖：npm install playwright && npx playwright install chromium
//   4. 运行：BRIDGE_ADMIN_PASS=<your-pass> node lighthouse-reports/dogfood-qc.js
//   5. 输出：控制台 PASS/FAIL 清单 + dogfood_shots/*.png 截图
//
// 环境变量：
//   BRIDGE_ADMIN_PASS — admin 密码（必填，不再支持硬编码默认值）
//
// 退出码：0 = 全部 PASS，1 = 任一 FAIL，2 = 配置缺失
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

// 安全铁律：密码必须显式注入,禁止任何默认值兜底,避免源码仓库泄露真实凭据
const ADMIN_PASS = process.env.BRIDGE_ADMIN_PASS;
if (!ADMIN_PASS) {
  console.error('[FATAL] BRIDGE_ADMIN_PASS 环境变量未设置。');
  console.error('        用法: BRIDGE_ADMIN_PASS=<your-pass> node lighthouse-reports/dogfood-qc.js');
  console.error('        密码可从启动 Bridge 服务的 stderr 输出获取,或用 BRIDGE_ADMIN_PASS 环境变量启动 Bridge。');
  process.exit(2);
}
const AUTH = 'Basic ' + Buffer.from(`admin:${ADMIN_PASS}`).toString('base64');
const BASE = 'http://localhost:8000';
const OUT_DIR = path.join(__dirname, '..', 'dogfood_shots');

const results = [];
function log(name, status, detail = '') {
  const line = `${status === 'PASS' ? '✓' : status === 'FAIL' ? '✗' : '○'} ${name}${detail ? ' — ' + detail : ''}`;
  console.log(line);
  results.push({ name, status, detail });
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    extraHTTPHeaders: { 'Authorization': AUTH },
  });

  try {
    // ============================================================
    // 1. /demo 入口：检查页面 + 数据状态
    // ============================================================
    console.log('\n=== 1. /demo demo 演示 入口 ===');
    const p1 = await context.newPage();
    const r1 = await p1.goto(`${BASE}/demo`, { waitUntil: 'networkidle', timeout: 15000 });
    if (r1 && r1.status() === 200) {
      log('GET /demo', 'PASS', `status 200`);
    } else {
      log('GET /demo', 'FAIL', `status ${r1 ? r1.status() : 'N/A'}`);
    }
    await p1.waitForTimeout(1500);

    const demoTitle = await p1.title();
    log('demo page title', demoTitle.includes('演示数据管理') ? 'PASS' : 'FAIL', demoTitle);

    const demoSeeded = await p1.locator('.demo-status').first().textContent().catch(() => '');
    log('demo seeded status', demoSeeded.includes('已就绪') ? 'PASS' : 'FAIL', demoSeeded.trim().slice(0, 60));

    const counts = await p1.evaluate(() => {
      const items = document.querySelectorAll('.demo-count-item');
      const text = Array.from(items).map(i => i.textContent.replace(/\s+/g, ' ').trim().slice(0, 50));
      return text;
    });
    log('demo counts visible', counts.length >= 8 ? 'PASS' : 'FAIL', `${counts.length} count items`);

    // 验证 CTA：故事模式 + 查看仪表盘
    const storyLink = await p1.locator('a[href="/demo/story"]').first().isVisible().catch(() => false);
    log('story mode CTA visible', storyLink ? 'PASS' : 'FAIL');

    const statsLink = await p1.locator('a[href="/admin/stats"]').first().isVisible().catch(() => false);
    log('view dashboard CTA visible', statsLink ? 'PASS' : 'FAIL');

    await p1.screenshot({ path: path.join(OUT_DIR, '31_demo_qa.png'), fullPage: false });
    await p1.close();

    // ============================================================
    // 2. /admin/stats 仪表盘：9 KPI + 多图表
    // ============================================================
    console.log('\n=== 2. /admin/stats 统计仪表盘 ===');
    const p2 = await context.newPage();
    const r2 = await p2.goto(`${BASE}/admin/stats`, { waitUntil: 'networkidle', timeout: 20000 });
    if (r2 && r2.status() === 200) {
      log('GET /admin/stats', 'PASS', 'status 200');
    } else {
      log('GET /admin/stats', 'FAIL', `status ${r2 ? r2.status() : 'N/A'}`);
    }
    await p2.waitForTimeout(2500);

    // 9 KPI 卡片
    const kpiCount = await p2.locator('.stat-kpi').count();
    log('9 KPI cards rendered', kpiCount === 9 ? 'PASS' : 'FAIL', `actual ${kpiCount}`);

    // 3 个 chart-group-title（Trend / Distribution / Density — 首字母大写）
    const groupTags = await p2.locator('.chart-group-title .group-tag').allTextContents();
    const hasTrend = groupTags.some(t => t.trim().toLowerCase() === 'trend');
    const hasDist = groupTags.some(t => t.trim().toLowerCase() === 'distribution');
    const hasDensity = groupTags.some(t => t.trim().toLowerCase() === 'density');
    log('Trend/Distribution/Density groups', (hasTrend && hasDist && hasDensity) ? 'PASS' : 'FAIL',
      `${hasTrend ? '✓' : '✗'}Trend ${hasDist ? '✓' : '✗'}Dist ${hasDensity ? '✓' : '✗'}Density`);

    // 趋势图 SVG
    const trendSvg = await p2.locator('.trend-chart svg').first().isVisible().catch(() => false);
    log('trend chart SVG visible', trendSvg ? 'PASS' : 'FAIL');

    // 热力图 30 天
    const heatmapCells = await p2.locator('.heatmap-cell').count();
    log('heatmap 30 days rendered', heatmapCells >= 30 ? 'PASS' : 'FAIL', `${heatmapCells} cells`);

    // 验证 5 级色阶覆盖
    const heatLevels = await p2.evaluate(() => {
      const cells = document.querySelectorAll('.heatmap-cell');
      const levels = new Set();
      cells.forEach(c => {
        for (const l of ['h-0', 'h-1', 'h-2', 'h-3', 'h-4']) {
          if (c.classList.contains(l)) levels.add(l);
        }
      });
      return Array.from(levels);
    });
    log('heatmap 5 levels (h-0~h-4)', heatLevels.length === 5 ? 'PASS' : 'FAIL', `levels: ${heatLevels.join(',')}`);

    // 桑基图（Phase 6.K1）
    const sankeyVisible = await p2.locator('.sankey-chart svg').first().isVisible().catch(() => false);
    log('sankey chart (Phase 6.K1) visible', sankeyVisible ? 'PASS' : 'FAIL');

    // 雷达图（Phase 6.K2）— 选择器 .radar-chart-wrap
    const radarVisible = await p2.locator('.radar-chart-wrap svg, .radar-chart-wrap').first().isVisible().catch(() => false);
    log('radar chart (Phase 6.K2) visible', radarVisible ? 'PASS' : 'FAIL');

    // 网络图（Phase 6.K3）
    const networkVisible = await p2.locator('.network-chart-wrap svg').first().isVisible().catch(() => false);
    log('network chart (Phase 6.K3) visible', networkVisible ? 'PASS' : 'FAIL');

    // 移动端断点检查（stats.css 768px）
    const statsCssLoaded = await p2.evaluate(() => {
      const sheets = Array.from(document.styleSheets);
      return sheets.some(s => s.href && s.href.includes('stats.css'));
    });
    log('stats.css loaded', statsCssLoaded ? 'PASS' : 'FAIL');

    await p2.screenshot({ path: path.join(OUT_DIR, '32_stats_qa.png'), fullPage: true });
    await p2.close();

    // ============================================================
    // 3. /demo/story 故事模式（Phase 6.L）
    // ============================================================
    console.log('\n=== 3. /demo/story 故事模式 ===');
    const p3 = await context.newPage();
    const r3 = await p3.goto(`${BASE}/demo/story`, { waitUntil: 'networkidle', timeout: 15000 });
    if (r3 && r3.status() === 200) {
      log('GET /demo/story', 'PASS', 'status 200');
    } else {
      log('GET /demo/story', 'FAIL', `status ${r3 ? r3.status() : 'N/A'}`);
    }
    await p3.waitForTimeout(2000);

    // 故事章节 5 个 — 选择器 .chapter-card
    const chapters = await p3.locator('.chapter-card').count();
    log('story chapters visible', chapters >= 1 ? 'PASS' : 'FAIL', `${chapters} chapters`);

    // 故事控制按钮（播放/暂停/上一步/下一步）
    const ctrlBtns = await p3.locator('.ctrl-btn, .ctrl-btn--primary').count();
    log('story control buttons', ctrlBtns >= 1 ? 'PASS' : 'FAIL', `${ctrlBtns} buttons`);

    // KPI 卡片
    const storyKpi = await p3.locator('.story-kpi, .story-kpi-card').count();
    log('story KPI cards', storyKpi >= 1 ? 'PASS' : 'FAIL', `${storyKpi} KPIs`);

    await p3.screenshot({ path: path.join(OUT_DIR, '33_story_qa.png'), fullPage: false });
    await p3.close();

    // ============================================================
    // 4. /admin/projects/1 项目详情：6 模块 + AI 助手 + Context Cards
    // ============================================================
    console.log('\n=== 4. /admin/projects/1 项目详情 ===');
    const p4 = await context.newPage();
    const r4 = await p4.goto(`${BASE}/admin/projects/1`, { waitUntil: 'networkidle', timeout: 15000 });
    if (r4 && r4.status() === 200) {
      log('GET /admin/projects/1', 'PASS', 'status 200');
    } else {
      log('GET /admin/projects/1', 'FAIL', `status ${r4 ? r4.status() : 'N/A'}`);
    }
    await p4.waitForTimeout(2500);

    // 项目标题
    const projTitle = await p4.locator('#projectTitle').first().textContent().catch(() => '');
    log('project title rendered', projTitle.trim().length > 0 ? 'PASS' : 'FAIL', `"${projTitle.trim().slice(0, 30)}"`);

    // 6 大模块 H2
    const h2s = await p4.locator('h2').allTextContents();
    const requiredModules = ['使用指南', '任务看板', '接力交接', '协作留言', '关联上下文', 'AI 助手'];
    const moduleFound = requiredModules.map(m => h2s.some(h => h.includes(m)));
    const allFound = moduleFound.every(v => v);
    log('6 modules H2 (使用指南/任务看板/接力交接/协作留言/关联上下文/AI 助手)',
      allFound ? 'PASS' : 'FAIL',
      requiredModules.map((m, i) => `${moduleFound[i] ? '✓' : '✗'}${m}`).join(' '));

    // 任务看板 3 列
    const kanbanCols = await p4.locator('.kanban-column, .kanban > div').count();
    log('kanban 3 columns', kanbanCols >= 3 ? 'PASS' : 'FAIL', `${kanbanCols} cols`);

    // 任务卡片
    const taskCards = await p4.locator('.task-card, .kanban-card').count();
    log('task cards rendered', taskCards >= 1 ? 'PASS' : 'FAIL', `${taskCards} cards`);

    // AI 助手聊天区
    const aiChatVisible = await p4.locator('#aiAssistantCard, .ai-chat-wrap').first().isVisible().catch(() => false);
    log('AI assistant chat visible', aiChatVisible ? 'PASS' : 'FAIL');

    // Context Cards
    const contextCards = await p4.locator('.ctx-card, .context-card').count();
    log('context cards rendered', contextCards >= 1 ? 'PASS' : 'FAIL', `${contextCards} cards`);

    // Focus 快照 + Muse 灵感
    const focusSnap = await p4.locator('text=Focus 快照').first().isVisible().catch(() => false);
    const museInsp = await p4.locator('text=Muse 灵感').first().isVisible().catch(() => false);
    log('Focus snapshot + Muse inspiration sections',
      (focusSnap && museInsp) ? 'PASS' : 'FAIL',
      `Focus:${focusSnap ? '✓' : '✗'} Muse:${museInsp ? '✓' : '✗'}`);

    await p4.screenshot({ path: path.join(OUT_DIR, '34_project_detail_qa.png'), fullPage: false });
    await p4.close();

    // ============================================================
    // 5. / 项目列表 admin 首页
    // ============================================================
    console.log('\n=== 5. / admin 首页项目列表 ===');
    const p5 = await context.newPage();
    const r5 = await p5.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 15000 });
    if (r5 && r5.status() === 200) {
      log('GET /', 'PASS', 'status 200');
    } else {
      log('GET /', 'FAIL', `status ${r5 ? r5.status() : 'N/A'}`);
    }
    await p5.waitForTimeout(1500);

    // 项目卡片
    const projCards = await p5.locator('.project-card').count();
    log('project cards rendered', projCards >= 1 ? 'PASS' : 'FAIL', `${projCards} cards`);

    // 状态筛选 tabs
    const statusTabs = await p5.locator('.status-tab').count();
    log('status filter tabs', statusTabs >= 3 ? 'PASS' : 'FAIL', `${statusTabs} tabs`);

    // 新建项目表单
    const createForm = await p5.locator('#createProjectForm').first().isVisible().catch(() => false);
    log('create project form visible', createForm ? 'PASS' : 'FAIL');

    await p5.screenshot({ path: path.join(OUT_DIR, '35_admin_index_qa.png'), fullPage: false });
    await p5.close();

    // ============================================================
    // 6. Phase 6.4 多角色视角切换（admin / owner / volunteer）
    // ============================================================
    console.log('\n=== 6. Phase 6.4 多角色视角切换 ===');
    const p6 = await context.newPage();
    const r6 = await p6.goto(`${BASE}/admin/projects/1`, { waitUntil: 'networkidle', timeout: 15000 });
    if (r6 && r6.status() === 200) {
      log('GET /admin/projects/1 (with view switcher)', 'PASS', 'status 200');
    } else {
      log('GET /admin/projects/1 (with view switcher)', 'FAIL', `status ${r6 ? r6.status() : 'N/A'}`);
    }
    await p6.waitForTimeout(2000);

    // 视角切换器可见 + 3 个按钮
    const switcherVisible = await p6.locator('#viewSwitcher').first().isVisible().catch(() => false);
    log('view switcher visible', switcherVisible ? 'PASS' : 'FAIL');

    const viewBtns = await p6.locator('.view-switch-btn').count();
    log('3 view buttons (admin/owner/volunteer)', viewBtns === 3 ? 'PASS' : 'FAIL', `${viewBtns} buttons`);

    // —— 默认 admin 视角：全模块可见 ——
    const adminActive = await p6.locator('.view-switch-btn[data-view="admin"].is-active').count();
    log('default admin view active', adminActive === 1 ? 'PASS' : 'FAIL');

    const adminGuidesVisible = await p6.locator('.v-guides').first().isVisible().catch(() => false);
    const adminContextVisible = await p6.locator('#contextCard').first().isVisible().catch(() => false);
    const adminAiVisible = await p6.locator('#aiAssistantCard').first().isVisible().catch(() => false);
    const adminCreateTaskVisible = await p6.locator('#createTaskForm').first().isVisible().catch(() => false);
    const adminDeleteBtnVisible = await p6.locator('#deleteProjectBtn').first().isVisible().catch(() => false);
    log('admin view: all modules visible',
      (adminGuidesVisible && adminContextVisible && adminAiVisible && adminCreateTaskVisible && adminDeleteBtnVisible) ? 'PASS' : 'FAIL',
      `guides:${adminGuidesVisible ? '✓' : '✗'} ctx:${adminContextVisible ? '✓' : '✗'} ai:${adminAiVisible ? '✓' : '✗'} createTask:${adminCreateTaskVisible ? '✓' : '✗'} delProj:${adminDeleteBtnVisible ? '✓' : '✗'}`);

    await p6.screenshot({ path: path.join(OUT_DIR, '36_view_admin.png'), fullPage: false });

    // —— owner 视角：隐藏删除项目按钮 ——
    await p6.locator('.view-switch-btn[data-view="owner"]').click();
    await p6.waitForTimeout(500);
    const ownerActive = await p6.locator('.view-switch-btn[data-view="owner"].is-active').count();
    log('switch to owner view', ownerActive === 1 ? 'PASS' : 'FAIL');

    const ownerDataView = await p6.locator('#main-content').getAttribute('data-view');
    log('owner view: data-view attribute', ownerDataView === 'owner' ? 'PASS' : 'FAIL', `actual="${ownerDataView}"`);

    const ownerDeleteBtnHidden = await p6.locator('#deleteProjectBtn').first().isVisible().catch(() => true);
    log('owner view: delete project button hidden', !ownerDeleteBtnHidden ? 'PASS' : 'FAIL');

    // owner 视角：其他模块仍可见
    const ownerGuidesVisible = await p6.locator('.v-guides').first().isVisible().catch(() => false);
    const ownerContextVisible = await p6.locator('#contextCard').first().isVisible().catch(() => false);
    const ownerAiVisible = await p6.locator('#aiAssistantCard').first().isVisible().catch(() => false);
    log('owner view: other modules still visible',
      (ownerGuidesVisible && ownerContextVisible && ownerAiVisible) ? 'PASS' : 'FAIL',
      `guides:${ownerGuidesVisible ? '✓' : '✗'} ctx:${ownerContextVisible ? '✓' : '✗'} ai:${ownerAiVisible ? '✓' : '✗'}`);

    // owner 视角：URL query 同步
    const ownerUrl = p6.url();
    log('owner view: URL query synced', ownerUrl.includes('view=owner') ? 'PASS' : 'FAIL', ownerUrl.slice(-40));

    await p6.screenshot({ path: path.join(OUT_DIR, '37_view_owner.png'), fullPage: false });

    // —— volunteer 视角：只读，隐藏 admin/owner 专属模块 ——
    await p6.locator('.view-switch-btn[data-view="volunteer"]').click();
    await p6.waitForTimeout(500);
    const volunteerActive = await p6.locator('.view-switch-btn[data-view="volunteer"].is-active').count();
    log('switch to volunteer view', volunteerActive === 1 ? 'PASS' : 'FAIL');

    const volDataView = await p6.locator('#main-content').getAttribute('data-view');
    log('volunteer view: data-view attribute', volDataView === 'volunteer' ? 'PASS' : 'FAIL', `actual="${volDataView}"`);

    // volunteer 视角：v-guides / contextCard / aiAssistantCard / createTaskForm 全部隐藏
    const volGuidesHidden = await p6.locator('.v-guides').first().isVisible().catch(() => true);
    const volContextHidden = await p6.locator('#contextCard').first().isVisible().catch(() => true);
    const volAiHidden = await p6.locator('#aiAssistantCard').first().isVisible().catch(() => true);
    const volCreateTaskHidden = await p6.locator('#createTaskForm').first().isVisible().catch(() => true);
    log('volunteer view: admin-only modules hidden',
      (!volGuidesHidden && !volContextHidden && !volAiHidden && !volCreateTaskHidden) ? 'PASS' : 'FAIL',
      `guides:${!volGuidesHidden ? '✓' : '✗'} ctx:${!volContextHidden ? '✓' : '✗'} ai:${!volAiHidden ? '✓' : '✗'} createTask:${!volCreateTaskHidden ? '✓' : '✗'}`);

    // volunteer 视角：任务看板仍可见（只读）
    const volTaskListVisible = await p6.locator('#taskList').first().isVisible().catch(() => false);
    log('volunteer view: task list visible (read-only)', volTaskListVisible ? 'PASS' : 'FAIL');

    // volunteer 视角：留言区仍可见（志愿者可发留言）
    const volMessageFormVisible = await p6.locator('#createMessageForm').first().isVisible().catch(() => false);
    log('volunteer view: message form visible (allowed)', volMessageFormVisible ? 'PASS' : 'FAIL');

    // volunteer 视角：任务状态切换 disabled（pointer-events: none）
    const volStatusSelectDisabled = await p6.evaluate(() => {
      const sel = document.querySelector('.status-select');
      if (!sel) return false;
      const style = window.getComputedStyle(sel);
      return style.pointerEvents === 'none';
    });
    log('volunteer view: status select pointer-events disabled', volStatusSelectDisabled ? 'PASS' : 'FAIL');

    // volunteer 视角：URL query 同步
    const volUrl = p6.url();
    log('volunteer view: URL query synced', volUrl.includes('view=volunteer') ? 'PASS' : 'FAIL', volUrl.slice(-40));

    // —— P1-4 最低限度测试：volunteer 视角下关键按钮应隐藏（防止 CSS 选择器漂移回归） ——
    // 1. autoGenerateHandoffBtn 应隐藏（P0-1 修复：原 #aiHandoffBtn 死选择器）
    const volAutoGenHidden = await p6.locator('#autoGenerateHandoffBtn').first().isVisible().catch(() => true);
    log('volunteer view: #autoGenerateHandoffBtn hidden (P0-1 regression guard)', !volAutoGenHidden ? 'PASS' : 'FAIL');

    // 2. empty-state anchor 应隐藏（P2-5 修复：避免指向已隐藏按钮导致死链）
    const volEmptyCtaHidden = await p6.locator('.v-empty-cta').first().isVisible().catch(() => true);
    log('volunteer view: .v-empty-cta hidden (P2-5 regression guard)', !volEmptyCtaHidden ? 'PASS' : 'FAIL');

    // 3. delete-message-btn 在 .bubble 内应隐藏（P0-2 修复：原 .message-item 死选择器）
    const volDelMsgBtnVisible = await p6.locator('.bubble .delete-message-btn').first().isVisible().catch(() => false);
    const volDelMsgBtnCount = await p6.locator('.bubble .delete-message-btn').count();
    log('volunteer view: .bubble .delete-message-btn hidden (P0-2 regression guard)',
      volDelMsgBtnCount === 0 || !volDelMsgBtnVisible ? 'PASS' : 'FAIL',
      `count=${volDelMsgBtnCount}`);

    await p6.screenshot({ path: path.join(OUT_DIR, '38_view_volunteer.png'), fullPage: false });

    // —— URL query 恢复：刷新页面后应保持 volunteer 视角 ——
    await p6.reload({ waitUntil: 'networkidle' });
    await p6.waitForTimeout(1500);
    const restoredView = await p6.locator('#main-content').getAttribute('data-view');
    log('URL query restore: volunteer view after reload', restoredView === 'volunteer' ? 'PASS' : 'FAIL', `actual="${restoredView}"`);

    await p6.screenshot({ path: path.join(OUT_DIR, '39_view_restored.png'), fullPage: false });
    await p6.close();

  } catch (e) {
    log('QA suite', 'FAIL', `exception: ${e.message}`);
  } finally {
    await browser.close();
  }

  // ============================================================
  // 汇总报告
  // ============================================================
  console.log('\n=== QA Summary ===');
  const pass = results.filter(r => r.status === 'PASS').length;
  const fail = results.filter(r => r.status === 'FAIL').length;
  console.log(`Total: ${results.length}, PASS: ${pass}, FAIL: ${fail}`);
  if (fail > 0) {
    console.log('\nFailed checks:');
    results.filter(r => r.status === 'FAIL').forEach(r => {
      console.log(`  ✗ ${r.name} — ${r.detail}`);
    });
  }
  process.exit(fail > 0 ? 1 : 0);
})();
