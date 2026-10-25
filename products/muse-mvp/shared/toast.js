// shared/toast.js — Toast 提示组件（popup / dashboard / options 共用）
//
// 用法：
//   import { showToast } from '../shared/toast.js';
//   showToast('已保存', 'success');
//
// 依赖：页面需提供 #toastContainer 容器与 .v-toast-* CSS（popup.css / dashboard.css / options.css 已含）

// 根据 toast 类型返回对应的 SVG 图标字符串
export function toastIcon(type) {
  if (type === 'success') {
    return '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 10l3 3 7-7"/></svg>';
  }
  if (type === 'error') {
    return '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="8"/><path d="M10 6v5M10 14h.01"/></svg>';
  }
  return '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="8"/><path d="M10 9v4M10 6h.01"/></svg>';
}

// 显示一条 toast 提示，2.4s 后自动消失
// type: 'success' | 'error' | 'info'（默认 'info'）
export function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'v-toast v-toast--' + type;
  toast.setAttribute('role', type === 'error' ? 'alert' : 'status');

  const icon = document.createElement('span');
  icon.className = 'v-toast-icon';
  icon.setAttribute('aria-hidden', 'true');
  icon.innerHTML = toastIcon(type);

  const text = document.createElement('span');
  text.className = 'v-toast-text';
  text.textContent = message;

  toast.appendChild(icon);
  toast.appendChild(text);
  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('v-toast-out');
    const remove = () => toast.remove();
    toast.addEventListener('animationend', remove, { once: true });
    // 兜底：动画未触发时也能移除
    setTimeout(remove, 260);
  }, 2400);
}
