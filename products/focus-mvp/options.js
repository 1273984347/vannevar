// options.js — 选项页交互（数据导入/导出 + AI 配置 + 账号登录 + Toast 提示）

import { showToast } from './shared/toast.js';
import { getAIConfig, setAIConfig, clearAIConfig, testAIConnection, getDemoMode, setDemoMode } from './ai.js';
import { getAuthState, login, register, logout } from './auth.js';
import { pushSync, pullSync, getSyncState } from './sync.js';
import { listBridgeProjects, getBridgeLinkConfig, setBridgeLinkConfig } from './bridge-link.js';
import { getTaskWatcherEnabled, setTaskWatcherEnabled } from './task-watcher.js';
import {
  getCryptoState,
  enableCrypto,
  disableCrypto,
  unlockCrypto,
  lockCrypto,
  adoptRemoteProfile,
} from './crypto.js';
import { fetchRemoteProfile, pushRemoteProfile } from './crypto-profile.js';

const exportBtn = document.getElementById('exportBtn');
const importBtn = document.getElementById('importBtn');
const importFile = document.getElementById('importFile');
const versionTag = document.getElementById('versionTag');
const clearAllDataBtn = document.getElementById('clearAllDataBtn');

// P1: 防御页面元素缺失导致初始化崩溃
if (!exportBtn || !importBtn || !importFile) {
  console.error('[Focus] options 必要元素缺失，无法初始化');
  throw new Error('options 必要元素缺失');
}

exportBtn.addEventListener('click', handleExport);
importBtn.addEventListener('click', () => importFile.click());
importFile.addEventListener('change', handleImport);
if (clearAllDataBtn) {
  clearAllDataBtn.addEventListener('click', handleClearAllData);
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
    console.error('[Focus] 读取登录状态失败：', err);
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
    setSyncStatus(`拉取完成：服务端 ${result.pulled} 条，本地新增 ${result.imported} 条`, 'ok');
    showToast(`已拉取 ${result.pulled} 条，新增 ${result.imported} 条`, 'success');
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

// ---------- Phase 4.4: E2E 加密同步管理 ----------
const cryptoStatusEl = document.getElementById('cryptoStatus');
const cryptoEnableForm = document.getElementById('cryptoEnableForm');
const cryptoManagePanel = document.getElementById('cryptoManagePanel');
const cryptoPassphraseInput = document.getElementById('cryptoPassphraseInput');
const cryptoPassphraseConfirmInput = document.getElementById('cryptoPassphraseConfirmInput');
const cryptoEnableBtn = document.getElementById('cryptoEnableBtn');
const cryptoUnlockForm = document.getElementById('cryptoUnlockForm');
const cryptoUnlockPassphraseInput = document.getElementById('cryptoUnlockPassphraseInput');
const cryptoUnlockBtn = document.getElementById('cryptoUnlockBtn');
const cryptoLockBtn = document.getElementById('cryptoLockBtn');
const cryptoDisableBtn = document.getElementById('cryptoDisableBtn');

if (cryptoEnableForm && cryptoManagePanel) {
  cryptoEnableForm.addEventListener('submit', handleCryptoEnable);
  cryptoUnlockForm.addEventListener('submit', handleCryptoUnlock);
  cryptoLockBtn.addEventListener('click', handleCryptoLock);
  cryptoDisableBtn.addEventListener('click', handleCryptoDisable);
  loadCryptoState();
}

async function loadCryptoState(suffix = '') {
  if (!cryptoStatusEl) return;
  try {
    const state = await getCryptoState();
    if (!state.enabled) {
      // 未启用：显示启用表单，隐藏管理面板
      cryptoEnableForm.style.display = '';
      cryptoManagePanel.style.display = 'none';
      setCryptoStatus('未启用加密 — 启用后所有新推送的快照将以密文形式上传到 Bridge', 'muted');
    } else if (state.ready) {
      // 已启用且已解锁：隐藏启用表单，显示管理面板（解锁表单可隐藏）
      cryptoEnableForm.style.display = 'none';
      cryptoManagePanel.style.display = '';
      cryptoUnlockForm.style.display = 'none';
      setCryptoStatus(`已启用并已解锁 · PBKDF2 迭代 ${state.iterations.toLocaleString()} 次 · salt 指纹 ${state.saltFingerprint}${suffix}`, 'ok');
    } else {
      // 已启用但未解锁（SW 重启后）：显示解锁表单
      cryptoEnableForm.style.display = 'none';
      cryptoManagePanel.style.display = '';
      cryptoUnlockForm.style.display = '';
      setCryptoStatus(`已启用但未解锁 · 请输入 passphrase 解锁后才能 push/pull 加密快照 · salt 指纹 ${state.saltFingerprint}`, 'muted');
    }
  } catch (err) {
    setCryptoStatus('读取加密状态失败：' + err.message, 'error');
  }
}

function setCryptoStatus(message, type) {
  if (!cryptoStatusEl) return;
  cryptoStatusEl.textContent = message;
  cryptoStatusEl.dataset.type = type || 'muted';
}

async function handleCryptoEnable(evt) {
  if (evt) evt.preventDefault();
  const passphrase = cryptoPassphraseInput.value;
  const passphraseConfirm = cryptoPassphraseConfirmInput.value;
  if (passphrase.length < 8) {
    setCryptoStatus('passphrase 至少 8 位', 'error');
    return;
  }
  if (passphrase !== passphraseConfirm) {
    setCryptoStatus('两次输入的 passphrase 不一致', 'error');
    return;
  }
  cryptoEnableBtn.disabled = true;
  setCryptoStatus('启用中…（PBKDF2 派生 key，约 1-2 秒）', 'muted');
  try {
    // 跨设备：若 Bridge 上已有加密档案，复用其 salt，使本设备与其它设备派生同一把密钥。
    // 云端不可达时降级为本地新建（不影响本机加密）。
    let remoteProfile = null;
    try {
      const state = await fetchRemoteProfile();
      // 只有确认云端「确实有档案」才复用其 salt。
      // 状态未知（网络/鉴权失败）时不能假设云端为空 —— 否则随后的上传会覆盖另一台设备的档案。
      if (state && state.status === 'ok') remoteProfile = state.profile;
    } catch (_) { /* 云端不可达 → 本地新建（且不会上传） */ }

    const result = await enableCrypto(passphrase, remoteProfile ? { remoteProfile } : {});
    // Phase 4.4: 同步将 SW 的 crypto.js 模块解锁（auto-sync 才能用上密钥）
    // 启用时 enableCrypto 内部已经把 key 装到 options 页的 crypto.js 内存；
    // SW 模块是独立实例，需通过 sendMessage 转发 passphrase 让 SW 自己派生 key。
    // B-P2-5/D-P2-2 修复：检查 SW 返回的 {ok, unlocked} 字段，失败时不显示"启用成功"toast
    let swUnlockOk = true;
    try {
      const resp = await chrome.runtime.sendMessage({ action: 'CRYPTO_UNLOCK', passphrase });
      if (!resp || !resp.ok || !resp.unlocked) {
        swUnlockOk = false;
        console.warn('[Options] SW unlock 返回失败:', resp);
      }
    } catch (e) {
      swUnlockOk = false;
      console.warn('[Options] 通知 SW 解锁失败（自动同步将无法加密推送）:', e);
    }
    // 把加密档案同步到 Bridge（跨设备解密的前提）。失败不影响本机加密。
    let profileSynced = false;
    try {
      const pushResult = await pushRemoteProfile();
      profileSynced = !!(pushResult && pushResult.synced);
    } catch (_) { /* 档案同步失败不影响本机加密 */ }

    if (swUnlockOk) {
      const crossDevice = result.reusedRemoteSalt
        ? ' · 已采用 Bridge 上的加密档案（可解密其它设备的数据）'
        : '';
      const profileNote = profileSynced ? '' : ' · ⚠ 加密档案未同步到 Bridge，跨设备解密暂不可用';
      setCryptoStatus(`已启用 E2E 加密 · salt 指纹 ${result.saltFingerprint}${crossDevice}${profileNote}`, profileSynced ? 'ok' : 'muted');
      showToast('E2E 加密已启用', 'success');
      cryptoPassphraseInput.value = '';
      cryptoPassphraseConfirmInput.value = '';
      // 手动切换 UI 到管理面板（不走 loadCryptoState，避免覆盖跨设备/档案同步提示）
      cryptoEnableForm.style.display = 'none';
      cryptoManagePanel.style.display = '';
      cryptoUnlockForm.style.display = 'none';
    } else {
      // options 页已启用（key 已装载），但 SW 未解锁——auto-sync 无法加密推送
      // R1b-R2 修复：不调用 loadCryptoState——它会读取 options 页状态（ready=true）覆盖 error 告警
      setCryptoStatus(`已启用 E2E 加密 · salt 指纹 ${result.saltFingerprint} · ⚠ SW 未解锁，自动同步将跳过加密推送，请到扩展弹窗或重启浏览器后重新解锁`, 'error');
      showToast('E2E 加密已启用，但 SW 解锁失败——自动同步将暂停', 'error');
      cryptoPassphraseInput.value = '';
      cryptoPassphraseConfirmInput.value = '';
      // 手动切换 UI 到管理面板（不通过 loadCryptoState，避免覆盖 error 状态）
      cryptoEnableForm.style.display = 'none';
      cryptoManagePanel.style.display = '';
      cryptoUnlockForm.style.display = '';
    }
  } catch (err) {
    setCryptoStatus('启用失败：' + err.message, 'error');
    showToast('启用失败：' + err.message, 'error');
  } finally {
    cryptoEnableBtn.disabled = false;
  }
}

async function handleCryptoUnlock(evt) {
  if (evt) evt.preventDefault();
  const passphrase = cryptoUnlockPassphraseInput.value;
  if (passphrase.length < 8) {
    setCryptoStatus('passphrase 至少 8 位', 'error');
    return;
  }
  cryptoUnlockBtn.disabled = true;
  setCryptoStatus('解锁中…', 'muted');
  try {
    const state = await getCryptoState();
    let ok = false;
    let adoptedRemote = false;

    if (state.enabled) {
      // 本机已有加密档案：用本地 salt 派生
      ok = await unlockCrypto(passphrase);
    } else {
      // 跨设备首次使用：本机没有加密档案，尝试采用 Bridge 上的档案（设备 B）
      const state = await fetchRemoteProfile();
      if (state.status === 'error') {
        setCryptoStatus('无法连接 Bridge 获取加密档案，请检查网络后重试', 'error');
        showToast('无法连接 Bridge', 'error');
        cryptoUnlockBtn.disabled = false;
        return;
      }
      if (state.status === 'absent') {
        setCryptoStatus('本机未启用加密，且 Bridge 上没有加密档案 —— 请先点「启用加密」', 'error');
        showToast('本机未启用加密', 'error');
        cryptoUnlockBtn.disabled = false;
        return;
      }
      const adopted = await adoptRemoteProfile(passphrase, state.profile);
      if (!adopted.ok) {
        setCryptoStatus('passphrase 与其它设备不一致，无法用同一密钥解密历史数据', 'error');
        showToast('passphrase 与其它设备不一致', 'error');
        cryptoUnlockBtn.disabled = false;
        return;
      }
      ok = true;
      adoptedRemote = true;
    }

    if (ok) {
      // Phase 4.4: 同步解锁 SW 的 crypto.js 模块（让 auto-sync 也能加解密）
      // B-P2-5/D-P2-2 修复：检查 SW 返回的 {ok, unlocked} 字段，失败时不显示"解锁成功"toast
      let swUnlockOk = true;
      try {
        const resp = await chrome.runtime.sendMessage({ action: 'CRYPTO_UNLOCK', passphrase });
        if (!resp || !resp.ok || !resp.unlocked) {
          swUnlockOk = false;
          console.warn('[Options] SW unlock 返回失败:', resp);
        }
      } catch (e) {
        swUnlockOk = false;
        console.warn('[Options] 通知 SW 解锁失败:', e);
      }
      if (swUnlockOk) {
        const suffix = adoptedRemote ? ' · 已采用 Bridge 上的加密档案，可解密其它设备推送的密文' : '';
        setCryptoStatus(`解锁成功${suffix} · 现在可以 push/pull 加密快照`, 'ok');
        showToast('加密已解锁', 'success');
        cryptoUnlockPassphraseInput.value = '';
        await loadCryptoState(suffix);
      } else {
        // options 页已解锁，但 SW 未解锁——auto-sync 仍无法工作
        setCryptoStatus('options 页已解锁，但 SW 解锁失败 · auto-sync 仍无法加密推送，请重试或重启浏览器', 'error');
        showToast('SW 解锁失败，自动同步仍暂停', 'error');
        // 不清 passphrase 输入框，方便用户重试
      }
    } else {
      setCryptoStatus('passphrase 错误，请重试', 'error');
      showToast('passphrase 错误', 'error');
    }
  } catch (err) {
    setCryptoStatus('解锁失败：' + err.message, 'error');
  } finally {
    cryptoUnlockBtn.disabled = false;
  }
}

async function handleCryptoLock() {
  try {
    lockCrypto();
    // Phase 4.4: 同步锁定 SW 的 crypto.js 模块
    // B-P2-5/D-P2-2 修复：检查 SW 返回的 {ok} 字段，失败时提示状态不一致
    let swLockOk = true;
    try {
      const resp = await chrome.runtime.sendMessage({ action: 'CRYPTO_LOCK' });
      if (!resp || !resp.ok) {
        swLockOk = false;
        console.warn('[Options] SW lock 返回失败:', resp);
      }
    } catch (e) {
      swLockOk = false;
      console.warn('[Options] 通知 SW 锁定失败:', e);
    }
    if (swLockOk) {
      setCryptoStatus('已锁定 · 需重新输入 passphrase 才能加解密', 'muted');
      showToast('已锁定', 'success');
      await loadCryptoState();
    } else {
      // options 页已锁定，但 SW 未锁定——状态不一致，auto-sync 仍可加密
      // R1b-R2 修复：不调用 loadCryptoState——它会读取 options 页状态（ready=false）覆盖 error 告警
      setCryptoStatus('options 页已锁定，但 SW 锁定失败 · auto-sync 仍可加密推送（状态不一致），建议重启浏览器', 'error');
      showToast('SW 锁定失败，状态不一致', 'error');
      // 手动切换 UI 到解锁表单（options 页已锁定，需重新解锁）
      cryptoUnlockForm.style.display = '';
    }
  } catch (err) {
    setCryptoStatus('锁定失败：' + err.message, 'error');
  }
}

async function handleCryptoDisable() {
  const passphrase = cryptoUnlockPassphraseInput.value || prompt('请输入当前 passphrase 以确认禁用加密：');
  if (!passphrase || passphrase.length < 8) {
    setCryptoStatus('需输入 passphrase 才能禁用加密', 'error');
    return;
  }
  // 二次确认：禁用后云端密文将无法解密
  // D-P2-1 修复：原文案"除非用相同 passphrase 重新启用"误导——每次启用生成新 salt，旧密文永久不可解密
  if (!confirm('禁用加密后，云端已有的密文快照将无法再被解密（即使使用相同 passphrase 重新启用也不行，因为每次启用会生成新 salt）。本地存储的 salt 与校验密文也会被清除。确认继续？')) {
    return;
  }
  cryptoDisableBtn.disabled = true;
  setCryptoStatus('禁用中…', 'muted');
  try {
    await disableCrypto(passphrase);
    // Phase 4.4: 同步清理 SW 内存 key
    // B-P2-5/D-P2-2 修复：检查 SW 返回的 {ok} 字段，失败时提示状态不一致
    let swDisableOk = true;
    try {
      const resp = await chrome.runtime.sendMessage({ action: 'CRYPTO_DISABLE_SW' });
      if (!resp || !resp.ok) {
        swDisableOk = false;
        console.warn('[Options] SW disable 返回失败:', resp);
      }
    } catch (e) {
      swDisableOk = false;
      console.warn('[Options] 通知 SW 禁用失败:', e);
    }
    if (swDisableOk) {
      setCryptoStatus('加密已禁用 · 后续推送将以明文形式上传', 'muted');
      showToast('加密已禁用', 'success');
      cryptoUnlockPassphraseInput.value = '';
      await loadCryptoState();
    } else {
      // options 页已禁用（storage 清除 + key 清除），但 SW 仍有 key——状态不一致
      // R1b-R2 修复：不调用 loadCryptoState——它会读取 options 页状态（enabled=false）覆盖 error 告警
      setCryptoStatus('options 页已禁用，但 SW 禁用失败 · auto-sync 可能仍尝试加密（状态不一致），建议重启浏览器', 'error');
      showToast('SW 禁用失败，状态不一致', 'error');
      cryptoUnlockPassphraseInput.value = '';
      // 手动切换 UI 到启用表单（options 页已禁用，需重新启用）
      cryptoEnableForm.style.display = '';
      cryptoManagePanel.style.display = 'none';
    }
  } catch (err) {
    setCryptoStatus('禁用失败：' + err.message, 'error');
    showToast('禁用失败：' + err.message, 'error');
  } finally {
    cryptoDisableBtn.disabled = false;
  }
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

// ---------- Phase 4.1: Bridge 任务通知开关 ----------
const taskWatcherToggle = document.getElementById('taskWatcherToggle');
const taskWatcherStatusEl = document.getElementById('taskWatcherStatus');

if (taskWatcherToggle) {
  // 初始化开关状态
  getTaskWatcherEnabled().then(enabled => {
    taskWatcherToggle.checked = !!enabled;
    setTaskWatcherStatus(enabled ? '已启用：每 5 分钟检查 Bridge 任务变更' : '已关闭', enabled ? 'ok' : 'muted');
  }).catch(err => {
    setTaskWatcherStatus('读取开关状态失败：' + err.message, 'error');
  });
  // 切换时保存
  taskWatcherToggle.addEventListener('change', async () => {
    const enabled = taskWatcherToggle.checked;
    try {
      await setTaskWatcherEnabled(enabled);
      setTaskWatcherStatus(enabled ? '已启用：每 5 分钟检查 Bridge 任务变更' : '已关闭', enabled ? 'ok' : 'muted');
      showToast(enabled ? '任务通知已启用' : '任务通知已关闭', 'success');
    } catch (err) {
      setTaskWatcherStatus('保存开关失败：' + err.message, 'error');
      showToast('保存开关失败：' + err.message, 'error');
      // 回滚 UI
      taskWatcherToggle.checked = !enabled;
    }
  });
}

function setTaskWatcherStatus(message, type) {
  if (!taskWatcherStatusEl) return;
  taskWatcherStatusEl.textContent = message;
  taskWatcherStatusEl.dataset.type = type || 'muted';
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
    console.error('[Focus] 读取演示模式状态失败：', err);
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
    showToast('API Key 已保存到本地', 'success');
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
  // F-LOW-4: 清除 AI 配置前二次确认，防止误点丢失 API Key
  if (!confirm('确定要清除 AI 配置吗？')) {
    return;
  }
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

// ---------- 导出 ----------
async function handleExport() {
  setLoading(exportBtn, true);
  try {
    const res = await chrome.runtime.sendMessage({ action: 'EXPORT_SNAPSHOTS' });
    if (!res || !res.ok) {
      showToast('导出失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    const data = res.data;
    const count = data?.count ?? (Array.isArray(data?.snapshots) ? data.snapshots.length : 0);
    if (count === 0) {
      showToast('暂无可导出的快照', 'info');
      return;
    }
    const json = JSON.stringify(data, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const filename = `vannevar-focus-export-${formatDate(new Date())}.json`;
    triggerDownload(url, filename);
    // 延迟释放，避免在浏览器尚未读取到 blob 前就被回收
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast(`已导出 ${count} 条快照`, 'success');
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
    if (!data || !Array.isArray(data.snapshots)) {
      showToast('文件格式错误：缺少 snapshots 字段', 'error');
      return;
    }

    const res = await chrome.runtime.sendMessage({ action: 'IMPORT_SNAPSHOTS', data });
    if (!res || !res.ok) {
      showToast('导入失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    const imported = Number(res.imported) || 0;
    showToast(`已导入 ${imported} 条快照`, 'success');
  } catch (err) {
    showToast('导入失败：' + err.message, 'error');
  } finally {
    setLoading(importBtn, false);
  }
}

// ---------- 清除所有快照数据（F-SEC-03） ----------
async function handleClearAllData() {
  // 二次确认，防止误点丢失全部快照
  if (!confirm('确定要清除所有快照数据吗？此操作不可撤销，且无法恢复。')) {
    return;
  }
  const secondConfirm = confirm('再次确认：将永久删除所有已保存的上下文快照。');
  if (!secondConfirm) {
    return;
  }
  setLoading(clearAllDataBtn, true);
  try {
    const res = await chrome.runtime.sendMessage({ action: 'CLEAR_ALL_DATA' });
    if (!res || !res.ok) {
      showToast('清除失败：' + (res?.error || '未知错误'), 'error');
      return;
    }
    const removed = Number(res.removed) || 0;
    showToast(`已清除 ${removed} 条快照`, 'success');
  } catch (err) {
    showToast('清除失败：' + err.message, 'error');
  } finally {
    setLoading(clearAllDataBtn, false);
  }
}

// ---------- 工具函数 ----------
function setLoading(btn, loading) {
  btn.classList.toggle('is-loading', loading);
  btn.disabled = loading;
  const labelEl = btn.querySelector('.btn-label');
  if (!labelEl) return;
  if (loading) {
    // 保存原始文本，便于完成后恢复
    if (!btn.dataset.originalLabel) {
      btn.dataset.originalLabel = labelEl.textContent;
    }
    const loadingText = btn.dataset.loadingText
      || (btn.id === 'exportBtn' ? '导出中…' : '导入中…');
    labelEl.textContent = loadingText;
  } else {
    if (btn.dataset.originalLabel) {
      labelEl.textContent = btn.dataset.originalLabel;
      delete btn.dataset.originalLabel;
    }
  }
}

function triggerDownload(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  // 对辅助技术隐藏临时下载链接，避免被读屏念出
  a.setAttribute('aria-hidden', 'true');
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

// ---------- 阶段2.8: 端侧本地模型 ----------
import {
  getLocalModelConfig,
  setLocalModelConfig,
  isWebGPUAvailable,
  loadLocalModel,
  unloadLocalModel,
  generateLocalSuggestion,
} from './local-model.js';

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
    // WebGPU 可用性检测
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
  // 模型变更后需重新加载
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
    const result = await generateLocalSuggestion('当前有 3 个标签页：文档撰写、邮件沟通、设计评审。请给出工作建议。');
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

function formatDate(d) {
  // F-P2-6: 使用 Intl.DateTimeFormat 替代手动拼接；去除分隔符以保持文件名格式 YYYYMMDD
  const formatted = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).format(d);
  return formatted.replace(/\//g, '');
}

// showToast / toastIcon 由 ./shared/toast.js 提供
