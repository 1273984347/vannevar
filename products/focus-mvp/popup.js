// popup.js — 插件主界面交互

import { showToast } from './shared/toast.js';
import { createCard, serializeCard, deserializeCard, FILE_EXTENSION } from './lib/vannevar-context-card.js';
import { getAgentState, markSuggestionsRead, dismissSuggestion, markSuggestionActed } from './agent.js';
import { pushSnapshotToBridge, getBridgeLinkConfig } from './bridge-link.js';
import { getSnapshotUuid } from './sync.js';
import { hasAuthToken } from './auth.js';

const saveBtn = document.getElementById('saveBtn');
const statusEl = document.getElementById('status');
const listEl = document.getElementById('snapshotList');
const emptyEl = document.getElementById('emptyState');
const searchInput = document.getElementById('searchInput');
const loadMoreBtn = document.getElementById('loadMoreBtn');
const importCardBtn = document.getElementById('importCardBtn');
const importCardInput = document.getElementById('importCardInput');
const snapshotSearchInput = document.getElementById('snapshotSearchInput');
const snapshotEmptyEl = document.getElementById('snapshotEmpty');
const agentSection = document.getElementById('agentSection');
const agentSuggestionListEl = document.getElementById('agentSuggestionList');

// P1: 防御页面元素缺失导致初始化崩溃
if (!saveBtn || !statusEl || !listEl || !emptyEl || !searchInput || !loadMoreBtn) {
  console.error('[Focus] popup 必要元素缺失，无法初始化');
  throw new Error('popup 必要元素缺失');
}

const labelEl = saveBtn.querySelector('.btn-label');

// 模块级 UI 状态：跨 renderSnapshots 保持稳定
// expandedIds：记录已展开详情的 snapshot id，搜索/编辑/删除后自动恢复展开
const expandedIds = new Set();

// 当前已加载的快照缓存（用于客户端搜索过滤 + 时间分组）
let currentSnapshots = [];
// resume 缓存：详情展开后存储，供客户端搜索匹配 contextTags（contextTags 不在 DB，按需生成）
const resumeCache = new Map();
// 首次渲染标记：后续渲染禁用入场动画，避免搜索时反复闪动
let listFirstRendered = false;

// 分页参数：单页 50 条（与 db.js getSnapshots 默认值一致）
const PAGE_SIZE = 50;
let listOffset = 0;       // 当前已加载到第几条
let listHasMore = false;  // 后端是否可能还有更多数据

saveBtn.addEventListener('click', () => {
  // F-COMPAT-01：顶层 try/catch + .catch 兜底，防止未处理 Promise rejection 被静默吞掉
  try {
    saveContext().catch((err) => showToast('错误：' + err.message, 'error'));
  } catch (err) {
    showToast('错误：' + err.message, 'error');
  }
});

// R2-P1 修复：空态 CTA <a href="#saveBtn"> 点击后自动触发 saveBtn click
// 设计意图：href 锚点引导用户看到操作位置 + hashchange 监听增强 UX（点击即执行）
window.addEventListener('hashchange', () => {
  if (location.hash === '#saveBtn' && document.activeElement === saveBtn) {
    saveBtn.click();
  }
});
loadMoreBtn.addEventListener('click', () => {
  try {
    loadMoreSnapshots().catch((err) => showToast('加载更多失败：' + err.message, 'error'));
  } catch (err) {
    showToast('加载更多失败：' + err.message, 'error');
  }
});

// Context Card 导入：点击按钮触发隐藏文件选择，读取 .vcf 后调用 IMPORT_SNAPSHOTS
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

document.addEventListener('DOMContentLoaded', () => {
  try {
    loadSnapshots().catch((err) => showToast('加载失败：' + err.message, 'error'));
  } catch (err) {
    showToast('加载失败：' + err.message, 'error');
  }
  // 阶段2.1: 加载 Agent 建议并清零 badge
  loadAgentSuggestions().catch(() => { /* Agent 加载失败静默，不影响主流程 */ });
});

// 搜索：300ms 防抖
let searchTimer = null;
searchInput.addEventListener('input', () => {
  try {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      try {
        const keyword = searchInput.value.trim();
        if (!keyword) {
          loadSnapshots().catch((err) => showToast('加载失败：' + err.message, 'error'));
        } else {
          searchSnapshots(keyword).catch((err) => showToast('搜索失败：' + err.message, 'error'));
        }
      } catch (err) {
        showToast('搜索失败：' + err.message, 'error');
      }
    }, 300);
  } catch (err) {
    showToast('搜索失败：' + err.message, 'error');
  }
});

// 快照列表客户端过滤（标题/URL/标签）：200ms 防抖，实时过滤已加载列表
let snapshotSearchTimer = null;
if (snapshotSearchInput) {
  snapshotSearchInput.addEventListener('input', () => {
    clearTimeout(snapshotSearchTimer);
    snapshotSearchTimer = setTimeout(() => {
      renderListView();
    }, 200);
  });
}

// 根据当前搜索框状态刷新列表（编辑标题、删除、保存后复用）
// 搜索模式下不分页，重置 offset；列表模式下保留当前 offset 重载
function reloadCurrentView() {
  const keyword = searchInput.value.trim();
  if (!keyword) {
    return loadSnapshots(listOffset);
  }
  return searchSnapshots(keyword);
}

async function saveContext() {
  setSaveLoading(true);
  try {
    const res = await chrome.runtime.sendMessage({ action: 'SAVE_CONTEXT' });
    if (!res || !res.ok) {
      showToast('保存失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    showToast(`已保存「${res.title}」（${res.count} 个标签页）`, 'success');
    await reloadCurrentView();
  } catch (err) {
    showToast('错误：' + err.message, 'error');
  } finally {
    setSaveLoading(false);
  }
}

// loadSnapshots 接受 keepOffset 参数：
//   - 不传或 0：重置到首页（搜索切换、保存后刷新场景）
//   - 正整数：从该 offset 开始重新加载当前页（用于删除后保持滚动位置）
async function loadSnapshots(keepOffset = 0) {
  try {
    const offset = keepOffset > 0 ? keepOffset : 0;
    const res = await chrome.runtime.sendMessage({
      action: 'GET_SNAPSHOTS',
      limit: PAGE_SIZE,
      offset
    });
    if (!res || !res.ok) {
      showToast('加载失败：' + (res?.error || '未知错误'), 'error');
      return;
    }

    const snaps = res.snapshots || [];
    // 首页加载：直接替换；后续页：由 loadMoreSnapshots 追加
    if (offset === 0) {
      listOffset = 0;
      renderSnapshots(snaps);
    } else {
      // keepOffset 模式：删除/编辑后重载当前页，保持已加载条数不变
      // 重新拉取从 0 到当前 offset+PAGE_SIZE 的数据并整体替换
      const fullRes = await chrome.runtime.sendMessage({
        action: 'GET_SNAPSHOTS',
        limit: offset + PAGE_SIZE,
        offset: 0
      });
      if (fullRes && fullRes.ok) {
        renderSnapshots(fullRes.snapshots || []);
      }
    }
    updateLoadMoreState(snaps.length);
  } catch (err) {
    showToast('加载失败：' + err.message, 'error');
  }
}

// 加载更多：offset 推进一页，结果追加到现有列表
async function loadMoreSnapshots() {
  if (loadMoreBtn.disabled) return;
  loadMoreBtn.disabled = true;
  loadMoreBtn.textContent = '加载中…';
  try {
    const nextOffset = listOffset + PAGE_SIZE;
    const res = await chrome.runtime.sendMessage({
      action: 'GET_SNAPSHOTS',
      limit: PAGE_SIZE,
      offset: nextOffset
    });
    if (!res || !res.ok) {
      showToast('加载更多失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    const snaps = res.snapshots || [];
    if (snaps.length > 0) {
      appendSnapshots(snaps);
      listOffset = nextOffset;
    }
    updateLoadMoreState(snaps.length);
  } catch (err) {
    showToast('加载更多失败：' + err.message, 'error');
  } finally {
    loadMoreBtn.disabled = false;
    loadMoreBtn.textContent = '加载更多';
  }
}

// 根据"本页返回条数"决定是否还有下一页
// 后端使用 LIMIT/OFFSET：返回满页 → 可能有更多；返回不足 → 已到底
function updateLoadMoreState(returnedCount) {
  listHasMore = returnedCount >= PAGE_SIZE;
  if (listHasMore) {
    loadMoreBtn.hidden = false;
    loadMoreBtn.style.display = '';
  } else {
    loadMoreBtn.hidden = true;
    loadMoreBtn.style.display = 'none';
  }
  // 搜索模式下隐藏"加载更多"（搜索后端固定 LIMIT 50，不分页）
  if (searchInput.value.trim()) {
    loadMoreBtn.hidden = true;
    loadMoreBtn.style.display = 'none';
  }
}

// 搜索请求序号：用于丢弃过期请求的结果，避免竞态覆盖
let searchSeq = 0;
async function searchSnapshots(keyword) {
  const seq = ++searchSeq;
  try {
    const res = await chrome.runtime.sendMessage({ action: 'SEARCH_SNAPSHOTS', keyword });
    // 过期请求（用户已输入更新的关键词），丢弃结果
    if (seq !== searchSeq) return;
    if (!res || !res.ok) {
      showToast('搜索失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    // 进入搜索模式：重置分页状态，隐藏"加载更多"
    listOffset = 0;
    listHasMore = false;
    renderSnapshots(res.snapshots);
    updateLoadMoreState(0);
  } catch (err) {
    if (seq !== searchSeq) return;
    showToast('搜索失败：' + err.message, 'error');
  }
}

// 渲染入口：更新 currentSnapshots 缓存后交给 renderListView 统一处理过滤 + 分组
function renderSnapshots(snapshots) {
  currentSnapshots = snapshots || [];
  renderListView();
}

// 追加下一页快照到缓存后整体重渲染（分组结构需要重组，无法简单 append）
function appendSnapshots(snapshots) {
  if (!snapshots || snapshots.length === 0) return;
  currentSnapshots = currentSnapshots.concat(snapshots);
  renderListView();
}

// 按时间倒序分组：今天 / 昨天 / 本周 / 更早
function groupSnapshotsByTime(snapshots) {
  const startOfToday = new Date(new Date().setHours(0, 0, 0, 0)).getTime();
  const startOfYesterday = startOfToday - 86400000;
  const startOfWeek = startOfToday - 6 * 86400000;

  const groups = {
    today: { label: '今天', items: [] },
    yesterday: { label: '昨天', items: [] },
    thisWeek: { label: '本周', items: [] },
    earlier: { label: '更早', items: [] }
  };

  for (const snap of snapshots) {
    const t = snap.createdAt;
    if (!Number.isFinite(t)) { groups.earlier.items.push(snap); continue; }
    if (t >= startOfToday) groups.today.items.push(snap);
    else if (t >= startOfYesterday) groups.yesterday.items.push(snap);
    else if (t >= startOfWeek) groups.thisWeek.items.push(snap);
    else groups.earlier.items.push(snap);
  }
  return groups;
}

// 客户端过滤：匹配标题、标签页 title/url、AI contextTags（仅已缓存项），不区分大小写
function filterSnapshotsByKeyword(snapshots, keyword) {
  if (!keyword) return snapshots.slice();
  const kw = keyword.toLowerCase();
  return snapshots.filter((snap) => {
    if (typeof snap.title === 'string' && snap.title.toLowerCase().includes(kw)) return true;
    if (Array.isArray(snap.tabs)) {
      for (const t of snap.tabs) {
        if (typeof t.title === 'string' && t.title.toLowerCase().includes(kw)) return true;
        if (typeof t.url === 'string' && t.url.toLowerCase().includes(kw)) return true;
      }
    }
    const cached = resumeCache.get(snap.id);
    if (cached && Array.isArray(cached.contextTags)) {
      for (const tag of cached.contextTags) {
        if (typeof tag === 'string' && tag.toLowerCase().includes(kw)) return true;
      }
    }
    return false;
  });
}

// 构造分组标题 <li>（语义上作为 heading，便于辅助技术导航）
function buildGroupHeader(label) {
  const li = document.createElement('li');
  li.className = 'snapshot-group-header';
  li.setAttribute('role', 'heading');
  li.setAttribute('aria-level', '3');
  li.textContent = label;
  return li;
}

// 统一渲染：先按 #snapshotSearchInput 过滤 → 再按时间分组 → 渲染
// 快照数量 <= 5 时不显示分组标题（少量快照不需要分组）
function renderListView() {
  // 后续渲染禁用入场动画，避免搜索/过滤时反复闪动
  if (listFirstRendered) {
    listEl.classList.add('snapshot-list--static');
  }

  const keyword = snapshotSearchInput ? snapshotSearchInput.value.trim() : '';
  const filtered = filterSnapshotsByKeyword(currentSnapshots, keyword);

  listEl.innerHTML = '';

  if (filtered.length === 0) {
    // 区分"完全没有快照"与"搜索无结果"
    listEl.style.display = 'none';
    if (currentSnapshots.length === 0) {
      emptyEl.style.display = 'block';
      if (snapshotEmptyEl) snapshotEmptyEl.hidden = true;
    } else {
      emptyEl.style.display = 'none';
      if (snapshotEmptyEl) snapshotEmptyEl.hidden = false;
    }
    listFirstRendered = true;
    return;
  }

  listEl.style.display = 'block';
  emptyEl.style.display = 'none';
  if (snapshotEmptyEl) snapshotEmptyEl.hidden = true;

  // 快照数量 <= 5：不分组，直接平铺
  if (filtered.length <= 5) {
    for (const snap of filtered) {
      listEl.appendChild(buildSnapshotCard(snap));
    }
    listFirstRendered = true;
    return;
  }

  // 快照数量 > 5：按时间倒序分组渲染（空组不显示）
  const groups = groupSnapshotsByTime(filtered);
  const order = ['today', 'yesterday', 'thisWeek', 'earlier'];
  for (const key of order) {
    const g = groups[key];
    if (!g.items.length) continue;
    listEl.appendChild(buildGroupHeader(g.label));
    for (const snap of g.items) {
      listEl.appendChild(buildSnapshotCard(snap));
    }
  }
  listFirstRendered = true;
}

// 构造单个快照卡片 <li>，并应用模块级展开/确认状态
function buildSnapshotCard(snap) {
  const li = document.createElement('li');
  li.className = 'snapshot-item';
  li.dataset.id = String(snap.id);

  const infoDiv = document.createElement('div');
  infoDiv.className = 'snapshot-info';

  const titleDiv = document.createElement('div');
  titleDiv.className = 'snapshot-title';
  titleDiv.textContent = snap.title;
  titleDiv.setAttribute('title', snap.title + '（双击编辑）');
  // 键盘入口：tabindex 使标题可聚焦，Enter/Space 触发编辑
  titleDiv.setAttribute('tabindex', '0');
  titleDiv.setAttribute('role', 'button');
  titleDiv.setAttribute('aria-label', `编辑标题：${snap.title}`);
  titleDiv.addEventListener('dblclick', () => enterEditMode(li, snap, titleDiv));
  titleDiv.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      enterEditMode(li, snap, titleDiv);
    }
  });

  const metaDiv = document.createElement('div');
  metaDiv.className = 'snapshot-meta';
  const timeStr = formatTime(snap.createdAt);
  metaDiv.textContent = `${timeStr} · ${snap.tabCount}个标签页`;

  infoDiv.appendChild(titleDiv);
  infoDiv.appendChild(metaDiv);

  const actionsDiv = document.createElement('div');
  actionsDiv.className = 'snapshot-actions';

  const detailBtn = document.createElement('button');
  detailBtn.type = 'button';
  detailBtn.className = 'detail-btn';
  detailBtn.dataset.id = String(snap.id);
  detailBtn.textContent = '详情';
  detailBtn.setAttribute('aria-expanded', 'false');
  detailBtn.setAttribute('aria-label', '查看标签页详情');
  detailBtn.addEventListener('click', () => toggleDetail(li, snap, detailBtn));

  const restoreBtn = document.createElement('button');
  restoreBtn.type = 'button';
  restoreBtn.className = 'restore-btn';
  restoreBtn.dataset.id = String(snap.id);
  restoreBtn.textContent = '恢复';
  restoreBtn.addEventListener('click', () => {
    // F-COMPAT-01：顶层 try/catch + .catch 兜底
    try {
      // 快照标签页为空时直接提示，不发起恢复请求
      if (!snap.tabCount || snap.tabCount === 0) {
        showToast('该上下文没有可恢复的标签页', 'info');
        return;
      }
      restoreContext(snap.id, snap.tabCount).catch((err) => showToast('恢复失败：' + err.message, 'error'));
    } catch (err) {
      showToast('恢复失败：' + err.message, 'error');
    }
  });

  const deleteBtn = document.createElement('button');
  deleteBtn.type = 'button';
  deleteBtn.className = 'delete-btn';
  deleteBtn.dataset.id = String(snap.id);
  deleteBtn.textContent = '删除';
  deleteBtn.setAttribute('aria-label', '删除该上下文');
  deleteBtn.addEventListener('click', () => {
    // F-COMPAT-01：顶层 try/catch + .catch 兜底
    try {
      deleteContext(snap.id).catch((err) => showToast('删除失败：' + err.message, 'error'));
    } catch (err) {
      showToast('删除失败：' + err.message, 'error');
    }
  });

  actionsDiv.appendChild(detailBtn);
  actionsDiv.appendChild(restoreBtn);
  actionsDiv.appendChild(deleteBtn);

  // 详情面板（默认隐藏，展开时挂载标签页列表）
  const detailDiv = document.createElement('div');
  detailDiv.className = 'snapshot-detail';
  detailDiv.dataset.id = String(snap.id);

  li.appendChild(infoDiv);
  li.appendChild(actionsDiv);
  li.appendChild(detailDiv);

  // 恢复展开状态（搜索/编辑/删除导致的重渲染不应丢失用户已展开的卡片）
  if (expandedIds.has(snap.id)) {
    toggleDetail(li, snap, detailBtn);
  }
  return li;
}

// 详情展开 / 收起
function toggleDetail(li, snap, detailBtn) {
  const detailDiv = li.querySelector('.snapshot-detail');
  if (!detailDiv) return;
  const expanded = li.classList.toggle('snapshot-item--expanded');
  detailBtn.setAttribute('aria-expanded', String(expanded));
  detailBtn.classList.toggle('is-active', expanded);
  detailBtn.textContent = expanded ? '收起' : '详情';
  // 同步模块级展开状态：仅记录当前仍在列表中的 id
  if (expanded) {
    expandedIds.add(snap.id);
  } else {
    expandedIds.delete(snap.id);
  }
  if (expanded && !detailDiv.hasChildNodes()) {
    // 顶部：Context Card 导出按钮
    renderDetailActions(detailDiv, snap);
    // Resume 摘要区域（异步加载，不阻塞标签页列表）
    renderResumeSection(detailDiv, snap);
    // 下方：标签页列表
    renderDetailTabs(detailDiv, snap);
  }
  if (expanded) {
    // 展开后将焦点移到详情容器，便于辅助技术跟进内容变化
    detailDiv.setAttribute('tabindex', '-1');
    detailDiv.focus();
  }
}

// Resume 摘要区域：详情展开时调用 GENERATE_RESUME 异步加载
function renderResumeSection(container, snap) {
  const section = document.createElement('div');
  section.className = 'resume-section';
  section.dataset.id = String(snap.id);

  const loading = document.createElement('div');
  loading.className = 'resume-loading';
  loading.textContent = '正在生成摘要…';
  section.appendChild(loading);
  container.appendChild(section);

  chrome.runtime.sendMessage({ action: 'GENERATE_RESUME', id: snap.id })
    .then((res) => {
      // 节点可能已被重新渲染（搜索/编辑/删除），需校验仍挂载
      if (!section.isConnected) return;
      // 失败静默：移除整个 resume 区域，不阻断标签页列表
      if (!res || !res.ok || !res.resume) {
        section.remove();
        return;
      }
      try {
        section.innerHTML = '';
        // 缓存 resume，供客户端搜索匹配 contextTags（contextTags 不在 DB，按需生成）
        // R5-FE-11: LRU 淘汰，最多 50 条，避免内存无限增长
        // R6-FE-5: 写入前先 delete 已存在的 key，更新 Map 迭代顺序，使其成为真正 LRU（原实现实为 FIFO）
        resumeCache.delete(snap.id);
        if (resumeCache.size >= 50) {
          const firstKey = resumeCache.keys().next().value;
          resumeCache.delete(firstKey);
        }
        resumeCache.set(snap.id, res.resume);
        renderResumeContent(section, res.resume);
        // F-PERF-01：AI 超时降级到规则引擎时 toast 提示用户
        if (res.resume.aiTimeout) {
          showToast('AI 响应超时，已使用本地规则引擎', 'info');
        }
      } catch (renderErr) {
// P2: 静默处理非关键扩展错误
        if (section.isConnected) section.remove();
      }
    })
    .catch((err) => {
      // 异常静默处理
// P2: 静默处理非关键扩展错误
      if (section.isConnected) section.remove();
    });
}

// R2-V3 修复：sanitize SVG 元素，移除 on* 事件属性、javascript: 协议和 <script> 标签
// DOMParser 用 image/svg+xml 解析后，SVG 中仍可能携带 on* 事件处理器和 script 子元素，
// appendChild 到 HTML 文档后 on* 仍可被触发，必须显式剥离。
// R2-V12 说明：此 sanitize 逻辑需 Playwright E2E 测试验证浏览器 DOM 行为，
// Python 单元测试无法覆盖 DOMParser 解析后的 DOM 事件绑定，属已知测试盲区。
function sanitizeSvg(root) {
  const all = [root, ...root.querySelectorAll('*')];
  for (const el of all) {
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on')) {
        el.removeAttribute(attr.name);
      } else if ((name === 'href' || name === 'xlink:href' || name === 'src')
                 && /^\s*(javascript|data):/i.test(attr.value)) {
        // R3-V7 修复：扩展协议检查——覆盖 data: 协议（可内嵌 base64 任意内容）与 src 属性
        el.removeAttribute(attr.name);
      }
    }
  }
  root.querySelectorAll('script').forEach(s => s.remove());
  // R3-V7 修复：移除 foreignObject 元素（可嵌入任意 HTML，绕过 SVG 沙箱）
  root.querySelectorAll('foreignObject').forEach(s => s.remove());
  // R5-FE-4: 移除 SMIL 动画元素（可触发副作用且 sanitize 不应保留动画）
  // R6-FE-6: 补充 animateColor 元素，避免遗漏动画类型
  root.querySelectorAll('set,animate,animateTransform,animateMotion,animateColor').forEach(s => s.remove());
}

function renderResumeContent(section, resume) {
  // AI 增强状态标签
  const badge = document.createElement('div');
  badge.className = 'resume-badge' + (resume.aiEnhanced ? ' resume-badge--ai' : ' resume-badge--rule');
  badge.textContent = resume.aiEnhanced ? '✨ AI 增强' : '规则引擎';
  section.appendChild(badge);
  if (!resume.aiEnhanced) {
    const hint = document.createElement('div');
    hint.className = 'resume-ai-hint';
    hint.textContent = '配置 AI 后可获得更智能的摘要 →';
    hint.setAttribute('role', 'button');
    hint.tabIndex = 0;
    hint.addEventListener('click', () => chrome.runtime.openOptionsPage());
    hint.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); chrome.runtime.openOptionsPage(); }
    });
    section.appendChild(hint);
  }

  // 摘要文本
  if (resume.summary) {
    const summaryDiv = document.createElement('div');
    summaryDiv.className = 'resume-summary';
    summaryDiv.textContent = resume.summary;
    section.appendChild(summaryDiv);
  }

  // P0-AI: AI 上下文标签（工作强度/紧急程度/协作状态/认知类型）
  // W079b: AI 增强时容器加 .ai-enhanced，标签用品牌色 pill，与规则引擎灰色 pill 视觉区分
  if (Array.isArray(resume.contextTags) && resume.contextTags.length > 0) {
    const tagsDiv = document.createElement('div');
    tagsDiv.className = 'resume-context-tags';
    if (resume.aiEnhanced) {
      tagsDiv.classList.add('ai-enhanced');
      const aiLabel = document.createElement('span');
      aiLabel.className = 'ai-context-label';
      aiLabel.textContent = '✨ AI';
      tagsDiv.appendChild(aiLabel);
    }
    for (const tag of resume.contextTags) {
      const tagEl = document.createElement('span');
      tagEl.className = 'context-tag';
      tagEl.textContent = tag;
      tagsDiv.appendChild(tagEl);
    }
    section.appendChild(tagsDiv);
  }

  // 场景标签（pill：icon + scene + count）
  if (Array.isArray(resume.scenes) && resume.scenes.length > 0) {
    const scenesDiv = document.createElement('div');
    scenesDiv.className = 'resume-scenes';
    for (const sc of resume.scenes) {
      const pill = document.createElement('span');
      pill.className = 'scene-pill';
      const icon = document.createElement('span');
      icon.className = 'scene-pill-icon';
      icon.setAttribute('aria-hidden', 'true');
      // V16 修复：安全渲染 SVG icon，避免 innerHTML 注入 XSS
      // 仅接受 <svg 开头的字符串，用 DOMParser 解析为 SVG 元素后 appendChild
      // R2-V3 修复：解析后 sanitize，移除 on* 事件属性、javascript: 协议和 <script> 标签
      if (sc.icon && typeof sc.icon === 'string' && sc.icon.trim().startsWith('<svg')) {
        try {
          const parsed = new DOMParser().parseFromString(sc.icon, 'image/svg+xml');
          const svgEl = parsed.documentElement;
          if (svgEl && svgEl.tagName.toLowerCase() === 'svg') {
            sanitizeSvg(svgEl);
            icon.appendChild(svgEl);
          }
        } catch (_) {
          // SVG 解析失败，留空
        }
      }
      const label = document.createElement('span');
      label.className = 'scene-pill-label';
      label.textContent = `${sc.scene} ${sc.count}`;
      pill.appendChild(icon);
      pill.appendChild(label);
      scenesDiv.appendChild(pill);
    }
    section.appendChild(scenesDiv);
  }

  // 下一步建议
  if (Array.isArray(resume.suggestions) && resume.suggestions.length > 0) {
    const sugDiv = document.createElement('div');
    sugDiv.className = 'resume-suggestions';
    const sugTitle = document.createElement('div');
    sugTitle.className = 'resume-subtitle';
    sugTitle.textContent = '下一步建议';
    sugDiv.appendChild(sugTitle);
    const ul = document.createElement('ul');
    for (const s of resume.suggestions) {
      const li = document.createElement('li');
      li.textContent = s;
      ul.appendChild(li);
    }
    sugDiv.appendChild(ul);
    section.appendChild(sugDiv);
  }

  // 关键词
  if (Array.isArray(resume.keywords) && resume.keywords.length > 0) {
    const kwDiv = document.createElement('div');
    kwDiv.className = 'resume-keywords';
    for (const kw of resume.keywords) {
      const tag = document.createElement('span');
      tag.className = 'keyword-tag';
      tag.textContent = kw;
      kwDiv.appendChild(tag);
    }
    section.appendChild(kwDiv);
  }
}

function renderDetailTabs(container, snap) {
  if (snap.tabsError) {
    const note = document.createElement('div');
    note.className = 'snapshot-tab-more';
    note.textContent = '标签页数据解析失败，无法预览';
    container.appendChild(note);
    return;
  }

  const tabs = Array.isArray(snap.tabs) ? snap.tabs : [];
  if (tabs.length === 0) {
    const note = document.createElement('div');
    note.className = 'snapshot-tab-more';
    note.textContent = '该上下文没有标签页';
    container.appendChild(note);
    return;
  }

  const ul = document.createElement('ul');
  ul.className = 'snapshot-tabs';

  const MAX = 10;

  // F-8: 校验 URL scheme，防止 javascript:/data: 等 XSS
  function _isSafeUrl(u) {
    try {
      const x = new URL(u);
      return x.protocol === 'http:' || x.protocol === 'https:';
    } catch {
      return false;
    }
  }

  function createTabLi(tab) {
    const tabLi = document.createElement('li');
    tabLi.className = 'snapshot-tab';

    const titleText = tab.title || tab.url || '(无标题)';
    const url = typeof tab.url === 'string' ? tab.url : '';

    // F-P2-4: 安全 URL 用 <a>，无 URL 或不安全 URL 用 <span>（保持相同样式 class）
    const hasSafeUrl = url && _isSafeUrl(url);
    let link;
    if (hasSafeUrl) {
      link = document.createElement('a');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    } else {
      // 无 URL 时降级为 span：禁用链接样式，仅展示标题
      link = document.createElement('span');
      link.style.cursor = 'default';
      link.style.color = 'var(--v-text-secondary)';
    }
    link.className = 'snapshot-tab-link';
    link.textContent = titleText;
    link.setAttribute('title', `${titleText}\n${url}`);

    const urlDiv = document.createElement('div');
    urlDiv.className = 'snapshot-tab-url';
    urlDiv.textContent = url || '';

    tabLi.appendChild(link);
    tabLi.appendChild(urlDiv);
    return tabLi;
  }

  const shown = tabs.slice(0, MAX);
  for (const tab of shown) {
    ul.appendChild(createTabLi(tab));
  }

  if (tabs.length > MAX) {
    const remaining = tabs.slice(MAX);
    const moreBtn = document.createElement('button');
    moreBtn.type = 'button';
    moreBtn.className = 'snapshot-tab-more';
    moreBtn.textContent = `…还有 ${remaining.length} 个`;
    moreBtn.setAttribute('aria-expanded', 'false');
    moreBtn.addEventListener('click', () => {
      moreBtn.setAttribute('aria-expanded', 'true');
      for (const tab of remaining) {
        ul.appendChild(createTabLi(tab));
      }
      moreBtn.remove();
    });
    ul.appendChild(moreBtn);
  }

  container.appendChild(ul);
}

// 编辑标题：双击 .snapshot-title 进入编辑模式
function enterEditMode(li, snap, titleDiv) {
  if (li.classList.contains('is-editing')) return;
  li.classList.add('is-editing');

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'snapshot-title-edit';
  input.value = snap.title;
  input.setAttribute('aria-label', '编辑标题');
  input.setAttribute('title', '回车保存 · Esc 取消');

  titleDiv.replaceWith(input);
  input.focus();
  input.select();

  let finished = false;
  const finish = async (commit) => {
    if (finished) return;
    finished = true;
    const newTitle = input.value.trim();

    // 取消，或标题未变更：直接还原
    if (!commit || newTitle === snap.title) {
      li.classList.remove('is-editing');
      input.replaceWith(titleDiv);
      if (!commit) {
        // ESC 取消：将焦点归还到标题，便于继续操作
        titleDiv.setAttribute('tabindex', '0');
        titleDiv.focus();
      }
      return;
    }
    if (!newTitle) {
      li.classList.remove('is-editing');
      input.replaceWith(titleDiv);
      showToast('标题不能为空', 'error');
      return;
    }
    try {
      const res = await chrome.runtime.sendMessage({ action: 'UPDATE_TITLE', id: snap.id, title: newTitle });
      if (!res || !res.ok) {
        showToast('更新失败：' + (res?.error || '未知错误'), 'error');
        li.classList.remove('is-editing');
        input.replaceWith(titleDiv);
        return;
      }
      showToast('标题已更新', 'success');
      await reloadCurrentView();
    } catch (err) {
      showToast('更新失败：' + err.message, 'error');
      li.classList.remove('is-editing');
      input.replaceWith(titleDiv);
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
  input.addEventListener('blur', () => finish(true));
}

async function restoreContext(id, count = 0) {
  const msg = count > 0 ? `正在恢复 ${count} 个标签页…` : '正在恢复上下文…';
  showToast(msg, 'info');
  try {
    const res = await chrome.runtime.sendMessage({ action: 'RESTORE_SNAPSHOT_BY_ID', id });
    if (!res || !res.ok) {
      showToast('恢复失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    if (res.restored === 0) {
      showToast('恢复失败：所有标签页均无法恢复', 'error');
    } else if (res.total && res.restored < res.total) {
      showToast(`已恢复 ${res.restored} 个标签页，部分可能已失效，已尽力恢复`, 'info');
    } else {
      showToast(`已恢复 ${res.restored} 个标签页`, 'success');
    }
  } catch (err) {
    showToast('恢复失败：' + err.message, 'error');
  }
}

async function deleteContext(id) {
  if (!confirm('确定要删除该上下文吗？此操作不可撤销。')) {
    return;
  }
  try {
    const res = await chrome.runtime.sendMessage({ action: 'DELETE_SNAPSHOT', id });
    if (!res || !res.ok) {
      showToast('删除失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    // 删除后清理该 id 的展开状态
    expandedIds.delete(id);
    // R5-FE-11: 同步清理 resume 缓存
    resumeCache.delete(id);
    await reloadCurrentView();
    showToast('已删除', 'success');
  } catch (err) {
    showToast('删除失败：' + err.message, 'error');
  }
}

function setSaveLoading(loading) {
  saveBtn.classList.toggle('is-loading', loading);
  saveBtn.disabled = loading;
  if (labelEl) labelEl.textContent = loading ? '保存中…' : '保存当前上下文';
}

// showToast / toastIcon 由 ./shared/toast.js 提供

function formatTime(timestamp) {
  if (!Number.isFinite(timestamp)) return '未知时间';
  const d = new Date(timestamp);
  if (isNaN(d.getTime())) return '未知时间';
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  if (isToday) {
    // F-P2-6: 使用 Intl.DateTimeFormat 替代 toLocaleTimeString
    const time = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(d);
    return `今天 ${time}`;
  }
  // 非今天：输出完整日期时间，例如 2026/07/05 14:30
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(d);
}

// ============================================================
// Context Card 导出 / 导入
// ============================================================

// 详情面板顶部：渲染「导出为 Context Card」+「推送到 Bridge」按钮
function renderDetailActions(container, snap) {
  const wrapper = document.createElement('div');
  wrapper.className = 'snapshot-detail-actions';

  // 导出 Context Card 按钮
  const exportBtn = document.createElement('button');
  exportBtn.type = 'button';
  exportBtn.className = 'export-card-btn';
  exportBtn.setAttribute('aria-label', '导出为 Context Card');
  exportBtn.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
    '<polyline points="7 10 12 15 17 10"/>' +
    '<line x1="12" y1="15" x2="12" y2="3"/>' +
    '</svg><span>导出为 Context Card</span>';
  exportBtn.addEventListener('click', () => {
    try {
      exportContextCard(snap);
      showToast('已导出 Context Card', 'success');
    } catch (err) {
      showToast('导出失败：' + err.message, 'error');
    }
  });
  wrapper.appendChild(exportBtn);

  // Phase 4.1：推送到 Bridge 项目档案按钮
  const pushBtn = document.createElement('button');
  pushBtn.type = 'button';
  pushBtn.className = 'push-bridge-btn';
  pushBtn.setAttribute('aria-label', '推送到 Bridge 项目档案');
  pushBtn.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M22 2L11 13"/>' +
    '<path d="M22 2l-7 20-4-9-9-4 20-7z"/>' +
    '</svg><span>推送到 Bridge</span>';
  pushBtn.addEventListener('click', () => {
    try {
      pushSnapshotToBridgeAction(snap).catch((err) => showToast('推送失败：' + err.message, 'error'));
    } catch (err) {
      showToast('推送失败：' + err.message, 'error');
    }
  });
  wrapper.appendChild(pushBtn);

  container.appendChild(wrapper);
}

// Phase 4.1：推送当前快照到 Bridge 项目档案
// 流程：校验登录 → 读取/生成 UUID → 构造 payload → 调用 bridge-link → toast 反馈
async function pushSnapshotToBridgeAction(snap) {
  // 1. 校验登录
  const loggedIn = await hasAuthToken();
  if (!loggedIn) {
    showToast('请先在设置中登录 Bridge 账号', 'info');
    chrome.runtime.openOptionsPage();
    return;
  }

  // 2. 校验已选目标项目
  const cfg = await getBridgeLinkConfig();
  if (!cfg.selectedProjectId) {
    showToast('请先在设置中选择目标 Bridge 项目', 'info');
    chrome.runtime.openOptionsPage();
    return;
  }

  // 3. 读取或生成稳定的 snapshot UUID（与 sync.js 共享 UUID 映射表）
  const uuid = await getSnapshotUuid(snap.id);

  // 4. 构造 payload：从 snap.tabs 提取 URLs
  const tabs = Array.isArray(snap.tabs) ? snap.tabs : [];
  const urls = tabs
    .map(t => typeof t.url === 'string' ? t.url : '')
    .filter(u => u);

  // 5. 调用 bridge-link 推送
  const result = await pushSnapshotToBridge({
    uuid,
    title: snap.title || '未命名上下文',
    summary: '',
    urls,
    tabCount: snap.tabCount || tabs.length,
    createdAt: snap.createdAt || Date.now(),
    projectId: cfg.selectedProjectId,
  });

  if (!result.success) {
    showToast('推送失败：' + (result.error || '未知错误'), 'error');
    return;
  }
  showToast('已推送到 Bridge 项目档案', 'success');
}

// 把单条快照导出为 .vcf 文件
function exportContextCard(snap) {
  const tabs = Array.isArray(snap.tabs) ? snap.tabs.map((t) => ({
    title: t.title || '',
    url: typeof t.url === 'string' ? t.url : ''
  })) : [];
  const cardData = {
    title: snap.title || '未命名上下文',
    tabs,
    tags: [],
    createdAt: snap.createdAt
  };
  const card = createCard('focus-snapshot', 'focus', cardData);
  const jsonStr = serializeCard(card);
  const blob = new Blob([jsonStr], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const safeName = String(snap.title || 'snapshot').replace(/[\\/:*?"<>|]/g, '_').slice(0, 60) || 'snapshot';
  const a = document.createElement('a');
  a.href = url;
  a.download = `focus-${safeName}${FILE_EXTENSION}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// 从 .vcf 文件导入 Context Card（仅接受 focus-snapshot 类型，恢复为快照）
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
  if (card.type !== 'focus-snapshot') {
    throw new Error(`仅支持导入 focus-snapshot 卡片，收到 ${card.type}`);
  }
  const data = card.data || {};
  const tabs = Array.isArray(data.tabs) ? data.tabs.filter((t) => t && typeof t.url === 'string' && /^https?:\/\//i.test(t.url)) : [];
  const createdAt = Number.isFinite(data.createdAt) ? data.createdAt : Date.now();
  // 复用现有 IMPORT_SNAPSHOTS 通道，保持与 settings 全量导入一致的写入路径
  const res = await chrome.runtime.sendMessage({
    action: 'IMPORT_SNAPSHOTS',
    data: {
      snapshots: [{
        title: typeof data.title === 'string' && data.title ? data.title : '导入的上下文',
        tabs,
        createdAt
      }]
    }
  });
  if (!res || !res.ok) {
    throw new Error(res?.error || '写入数据库失败');
  }
  showToast(`已导入 Context Card（${tabs.length} 个标签页）`, 'success');
  await reloadCurrentView();
}

// ============================================================
// 阶段2.1: Agent 主动建议展示
// ============================================================

// 加载 Agent 建议：取未读/最近建议 → 渲染 → 标记已读清零 badge
async function loadAgentSuggestions() {
  if (!agentSection || !agentSuggestionListEl) return;
  let agentState;
  try {
    agentState = await getAgentState();
  } catch (err) {
    return; // Agent 状态读取失败静默
  }
  const suggestions = (agentState.suggestions || []).slice(0, 3); // 最多展示 3 条
  if (suggestions.length === 0) {
    agentSection.hidden = true;
    return;
  }
  agentSuggestionListEl.innerHTML = '';
  for (const s of suggestions) {
    agentSuggestionListEl.appendChild(buildAgentSuggestionItem(s));
  }
  agentSection.hidden = false;

  // 展示后标记已读，清零 badge（后台异步执行，不阻塞渲染）
  markSuggestionsRead().catch(() => { /* 静默 */ });
}

// 构造单条建议卡片
function buildAgentSuggestionItem(s) {
  const li = document.createElement('li');
  li.className = 'agent-suggestion-item';
  if (s.acted) li.classList.add('is-acted');
  li.dataset.id = s.id;

  const titleDiv = document.createElement('div');
  titleDiv.className = 'agent-suggestion-title';
  titleDiv.textContent = s.title;

  const bodyDiv = document.createElement('div');
  bodyDiv.className = 'agent-suggestion-body';
  bodyDiv.textContent = s.body;

  const actionsDiv = document.createElement('div');
  actionsDiv.className = 'agent-suggestion-actions';

  // 采纳按钮：解析 action 执行对应操作
  if (s.action) {
    const actBtn = document.createElement('button');
    actBtn.type = 'button';
    actBtn.className = 'agent-act-btn';
    actBtn.textContent = '采纳';
    actBtn.setAttribute('aria-label', '采纳此建议');
    actBtn.addEventListener('click', () => {
      handleAgentAction(s).catch((err) => showToast('操作失败：' + err.message, 'error'));
    });
    actionsDiv.appendChild(actBtn);
  }

  // 忽略按钮
  const dismissBtn = document.createElement('button');
  dismissBtn.type = 'button';
  dismissBtn.textContent = '忽略';
  dismissBtn.setAttribute('aria-label', '忽略此建议');
  dismissBtn.addEventListener('click', () => {
    dismissSuggestion(s.id).catch(() => { /* 静默 */ });
    li.remove();
    // 若列表清空，隐藏整个区域
    if (agentSuggestionListEl.children.length === 0) {
      agentSection.hidden = true;
    }
  });
  actionsDiv.appendChild(dismissBtn);

  const metaDiv = document.createElement('div');
  metaDiv.className = 'agent-suggestion-meta';
  metaDiv.textContent = formatAgentTime(s.createdAt);

  li.appendChild(titleDiv);
  li.appendChild(bodyDiv);
  li.appendChild(actionsDiv);
  li.appendChild(metaDiv);
  return li;
}

// 解析建议 action 并执行对应操作
async function handleAgentAction(s) {
  // 标记已采纳
  await markSuggestionActed(s.id);
  // 更新 UI：添加 acted 样式，移除采纳按钮
  const li = agentSuggestionListEl.querySelector(`[data-id="${s.id}"]`);
  if (li) {
    li.classList.add('is-acted');
    const actBtn = li.querySelector('.agent-act-btn');
    if (actBtn) actBtn.remove();
  }

  if (!s.action) return;
  const [cmd, ...args] = s.action.split(':');
  if (cmd === 'restore_snapshot') {
    const id = Number(args[0]);
    if (Number.isFinite(id)) {
      await restoreContext(id);
    }
  } else if (cmd === 'save_context') {
    await saveContext();
  } else if (cmd === 'generate_resume') {
    // 打开 options 页面查看全部快照并生成回顾
    chrome.runtime.openOptionsPage();
  }
}

function formatAgentTime(timestamp) {
  if (!Number.isFinite(timestamp)) return '';
  const d = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return '刚刚';
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24 && d.toDateString() === now.toDateString()) return `${diffHour} 小时前`;
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(d);
}
