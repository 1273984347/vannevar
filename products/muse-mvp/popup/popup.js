// popup.js — Muse 捕捉灵感界面

import { showToast } from '../shared/toast.js';
import { recommendMoodTags } from '../core/ai.js';
import { getAgentState, markSuggestionsRead, dismissSuggestion, markSuggestionActed } from '../core/agent.js';
import { pushInspirationToBridge, getBridgeLinkConfig } from '../core/bridge-link.js';
import { getInspirationUuid } from '../core/sync.js';
import { hasAuthToken } from '../core/auth.js';

const pageTitleEl = document.getElementById('pageTitle');
const pageUrlEl = document.getElementById('pageUrl');
const selectedTextBox = document.getElementById('selectedTextBox');
const selectedTextEl = document.getElementById('selectedText');
const selectionHintEl = document.getElementById('selectionHint');
const noteInput = document.getElementById('noteInput');
const moodTagsEl = document.getElementById('moodTags');
const saveBtn = document.getElementById('saveBtn');
const statusEl = document.getElementById('status');
const recentSearchInput = document.getElementById('recentSearchInput');
const recentListEl = document.getElementById('recentList');
const recentEmptyEl = document.getElementById('recentEmpty');
const agentSection = document.getElementById('agentSection');
const agentSuggestionListEl = document.getElementById('agentSuggestionList');
// Phase 4.2: Media Session 检测提示元素
const mediaBoxEl = document.getElementById('mediaBox');
const mediaBoxTitleEl = document.getElementById('mediaBoxTitle');
const mediaBoxSubEl = document.getElementById('mediaBoxSub');

// P1: 防御页面元素缺失导致初始化崩溃
if (!pageTitleEl || !pageUrlEl || !noteInput || !moodTagsEl || !saveBtn || !statusEl) {
  console.error('[Muse] popup 必要元素缺失，无法初始化');
  throw new Error('popup 必要元素缺失');
}

const saveLabelEl = saveBtn.querySelector('.btn-label');

let captured = null;
let selectedMood = '';
let searchTimer = null;
const RECENT_LIMIT = 5;

// ---------- 保存成功 ✦ 闪现动效 ----------
function flashSaveButton() {
  if (!saveBtn) return;
  saveBtn.classList.remove('is-flashing');
  // 用双 rAF 替代强制 reflow 重启动画
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      saveBtn.classList.add('is-flashing');
    });
  });
  setTimeout(() => saveBtn.classList.remove('is-flashing'), 900);
}

async function init() {
  try {
    const moodRes = await chrome.runtime.sendMessage({ action: 'GET_MOODS' });
    if (!moodRes || !moodRes.ok) {
      moodTagsEl.innerHTML = '<div class="mood-error">加载标签失败</div>';
    } else {
      renderMoodTags(moodRes.moods);
    }

    const res = await chrome.runtime.sendMessage({ action: 'CAPTURE_INSPIRATION' });
    if (!res || !res.ok) {
      throw new Error(res?.error || '无法读取当前页面');
    }
    captured = res.item;
    pageTitleEl.textContent = captured.title;
    pageUrlEl.textContent = captured.url;
    if (captured.selectedText) {
      selectedTextEl.textContent = captured.selectedText;
      selectedTextBox.style.display = 'block';
      if (selectionHintEl) selectionHintEl.style.display = 'none';
    }

    // Phase 4.2: 如果检测到 Media Session 元数据，显示提示并预填表单
    if (captured.media && captured.media.title) {
      showMediaBox(captured.media);
      // 自动预填：标题改为媒体标题，备注预填 artist - album（用户可改）
      // 同步更新 captured.title 使保存时使用媒体标题（与界面显示一致）
      captured.title = captured.media.title;
      pageTitleEl.textContent = captured.media.title;
      const subLine = [captured.media.artist, captured.media.album].filter(Boolean).join(' - ');
      if (subLine && !noteInput.value.trim()) {
        noteInput.value = subLine;
      }
    }

    // P0-AI: 异步获取 AI 推荐氛围标签（不阻塞界面）
    loadAIRecommendedMoods(captured);
  } catch (err) {
    pageTitleEl.textContent = '无法读取当前页面';
    setStatus(err.message, 'error');
  }
}

// Phase 4.2: 显示媒体会话提示框
function showMediaBox(media) {
  if (!mediaBoxEl) return;
  mediaBoxTitleEl.textContent = media.title || '';
  const sub = [media.artist, media.album].filter(Boolean).join(' · ');
  mediaBoxSubEl.textContent = sub || '';
  // 根据 source 切换图标
  const iconEl = mediaBoxEl.querySelector('.media-box-icon');
  if (iconEl) {
    iconEl.textContent = media.source === 'video' ? '🎬' : (media.source === 'podcast' ? '🎙' : '🎵');
  }
  mediaBoxEl.style.display = 'flex';
}

// P0-AI: 异步加载 AI 推荐氛围标签
async function loadAIRecommendedMoods(pageInfo) {
  try {
    const tags = await recommendMoodTags(pageInfo);
    if (!tags || !tags.length) return; // 未配置 AI 或失败，静默降级

    // 找到 AI 推荐区域（若不存在则创建）
    let aiSection = document.getElementById('aiMoodSection');
    if (!aiSection) {
      aiSection = document.createElement('div');
      aiSection.id = 'aiMoodSection';
      aiSection.className = 'ai-mood-section';
      const label = document.createElement('div');
      label.className = 'ai-mood-label';
      label.textContent = '✨ AI 推荐';
      aiSection.appendChild(label);
      const tagsContainer = document.createElement('div');
      tagsContainer.className = 'ai-mood-tags';
      tagsContainer.id = 'aiMoodTags';
      aiSection.appendChild(tagsContainer);
      // 插入到现有标签区域之后
      moodTagsEl.parentElement.insertBefore(aiSection, moodTagsEl.nextSibling);
    }
    const tagsContainer = document.getElementById('aiMoodTags');
    tagsContainer.innerHTML = '';

    // 标签分层：前 3 主推，其余备选（避免选择瘫痪）
    const primaryTags = tags.slice(0, 3);
    const secondaryTags = tags.slice(3);

    const createAIMoodTag = (tag, tier) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `mood-tag ai-mood-tag ${tier}`;
      btn.textContent = tag;
      btn.dataset.mood = tag;
      btn.addEventListener('click', () => {
        selectedMood = selectedMood === tag ? '' : tag;
        updateMoodSelection();
        // 同步高亮 AI 推荐标签
        document.querySelectorAll('.ai-mood-tag').forEach(b => {
          b.classList.toggle('active', b.dataset.mood === selectedMood);
        });
      });
      return btn;
    };

    if (primaryTags.length) {
      const primaryRank = document.createElement('span');
      primaryRank.className = 'ai-mood-rank';
      primaryRank.textContent = '主推';
      tagsContainer.appendChild(primaryRank);
      for (const tag of primaryTags) {
        tagsContainer.appendChild(createAIMoodTag(tag, 'primary'));
      }
    }

    if (secondaryTags.length) {
      const secondaryRank = document.createElement('span');
      secondaryRank.className = 'ai-mood-rank secondary';
      secondaryRank.textContent = '备选';
      tagsContainer.appendChild(secondaryRank);
      for (const tag of secondaryTags) {
        tagsContainer.appendChild(createAIMoodTag(tag, 'secondary'));
      }
    }
    aiSection.style.display = 'block';
  } catch (err) {
    // 静默降级
  }
}

function renderMoodTags(moods) {
  moodTagsEl.innerHTML = '';
  for (const mood of moods) {
    const name = typeof mood === 'string' ? mood : mood.name;
    const color = typeof mood === 'string' ? null : mood.color;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'mood-tag';
    btn.textContent = name;
    btn.dataset.mood = name;
    if (color) {
      btn.style.setProperty('--mood-color', color);
    }
    btn.addEventListener('click', () => {
      selectedMood = selectedMood === name ? '' : name;
      updateMoodSelection();
    });
    moodTagsEl.appendChild(btn);
  }
}

function updateMoodSelection() {
  document.querySelectorAll('.mood-tag').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mood === selectedMood);
  });
}

async function saveInspiration() {
  if (!captured) {
    showToast('无法捕获当前页面', 'error');
    return;
  }
  setSaveLoading(true);
  setStatus('保存中…', '');
  let saved = false;
  try {
    const data = {
      title: captured.title,
      url: captured.url,
      note: noteInput.value.trim(),
      mood: selectedMood,
      selectedText: captured.selectedText
    };
    // Phase 4.2: 附带媒体元数据（如有）
    if (captured.media) {
      data.mediaType = captured.media.source || null;
      data.mediaArtist = captured.media.artist || null;
      data.mediaAlbum = captured.media.album || null;
      data.mediaArtworkUrl = captured.media.artworkUrl || null;
    }
    const res = await chrome.runtime.sendMessage({ action: 'SAVE_INSPIRATION', data });
    if (res && res.ok) {
      saved = true;
      setStatus('灵感已保存', 'success');
      showToast('灵感已保存 ✦', 'success');
      noteInput.value = '';
      selectedMood = '';
      updateMoodSelection();
      // 清空选中文字状态，避免重复保存同一段选中片段
      captured.selectedText = '';
      selectedTextEl.textContent = '';
      selectedTextBox.style.display = 'none';
      if (selectionHintEl) selectionHintEl.style.display = '';
      // 刷新最近灵感列表
      await loadRecentInspirations();
    } else {
      setStatus('保存失败：' + res?.error, 'error');
      showToast('保存失败', 'error');
    }
  } catch (err) {
    setStatus('保存失败：' + err.message, 'error');
  } finally {
    setSaveLoading(false);
    // 闪现动效在 loading 清除后触发，确保 ✦ 图标可见
    if (saved) flashSaveButton();
  }
}

// 保存按钮 loading 态：切换 is-loading / disabled / 文案 / spinner（参照 Focus popup.js）
function setSaveLoading(loading) {
  saveBtn.classList.toggle('is-loading', loading);
  saveBtn.disabled = loading;
  if (saveLabelEl) saveLabelEl.textContent = loading ? '保存中…' : '保存灵感';
}

function setStatus(text, type) {
  statusEl.textContent = text;
  statusEl.className = 'status' + (type ? ' ' + type : '');
}

// ---------- 最近灵感列表 ----------
async function loadRecentInspirations() {
  try {
    const res = await chrome.runtime.sendMessage({ action: 'GET_INSPIRATIONS', limit: RECENT_LIMIT });
    if (!res || !res.ok) return;
    renderRecentList(res.inspirations || [], false);
  } catch (err) {
    // 静默：历史列表加载失败不影响捕捉功能
// P2: 静默处理非关键扩展错误
  }
}

async function searchRecentInspirations(keyword) {
  try {
    const res = await chrome.runtime.sendMessage({ action: 'SEARCH_INSPIRATIONS', keyword });
    if (!res || !res.ok) return;
    renderRecentList(res.inspirations || [], true);
  } catch (err) {
// P2: 静默处理非关键扩展错误
  }
}

function renderRecentList(items, isSearching) {
  recentListEl.innerHTML = '';
  if (!items || items.length === 0) {
    recentListEl.style.display = 'none';
    recentEmptyEl.style.display = 'block';
    const emptyTextEl = recentEmptyEl.querySelector('.empty-text');
    if (emptyTextEl) {
      emptyTextEl.textContent = isSearching ? '没有匹配的灵感' : '还没有灵感记录';
    } else {
      recentEmptyEl.textContent = isSearching ? '没有匹配的灵感' : '还没有灵感记录';
    }
    return;
  }
  recentListEl.style.display = '';
  recentEmptyEl.style.display = 'none';

  // 使用 DocumentFragment 一次性 append，减少回流
  const frag = document.createDocumentFragment();
  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'recent-item';

    const mainDiv = document.createElement('div');
    mainDiv.className = 'recent-item-main';

    const titleEl = document.createElement('div');
    titleEl.className = 'recent-item-title';
    titleEl.textContent = item.title || '(无标题)';
    titleEl.setAttribute('title', item.title || '');

    const metaEl = document.createElement('div');
    metaEl.className = 'recent-item-meta';

    if (item.mood) {
      const moodSpan = document.createElement('span');
      moodSpan.className = 'recent-item-mood';
      moodSpan.textContent = item.mood;
      metaEl.appendChild(moodSpan);
    } else {
      const moodSpan = document.createElement('span');
      moodSpan.className = 'recent-item-mood unlabeled';
      moodSpan.textContent = '未标注';
      metaEl.appendChild(moodSpan);
    }

    const timeSpan = document.createElement('span');
    timeSpan.textContent = formatRecentTime(item.createdAt);
    metaEl.appendChild(timeSpan);

    mainDiv.appendChild(titleEl);
    mainDiv.appendChild(metaEl);

    // Phase 4.1：推送到 Bridge 项目档案按钮
    const pushBtn = document.createElement('button');
    pushBtn.type = 'button';
    pushBtn.className = 'recent-item-push';
    pushBtn.setAttribute('aria-label', '推送到 Bridge 项目档案');
    pushBtn.title = '推送到 Bridge';
    pushBtn.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M22 2L11 13"/>' +
      '<path d="M22 2l-7 20-4-9-9-4 20-7z"/>' +
      '</svg>';
    pushBtn.addEventListener('click', () => {
      try {
        pushInspirationToBridgeAction(item).catch((err) => showToast('推送失败：' + err.message, 'error'));
      } catch (err) {
        showToast('推送失败：' + err.message, 'error');
      }
    });

    li.appendChild(mainDiv);
    li.appendChild(pushBtn);
    frag.appendChild(li);
  }
  recentListEl.appendChild(frag);
}

// Phase 4.1：推送灵感到 Bridge 项目档案
// 流程：校验登录 → 读取/生成 UUID → 构造 payload → 调用 bridge-link → toast 反馈
async function pushInspirationToBridgeAction(item) {
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

  // 3. 读取或生成稳定的 inspiration UUID（与 sync.js 共享 UUID 映射表）
  const uuid = await getInspirationUuid(item.id);

  // 4. 调用 bridge-link 推送
  const result = await pushInspirationToBridge({
    uuid,
    title: item.title || '未命名灵感',
    url: item.url || '',
    note: item.note || '',
    mood: item.mood || '',
    createdAt: item.createdAt || Date.now(),
  }, cfg.selectedProjectId);

  if (!result.success) {
    showToast('推送失败：' + (result.error || '未知错误'), 'error');
    return;
  }
  showToast('已推送到 Bridge 项目档案', 'success');
}

function formatRecentTime(timestamp) {
  if (!Number.isFinite(timestamp)) return '';
  const d = new Date(timestamp);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  if (isToday) return `今天 ${time}`;
  return `${d.getMonth() + 1}/${d.getDate()} ${time}`;
}

// 搜索框：300ms 防抖，空关键词恢复显示最近 5 条
recentSearchInput.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    const keyword = recentSearchInput.value.trim();
    if (!keyword) {
      loadRecentInspirations();
    } else {
      searchRecentInspirations(keyword);
    }
  }, 300);
});

saveBtn.addEventListener('click', saveInspiration);

init();
loadRecentInspirations();
// 阶段2.1: 加载 Agent 建议并清零 badge
loadAgentSuggestions().catch(() => { /* Agent 加载失败静默，不影响主流程 */ });

// ============================================================
// 阶段2.1: Agent 主动建议展示
// ============================================================

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
// Muse 的 action 类型：filter_mood / filter_source / generate_resume / open_dashboard
async function handleAgentAction(s) {
  await markSuggestionActed(s.id);
  const li = agentSuggestionListEl.querySelector(`[data-id="${s.id}"]`);
  if (li) {
    li.classList.add('is-acted');
    const actBtn = li.querySelector('.agent-act-btn');
    if (actBtn) actBtn.remove();
  }

  if (!s.action) return;
  const [cmd, ...args] = s.action.split(':');
  // Muse 的所有 action 最终都导向打开 dashboard（可能带 query 参数）
  const dashboardUrl = new URL(chrome.runtime.getURL('dashboard/dashboard.html'));
  if (cmd === 'filter_mood') {
    dashboardUrl.searchParams.set('mood', args.join(':'));
  } else if (cmd === 'filter_source') {
    dashboardUrl.searchParams.set('source', args.join(':'));
  }
  // generate_resume / open_dashboard：直接打开 dashboard
  chrome.tabs.create({ url: dashboardUrl.href }).catch(() => {});
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
