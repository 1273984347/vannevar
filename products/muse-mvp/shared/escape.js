// shared/escape.js — HTML 转义工具（popup / dashboard 共用）
//
// 用法：
//   import { escapeHtml } from '../shared/escape.js';
//   escapeHtml('<script>');  // '&lt;script&gt;'

// 转义 HTML 特殊字符（& < > " '），防止 XSS。
// 用于将动态文本安全地插入 innerHTML 模板。
export function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
