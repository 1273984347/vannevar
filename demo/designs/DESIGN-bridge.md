# Bridge — 设计规范

> 基准文件：`products/bridge-mvp/static/style.css`
> 适用范围：bridge-demo.html、bridge-mvp/templates/*、bridge-mvp/static/*、未来所有 Bridge 相关 UI

## 设计令牌

### 色彩（亮色模式）
| 令牌 | 色值 | Tailwind 对应 | 用途 |
|------|------|--------------|------|
| --v-bg | #ecfdf5 | emerald-50 | 页面背景底色 |
| --v-bg-radial | radial-gradient(circle at 50% 0%, #d1fae5 0%, #ecfdf5 60%) | — | 页面径向渐变背景 |
| --v-surface | #ffffff | white | 卡片背景 |
| --v-surface-2 | #052e2b | — | 深色模式次级表面 |
| --v-border | #d1fae5 | emerald-100 | 卡片边框 |
| --v-text | #064e3b | emerald-900 | 主标题 |
| --v-text-secondary | #065f46 | emerald-800 | 正文 |
| --v-text-muted | #059669 | emerald-600 | 副标题/辅助 |
| --v-text-label | #34d399 | emerald-400 | 分层标签 |
| --v-primary | #047857 | emerald-700 | 主按钮/图标 |
| --v-primary-dark | #065f46 | emerald-800 | hover/强调 |
| --v-primary-light | #10b981 | emerald-500 | 品牌高亮 |
| --v-accent | #0d9488 | teal-600 | 交互高亮/focus |
| --v-gradient | linear-gradient(135deg, #047857, #0d9488) | — | Logo/核心按钮对角渐变 |
| --v-gradient-rail | linear-gradient(90deg, #047857, #0d9488, #10b981) | — | 卡片顶部装饰渐变线 |
| --status-todo | #64748b | slate-500 | 待办状态 |
| --status-doing | #f59e0b | amber-500 | 进行中状态 |
| --status-done | #10b981 | emerald-500 | 完成状态 |
| --v-archived-bg | #f3f4f6 | gray-100 | 已归档 badge 背景 |
| --v-archived-fg | #6b7280 | gray-500 | 已归档 badge 文字 |
| --v-archived-border | #e5e7eb | gray-200 | 已归档 badge 边框 |

### 深色模式
| 令牌 | 色值 | Tailwind 对应 |
|------|------|--------------|
| --v-bg | #022c22 | emerald-950 |
| --v-bg-radial | radial-gradient(circle at 50% 0%, #064e3b 0%, #022c22 65%) | — |
| --v-surface | #064e3b | emerald-900 |
| --v-surface-2 | #052e2b | — |
| --v-border | #065f46 | emerald-800 |
| --v-text | #ecfdf5 | emerald-50 |
| --v-text-secondary | #a7f3d0 | emerald-200 |
| --v-text-muted | #6ee7b7 | emerald-300 |
| --v-text-label | #34d399 | emerald-400 |
| --v-gradient | linear-gradient(135deg, #10b981, #14b8a6) | — |
| --v-gradient-rail | linear-gradient(90deg, #10b981, #14b8a6, #34d399) | — |
| --v-archived-bg | rgba(148, 163, 184, 0.16) | — |
| --v-archived-fg | #cbd5e1 | gray-300 |
| --v-archived-border | rgba(148, 163, 184, 0.32) | — |

### 字体
- 字体栈：`'Inter', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
- Google Fonts 链接同 Focus

### 圆角
| 令牌 | 值 | 用途 |
|------|-----|------|
| --v-radius-sm | 8px | 小按钮/输入框/任务卡 |
| --v-radius | 14px | 外层卡片/看板列 |
| --v-radius-lg | 20px | 大按钮/modal |

### 间距
基于 8px 网格：4 / 8 / 12 / 16 / 18 / 20 / 24 / 32

### 分层标签
```css
.section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--v-text-label); /* emerald-400 */
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
```

### 阴影
| 令牌 | 值 |
|------|-----|
| --v-shadow-sm | 0 1px 2px rgba(4, 120, 87, 0.04), 0 1px 3px rgba(4, 120, 87, 0.06) |
| --v-shadow | 0 2px 4px rgba(4, 120, 87, 0.04), 0 4px 8px rgba(4, 120, 87, 0.06) |
| --v-shadow-md | 0 4px 8px rgba(4, 120, 87, 0.06), 0 8px 16px rgba(4, 120, 87, 0.08) |
| --v-shadow-lg | 0 8px 24px rgba(4, 120, 87, 0.10), 0 16px 48px rgba(4, 120, 87, 0.12) |
| --v-shadow-brand | 0 8px 24px -4px rgba(16, 185, 129, 0.20) |

### 过渡
| 令牌 | 值 |
|------|-----|
| --v-transition | 0.25s cubic-bezier(0.16, 1, 0.3, 1) |
| --v-transition-fast | 0.15s cubic-bezier(0.16, 1, 0.3, 1) |

## 组件规范

### 玻璃态 Header / Topbar
- 位置：sticky top: 0
- 背景：`var(--v-glass-bg)`（亮色 rgba(255,255,255,0.72)；深色 rgba(6,78,59,0.72)）
- 背景模糊：`backdrop-filter: blur(12px) saturate(140%)`
- 底部边框：`1px solid var(--v-glass-border)`（亮色 rgba(255,255,255,0.6)；深色 rgba(236,253,245,0.08)）

### 卡片 / 项目卡 / 看板列
- 背景：`var(--v-surface)`
- 边框：`1px solid var(--v-border)`
- 圆角：`var(--v-radius)` (14px)
- 阴影：`var(--v-shadow-sm)`
- hover：`box-shadow: var(--v-shadow-brand), var(--v-shadow-md)` + `transform: translateY(-2px)`
- 顶部装饰线：3px `--v-gradient-rail`

### 主按钮
- 背景：`var(--v-gradient)`
- 圆角：`var(--v-radius-lg)` (20px)
- 阴影：`var(--v-shadow-brand)`
- hover：`translateY(-1px)` + `box-shadow: var(--v-shadow-md)`
- active：`scale(0.98)`

### 任务卡
- 圆角：`var(--v-radius-sm)` (8px)
- 状态条：左侧 3px 颜色条（todo/doing/done）

### 统计页 KPI / Chart Card
- 背景：`var(--v-surface)`
- 顶部装饰线：3px `--v-gradient-rail`
- 圆角：`var(--v-radius)` (14px)

## 动效规范

### 入场动画
- 容器 stagger：`fadeSlideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1)`
- stagger 间隔：`0.05s` / `0.1s`

### Hover 微交互
- 卡片：`translateY(-2px)` + 翠绿色品牌阴影
- 按钮：`translateY(-1px)` + 阴影升级
- 链接：颜色过渡到 `--v-primary-light`

### 无障碍
- 必须实现 `:focus-visible` 焦点环
- 必须实现 `prefers-reduced-motion: reduce` 关闭动画
- skip-link 必须存在

## 禁止事项（已废弃的旧规则）
1. ❌ 不使用靛紫色 (#6366f1 / #8b5cf6)
2. ❌ 不使用非标准色阶值 — 使用 Tailwind emerald/teal 标准色阶
3. ❌ 不使用系统字体栈作为主字体 — 必须引入 Inter

## 变更记录
- 2026-07-05: 同步真实实现效果 — 135deg 对角渐变、径向渐变背景、玻璃态 header、翠绿色多层阴影、stagger 入场动画、圆角升级 8/14/20px、已归档 badge 深浅色模式适配
- 2026-06-25: 配色从靛紫改回 emerald+teal 翠绿色系，文字/边框从 slate 灰改回 emerald 绿，与 bridge-architecture.html 对齐
