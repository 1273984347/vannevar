# Focus — 设计规范

> 基准文件：`products/focus-mvp/popup.css`
> 适用范围：focus-demo.html、focus-mvp/popup.*、未来所有 Focus 相关 UI

## 设计令牌

### 色彩（亮色模式）
| 令牌 | 色值 | Tailwind 对应 | 用途 |
|------|------|--------------|------|
| --v-bg | #f8fafc | slate-50 | 页面背景底色 |
| --v-bg-radial | radial-gradient(circle at 50% 0%, #f1f5f9, #f8fafc 70%) | — | 页面径向渐变背景，顶部聚光 |
| --v-surface | #ffffff | white | 卡片背景 |
| --v-surface-glass | rgba(255, 255, 255, 0.72) | — | 玻璃态 header 背景 |
| --v-border | #e2e8f0 | slate-200 | 卡片边框 |
| --v-border-soft | rgba(15, 23, 42, 0.06) | — | 微妙边框/分隔线 |
| --v-text | #1e293b | slate-800 | 主标题/正文 |
| --v-text-secondary | #64748b | slate-500 | 副标题/正文 |
| --v-text-muted | #94a3b8 | slate-400 | 辅助文字/标签 |
| --v-primary | #1e293b | slate-800 | 主按钮/图标 |
| --v-primary-dark | #0f172a | slate-900 | hover/强调 |
| --v-primary-light | #3b82f6 | blue-500 | 品牌高亮/链接 |
| --v-accent | #3b82f6 | blue-500 | 交互高亮/focus |
| --v-gradient | linear-gradient(135deg, #1e293b, #334155) | — | Logo/核心按钮对角渐变 |
| --v-gradient-soft | linear-gradient(135deg, rgba(30,41,59,0.06), rgba(59,130,246,0.06)) | — | 卡片背景微渐变 |
| --v-card-stripe | linear-gradient(90deg, #1e293b, #3b82f6, #334155) | — | 卡片顶部装饰渐变线 |

### 深色模式
| 令牌 | 色值 | Tailwind 对应 |
|------|------|--------------|
| --v-bg | #0f172a | slate-900 |
| --v-bg-radial | radial-gradient(circle at 50% 0%, #1e293b, #0f172a 70%) | — |
| --v-surface | #1e293b | slate-800 |
| --v-surface-glass | rgba(30, 41, 59, 0.72) | — |
| --v-border | #334155 | slate-700 |
| --v-border-soft | rgba(255, 255, 255, 0.06) | — |
| --v-text | #f1f5f9 | slate-100 |
| --v-text-secondary | #94a3b8 | slate-400 |
| --v-text-muted | #64748b | slate-500 |
| --v-card-stripe | linear-gradient(90deg, #3b82f6, #1e293b, #475569) | — |

### 字体
- 字体栈：`'Inter', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
- Google Fonts 链接：`https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Noto+Sans+SC:wght@400;500;700&display=swap`
- 中文行高：`1.7`

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
  color: var(--v-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
```

### 阴影
| 令牌 | 值 |
|------|-----|
| --v-shadow-sm | 0 1px 2px rgba(15,23,42,0.04), 0 1px 3px rgba(15,23,42,0.06) |
| --v-shadow | 0 2px 4px rgba(15,23,42,0.04), 0 4px 8px rgba(15,23,42,0.06) |
| --v-shadow-md | 0 4px 8px rgba(15,23,42,0.06), 0 8px 16px rgba(15,23,42,0.08) |
| --v-shadow-lg | 0 8px 24px rgba(15,23,42,0.10), 0 16px 48px rgba(15,23,42,0.12) |
| --v-shadow-brand | 0 8px 24px -4px rgba(59,130,246,0.20) |
| --v-shadow-brand-lg | 0 12px 28px -6px rgba(59,130,246,0.28) |

### 过渡
| 令牌 | 值 |
|------|-----|
| --v-transition | 0.25s cubic-bezier(0.16, 1, 0.3, 1) |
| --v-transition-fast | 0.18s cubic-bezier(0.16, 1, 0.3, 1) |

## 组件规范

### 玻璃态 Header
- 位置：sticky top: 0
- 背景：`var(--v-surface-glass)`
- 背景模糊：`backdrop-filter: blur(12px) saturate(140%)`
- 底部边框：`1px solid var(--v-border-soft)`
- 顶部装饰线：3px `--v-card-stripe` 渐变

### 卡片
- 背景：`var(--v-surface)`
- 边框：`1px solid var(--v-border)`
- 圆角：`var(--v-radius)` (14px)
- 阴影：`var(--v-shadow-sm)`
- hover：`box-shadow: var(--v-shadow-md)` + `transform: translateY(-2px)`

### 主按钮
- 背景：`var(--v-gradient)`
- 圆角：`var(--v-radius-lg)` (20px)
- 阴影：`var(--v-shadow-brand)`
- hover：`translateY(-1px)` + `box-shadow: var(--v-shadow-brand-lg)`
- active：`scale(0.98)`

### 空状态插画
- 尺寸：72px × 72px
- 颜色：`var(--v-text-muted)`，opacity 0.35

## 动效规范

### 入场动画
- 容器 stagger：`fadeSlideUp 0.35s cubic-bezier(0.16, 1, 0.3, 1)`
- stagger 间隔：`0.05s` / `0.08s`
- keyframes：`from { opacity:0; transform: translateY(12px); } to { opacity:1; transform: translateY(0); }`

### Hover 微交互
- 卡片：`translateY(-2px)` + 阴影升级
- 图标按钮：`translateY(-1px) rotate(-2deg)`
- 链接/文字按钮：颜色过渡到 `--v-primary-light`

### 无障碍
- 必须实现 `:focus-visible` 焦点环（2px solid primary，offset 2px）
- 必须实现 `prefers-reduced-motion: reduce` 关闭动画
- skip-link 必须存在

## 禁止事项（已废弃的旧规则）
1. ❌ 不使用靛紫色 (#6366f1 / #8b5cf6) — 这是旧的品牌统一色，已废弃
2. ❌ 不使用非标准色阶值（如 #0b1120）— 使用 Tailwind slate 标准色阶
3. ❌ 不使用系统字体栈作为主字体 — 必须引入 Inter

## 变更记录
- 2026-07-05: 同步真实实现效果 — 135deg 对角渐变、径向渐变背景、玻璃态 header、品牌色多层阴影、stagger 入场动画、圆角升级 8/14/20px
- 2026-06-25: 配色从靛紫改回 slate+blue 蓝灰色系，与 focus-architecture.html 对齐
