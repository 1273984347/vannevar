// dashboard.js — Muse 灵感博物馆

import { extractDomain } from '../core/resume.js';
import { showToast } from '../shared/toast.js';
import { escapeHtml } from '../shared/escape.js';
import { createCard, serializeCard, deserializeCard, FILE_EXTENSION } from '../shared/vannevar-context-card.js';
import { moderateText } from '../shared/moderation.js';
import { groupByMusic } from '../core/media-group.js';

const timelineEl = document.getElementById('timeline');
const emptyStateEl = document.getElementById('emptyState');
const searchInput = document.getElementById('searchInput');
const refreshBtn = document.getElementById('refreshBtn');
const exportMarkdownBtn = document.getElementById('exportMarkdownBtn');
const importCardBtn = document.getElementById('importCardBtn');
const importCardInput = document.getElementById('importCardInput');
const moodFiltersEl = document.getElementById('moodFilters');
const sidebarEl = document.getElementById('sidebar');
const navToggleEl = document.getElementById('navToggle');
const resumeLoadingEl = document.getElementById('resumeLoading');
const resumeContentEl = document.getElementById('resumeContent');
const pinnedPanelEl = document.getElementById('pinnedPanel');
const pinnedListEl = document.getElementById('pinnedList');
const pinnedCountEl = document.getElementById('pinnedCount');

// P1: 防御页面元素缺失导致初始化崩溃
if (!timelineEl || !emptyStateEl || !searchInput || !moodFiltersEl) {
  console.error('[Muse] dashboard 必要元素缺失，无法初始化');
  throw new Error('dashboard 必要元素缺失');
}

// 日期分组配置：今天 / 昨天 / 本周 / 更早
const DATE_GROUPS = [
  { key: 'today', label: '今天' },
  { key: 'yesterday', label: '昨天' },
  { key: 'thisWeek', label: '本周' },
  { key: 'earlier', label: '更早' }
];

// 图钉图标 SVG（已置顶 / 未置顶状态共用，靠 .is-pinned 类区分颜色）
const PIN_ICON_SVG = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M9 4h6l-1 5 4 3v2h-5v6l-1 2-1-2v-6H6v-2l4-3-1-5z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';

// 编辑标题按钮的铅笔图标
const EDIT_ICON_SVG = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M4 20h4L18.5 9.5a2.12 2.12 0 0 0-3-3L5 17v3z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M13.5 6.5l3 3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';

// 演示模式示例灵感（不写入数据库，仅前端内存渲染）
// 用户首次打开 Dashboard 即可看到 AI 推荐氛围标签的效果
const DEMO_INSPIRATIONS = [
  { id: 'demo-1', title: '关于留白与克制', url: 'https://example.com/essay', note: '留白不是空，是未言之意', mood: '克制', selectedText: '留白比填满更难，因为它要求你相信沉默的力量。', createdAt: Date.now() - 3600000, pinned: false, isDemo: true, mediaType: 'music', mediaArtist: '周深', mediaAlbum: '深的记忆', mediaArtworkUrl: '' },
  { id: 'demo-2', title: '夜晚的思考节奏', url: 'https://example.com/night', note: '夜间写作的生理基础', mood: '夜晚', selectedText: '深夜的注意力不是更集中，而是过滤更少——所以创意涌现，但判断力下降。', createdAt: Date.now() - 86400000, pinned: false, isDemo: true, mediaType: 'music', mediaArtist: '周杰伦', mediaAlbum: '十一月的萧邦', mediaArtworkUrl: '' },
  { id: 'demo-3', title: '东方美学的底色', url: 'https://example.com/aesthetic', note: '侘寂与留白的关系', mood: '专注', selectedText: '侘寂不是残缺美，是对时间流逝的诚实——器物的裂痕是使用史，不是瑕疵。', createdAt: Date.now() - 172800000, pinned: false, isDemo: true, mediaType: 'music', mediaArtist: 'S.E.N.S.', mediaAlbum: '故宫的记忆', mediaArtworkUrl: '' }
];

let allInspirations = [];
// 搜索结果由后端 SEARCH_INSPIRATIONS 返回；为 null 表示当前非搜索态，使用 allInspirations
let searchResults = null;
let currentFilter = 'all';
let currentKeyword = '';
let currentView = 'time'; // 'time' | 'source' | 'music'
const PAGE_SIZE = 20;
let normalRenderLimit = PAGE_SIZE;
// 演示模式开关（由 loadData() 从 chrome.storage.local 读取，render() 直接消费）
let demoModeEnabled = false;

// 读取演示模式状态（与 core/ai.js 的 STORAGE_KEY_DEMO = 'demoMode' 一致）
async function isDemoMode() {
  try {
    const result = await chrome.storage.local.get('demoMode');
    return result.demoMode === true;
  } catch (err) {
    return false;
  }
}

async function loadData() {
  try {
    // 先读取演示模式，存到模块级变量供 render() 同步消费
    demoModeEnabled = await isDemoMode();
    const res = await chrome.runtime.sendMessage({ action: 'GET_INSPIRATIONS' });
    if (!res || !res.ok) {
      console.error('[Dashboard] 加载失败:', res?.error);
      allInspirations = [];
      render();
      return;
    }
    allInspirations = res.inspirations;
    // 若当前处于搜索态，删除/置顶/编辑后需重新触发后端搜索以刷新结果
    if (searchResults !== null && currentKeyword.trim()) {
      await performSearch(currentKeyword);
      return;
    }
    render();
  } catch (err) {
    console.error('[Dashboard] 加载失败:', err);
    allInspirations = [];
    render();
    showToast('加载灵感失败', 'error');
  }
}

async function loadMoods() {
  try {
    const res = await chrome.runtime.sendMessage({ action: 'GET_MOODS' });
    if (!res || !res.ok) {
      moodFiltersEl.innerHTML = '<div class="mood-error">加载标签失败</div>';
      return;
    }
    moodFiltersEl.innerHTML = '';
    for (const mood of res.moods) {
      const name = typeof mood === 'string' ? mood : mood.name;
      const color = typeof mood === 'string' ? null : mood.color;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'nav-item';
      btn.dataset.filter = name;
      btn.setAttribute('aria-pressed', 'false');
      const left = document.createElement('span');
      left.className = 'nav-mood-left';
      if (color) {
        const dot = document.createElement('span');
        dot.className = 'nav-mood-dot';
        dot.style.background = color;
        dot.setAttribute('aria-hidden', 'true');
        left.appendChild(dot);
      }
      const label = document.createElement('span');
      label.textContent = name;
      left.appendChild(label);
      btn.appendChild(left);
      btn.addEventListener('click', () => setFilter(name));
      moodFiltersEl.appendChild(btn);
    }
  } catch (err) {
    console.error('[Dashboard] 加载氛围标签失败:', err);
    moodFiltersEl.innerHTML = '<div class="mood-error">加载标签失败</div>';
  }
}

// 异步加载灵感回顾摘要（不阻塞主时间线渲染）
async function loadResume() {
  if (!resumeLoadingEl || !resumeContentEl) return;
  resumeLoadingEl.style.display = '';
  resumeContentEl.style.display = 'none';
  try {
    const res = await chrome.runtime.sendMessage({ action: 'GENERATE_RESUME' });
    if (!res || !res.ok || !res.resume) {
      // 静默失败：隐藏整个面板，不影响主流程
      resumeLoadingEl.textContent = '暂无可用的灵感回顾';
      return;
    }
    resumeLoadingEl.style.display = 'none';
    renderResume(res.resume);
    resumeContentEl.style.display = '';
  } catch (err) {
// P2: 静默处理非关键扩展错误
    resumeLoadingEl.textContent = '灵感回顾加载失败';
  }
}

function renderResume(resume) {
  resumeContentEl.innerHTML = '';

  // W079b: AI 增强时给 .resume-panel 容器加 .ai-enhanced，用品牌色左边框 + 浅色背景区分
  const resumePanel = resumeContentEl.closest('.resume-panel');
  if (resumePanel) {
    resumePanel.classList.toggle('ai-enhanced', resume.aiEnhanced === true);
  }

  // AI 增强状态标签
  const badge = document.createElement('div');
  badge.className = 'resume-badge' + (resume.aiEnhanced ? ' resume-badge--ai' : ' resume-badge--rule');
  badge.textContent = resume.aiEnhanced ? '✨ AI 增强' : '规则引擎';
  resumeContentEl.appendChild(badge);
  if (!resume.aiEnhanced) {
    const hint = document.createElement('div');
    hint.className = 'resume-ai-hint';
    hint.textContent = '配置 AI 后可获得更智能的灵感回顾 →';
    hint.setAttribute('role', 'button');
    hint.tabIndex = 0;
    hint.addEventListener('click', () => chrome.runtime.openOptionsPage());
    hint.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); chrome.runtime.openOptionsPage(); }
    });
    resumeContentEl.appendChild(hint);
  }

  // 摘要文本
  if (resume.summary) {
    const summaryDiv = document.createElement('div');
    summaryDiv.className = 'resume-summary';
    summaryDiv.textContent = resume.summary;
    resumeContentEl.appendChild(summaryDiv);
  }

  // 氛围分布 / 热门来源 / 关键词 — 网格布局
  const grid = document.createElement('div');
  grid.className = 'resume-grid';

  // 氛围分布（带进度条）
  if (Array.isArray(resume.moodDistribution) && resume.moodDistribution.length > 0) {
    const block = document.createElement('div');
    const title = document.createElement('div');
    title.className = 'resume-block-title';
    title.textContent = '氛围分布';
    block.appendChild(title);
    const list = document.createElement('div');
    list.className = 'resume-mood-list';
    const maxCount = resume.moodDistribution[0].count || 1;
    for (const m of resume.moodDistribution) {
      const row = document.createElement('div');
      row.className = 'resume-mood-row';
      const name = document.createElement('span');
      name.className = 'resume-mood-name';
      name.textContent = m.mood;
      const bar = document.createElement('span');
      bar.className = 'resume-mood-bar';
      const fill = document.createElement('span');
      fill.className = 'resume-mood-bar-fill';
      fill.style.width = `${Math.round((m.count / maxCount) * 100)}%`;
      bar.appendChild(fill);
      const count = document.createElement('span');
      count.className = 'resume-count';
      count.textContent = String(m.count);
      row.appendChild(name);
      row.appendChild(bar);
      row.appendChild(count);
      list.appendChild(row);
    }
    block.appendChild(list);
    grid.appendChild(block);
  }

  // 热门来源
  if (Array.isArray(resume.topSites) && resume.topSites.length > 0) {
    const block = document.createElement('div');
    const title = document.createElement('div');
    title.className = 'resume-block-title';
    title.textContent = '热门来源';
    block.appendChild(title);
    const list = document.createElement('div');
    list.className = 'resume-sites-list';
    for (const s of resume.topSites) {
      const row = document.createElement('div');
      row.className = 'resume-site-row';
      const name = document.createElement('span');
      name.className = 'resume-site-name';
      name.textContent = s.domain;
      name.setAttribute('title', s.domain);
      const count = document.createElement('span');
      count.className = 'resume-count';
      count.textContent = String(s.count);
      row.appendChild(name);
      row.appendChild(count);
      list.appendChild(row);
    }
    block.appendChild(list);
    grid.appendChild(block);
  }

  // 关键词
  if (Array.isArray(resume.keywords) && resume.keywords.length > 0) {
    const block = document.createElement('div');
    const title = document.createElement('div');
    title.className = 'resume-block-title';
    title.textContent = '关键词';
    block.appendChild(title);
    const kwDiv = document.createElement('div');
    kwDiv.className = 'resume-keywords';
    for (const kw of resume.keywords) {
      const tag = document.createElement('span');
      tag.className = 'resume-keyword-tag';
      tag.textContent = kw;
      kwDiv.appendChild(tag);
    }
    block.appendChild(kwDiv);
    grid.appendChild(block);
  }

  resumeContentEl.appendChild(grid);

  // 下一步建议
  if (Array.isArray(resume.suggestions) && resume.suggestions.length > 0) {
    const sugDiv = document.createElement('div');
    sugDiv.className = 'resume-suggestions';
    const ul = document.createElement('ul');
    for (const s of resume.suggestions) {
      const li = document.createElement('li');
      li.textContent = s;
      ul.appendChild(li);
    }
    sugDiv.appendChild(ul);
    resumeContentEl.appendChild(sugDiv);
  }
}

function updateURLParams() {
  const params = new URLSearchParams(window.location.search);
  params.set('filter', currentFilter);
  params.set('view', currentView);
  window.history.replaceState({}, '', `${window.location.pathname}?${params.toString()}`);
}

function applyFilterUI(filter) {
  document.querySelectorAll('.nav-item').forEach((btn) => {
    const isActive = btn.dataset.filter === filter;
    btn.classList.toggle('active', isActive);
    // 同步筛选器选中状态，便于辅助技术识别当前筛选器
    btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });
}

function setFilter(filter) {
  currentFilter = filter;
  applyFilterUI(filter);
  updateURLParams();
  render(true);
}

// 关键字搜索走后端 SEARCH_INSPIRATIONS（与 popup 一致，避免前后端搜索逻辑分叉）。
// 非搜索态仍使用本地 allInspirations，保留氛围筛选与分组视图所需的完整数据。
async function performSearch(keyword) {
  const kw = typeof keyword === 'string' ? keyword.trim() : '';
  if (!kw) {
    searchResults = null;
    render();
    return;
  }
  try {
    const res = await chrome.runtime.sendMessage({ action: 'SEARCH_INSPIRATIONS', keyword: kw });
    if (!res || !res.ok) {
      showToast('搜索失败：' + (res?.error || '未知错误'), 'error');
      searchResults = [];
    } else {
      searchResults = res.inspirations || [];
    }
  } catch (err) {
    console.error('[Dashboard] 搜索失败:', err);
    showToast('搜索失败', 'error');
    searchResults = [];
  }
  render(true);
}

function getFiltered() {
  // 搜索态用后端结果，非搜索态用本地全量数据；氛围筛选统一在客户端做
  const source = searchResults !== null ? searchResults : allInspirations;
  return source.filter((item) => {
    return currentFilter === 'all' || item.mood === currentFilter;
  });
}

function isSafeUrl(url) {
  if (!url || typeof url !== 'string') return false;
  const lower = url.trim().toLowerCase();
  return lower.startsWith('http://') || lower.startsWith('https://');
}

function groupByDate(items) {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startOfYesterday = startOfToday - 86400000;
  const startOfWeek = startOfToday - 6 * 86400000; // 今天起向前 7 天内归入"本周"
  const groups = { today: [], yesterday: [], thisWeek: [], earlier: [] };
  for (const item of items) {
    const t = item.createdAt;
    if (!Number.isFinite(t)) { groups.earlier.push(item); continue; }
    if (t >= startOfToday) groups.today.push(item);
    else if (t >= startOfYesterday) groups.yesterday.push(item);
    else if (t >= startOfWeek) groups.thisWeek.push(item);
    else groups.earlier.push(item);
  }
  return groups;
}

function buildCard(item, index) {
  const card = document.createElement('article');
  card.className = 'card';
  if (item.pinned) card.classList.add('is-pinned');
  if (item.isDemo) card.classList.add('is-demo');
  // Phase 4.2: 媒体灵感卡片加 is-media 变体
  if (item.mediaType) card.classList.add('is-media', `is-media-${item.mediaType}`);
  // stagger 入场：最多前 9 张有递增延迟
  card.style.animationDelay = `${Math.min(index, 8) * 35}ms`;

  const moodHtml = item.mood
    ? (item.isDemo
      ? `<span class="mood-badge is-ai">${escapeHtml(item.mood)}<span class="mood-ai-tag" aria-label="AI 推荐"><span aria-hidden="true">✨</span> AI 推荐</span></span>`
      : `<span class="mood-badge">${escapeHtml(item.mood)}</span>`)
    : '';
  const noteHtml = item.note ? `<div class="card-note">${escapeHtml(item.note)}</div>` : '';
  const quoteHtml = item.selectedText ? `<blockquote class="card-quote">${escapeHtml(item.selectedText)}</blockquote>` : '';

  const safe = isSafeUrl(item.url);
  const titleHtml = safe
    ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a>`
    : `<span class="unsafe-url">${escapeHtml(item.title)}</span>`;
  const openLinkHtml = safe
    ? `<a class="open-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">打开原链接</a>`
    : `<span class="open-link disabled">链接不可用</span>`;

  // Phase 4.2: 媒体元数据徽章 + 副标题
  const MEDIA_ICONS = { music: '🎵', podcast: '🎙', video: '🎬' };
  const MEDIA_LABELS = { music: '音乐', podcast: '播客', video: '视频' };
  const mediaBadgeHtml = item.mediaType
    ? `<span class="media-badge media-badge--${escapeHtml(item.mediaType)}" title="${escapeHtml(MEDIA_LABELS[item.mediaType] || item.mediaType)}">${MEDIA_ICONS[item.mediaType] || 'Media'} ${escapeHtml(MEDIA_LABELS[item.mediaType] || item.mediaType)}</span>`
    : '';
  const mediaSubHtml = (item.mediaType && (item.mediaArtist || item.mediaAlbum))
    ? `<div class="card-media-sub">${escapeHtml([item.mediaArtist, item.mediaAlbum].filter(Boolean).join(' · '))}</div>`
    : '';
  const mediaArtworkHtml = (item.mediaType && item.mediaArtworkUrl && isSafeUrl(item.mediaArtworkUrl))
    ? `<div class="card-media-artwork"><img src="${escapeHtml(item.mediaArtworkUrl)}" alt="" loading="lazy" onerror="this.parentElement.style.display='none'"></div>`
    : '';

  const pinBtnHtml = `<button type="button" class="pin-btn${item.pinned ? ' is-pinned' : ''}" data-id="${escapeHtml(String(item.id))}" data-pin="1" title="${item.pinned ? '取消置顶' : '置顶'}" aria-label="${item.pinned ? '取消置顶' : '置顶'}">${PIN_ICON_SVG}<span class="pin-label">${item.pinned ? '已置顶' : '置顶'}</span></button>`;

  // 独立的"编辑"按钮：替代原双击标题进入编辑模式的交互，
  // 避免单击/双击区分带来的 250ms 延迟，链接单击即可直接打开
  // 示例卡片（isDemo）不渲染编辑按钮
  const editBtnHtml = item.isDemo
    ? ''
    : `<button type="button" class="edit-btn" data-id="${escapeHtml(String(item.id))}" title="编辑标题" aria-label="编辑标题">${EDIT_ICON_SVG}</button>`;

  // Context Card 导出按钮：把当前灵感导出为 .vcf 文件
  const exportCardBtnHtml = `<button type="button" class="export-card-btn" data-id="${escapeHtml(String(item.id))}" title="导出为 Context Card" aria-label="导出为 Context Card"><span>导出卡片</span></button>`;

  // 示例卡片（isDemo）禁用置顶/导出/删除按钮，仅保留打开原链接
  const extraActionsHtml = item.isDemo ? '' : `
      ${pinBtnHtml}
      ${exportCardBtnHtml}
      <button type="button" class="delete-btn" data-id="${escapeHtml(String(item.id))}" aria-live="polite">删除</button>`;

  card.innerHTML = `
    ${mediaArtworkHtml}
    <div class="card-header">
      <div>
        <div class="card-title">${titleHtml}</div>
        <div class="card-meta">
          ${mediaBadgeHtml}
          ${moodHtml}
          <span>${formatTime(item.createdAt)}</span>
        </div>
        ${mediaSubHtml}
      </div>
      ${editBtnHtml}
    </div>
    ${quoteHtml}
    ${noteHtml}
    <div class="card-actions">
      ${openLinkHtml}
      ${extraActionsHtml}
    </div>
  `;

  // 编辑按钮触发标题编辑模式（取代原双击逻辑）— 示例卡片无编辑按钮，跳过
  if (!item.isDemo) {
    const editBtn = card.querySelector('.edit-btn');
    if (editBtn) {
      editBtn.addEventListener('click', () => {
        const titleEl = card.querySelector('.card-title');
        if (titleEl) enterEditMode(card, item, titleEl);
      });
    }
  }

  return card;
}

// 点击 .edit-btn 进入标题编辑模式：回车保存（调用 UPDATE_INSPIRATION），Esc 取消
function enterEditMode(card, item, titleEl) {
  if (card.classList.contains('is-editing')) return;
  card.classList.add('is-editing');

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'card-title-edit';
  input.value = item.title;
  input.setAttribute('aria-label', '编辑标题');
  input.setAttribute('title', '回车保存 · Esc 取消');

  // 用 replaceWith 临时替换 titleEl，保留原节点及其全部监听器，取消时再换回
  titleEl.replaceWith(input);
  input.focus();
  input.select();

  let finished = false;
  const finish = async (commit) => {
    if (finished) return;
    finished = true;
    const newTitle = input.value.trim();

    // 取消或标题未变更：还原原 titleEl
    if (!commit || newTitle === item.title) {
      card.classList.remove('is-editing');
      input.replaceWith(titleEl);
      return;
    }
    if (!newTitle) {
      card.classList.remove('is-editing');
      input.replaceWith(titleEl);
      showToast('标题不能为空', 'error');
      return;
    }
    try {
      const res = await chrome.runtime.sendMessage({
        action: 'UPDATE_INSPIRATION',
        id: item.id,
        data: { title: newTitle }
      });
      if (!res || !res.ok) {
        showToast('更新失败：' + (res?.error || '未知错误'), 'error');
        card.classList.remove('is-editing');
        input.replaceWith(titleEl);
        return;
      }
      showToast('标题已更新', 'success');
      await loadData();
      loadResume();
    } catch (err) {
      showToast('更新失败：' + err.message, 'error');
      card.classList.remove('is-editing');
      input.replaceWith(titleEl);
    }
  };

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      finish(true);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      finish(false);
    }
  });
  // 失焦不再自动保存：仅 Enter 提交、Esc 取消。失焦按取消处理，避免误存半截输入
  input.addEventListener('blur', () => finish(false));
}

function render(resetPagination = false) {
  if (resetPagination) normalRenderLimit = PAGE_SIZE;
  const items = getFiltered();
  timelineEl.innerHTML = '';
  if (pinnedListEl) pinnedListEl.innerHTML = '';

  // 分离置顶 / 未置顶
  const pinnedItems = items.filter((i) => i.pinned);
  const normalItems = items.filter((i) => !i.pinned);

  // 渲染置顶区（pinned 元素缺失时跳过，不阻塞主时间线）
  if (pinnedItems.length > 0 && pinnedPanelEl && pinnedCountEl && pinnedListEl) {
    pinnedPanelEl.style.display = '';
    pinnedCountEl.textContent = String(pinnedItems.length);
    let pinIndex = 0;
    for (const item of pinnedItems) {
      pinnedListEl.appendChild(buildCard(item, pinIndex++));
    }
  } else if (pinnedPanelEl) {
    pinnedPanelEl.style.display = 'none';
  }

  if (items.length === 0) {
    // M-P1-1: 仅当非搜索态且无真实数据时才渲染示例灵感；
    // 搜索无结果（searchResults !== null）走正常空状态提示，避免误展示示例。
    // currentFilter 由 renderDemoInspirations() 内部筛选处理（M-P1-3）。
    if (demoModeEnabled && searchResults === null && allInspirations.length === 0) {
      emptyStateEl.style.display = 'none';
      renderDemoInspirations();
      updateFilterCounts();
      return;
    }
    timelineEl.appendChild(emptyStateEl);
    emptyStateEl.style.display = '';
    updateFilterCounts();
    return;
  }

  emptyStateEl.style.display = 'none';

  let cardIndex = 0;
  let renderedNormal = 0;
  if (currentView === 'source') {
    ({ renderedNormal, cardIndex } = renderSourceView(normalItems, cardIndex));
  } else if (currentView === 'music') {
    ({ renderedNormal, cardIndex } = renderMusicView(normalItems, cardIndex));
  } else {
    ({ renderedNormal, cardIndex } = renderTimeView(normalItems, cardIndex));
  }

  // 未渲染完则提供“加载更多”按钮
  if (normalItems.length > renderedNormal) {
    const remaining = normalItems.length - renderedNormal;
    const loadMoreBtn = document.createElement('button');
    loadMoreBtn.type = 'button';
    loadMoreBtn.className = 'load-more-btn';
    loadMoreBtn.textContent = `加载更多 (${remaining})`;
    loadMoreBtn.addEventListener('click', () => {
      normalRenderLimit += PAGE_SIZE;
      render();
    });
    timelineEl.appendChild(loadMoreBtn);
  }

  // delete-btn / pin-btn 改为父容器事件委托，见文件底部绑定

  updateFilterCounts();
}

// 演示模式：渲染示例灵感卡片（不写入数据库，纯前端内存渲染）
// 根据 currentFilter 筛选示例；置顶的示例卡片进置顶区，其余进时间线，底部附"连接 API Key"提示
function renderDemoInspirations() {
  // M-P1-3: 根据当前氛围筛选器筛选示例灵感
  const filteredDemos = currentFilter === 'all'
    ? DEMO_INSPIRATIONS
    : DEMO_INSPIRATIONS.filter((i) => i.mood === currentFilter);

  // M-P2-4: pinned 元素缺失时不阻塞渲染
  const pinnedDemos = filteredDemos.filter((i) => i.pinned);
  const normalDemos = filteredDemos.filter((i) => !i.pinned);

  if (pinnedDemos.length > 0 && pinnedPanelEl && pinnedCountEl && pinnedListEl) {
    pinnedPanelEl.style.display = '';
    pinnedCountEl.textContent = String(pinnedDemos.length);
    let pinIndex = 0;
    for (const item of pinnedDemos) {
      pinnedListEl.appendChild(buildCard(item, pinIndex++));
    }
  } else if (pinnedPanelEl) {
    pinnedPanelEl.style.display = 'none';
  }

  // M-P1-3: 筛选后无匹配示例时提示
  if (filteredDemos.length === 0) {
    const emptyHint = document.createElement('div');
    emptyHint.className = 'demo-hint';
    emptyHint.textContent = '无匹配示例';
    timelineEl.appendChild(emptyHint);
    return;
  }

  const groupEl = document.createElement('div');
  groupEl.className = 'date-group';
  const header = document.createElement('h2');
  header.className = 'group-header';
  header.innerHTML = `<span class="group-label">示例灵感</span><span class="group-count">${filteredDemos.length}</span><span class="group-divider"></span>`;
  groupEl.appendChild(header);
  let cardIndex = 0;
  for (const item of normalDemos) {
    groupEl.appendChild(buildCard(item, cardIndex++));
  }
  timelineEl.appendChild(groupEl);

  // 演示提示：可点击跳转 options 页配置 API Key
  const hint = document.createElement('div');
  hint.className = 'demo-hint';
  hint.textContent = '以上为演示示例，连接 API Key 解锁你的专属灵感分析 →';
  hint.setAttribute('role', 'button');
  hint.tabIndex = 0;
  hint.addEventListener('click', () => chrome.runtime.openOptionsPage());
  hint.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); chrome.runtime.openOptionsPage(); }
  });
  timelineEl.appendChild(hint);
}

function renderTimeView(items, startIdx) {
  const groups = groupByDate(items);
  let cardIndex = startIdx;
  let rendered = 0;
  for (const cfg of DATE_GROUPS) {
    const groupItems = groups[cfg.key];
    if (groupItems.length === 0) continue;

    const groupEl = document.createElement('div');
    groupEl.className = 'date-group';

    const header = document.createElement('h2');
    header.className = 'group-header';
    header.innerHTML = `<span class="group-label">${cfg.label}</span><span class="group-count">${groupItems.length}</span><span class="group-divider"></span>`;
    groupEl.appendChild(header);

    const slotsLeft = Math.max(0, normalRenderLimit - rendered);
    const toRender = groupItems.slice(0, slotsLeft);
    for (const item of toRender) {
      groupEl.appendChild(buildCard(item, cardIndex++));
    }
    rendered += toRender.length;
    timelineEl.appendChild(groupEl);
    if (rendered >= normalRenderLimit) break;
  }
  return { renderedNormal: rendered, cardIndex };
}

// 按来源域名分组：组内按 created_at DESC，组间按灵感数量降序
function groupBySource(items) {
  const map = new Map();
  for (const item of items) {
    const domain = extractDomain(item.url) || '(未知来源)';
    if (!map.has(domain)) map.set(domain, []);
    map.get(domain).push(item);
  }
  const groups = [...map.entries()].map(([domain, list]) => ({
    domain,
    items: list.sort((a, b) => b.createdAt - a.createdAt)
  }));
  groups.sort((a, b) => b.items.length - a.items.length);
  return groups;
}

function renderSourceView(items, startIdx) {
  const groups = groupBySource(items);
  let cardIndex = startIdx;
  let rendered = 0;
  for (const group of groups) {
    const groupEl = document.createElement('div');
    groupEl.className = 'source-group';

    const header = document.createElement('h2');
    header.className = 'source-group-header';
    // 用域名首字母作为占位图标，避免加载外部 favicon 泄露浏览历史给 Google
    const initial = (group.domain && group.domain.charAt(0))
      ? group.domain.charAt(0).toUpperCase()
      : '?';
    const iconHtml = `<span class="source-group-icon" aria-hidden="true">${escapeHtml(initial)}</span>`;
    header.innerHTML = `${iconHtml}<span class="source-group-name" title="${escapeHtml(group.domain)}">${escapeHtml(group.domain)}</span><span class="group-count">${group.items.length}</span><span class="group-divider"></span>`;
    groupEl.appendChild(header);

    const slotsLeft = Math.max(0, normalRenderLimit - rendered);
    const toRender = group.items.slice(0, slotsLeft);
    for (const item of toRender) {
      groupEl.appendChild(buildCard(item, cardIndex++));
    }
    rendered += toRender.length;
    timelineEl.appendChild(groupEl);
    if (rendered >= normalRenderLimit) break;
  }
  return { renderedNormal: rendered, cardIndex };
}

function renderMusicView(items, startIdx) {
  const groups = groupByMusic(items);
  let cardIndex = startIdx;
  let rendered = 0;
  for (const group of groups) {
    const groupEl = document.createElement('div');
    groupEl.className = 'music-group';

    const header = document.createElement('h2');
    header.className = 'music-group-header';
    const mediaIconSvg = group.mediaType === 'music' ? '🎵' : group.mediaType === 'podcast' ? '🎙' : group.mediaType === 'video' ? '🎬' : '·';
    const iconClass = group.mediaType ? `music-group-icon music-group-icon--${group.mediaType}` : 'music-group-icon music-group-icon--none';
    const iconHtml = `<span class="${iconClass}" aria-hidden="true">${escapeHtml(mediaIconSvg)}</span>`;
    const artworkHtml = (group.artworkUrl && isSafeUrl(group.artworkUrl))
      ? `<span class="music-group-artwork"><img src="${escapeHtml(group.artworkUrl)}" alt="" loading="lazy" onerror="this.parentElement.style.display='none'"></span>`
      : '';
    const metaParts = [];
    if (group.mediaType) metaParts.push(group.items.length === 1 ? '1 条灵感' : `${group.items.length} 条灵感`);
    else metaParts.push(`${group.items.length} 条无媒体信息`);
    const metaHtml = `<span class="music-group-meta">${escapeHtml(metaParts.join(' · '))}</span>`;
    header.innerHTML = `${iconHtml}${artworkHtml}<span class="music-group-name" title="${escapeHtml(group.label)}">${escapeHtml(group.label)}</span>${metaHtml}<span class="group-count">${group.items.length}</span><span class="group-divider"></span>`;
    groupEl.appendChild(header);

    const slotsLeft = Math.max(0, normalRenderLimit - rendered);
    const toRender = group.items.slice(0, slotsLeft);
    for (const item of toRender) {
      groupEl.appendChild(buildCard(item, cardIndex++));
    }
    rendered += toRender.length;
    timelineEl.appendChild(groupEl);
    if (rendered >= normalRenderLimit) break;
  }
  return { renderedNormal: rendered, cardIndex };
}

async function handleTogglePin(id) {
  try {
    const res = await chrome.runtime.sendMessage({ action: 'TOGGLE_PIN', id });
    if (!res || !res.ok) {
      showToast('置顶失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    showToast(res.pinned ? '已置顶' : '已取消置顶', 'success');
    await loadData();
    loadResume();
  } catch (err) {
    console.error('[Muse] 切换置顶失败:', err.message);
    showToast('置顶失败', 'error');
  }
}

function applyViewUI(view) {
  document.querySelectorAll('.view-switcher-item').forEach((btn) => {
    const isActive = btn.dataset.view === view;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
    // roving tabindex：仅活动 tab 在 tab 序列中
    btn.setAttribute('tabindex', isActive ? '0' : '-1');
  });
}

function setView(view) {
  if (view !== 'time' && view !== 'source' && view !== 'music') return;
  if (currentView === view) return;
  currentView = view;
  applyViewUI(view);
  updateURLParams();
  render(true);
}

function updateFilterCounts() {
  // M-P2-5: demoMode 下无真实数据时，基于 DEMO_INSPIRATIONS 计算计数，避免侧栏全 0
  const source = (demoModeEnabled && allInspirations.length === 0)
    ? DEMO_INSPIRATIONS
    : allInspirations;
  const buttons = document.querySelectorAll('.nav-item');
  buttons.forEach((btn) => {
    const filter = btn.dataset.filter;
    const count = filter === 'all'
      ? source.length
      : source.filter((i) => i.mood === filter).length;
    let badge = btn.querySelector('.nav-count');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'nav-count';
      btn.appendChild(badge);
    }
    badge.textContent = String(count);
  });
}

async function deleteInspiration(id) {
  const btn = document.querySelector(`.delete-btn[data-id="${id}"]`);
  if (!confirm('确定要删除这条灵感吗？')) {
    return;
  }
  try {
    const res = await chrome.runtime.sendMessage({ action: 'DELETE_INSPIRATION', id });
    if (!res || !res.ok) {
      console.error('删除失败:', res?.error);
      showToast('删除失败', 'error');
      if (btn) {
        btn.textContent = '删除失败';
        setTimeout(() => { btn.textContent = '删除'; }, 3000);
      }
      return;
    }
    await loadData();
    loadResume();
    showToast('已删除灵感', 'success');
  } catch (err) {
    console.error('删除失败:', err.message);
    showToast('删除失败', 'error');
    if (btn) {
      btn.textContent = '删除失败';
      setTimeout(() => { btn.textContent = '删除'; }, 3000);
    }
  }
}

function formatTime(timestamp) {
  const d = new Date(timestamp);
  return d.toLocaleString('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  });
}

// 搜索输入防抖：300ms 内连续输入只触发一次后端搜索，避免大量请求
let searchTimer;
searchInput.addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    currentKeyword = e.target.value;
    performSearch(currentKeyword);
  }, 300);
});

refreshBtn.addEventListener('click', () => {
  loadData();
  loadResume();
});

// 导出当前筛选+搜索条件下的所有灵感为 Markdown 文件
if (exportMarkdownBtn) {
  exportMarkdownBtn.addEventListener('click', () => exportInspirationsAsMarkdown());
}

document.querySelector('.nav-item[data-filter="all"]').addEventListener('click', () => setFilter('all'));

// 卡片内 delete-btn / pin-btn / export-card-btn 事件委托：在 timeline 与 pinned 两个容器上统一监听，
// 避免每次 render 后循环绑定；点击命中时按 data-id 派发
function attachCardClickDelegation(container) {
  if (!container) return;
  container.addEventListener('click', (e) => {
    const deleteBtn = e.target.closest('.delete-btn');
    if (deleteBtn) {
      const id = Number(deleteBtn.dataset.id);
      if (!Number.isFinite(id)) return;
      deleteInspiration(id);
      return;
    }
    const pinBtn = e.target.closest('.pin-btn');
    if (pinBtn) {
      const id = Number(pinBtn.dataset.id);
      if (!Number.isFinite(id)) return;
      handleTogglePin(id);
      return;
    }
    const exportBtn = e.target.closest('.export-card-btn');
    if (exportBtn) {
      const id = Number(exportBtn.dataset.id);
      if (!Number.isFinite(id)) return;
      exportInspirationCard(id);
    }
  });
}
attachCardClickDelegation(timelineEl);
attachCardClickDelegation(pinnedListEl);

// Context Card 导入：点击按钮触发隐藏文件选择，读取 .vcf 后调用 IMPORT_INSPIRATIONS
if (importCardBtn && importCardInput) {
  importCardBtn.addEventListener('click', () => importCardInput.click());
  importCardInput.addEventListener('change', () => {
    const file = importCardInput.files && importCardInput.files[0];
    if (!file) return;
    importContextCard(file)
      .catch((err) => showToast('导入失败：' + err.message, 'error'))
      .finally(() => { importCardInput.value = ''; });
  });
}

// 把单条灵感导出为 .vcf 文件
function exportInspirationCard(id) {
  const item = allInspirations.find((i) => i.id === id) ||
    (searchResults !== null ? (searchResults || []).find((i) => i.id === id) : null);
  if (!item) {
    showToast('未找到该灵感', 'error');
    return;
  }
  // 导出前做敏感词检测：不阻止导出，命中时弹确认提示，用户取消则中止
  const textToCheck = [
    typeof item.title === 'string' ? item.title : '',
    typeof item.selectedText === 'string' ? item.selectedText : '',
    typeof item.note === 'string' ? item.note : ''
  ].join('\n');
  const moderation = moderateText(textToCheck);
  if (moderation.flagged) {
    const ok = confirm(`${moderation.warning}\n确认导出？`);
    if (!ok) {
      showToast('已取消导出', 'info');
      return;
    }
  }
  try {
    const cardData = {
      title: item.title || '未命名灵感',
      url: typeof item.url === 'string' ? item.url : '',
      selectedText: typeof item.selectedText === 'string' ? item.selectedText : '',
      note: typeof item.note === 'string' ? item.note : '',
      mood: typeof item.mood === 'string' ? item.mood : '',
      createdAt: item.createdAt
    };
    const card = createCard('muse-inspiration', 'muse', cardData);
    const jsonStr = serializeCard(card);
    const blob = new Blob([jsonStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const safeName = String(item.title || 'inspiration').replace(/[\\/:*?"<>|]/g, '_').slice(0, 60) || 'inspiration';
    const a = document.createElement('a');
    a.href = url;
    a.download = `muse-${safeName}${FILE_EXTENSION}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('已导出 Context Card', 'success');
  } catch (err) {
    showToast('导出失败：' + err.message, 'error');
  }
}

// 把当前筛选+搜索条件下的所有灵感（不限分页）序列化为 Markdown 并下载
// 复用 getFiltered() 获取完整列表（已含氛围筛选与搜索态处理），按时间倒序分组输出
function exportInspirationsAsMarkdown() {
  const items = getFiltered();
  if (items.length === 0) {
    showToast('当前没有可导出的灵感', 'info');
    return;
  }

  // 按 createdAt DESC 排序（时间倒序），与 dashboard 时间分组语义一致
  const sorted = [...items].sort((a, b) => b.createdAt - a.createdAt);
  const groups = groupByDate(sorted);
  const now = Date.now();

  // 顶部元信息
  const lines = [];
  lines.push('# Muse 灵感导出');
  lines.push('');
  lines.push(`- 导出时间: ${formatMarkdownDate(now)}`);
  lines.push(`- 灵感总数: ${sorted.length}`);
  if (currentFilter !== 'all') {
    lines.push(`- 氛围筛选: ${currentFilter}`);
  }
  if (searchResults !== null && currentKeyword.trim()) {
    lines.push(`- 搜索关键词: ${currentKeyword.trim()}`);
  }
  lines.push('');
  lines.push('---');
  lines.push('');

  // 按日期分组输出（今天 / 昨天 / 本周 / 更早，时间倒序）
  for (const cfg of DATE_GROUPS) {
    const groupItems = groups[cfg.key];
    if (!groupItems || groupItems.length === 0) continue;
    lines.push(`## ${cfg.label}（${groupItems.length} 条）`);
    lines.push('');
    for (const item of groupItems) {
      lines.push(`### ${flattenText(item.title || '未命名灵感')}`);
      lines.push('');
      lines.push(`- URL: ${item.url || '(无)'}`);
      lines.push(`- 氛围: ${item.mood ? '#' + flattenText(item.mood) : '(无)'}`);
      lines.push(`- 时间: ${formatMarkdownDate(item.createdAt)}`);
      lines.push(`- 笔记: ${item.note ? flattenText(item.note) : '(无)'}`);
      lines.push(`- 摘录: ${item.selectedText ? flattenText(item.selectedText) : '(无)'}`);
      lines.push('');
    }
  }

  const md = lines.join('\n');
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `muse-inspirations-${formatMarkdownFileStamp(now)}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showToast(`已导出 ${sorted.length} 条灵感`, 'success');
}

// Markdown 正文时间格式：YYYY-MM-DD HH:mm
function formatMarkdownDate(timestamp) {
  const d = new Date(timestamp);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Markdown 文件名时间戳：YYYYMMDD-HHmm
function formatMarkdownFileStamp(timestamp) {
  const d = new Date(timestamp);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

// 把多行文本压成单行，避免破坏 Markdown 列表结构
function flattenText(text) {
  if (typeof text !== 'string') return '';
  return text.replace(/[\r\n]+/g, ' ').trim();
}

// 从 .vcf 文件导入 Context Card（仅接受 muse-inspiration 类型，作为新灵感写入）
async function importContextCard(file) {
  const text = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('文件读取失败'));
    reader.readAsText(file);
  });
  let card;
  try {
    card = deserializeCard(text);
  } catch (err) {
    throw new Error(err.message || 'Context Card 格式无效');
  }
  if (card.type !== 'muse-inspiration') {
    throw new Error(`仅支持导入 muse-inspiration 卡片，收到 ${card.type}`);
  }
  const data = card.data || {};
  // 复用现有 IMPORT_INSPIRATIONS 通道（合并写入模式）
  const res = await chrome.runtime.sendMessage({
    action: 'IMPORT_INSPIRATIONS',
    data: {
      inspirations: [{
        title: typeof data.title === 'string' && data.title ? data.title : '导入的灵感',
        url: typeof data.url === 'string' ? data.url : '',
        note: typeof data.note === 'string' ? data.note : '',
        mood: typeof data.mood === 'string' ? data.mood : '',
        selectedText: typeof data.selectedText === 'string' ? data.selectedText : '',
        createdAt: Number.isFinite(data.createdAt) ? data.createdAt : Date.now(),
        pinned: false
      }]
    }
  });
  if (!res || !res.ok) {
    throw new Error(res?.error || '写入数据库失败');
  }
  // M-P2-1: demoMode 下保存首条灵感时额外提示，让示例→真实数据的过渡不突兀
  const wasDemoMode = demoModeEnabled;
  const wasEmpty = allInspirations.length === 0;
  showToast('已导入 Context Card', 'success');
  await loadData();
  loadResume();
  if (wasDemoMode && wasEmpty) {
    showToast('你的第一条灵感已保存', 'success');
  }
}

// 视图切换器：按时间 / 按来源
document.querySelectorAll('.view-switcher-item').forEach((btn) => {
  btn.addEventListener('click', () => setView(btn.dataset.view));
});

// 视图 tablist 键盘导航：←/→ 切换、Home 跳首个、End 跳末个
const viewTablist = document.querySelector('.view-switcher');
if (viewTablist) {
  viewTablist.addEventListener('keydown', (e) => {
    const tabs = Array.from(document.querySelectorAll('.view-switcher-item'));
    if (tabs.length === 0) return;
    const currentIndex = tabs.findIndex((t) => t.getAttribute('tabindex') === '0');
    let targetIndex = -1;
    switch (e.key) {
      case 'ArrowRight':
        targetIndex = (currentIndex + 1) % tabs.length;
        break;
      case 'ArrowLeft':
        targetIndex = (currentIndex - 1 + tabs.length) % tabs.length;
        break;
      case 'Home':
        targetIndex = 0;
        break;
      case 'End':
        targetIndex = tabs.length - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    const targetTab = tabs[targetIndex];
    setView(targetTab.dataset.view);
    targetTab.focus();
  });
}

if (navToggleEl && sidebarEl) {
  navToggleEl.addEventListener('click', () => {
    const open = sidebarEl.classList.toggle('nav-open');
    navToggleEl.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
}

// 空状态氛围种子按钮：点击后提示用户去网页捕获灵感（不尝试 window.close，避免非脚本打开的标签页静默失败）
document.querySelectorAll('.seed-tag').forEach((btn) => {
  btn.addEventListener('click', () => {
    showToast('请切换到目标网页，选中文字即可捕获灵感', 'info');
  });
});

function initFromURL() {
  const params = new URLSearchParams(window.location.search);
  const filter = params.get('filter');
  const view = params.get('view');
  if (filter) currentFilter = filter;
  if (view === 'time' || view === 'source' || view === 'music') currentView = view;
}

initFromURL();

// M-P1-2: 监听 demoMode 变化，用户在 options 页切换后 Dashboard 自动同步刷新
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && 'demoMode' in changes) {
    loadData();
  }
});

// 主数据加载并行执行；均完成后刷新筛选器计数，覆盖 loadMoods 晚于 loadData 的情况
// 灵感回顾独立异步加载，不阻塞主时间线渲染
Promise.all([loadMoods(), loadData()]).then(() => {
  applyFilterUI(currentFilter);
  applyViewUI(currentView);
  updateFilterCounts();
  render(true);
});
loadResume();
