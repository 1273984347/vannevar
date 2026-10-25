// shared/vannevar-shared.js — Vannevar 三产品共享工具层（Bridge 版本）
// 统一入口：聚合现有共享模块 + 补齐 modal/animation 共享
//
// Bridge 是 FastAPI Web 服务，不能用 ES modules import；本文件用 IIFE 挂载到 window.VannevarShared。
// 用法：
//   <script src="/static/shared/vannevar-shared.js"></script>
//   <script>
//     const { showToast, showModal, closeModal, escapeHtml, debounce, throttle, rafThrottle, Easing, viewFade } = window.VannevarShared;
//   </script>
//
// 与现有模块的关系：
//   - escapeHtml 对齐 muse-mvp/shared/escape.js 的转义语义
//   - showToast 复用 .v-toast / .v-toast--{type} 类名（base.html 已有 CSS）
//   - showModal 复用 .modal-mask / .modal 类名（base.html 已有 CSS）
//   - Easing 与 vannevar-brand.css 的 --v-ease 曲线协同
//
// 注意：本文件作为「可用入口」存在，证明共享层已抽离；不强制迁移现有实现，
//       base.html 内联的 showToast / showModal 保持不变，避免回归风险。
(function (global) {
  'use strict';

  // ============================================================
  // 1. 转义工具（对齐 Muse shared/escape.js 语义）
  // ============================================================
  function escapeHtml(text) {
    if (typeof text !== 'string') return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return text.replace(/[&<>"']/g, function (ch) { return map[ch]; });
  }

  // ============================================================
  // 2. Toast 工具（复用 .v-toast / .v-toast--{type} 类名）
  // ============================================================
  // type: 'info' | 'success' | 'error' | 'warning'
  // 不依赖 #toastContainer：直接 append 到 body，避免页面缺容器时静默失败。
  function showToast(message, type, duration) {
    type = type || 'info';
    duration = typeof duration === 'number' ? duration : 3000;
    let toast = document.querySelector('.v-toast');
    if (toast) toast.remove();
    toast = document.createElement('div');
    toast.className = 'v-toast v-toast--' + type;
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.textContent = message;
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('is-visible'));
    setTimeout(() => {
      toast.classList.remove('is-visible');
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  // ============================================================
  // 3. Modal 工具（含焦点陷阱 / Esc 关闭 / 焦点归还）
  // 复用 .modal-mask / .modal 类名（bridge base.html 已有 CSS）
  // ============================================================
  function showModal(opts) {
    opts = opts || {};
    const title = opts.title;
    const content = opts.content;
    const confirmText = opts.confirmText || '确认';
    const cancelText = opts.cancelText || '取消';
    const danger = opts.danger === true;
    const onConfirm = opts.onConfirm;
    const onCancel = opts.onCancel;

    const triggerEl = document.activeElement;

    const mask = document.createElement('div');
    mask.className = 'modal-mask';
    mask.setAttribute('role', 'dialog');
    mask.setAttribute('aria-modal', 'true');
    mask.setAttribute('aria-labelledby', 'modalTitle');

    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML =
      '<h3 id="modalTitle"></h3>' +
      '<div class="modal-body"></div>' +
      '<div class="modal-actions">' +
        '<button type="button" class="secondary-btn" data-act="cancel"></button>' +
        '<button type="button" data-act="confirm"></button>' +
      '</div>';

    modal.querySelector('#modalTitle').textContent = title || '';
    const bodyEl = modal.querySelector('.modal-body');
    if (typeof content === 'string') {
      bodyEl.textContent = content;
    } else if (content instanceof HTMLElement) {
      bodyEl.appendChild(content);
    }

    const cancelBtn = modal.querySelector('[data-act="cancel"]');
    const confirmBtn = modal.querySelector('[data-act="confirm"]');
    cancelBtn.textContent = cancelText;
    confirmBtn.textContent = confirmText;
    confirmBtn.className = danger ? 'danger-btn' : 'primary-btn';

    mask.appendChild(modal);
    document.body.appendChild(mask);

    // 收集 modal 内可聚焦元素
    function getFocusable() {
      const sel = 'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';
      return Array.from(modal.querySelectorAll(sel));
    }

    function handleConfirm() {
      const ret = typeof onConfirm === 'function' ? onConfirm() : undefined;
      if (ret && typeof ret.then === 'function') {
        ret.then(ok => { if (ok !== false) closeModal(modal); });
      } else {
        closeModal(modal);
      }
    }

    function handleCancel() {
      if (typeof onCancel === 'function') onCancel();
      closeModal(modal);
    }

    // 焦点陷阱 + Esc 关闭
    function keydownHandler(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        handleCancel();
        return;
      }
      if (e.key === 'Tab') {
        const focusable = getFocusable();
        if (focusable.length === 0) {
          e.preventDefault();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    mask.addEventListener('click', e => {
      if (e.target === mask) handleCancel();
    });
    cancelBtn.addEventListener('click', handleCancel);
    confirmBtn.addEventListener('click', handleConfirm);
    document.addEventListener('keydown', keydownHandler);

    // 把清理数据挂在 modal 上，供 closeModal 使用
    modal._vannevarMask = mask;
    modal._vannevarTrigger = triggerEl;
    modal._vannevarKeydown = keydownHandler;

    // 自动聚焦第一个可聚焦元素
    requestAnimationFrame(() => {
      const focusable = getFocusable();
      if (focusable.length > 0) {
        focusable[0].focus();
      } else {
        modal.setAttribute('tabindex', '-1');
        modal.focus();
      }
    });

    return modal;
  }

  function closeModal(modal) {
    if (!modal || !modal._vannevarMask) return;
    const mask = modal._vannevarMask;
    const trigger = modal._vannevarTrigger;
    const keydownHandler = modal._vannevarKeydown;

    document.removeEventListener('keydown', keydownHandler);
    if (mask.parentNode) mask.parentNode.removeChild(mask);

    // 归还焦点到触发元素
    if (trigger && typeof trigger.focus === 'function') {
      setTimeout(() => trigger.focus(), 0);
    }

    modal._vannevarMask = null;
    modal._vannevarTrigger = null;
    modal._vannevarKeydown = null;
  }

  // ============================================================
  // 4. 动画曲线（三产品统一，与 vannevar-brand.css --v-ease 协同）
  // ============================================================
  const Easing = {
    easeInOut: 'cubic-bezier(0.4, 0, 0.2, 1)',
    easeOut: 'cubic-bezier(0.0, 0, 0.2, 1)',
    easeIn: 'cubic-bezier(0.4, 0, 1, 1)',
    spring: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
    swift: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
  };

  // ============================================================
  // 5. 防抖 / 节流
  // ============================================================
  function debounce(fn, delay) {
    delay = typeof delay === 'number' ? delay : 200;
    let timer = null;
    return function () {
      const args = arguments;
      const ctx = this;
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(ctx, args), delay);
    };
  }

  function throttle(fn, delay) {
    delay = typeof delay === 'number' ? delay : 100;
    let lastCall = 0;
    return function () {
      const args = arguments;
      const ctx = this;
      const now = Date.now();
      if (now - lastCall >= delay) {
        lastCall = now;
        fn.apply(ctx, args);
      }
    };
  }

  function rafThrottle(fn) {
    let scheduled = false;
    return function () {
      if (scheduled) return;
      const args = arguments;
      const ctx = this;
      scheduled = true;
      requestAnimationFrame(() => {
        fn.apply(ctx, args);
        scheduled = false;
      });
    };
  }

  // ============================================================
  // 6. viewFade（页面切换淡入，三产品通用）
  // ============================================================
  function viewFade(element, options) {
    options = options || {};
    const duration = typeof options.duration === 'number' ? options.duration : 300;
    const easing = options.easing || Easing.easeOut;
    if (!element) return;
    element.style.opacity = '0';
    element.style.transition = 'opacity ' + duration + 'ms ' + easing;
    requestAnimationFrame(() => {
      element.style.opacity = '1';
    });
  }

  // ============================================================
  // 导出到 window.VannevarShared
  // ============================================================
  const VannevarShared = {
    escapeHtml: escapeHtml,
    showToast: showToast,
    showModal: showModal,
    closeModal: closeModal,
    Easing: Easing,
    debounce: debounce,
    throttle: throttle,
    rafThrottle: rafThrottle,
    viewFade: viewFade,
  };

  global.VannevarShared = VannevarShared;
})(typeof window !== 'undefined' ? window : this);
