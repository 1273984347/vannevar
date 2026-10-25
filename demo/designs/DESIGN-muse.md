# Muse — 设计规范

> 基准文件：`products/muse-mvp/shared/vannevar-brand.css`
> 适用范围：muse-demo.html、muse-mvp/popup/*、muse-mvp/dashboard/*、未来所有 Muse 相关 UI

## 设计令牌

### 色彩（亮色模式）
| 令牌 | 色值 | Tailwind 对应 | 用途 |
|------|------|--------------|------|
| --v-bg | #fff7ed | orange-50 | 页面背景底色 |
| --v-bg-gradient | radial-gradient(circle at 50% 0%, #fffbeb, #fff7ed) | — | 页面径向渐变背景，顶部聚光 |
| --v-surface | #ffffff | white | 卡片背景 |
| --v-border | #ffedd5 | orange-100 | 卡片边框 |
| --v-text | #292524 | stone-800 | 主标题/正文 |
| --v-text-secondary | #44403c | stone-700 | 正文/副标题 |
| --v-text-muted | #78716c | stone-500 | 副标题/辅助文字 |
| --v-text-label | #a8a29e | stone-400 | 分层标签 |
| --v-primary | #c2410c | orange-700 | 主按钮/图标 |
| --v-primary-dark | #9a3412 | orange-800 | hover/按钮阴影投射 |
| --v-primary-light | #f97316 | orange-500 | 品牌高亮 |
| --v-accent | #b45309 | amber-700 | 交互高亮/focus |
| --v-gradient | linear-gradient(135deg, #c2410c, #b45309) | — | Logo/核心按钮对角渐变 |
| --v-gradient-btn | linear-gradient(135deg, #c2410c, #9a3412) | — | 按钮渐变，立体感更强 |
| --v-gradient-accent | linear-gradient(90deg, #c2410c, #f59e0b) | — | 卡片顶部装饰渐变线 |

### 深色模式
| 令牌 | 色值 |
|------|------|
| --v-bg | #1a120b |
| --v-bg-gradient | radial-gradient(circle at 50% 0%, #2a1a0e, #1a120b) |
| --v-surface | #241a12 |
| --v-border | #3a2a1c |
| --v-text | #f5ead8 |
| --v-text-secondary | #c4b59e |
| --v-text-muted | #a89b85 |
| --v-text-label | #a8a29e |
| --v-accent | #f59e0b |

### 字体
- 字体栈：`'Inter', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
- Google Fonts 链接同 Focus

### 圆角
| 令牌 | 值 | 用途 |
|------|-----|------|
| --v-radius-sm | 8px | 小按钮/输入框/图标 |
| --v-radius | 14px | 外层卡片 |
| --v-radius-lg | 20px | 大按钮/特殊容器 |

### 间距
基于 8px 网格：4 / 8 / 12 / 16 / 18 / 20 / 24 / 32

### 分层标签
```css
.section-label {
  font-size: 12px;
  font-weight: 600;
  color: #a8a29e; /* stone-400 */
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
```

### 阴影
| 令牌 | 值 |
|------|-----|
| --v-shadow-sm | 0 1px 2px rgba(194,65,12,0.04), 0 1px 3px rgba(194,65,12,0.06) |
| --v-shadow | 0 2px 4px rgba(194,65,12,0.04), 0 4px 8px rgba(194,65,12,0.06) |
| --v-shadow-md | 0 4px 8px rgba(194,65,12,0.06), 0 8px 16px rgba(194,65,12,0.08) |
| --v-shadow-lg | 0 8px 24px rgba(194,65,12,0.10), 0 16px 48px rgba(194,65,12,0.12) |
| --v-shadow-hover | 0 8px 24px -4px rgba(217,119,6,0.20), 0 4px 12px rgba(194,65,12,0.08) |
| --v-shadow-btn | 0 4px 12px rgba(194,65,12,0.28) |
| --v-shadow-btn-hover | 0 8px 24px -4px rgba(217,119,6,0.40) |

### 过渡
| 令牌 | 值 |
|------|-----|
| --v-transition | 0.25s cubic-bezier(0.16, 1, 0.3, 1) |
| --v-transition-fast | 0.18s cubic-bezier(0.16, 1, 0.3, 1) |

## 组件规范

### 玻璃态 Header
- 位置：sticky top: 0
- 背景：`var(--v-glass-bg)`（亮色 rgba(255, 247, 237, 0.72)；深色 rgba(26, 18, 11, 0.72)）
- 背景模糊：`backdrop-filter: blur(12px) saturate(140%)`
- 底部边框：`1px solid var(--v-glass-border)`

### 卡片
- 背景：`var(--v-surface)`
- 边框：`1px solid var(--v-border)`
- 圆角：`var(--v-radius)` (14px)
- 阴影：`var(--v-shadow-sm)`
- hover：`box-shadow: var(--v-shadow-hover)` + `transform: translateY(-2px)`

### 主按钮
- 背景：`var(--v-gradient-btn)`
- 圆角：`var(--v-radius-lg)` (20px)
- 阴影：`var(--v-shadow-btn)`
- hover：`translateY(-1px)` + `box-shadow: var(--v-shadow-btn-hover)`
- active：`scale(0.98)`

### 氛围标签（mood tag）
- 圆角：`var(--v-radius-lg)` (20px pill)
- 选中态：背景 `--v-primary-soft`，边框 `--v-border`

## 动效规范

### 入场动画
- 容器 stagger：`fadeSlideUp 0.35s cubic-bezier(0.16, 1, 0.3, 1)`
- stagger 间隔：`0.05s` / `0.08s`

### Hover 微交互
- 卡片：`translateY(-2px)` + 暖色调阴影升级
- 图标按钮：`translateY(-1px) scale(1.05)`
- 氛围标签选中：`scale(1.05)`

### 无障碍
- 必须实现 `:focus-visible` 焦点环
- 必须实现 `prefers-reduced-motion: reduce` 关闭动画
- skip-link 必须存在

## 禁止事项（已废弃的旧规则）
1. ❌ 不使用靛紫色 (#6366f1 / #8b5cf6)
2. ❌ 不使用非标准色阶值 — 使用 Tailwind stone/orange/amber 标准色阶
3. ❌ 不使用系统字体栈作为主字体 — 必须引入 Inter
4. ❌ 深色模式不引入 rose/pink 色调 — 保持纯 orange/amber 暖色系

## 变更记录
- 2026-07-05: 同步真实实现效果 — 135deg 对角渐变、径向渐变背景、玻璃态 header、暖色调多层阴影、stagger 入场动画、圆角升级 8/14/20px
- 2026-06-25: 配色从靛紫改回 orange+amber 暖橙色系，与 muse-architecture.html 对齐
