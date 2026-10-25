// options.js — Muse 选项页交互（灵感导入/导出 + AI 配置 + 账号登录 + 氛围标签管理 + Toast 提示）

import { getAIConfig, setAIConfig, clearAIConfig, testAIConnection, getDemoMode, setDemoMode } from '../core/ai.js';
import { getAuthState, login, register, logout } from '../core/auth.js';
import { pushSync, pullSync, getSyncState } from '../core/sync.js';
import { listBridgeProjects, getBridgeLinkConfig, setBridgeLinkConfig } from '../core/bridge-link.js';
import { showToast } from '../shared/toast.js';

const exportBtn = document.getElementById('exportBtn');
const importBtn = document.getElementById('importBtn');
const importFile = document.getElementById('importFile');
const versionTag = document.getElementById('versionTag');

// 氛围管理相关元素
const moodListEl = document.getElementById('moodList');
const moodFormEl = document.getElementById('moodForm');
const moodNameInput = document.getElementById('moodNameInput');
const moodColorInput = document.getElementById('moodColorInput');

// P1: 防御页面元素缺失导致初始化崩溃
if (!exportBtn || !importBtn || !importFile) {
  console.error('[Muse] options 必要元素缺失，无法初始化');
  throw new Error('options 必要元素缺失');
}

exportBtn.addEventListener('click', handleExport);
importBtn.addEventListener('click', () => importFile.click());
importFile.addEventListener('change', handleImport);

if (moodFormEl) {
  moodFormEl.addEventListener('submit', handleAddMood);
}

// ---------- 阶段1.4: 账号登录 ----------
const authForm = document.getElementById('authForm');
const authEmailInput = document.getElementById('authEmailInput');
const authPasswordInput = document.getElementById('authPasswordInput');
const authDisplayNameInput = document.getElementById('authDisplayNameInput');
const authLoginBtn = document.getElementById('authLoginBtn');
const authRegisterBtn = document.getElementById('authRegisterBtn');
const authLogoutBtn = document.getElementById('authLogoutBtn');
const authStatusEl = document.getElementById('authStatus');
const authLoggedInEl = document.getElementById('authLoggedIn');
const authLoggedOutEl = document.getElementById('authLoggedOut');
const authEmailDisplay = document.getElementById('authEmail');

if (authForm) {
  authForm.addEventListener('submit', handleLogin);
  authRegisterBtn.addEventListener('click', handleRegister);
  authLogoutBtn.addEventListener('click', handleLogout);
  loadAuthState();
}

async function loadAuthState() {
  try {
    const state = await getAuthState();
    if (state.loggedIn) {
      authLoggedInEl.style.display = '';
      authLoggedOutEl.style.display = 'none';
      authEmailDisplay.textContent = state.email;
    } else {
      authLoggedInEl.style.display = 'none';
      authLoggedOutEl.style.display = '';
    }
  } catch (err) {
    console.error('[Muse] 读取登录状态失败：', err);
  }
}

function setAuthStatus(message, type) {
  if (!authStatusEl) return;
  authStatusEl.textContent = message;
  authStatusEl.dataset.type = type || 'muted';
}

async function handleLogin(evt) {
  if (evt) evt.preventDefault();
  const email = authEmailInput.value.trim();
  const password = authPasswordInput.value;
  if (!email || !password) {
    setAuthStatus('请填写邮箱和密码', 'error');
    return;
  }
  authLoginBtn.disabled = true;
  setAuthStatus('登录中…', 'muted');
  try {
    await login(email, password);
    authPasswordInput.value = '';
    setAuthStatus('', 'muted');
    showToast('登录成功', 'success');
    await loadAuthState();
  } catch (err) {
    setAuthStatus('登录失败：' + err.message, 'error');
    showToast('登录失败：' + err.message, 'error');
  } finally {
    authLoginBtn.disabled = false;
  }
}

async function handleRegister() {
  const email = authEmailInput.value.trim();
  const password = authPasswordInput.value;
  const displayName = authDisplayNameInput.value.trim();
  if (!email || !password) {
    setAuthStatus('请填写邮箱和密码', 'error');
    return;
  }
  authRegisterBtn.disabled = true;
  setAuthStatus('注册中…', 'muted');
  try {
    await register(email, password, displayName);
    authPasswordInput.value = '';
    setAuthStatus('', 'muted');
    showToast('注册成功，已自动登录', 'success');
    await loadAuthState();
  } catch (err) {
    setAuthStatus('注册失败：' + err.message, 'error');
    showToast('注册失败：' + err.message, 'error');
  } finally {
    authRegisterBtn.disabled = false;
  }
}

async function handleLogout() {
  authLogoutBtn.disabled = true;
  try {
    await logout();
    showToast('已登出', 'info');
    await loadAuthState();
  } catch (err) {
    showToast('登出失败：' + err.message, 'error');
  } finally {
    authLogoutBtn.disabled = false;
  }
}

// ---------- 阶段1.5: 数据同步 ----------
const syncPushBtn = document.getElementById('syncPushBtn');
const syncPullBtn = document.getElementById('syncPullBtn');
const syncStatusEl = document.getElementById('syncStatus');

if (syncPushBtn) {
  syncPushBtn.addEventListener('click', handleSyncPush);
  syncPullBtn.addEventListener('click', handleSyncPull);
  loadSyncState();
}

async function loadSyncState() {
  if (!syncStatusEl) return;
  try {
    const state = await getSyncState();
    if (!state.loggedIn) {
      syncStatusEl.textContent = '未登录，同步不可用';
      syncStatusEl.dataset.type = 'muted';
    } else if (state.lastSyncAt > 0) {
      const time = new Date(state.lastSyncAt).toLocaleString('zh-CN');
      syncStatusEl.textContent = `上次同步：${time}`;
      syncStatusEl.dataset.type = 'ok';
    } else {
      syncStatusEl.textContent = '尚未同步';
      syncStatusEl.dataset.type = 'muted';
    }
  } catch (err) {
    syncStatusEl.textContent = '读取同步状态失败';
    syncStatusEl.dataset.type = 'error';
  }
}

async function handleSyncPush() {
  setLoading(syncPushBtn, true);
  setSyncStatus('推送中…', 'muted');
  try {
    const result = await pushSync();
    setSyncStatus(`推送完成：新增 ${result.created}，更新 ${result.updated}，跳过 ${result.skipped}`, 'ok');
    showToast(`已推送 ${result.pushed} 条到云端`, 'success');
    await loadSyncState();
  } catch (err) {
    setSyncStatus('推送失败：' + err.message, 'error');
    showToast('推送失败：' + err.message, 'error');
  } finally {
    setLoading(syncPushBtn, false);
  }
}

async function handleSyncPull() {
  setLoading(syncPullBtn, true);
  setSyncStatus('拉取中…', 'muted');
  try {
    const result = await pullSync();
    setSyncStatus(`拉取完成：服务端 ${result.pulled} 条，本地新增灵感 ${result.importedInspirations} 条，氛围 ${result.importedMoods} 个`, 'ok');
    showToast(`已拉取 ${result.pulled} 条`, 'success');
    await loadSyncState();
  } catch (err) {
    setSyncStatus('拉取失败：' + err.message, 'error');
    showToast('拉取失败：' + err.message, 'error');
  } finally {
    setLoading(syncPullBtn, false);
  }
}

function setSyncStatus(message, type) {
  if (!syncStatusEl) return;
  syncStatusEl.textContent = message;
  syncStatusEl.dataset.type = type || 'muted';
}

// ---------- Phase 4.1: Bridge 项目关联 ----------
const bridgeLinkForm = document.getElementById('bridgeLinkForm');
const bridgeProjectSelect = document.getElementById('bridgeProjectSelect');
const bridgeRefreshBtn = document.getElementById('bridgeRefreshBtn');
const bridgeSaveBtn = document.getElementById('bridgeSaveBtn');
const bridgeLinkStatusEl = document.getElementById('bridgeLinkStatus');

if (bridgeLinkForm) {
  bridgeRefreshBtn.addEventListener('click', handleRefreshBridgeProjects);
  bridgeLinkForm.addEventListener('submit', handleSaveBridgeLink);
  loadBridgeLinkConfig();
}

async function loadBridgeLinkConfig() {
  if (!bridgeLinkStatusEl) return;
  try {
    const cfg = await getBridgeLinkConfig();
    if (cfg.selectedProjectId) {
      setBridgeLinkStatus(`当前关联项目 ID：${cfg.selectedProjectId}（点击「刷新项目列表」可加载完整项目名）`, 'ok');
    } else {
      setBridgeLinkStatus('尚未关联项目，请先登录后点击「刷新项目列表」', 'muted');
    }
  } catch (err) {
    setBridgeLinkStatus('读取关联配置失败：' + err.message, 'error');
  }
}

async function handleRefreshBridgeProjects() {
  setLoading(bridgeRefreshBtn, true);
  setBridgeLinkStatus('加载项目列表中…', 'muted');
  try {
    const projects = await listBridgeProjects();
    if (!projects || projects.length === 0) {
      bridgeProjectSelect.innerHTML = '<option value="">（暂无项目，请先在 Bridge 后台创建项目）</option>';
      bridgeProjectSelect.disabled = true;
      setBridgeLinkStatus('Bridge 上暂无项目', 'muted');
      return;
    }
    const cfg = await getBridgeLinkConfig();
    const optionsHtml = projects.map(p => {
      const id = String(p.id);
      const name = String(p.name || `项目 ${id}`).replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const selected = id === String(cfg.selectedProjectId) ? ' selected' : '';
      return `<option value="${id}"${selected}>${name}</option>`;
    }).join('');
    bridgeProjectSelect.innerHTML = optionsHtml;
    bridgeProjectSelect.disabled = false;
    setBridgeLinkStatus(`已加载 ${projects.length} 个项目，请选择目标项目`, 'ok');
  } catch (err) {
    setBridgeLinkStatus('加载项目列表失败：' + err.message, 'error');
    showToast('加载 Bridge 项目失败：' + err.message, 'error');
  } finally {
    setLoading(bridgeRefreshBtn, false);
  }
}

async function handleSaveBridgeLink(evt) {
  if (evt) evt.preventDefault();
  const projectId = bridgeProjectSelect.value;
  if (!projectId) {
    setBridgeLinkStatus('请先选择目标项目', 'error');
    showToast('请先选择目标项目', 'error');
    return;
  }
  setLoading(bridgeSaveBtn, true);
  try {
    await setBridgeLinkConfig({ selectedProjectId: projectId });
    setBridgeLinkStatus(`已保存关联，目标项目 ID：${projectId}`, 'ok');
    showToast('Bridge 项目关联已保存', 'success');
  } catch (err) {
    setBridgeLinkStatus('保存关联失败：' + err.message, 'error');
    showToast('保存关联失败：' + err.message, 'error');
  } finally {
    setLoading(bridgeSaveBtn, false);
  }
}

function setBridgeLinkStatus(message, type) {
  if (!bridgeLinkStatusEl) return;
  bridgeLinkStatusEl.textContent = message;
  bridgeLinkStatusEl.dataset.type = type || 'muted';
}

// ---------- AI 配置 ----------
const aiForm = document.getElementById('aiForm');
const aiBaseUrlInput = document.getElementById('aiBaseUrl');
const aiApiKeyInput = document.getElementById('aiApiKey');
const aiModelInput = document.getElementById('aiModel');
const aiSaveBtn = document.getElementById('aiSaveBtn');
const aiTestBtn = document.getElementById('aiTestBtn');
const aiClearBtn = document.getElementById('aiClearBtn');
const aiStatusEl = document.getElementById('aiStatus');

if (aiForm) {
  aiForm.addEventListener('submit', handleSaveAI);
  aiTestBtn.addEventListener('click', handleTestAI);
  aiClearBtn.addEventListener('click', handleClearAI);
  loadAIConfig();
}

// ---------- 演示模式 ----------
const demoModeToggle = document.getElementById('demoModeToggle');
if (demoModeToggle) {
  loadDemoMode();
  demoModeToggle.addEventListener('change', handleDemoModeToggle);
}

async function loadDemoMode() {
  try {
    demoModeToggle.checked = await getDemoMode();
  } catch (err) {
    console.error('[Muse] 读取演示模式状态失败：', err);
  }
}

async function handleDemoModeToggle(evt) {
  const enabled = evt.target.checked;
  try {
    await setDemoMode(enabled);
    showToast(enabled ? '演示模式已开启' : '演示模式已关闭', enabled ? 'success' : 'info');
  } catch (err) {
    showToast('保存演示模式失败：' + err.message, 'error');
    demoModeToggle.checked = !enabled;
  }
}

async function loadAIConfig() {
  try {
    const config = await getAIConfig();
    if (config) {
      aiBaseUrlInput.value = config.baseUrl || '';
      aiApiKeyInput.value = config.apiKey || '';
      aiModelInput.value = config.model || '';
      setAIStatus(config.apiKey && config.baseUrl ? '已配置 AI 增强。' : '未配置，将使用规则引擎。', config.apiKey && config.baseUrl ? 'ok' : 'muted');
    } else {
      setAIStatus('未配置，将使用规则引擎。', 'muted');
    }
  } catch (err) {
    setAIStatus('读取配置失败：' + err.message, 'error');
  }
}

async function handleSaveAI(evt) {
  if (evt) evt.preventDefault();
  const baseUrl = aiBaseUrlInput.value.trim();
  const apiKey = aiApiKeyInput.value.trim();
  const model = aiModelInput.value.trim();
  if (!baseUrl || !apiKey) {
    setAIStatus('请填写 API Base URL 和 API Key。', 'error');
    showToast('请填写 API Base URL 和 API Key', 'error');
    return;
  }
  try {
    new URL(baseUrl);
  } catch (_) {
    setAIStatus('API Base URL 格式不正确。', 'error');
    showToast('API Base URL 格式不正确', 'error');
    return;
  }
  setLoading(aiSaveBtn, true);
  try {
    await setAIConfig({ baseUrl, apiKey, model: model || 'gpt-4o-mini' });
    setAIStatus('配置已保存。', 'ok');
    showToast('AI 配置已保存', 'success');
  } catch (err) {
    setAIStatus('保存失败：' + err.message, 'error');
    showToast('保存失败：' + err.message, 'error');
  } finally {
    setLoading(aiSaveBtn, false);
  }
}

async function handleTestAI() {
  const baseUrl = aiBaseUrlInput.value.trim();
  const apiKey = aiApiKeyInput.value.trim();
  const model = aiModelInput.value.trim();
  if (!baseUrl || !apiKey) {
    setAIStatus('请先填写并保存 API Base URL 和 API Key。', 'error');
    showToast('请先填写 API Base URL 和 API Key', 'error');
    return;
  }
  setLoading(aiTestBtn, true);
  setAIStatus('测试连接中…', 'muted');
  try {
    await testAIConnection({ baseUrl, apiKey, model: model || 'gpt-4o-mini' });
    setAIStatus('连接成功！AI 增强可用。', 'ok');
    showToast('AI 连接成功', 'success');
  } catch (err) {
    setAIStatus('连接失败：' + err.message, 'error');
    showToast('连接失败：' + err.message, 'error');
  } finally {
    setLoading(aiTestBtn, false);
  }
}

async function handleClearAI() {
  setLoading(aiClearBtn, true);
  try {
    await clearAIConfig();
    aiBaseUrlInput.value = '';
    aiApiKeyInput.value = '';
    aiModelInput.value = '';
    setAIStatus('已清除配置，将使用规则引擎。', 'muted');
    showToast('已清除 AI 配置', 'info');
  } catch (err) {
    setAIStatus('清除失败：' + err.message, 'error');
    showToast('清除失败：' + err.message, 'error');
  } finally {
    setLoading(aiClearBtn, false);
  }
}

function setAIStatus(message, type) {
  if (!aiStatusEl) return;
  aiStatusEl.textContent = message;
  aiStatusEl.dataset.type = type || 'muted';
}

// 写入 manifest 版本号（如果可用）
if (chrome.runtime?.getManifest) {
  try {
    const ver = chrome.runtime.getManifest().version;
    if (ver) versionTag.textContent = `v${ver}`;
  } catch (_) { /* 忽略，保持默认 0.1.0 */ }
}

// 页面加载时拉取氛围列表
loadMoods();

// ---------- 导出 ----------
async function handleExport() {
  setLoading(exportBtn, true);
  try {
    const res = await chrome.runtime.sendMessage({ action: 'EXPORT_INSPIRATIONS' });
    if (!res || !res.ok) {
      showToast('导出失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    const data = res.data;
    const count = data?.count ?? (Array.isArray(data?.inspirations) ? data.inspirations.length : 0);
    if (count === 0) {
      showToast('暂无可导出的灵感', 'info');
      return;
    }
    const json = JSON.stringify(data, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const filename = `vannevar-muse-export-${formatDate(new Date())}.json`;
    triggerDownload(url, filename);
    // 延迟释放 blob URL：浏览器下载启动是异步的，立即 revoke 在某些环境下
    // 会导致下载链接失效（特别是 Firefox / 较旧 Chrome）。1s 缓冲足够下载启动。
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast(`已导出 ${count} 条灵感`, 'success');
  } catch (err) {
    showToast('导出失败：' + err.message, 'error');
  } finally {
    setLoading(exportBtn, false);
  }
}

// ---------- 导入 ----------
async function handleImport(evt) {
  const file = evt.target.files && evt.target.files[0];
  // 重置 input.value 以便相同文件可重复选择
  evt.target.value = '';
  if (!file) return;

  setLoading(importBtn, true);
  try {
    const text = await readFileAsText(file);
    let data;
    try {
      data = JSON.parse(text);
    } catch (_) {
      showToast('文件格式错误：不是有效的 JSON', 'error');
      return;
    }
    if (!data || !Array.isArray(data.inspirations)) {
      showToast('文件格式错误：缺少 inspirations 字段', 'error');
      return;
    }

    const res = await chrome.runtime.sendMessage({ action: 'IMPORT_INSPIRATIONS', data });
    if (!res || !res.ok) {
      showToast('导入失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    const imported = Number(res.imported) || 0;
    showToast(`已导入 ${imported} 条灵感`, 'success');
  } catch (err) {
    showToast('导入失败：' + err.message, 'error');
  } finally {
    setLoading(importBtn, false);
  }
}

// ---------- 工具函数 ----------
function setLoading(btn, loading) {
  btn.classList.toggle('is-loading', loading);
  btn.disabled = loading;
}

function triggerDownload(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function readFileAsText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('文件读取失败'));
    reader.readAsText(file);
  });
}

function formatDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}${m}${day}`;
}

// ---------- 阶段2.8: 端侧本地模型 ----------
import {
  getLocalModelConfig,
  setLocalModelConfig,
  isWebGPUAvailable,
  loadLocalModel,
  unloadLocalModel,
  generateLocalSuggestion,
} from '../core/local-model.js';

const localModelToggle = document.getElementById('localModelToggle');
const localModelSelect = document.getElementById('localModelSelect');
const localModelLoadBtn = document.getElementById('localModelLoadBtn');
const localModelTestBtn = document.getElementById('localModelTestBtn');
const localModelUnloadBtn = document.getElementById('localModelUnloadBtn');
const localModelProgress = document.getElementById('localModelProgress');
const localModelStatus = document.getElementById('localModelStatus');
const webGPUStatus = document.getElementById('webGPUStatus');

if (localModelToggle) {
  localModelToggle.addEventListener('change', handleLocalModelToggle);
  localModelLoadBtn.addEventListener('click', handleLocalModelLoad);
  localModelTestBtn.addEventListener('click', handleLocalModelTest);
  localModelUnloadBtn.addEventListener('click', handleLocalModelUnload);
  localModelSelect.addEventListener('change', handleLocalModelSelectChange);
  loadLocalModelState();
}

async function loadLocalModelState() {
  try {
    if (isWebGPUAvailable()) {
      webGPUStatus.textContent = 'WebGPU 可用 ✓';
      webGPUStatus.style.color = 'var(--v-success, #10b981)';
    } else {
      webGPUStatus.textContent = 'WebGPU 不可用（需 Chrome 113+）';
      webGPUStatus.style.color = 'var(--v-danger, #ef4444)';
      localModelToggle.disabled = true;
    }
    const config = await getLocalModelConfig();
    localModelToggle.checked = !!config.enabled;
    if (config.modelId) {
      localModelSelect.value = config.modelId;
    }
  } catch (err) {
    localModelStatus.textContent = '配置加载失败：' + err.message;
  }
}

async function handleLocalModelToggle(e) {
  const enabled = e.target.checked;
  try {
    const config = await getLocalModelConfig();
    config.enabled = enabled;
    await setLocalModelConfig(config);
    localModelStatus.textContent = enabled ? '已启用本地模型' : '已禁用本地模型';
    showToast(enabled ? '本地模型已启用' : '本地模型已禁用', 'success');
  } catch (err) {
    localModelStatus.textContent = '保存失败：' + err.message;
    e.target.checked = !enabled;
  }
}

async function handleLocalModelSelectChange(e) {
  const config = await getLocalModelConfig();
  config.modelId = e.target.value;
  await setLocalModelConfig(config);
  await unloadLocalModel();
  localModelStatus.textContent = '模型已切换，请重新加载';
}

async function handleLocalModelLoad() {
  if (!isWebGPUAvailable()) {
    showToast('WebGPU 不可用，无法加载本地模型', 'error');
    return;
  }
  setLoading(localModelLoadBtn, true);
  localModelProgress.style.display = 'block';
  localModelProgress.textContent = '加载中 0%…';
  try {
    await loadLocalModel((progress) => {
      localModelProgress.textContent = `加载中 ${Math.round(progress * 100)}%…`;
    });
    localModelProgress.textContent = '加载完成 ✓';
    localModelStatus.textContent = '本地模型已就绪，可点击"测试推理"';
    showToast('本地模型加载完成', 'success');
  } catch (err) {
    localModelStatus.textContent = '加载失败：' + err.message;
    showToast('加载失败：' + err.message, 'error');
    localModelProgress.style.display = 'none';
  } finally {
    setLoading(localModelLoadBtn, false);
  }
}

async function handleLocalModelTest() {
  setLoading(localModelTestBtn, true);
  localModelStatus.textContent = '推理中…请保持窗口打开';
  try {
    const result = await generateLocalSuggestion('近期灵感聚焦于写作技法与东方美学，请给出整理建议。');
    if (result) {
      localModelStatus.textContent = '推理结果：' + result;
      showToast('本地推理成功', 'success');
    } else {
      localModelStatus.textContent = '推理失败，请先加载模型';
      showToast('推理失败', 'error');
    }
  } catch (err) {
    localModelStatus.textContent = '推理失败：' + err.message;
    showToast('推理失败：' + err.message, 'error');
  } finally {
    setLoading(localModelTestBtn, false);
  }
}

async function handleLocalModelUnload() {
  await unloadLocalModel();
  localModelProgress.style.display = 'none';
  localModelStatus.textContent = '已释放显存';
  showToast('显存已释放', 'success');
}

// ---------- 氛围标签管理 ----------
async function loadMoods() {
  if (!moodListEl) return;
  moodListEl.innerHTML = '<div class="mood-list-loading">加载中…</div>';
  try {
    const res = await chrome.runtime.sendMessage({ action: 'GET_MOODS' });
    if (!res || !res.ok) {
      moodListEl.innerHTML = '<div class="mood-list-empty">加载失败</div>';
      return;
    }
    renderMoodList(res.moods || []);
  } catch (err) {
    moodListEl.innerHTML = '<div class="mood-list-empty">加载失败：' + escapeText(err.message) + '</div>';
  }
}

function renderMoodList(moods) {
  moodListEl.innerHTML = '';
  if (!moods || moods.length === 0) {
    moodListEl.innerHTML = '<div class="mood-list-empty">暂无氛围标签，添加第一个吧</div>';
    return;
  }
  for (const mood of moods) {
    const row = document.createElement('div');
    row.className = 'mood-row';
    row.dataset.id = mood.id;

    const swatch = document.createElement('span');
    swatch.className = 'mood-row-swatch';
    swatch.style.background = mood.color || '#c2410c';
    swatch.setAttribute('aria-hidden', 'true');

    const name = document.createElement('span');
    name.className = 'mood-row-name';
    name.textContent = mood.name;

    const colorText = document.createElement('span');
    colorText.className = 'mood-row-color';
    colorText.textContent = mood.color || '';

    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'mood-row-edit';
    editBtn.textContent = '编辑';
    editBtn.dataset.id = mood.id;
    editBtn.addEventListener('click', () => enterMoodEditMode(row, mood));

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'mood-row-delete';
    delBtn.textContent = '删除';
    delBtn.dataset.id = mood.id;
    delBtn.dataset.name = mood.name;
    // 删除确认状态变化通过 aria-live 通知辅助技术
    delBtn.setAttribute('aria-live', 'polite');
    delBtn.addEventListener('click', () => handleDeleteMood(delBtn, mood));

    row.appendChild(swatch);
    row.appendChild(name);
    row.appendChild(colorText);
    row.appendChild(editBtn);
    row.appendChild(delBtn);
    moodListEl.appendChild(row);
  }
}

// 进入氛围编辑模式：名称与颜色变为可编辑 input，回车保存调用 UPDATE_MOOD，Esc 取消
function enterMoodEditMode(row, mood) {
  if (row.classList.contains('is-editing')) return;
  row.classList.add('is-editing');

  const nameSpan = row.querySelector('.mood-row-name');
  const colorSpan = row.querySelector('.mood-row-color');
  const swatch = row.querySelector('.mood-row-swatch');
  const editBtn = row.querySelector('.mood-row-edit');
  const delBtn = row.querySelector('.mood-row-delete');

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'mood-row-name-input';
  nameInput.value = mood.name;
  nameInput.maxLength = 20;
  nameInput.setAttribute('aria-label', '编辑氛围名称');
  nameInput.setAttribute('title', '回车保存 · Esc 取消');

  const colorInput = document.createElement('input');
  colorInput.type = 'color';
  colorInput.className = 'mood-row-color-input';
  colorInput.value = mood.color || '#c2410c';
  colorInput.setAttribute('aria-label', '编辑氛围颜色');

  // 颜色实时同步到色块
  const syncSwatch = () => { swatch.style.background = colorInput.value; };
  colorInput.addEventListener('input', syncSwatch);

  nameSpan.replaceWith(nameInput);
  colorSpan.replaceWith(colorInput);

  // 隐藏编辑/删除按钮，显示保存/取消
  editBtn.style.display = 'none';
  delBtn.style.display = 'none';

  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'mood-row-save';
  saveBtn.textContent = '保存';

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'mood-row-cancel';
  cancelBtn.textContent = '取消';

  row.appendChild(saveBtn);
  row.appendChild(cancelBtn);

  nameInput.focus();
  nameInput.select();

  let finished = false;
  const restoreView = () => {
    if (finished) return;
    finished = true;
    row.classList.remove('is-editing');
    nameInput.replaceWith(nameSpan);
    colorInput.replaceWith(colorSpan);
    swatch.style.background = mood.color || '#c2410c';
    saveBtn.remove();
    cancelBtn.remove();
    editBtn.style.display = '';
    delBtn.style.display = '';
  };

  const finish = async (commit) => {
    if (finished) return;
    const newName = nameInput.value.trim();
    const newColor = colorInput.value;

    if (!commit) {
      restoreView();
      return;
    }
    if (!newName) {
      showToast('氛围名称不能为空', 'error');
      nameInput.focus();
      return;
    }
    if (newName === mood.name && newColor === mood.color) {
      restoreView();
      return;
    }
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';
    try {
      const res = await chrome.runtime.sendMessage({
        action: 'UPDATE_MOOD',
        id: mood.id,
        data: { name: newName, color: newColor }
      });
      if (!res || !res.ok) {
        showToast('更新失败：' + (res?.error || '未知错误'), 'error');
        saveBtn.disabled = false;
        saveBtn.textContent = '保存';
        return;
      }
      showToast('氛围已更新', 'success');
      await loadMoods();
    } catch (err) {
      showToast('更新失败：' + err.message, 'error');
      saveBtn.disabled = false;
      saveBtn.textContent = '保存';
    }
  };

  nameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      finish(true);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      finish(false);
    }
  });
  colorInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      finish(true);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      finish(false);
    }
  });
  saveBtn.addEventListener('click', () => finish(true));
  cancelBtn.addEventListener('click', () => finish(false));
  // 不再监听 blur 自动保存：仅 Enter / 保存按钮提交，Esc / 取消按钮放弃
}

async function handleAddMood(evt) {
  evt.preventDefault();
  const name = moodNameInput.value.trim();
  const color = moodColorInput.value || '#c2410c';
  if (!name) {
    showToast('请输入氛围名称', 'error');
    return;
  }
  const submitBtn = moodFormEl.querySelector('button[type="submit"]');
  setLoading(submitBtn, true);
  try {
    const res = await chrome.runtime.sendMessage({ action: 'ADD_MOOD', name, color });
    if (!res || !res.ok) {
      showToast('添加失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    showToast(`已添加氛围「${name}」`, 'success');
    moodNameInput.value = '';
    await loadMoods();
  } catch (err) {
    showToast('添加失败：' + err.message, 'error');
  } finally {
    setLoading(submitBtn, false);
  }
}

async function handleDeleteMood(btn, mood) {
  if (!confirm(`确定要删除氛围「${mood.name}」吗？`)) {
    return;
  }
  btn.disabled = true;
  btn.textContent = '删除中…';
  try {
    const res = await chrome.runtime.sendMessage({ action: 'DELETE_MOOD', id: mood.id });
    if (!res || !res.ok) {
      showToast('删除失败：' + (res?.error || '未知错误'), 'error');
      btn.textContent = '删除';
      btn.disabled = false;
      return;
    }
    showToast(`已删除氛围「${mood.name}」`, 'success');
    await loadMoods();
  } catch (err) {
    showToast('删除失败：' + err.message, 'error');
    btn.textContent = '删除';
    btn.disabled = false;
  }
}

function escapeText(text) {
  const div = document.createElement('div');
  div.textContent = text == null ? '' : String(text);
  return div.innerHTML;
}
