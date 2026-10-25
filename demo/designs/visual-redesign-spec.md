# Vannevar 视觉重设计规格书 · Phase 6.6

> **基准文件**:`products/bridge-mvp/static/style.css` + `stats.css`
> **改造范围**:Bridge 3 个首屏页(stats / project-detail / index)
> **母题方向**:东方留白 × Memex 档案(1945 原典致敬)
> **设计变体**:B · 复古档案(C · 现代档案标注为后续商业化候选)
> **创建日期**:2026-07-21
> **状态**:已实施（`products/bridge-mvp/static/archive-paper.css` + `stats.css` 已落地；对比度门禁已针对该设计做过专项修复，深浅两模式 56 项全绿）

---

## §0 决策记录

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Q1 视觉母题 | B+C 融合(东方留白 × Memex 档案) | 与写作偏好同源,叙事感强 |
| Q2 三产品差异化 | B 三材质区分(方格本/宣纸/案卷) | 材质隐喻天然成立 |
| Q3 改造范围 | B 重点页面试点(Bridge 3 页) | 首屏冲击力,回归风险可控 |
| Q4.1 深色模式 | C 褪色档案模式 | 保留切换器,叙事感更强 |
| Q4.2 动画风格 | C 混合(UI 墨迹 + 图表保留) | 平衡叙事与性能感知 |
| Q4.3 图表视觉 | B 重做毛笔笔触 | 毛笔线 + 盖章密度 + 墨迹流向 |
| Q4.4 a11y 残留 | A 一并修 | Phase 6.7 提前完成 |
| Q5 设计变体 | B 复古档案(主)/ C 现代档案(商业化候选) | 叙事感最强,记忆点最高 |

---

## §1 设计令牌系统(新)

### 1.1 色板(亮色 · 案卷纸)

```css
:root {
  /* 纸张色板 */
  --v-paper:        #e8dcc0;   /* 案卷纸主色(泛黄) */
  --v-paper-light:  #f0e6c8;   /* 索引卡背景 */
  --v-paper-dark:   #d4c5a9;   /* 折痕/边框 */
  --v-paper-edge:   #b8a878;   /* 纸边阴影 */

  /* 墨色 */
  --v-ink:          #1a3a18;   /* 深松烟墨(主文字) */
  --v-ink-medium:   #3a2e15;   /* 中墨(正文) */
  --v-ink-light:    #6b5a35;   /* 淡墨(辅助) */
  --v-ink-faded:    #8b7355;   /* 褪色墨(标签) */

  /* 松烟绿点缀(Bridge 品牌色保留语义) */
  --v-primary:      #1a3a18;   /* 深松烟(主按钮) */
  --v-primary-dark: #0d2010;   /* hover */
  --v-accent:       #2d4a2b;   /* 交互高亮 */

  /* 状态色(墨色化) */
  --status-todo:    #6b5a35;   /* 淡墨 */
  --status-doing:   #8b4513;   /* 赭石 */
  --status-done:    #1a3a18;   /* 深松烟 */

  /* 热力图 4 级松烟色阶 */
  --v-heat-1: rgba(26, 58, 24, 0.15);
  --v-heat-2: rgba(26, 58, 24, 0.40);
  --v-heat-3: rgba(26, 58, 24, 0.70);
  --v-heat-4: rgba(26, 58, 24, 0.95);

  /* 图表色板(单色系松烟) */
  --v-chart-line-1: #1a3a18;   /* 主线 */
  --v-chart-line-2: #2d4a2b;   /* 次线 */
  --v-chart-line-3: #4a6a48;   /* 三线 */
  --v-chart-line-4: #8b4513;   /* 赭石点缀 */
  --v-chart-grid:   rgba(26, 58, 24, 0.08);

  /* 圆角(档案直角化) */
  --v-radius-sm: 0px;
  --v-radius:    2px;
  --v-radius-lg: 4px;

  /* 阴影(单层墨迹) */
  --v-shadow-sm: 0 1px 2px rgba(26, 58, 24, 0.08);
  --v-shadow:    0 2px 4px rgba(26, 58, 24, 0.10);
  --v-shadow-md: 2px 2px 0 #b8a878;   /* 案卷偏移阴影 */
  --v-shadow-lg: 4px 4px 0 #b8a878;

  /* 过渡 */
  --v-transition:      0.3s cubic-bezier(0.4, 0, 0.2, 1);
  --v-transition-ink:  0.4s ease-out;  /* 墨迹晕染专用 */
}
```

### 1.2 色板(褪色档案深色模式)

```css
:root[data-theme="dark"] {
  /* 老化纸张色板(深咖) */
  --v-paper:        #2a2018;   /* 老化案卷纸 */
  --v-paper-light:  #332822;   /* 索引卡 */
  --v-paper-dark:   #1a1410;   /* 折痕 */
  --v-paper-edge:   #0d0a08;   /* 纸边 */

  /* 褪色墨 */
  --v-ink:          #d4c5a9;   /* 褪色墨(主文字) */
  --v-ink-medium:   #b8a878;   /* 中墨 */
  --v-ink-light:    #8b7355;   /* 淡墨 */
  --v-ink-faded:    #6b5a35;   /* 褪色 */

  --v-primary:      #d4c5a9;
  --v-primary-dark: #b8a878;
  --v-accent:       #8b9a78;   /* 褪色松烟绿 */

  --status-todo:    #8b7355;
  --status-doing:   #a06342;
  --status-done:    #8b9a78;

  --v-heat-1: rgba(212, 197, 169, 0.15);
  --v-heat-2: rgba(212, 197, 169, 0.40);
  --v-heat-3: rgba(212, 197, 169, 0.70);
  --v-heat-4: rgba(212, 197, 169, 0.95);

  --v-chart-line-1: #d4c5a9;
  --v-chart-line-2: #8b9a78;
  --v-chart-line-3: #b8a878;
  --v-chart-line-4: #a06342;
  --v-chart-grid:   rgba(212, 197, 169, 0.10);
}
```

### 1.3 字体系统

```css
:root {
  /* 衬线主字(中文优先) */
  --v-font-serif: 'Noto Serif SC', 'Source Han Serif SC', Georgia, 'Times New Roman', serif;
  /* 等宽(数据/编号) */
  --v-font-mono:  'JetBrains Mono', 'Source Code Pro', 'Courier New', monospace;
  /* 楷书点缀(标题/章节) */
  --v-font-kai:   'KaiTi', 'STKaiti', 'Noto Serif SC', serif;

  --v-font-body:  var(--v-font-serif);
  --v-font-display: var(--v-font-serif);
  --v-font-data:  var(--v-font-mono);
}
```

**字阶**:
- H1 仪表盘标题:24px / 700 / letter-spacing 0.02em
- H2 区块标题:18px / 600 / letter-spacing 0.1em(松散)
- KPI 数值:26px / 700 / mono
- 正文:14px / 400 / line-height 1.7
- 标签:10-11px / 600 / letter-spacing 0.2em(全大写或字距松散)

---

## §2 三材质实现规格

### 2.1 Bridge · 案卷纸(本次实施)

```css
/* 案卷纸纹理:横线纸 + 偏移阴影 */
.bridge-paper {
  background-color: var(--v-paper);
  background-image: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 22px,
    rgba(26, 58, 24, 0.04) 22px,
    rgba(26, 58, 24, 0.04) 23px
  );
  border: 1px solid var(--v-paper-dark);
  box-shadow: var(--v-shadow-md);
}
```

### 2.2 Focus · 方格本(后续阶段,本次仅存档)

```css
.focus-paper {
  background-color: #f4ecd8;
  background-image:
    linear-gradient(rgba(30, 58, 95, 0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(30, 58, 95, 0.08) 1px, transparent 1px);
  background-size: 12px 12px;
}
```

### 2.3 Muse · 宣纸稿(后续阶段,本次仅存档)

```css
.muse-paper {
  background-color: #faf6ef;
  background-image: radial-gradient(circle at 20% 30%, rgba(139, 58, 47, 0.03) 0%, transparent 50%),
                    radial-gradient(circle at 80% 70%, rgba(139, 58, 47, 0.03) 0%, transparent 50%);
}
```

---

## §3 组件改造清单

### 3.1 索引卡(KPI 卡片)

**Before**(玻璃态):圆角 14px + 多层柔和阴影 + 翠绿渐变顶线
**After**(索引卡):
- 直角(border-radius: 0)
- 1px 实线边框 var(--v-paper-dark)
- 右上角戳印编号(壹贰叁肆伍陆柒捌玖拾)
- 左侧 3px 松烟绿装饰线
- 偏移阴影 2px 2px 0 var(--v-paper-edge)

### 3.2 戳印标识(替换原 Logo 渐变)

```css
.v-stamp {
  border: 2px solid var(--v-ink);
  padding: 3px 8px;
  font-size: 10px;
  letter-spacing: 0.2em;
  font-weight: 700;
  font-family: var(--v-font-serif);
  color: var(--v-ink);
  background: var(--v-paper-light);
  display: inline-block;
  transform: rotate(-1deg);  /* 手工盖偏感 */
}
```

### 3.3 章节标题(替换原 H2)

```css
.v-section-title {
  font-family: var(--v-font-serif);
  font-size: 12px;
  letter-spacing: 0.2em;
  color: var(--v-ink-light);
  font-weight: 600;
  padding-left: 12px;
  border-left: 3px solid var(--v-ink);
  margin-bottom: 8px;
}
```

### 3.4 按钮(墨迹风)

**主按钮**:实心 var(--v-ink) 背景 + var(--v-paper) 文字 + hover 墨迹晕染(box-shadow 扩散)
**次按钮**:1px var(--v-ink) 边框 + 透明背景 + hover 反色

### 3.5 状态标签(印章风)

任务状态用印章圆形替代原圆角 badge:
- 待办:淡墨空心圆
- 进行中:赭石实心圆
- 完成:深松烟盖章(带 -1deg 旋转)

### 3.6 表单输入(纸面书写)

- 边框仅底部 1px var(--v-ink-medium)
- 背景透明(透出纸纹)
- focus 时底部边框加粗至 2px + 墨迹晕染

---

## §4 动画系统(混合)

### 4.1 UI 元素 · 墨迹晕染

```css
/* hover 墨迹扩散 */
.v-ink-hover {
  position: relative;
  transition: var(--v-transition);
}
.v-ink-hover::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--v-ink);
  opacity: 0;
  transition: opacity var(--v-transition-ink);
  pointer-events: none;
}
.v-ink-hover:hover::after {
  opacity: 0.08;
}
```

### 4.2 入场 · 淡入如墨

```css
@keyframes ink-fade-in {
  from { opacity: 0; transform: translateY(4px); filter: blur(2px); }
  to   { opacity: 1; transform: translateY(0); filter: blur(0); }
}
.v-enter { animation: ink-fade-in 0.5s ease-out both; }
```

### 4.3 数据图表 · 保留原动画

- 趋势线 stroke-dashoffset 绘制保留
- 热力图 stagger 入场保留
- 仅色板替换为松烟绿系

### 4.4 空状态 · 水墨插画

原 3D SVG 插画替换为水墨风空状态(简笔松枝 / 远山 / 空白卷轴)。

---

## §5 图表毛笔笔触改造

### 5.1 趋势线(折线图)

```svg
<!-- Before: stroke-width 2, stroke #10b981 -->
<!-- After: 毛笔笔触 -->
<path
  d="M 0 60 Q 50 55, 80 45 T 160 30 T 240 38 T 320 20 T 400 25"
  stroke="var(--v-chart-line-1)"
  stroke-width="3.5"
  stroke-linecap="round"
  stroke-linejoin="round"
  fill="none"
/>
<!-- 数据点:印章圆点 -->
<circle cx="80" cy="45" r="4"
  fill="var(--v-ink)" stroke="var(--v-paper-light)" stroke-width="1.5"/>
```

### 5.2 热力图(盖章密度)

原方块改圆形(盖章):
```css
.heat-cell { border-radius: 50%; }
```
色阶用 --v-heat-1 到 --v-heat-4(松烟绿透明度递增)。

### 5.3 桑基图(墨迹流向)

原直线改贝塞尔曲线 + stroke-width 渐变(模拟毛笔提按)。

### 5.4 雷达图

原多边形改毛笔笔触边线 + 松烟绿半透明填充。

---

## §6 a11y 修复清单(Phase 6.7 提前)

| # | 问题 | 修复 | 优先级 |
|---|------|------|:------:|
| 1 | CLS:img 缺 width/height | 补全所有 `<img>` 的 width/height 属性 | P0 |
| 2 | CLS:字体加载抖动 | `font-display: swap` + 预加载 | P0 |
| 3 | `transition: var(--v-transition)` 等效 all | 改为具体属性 `transition: opacity 0.3s, transform 0.3s` | P1 |
| 4 | `:focus { outline: none }` | 改 `:focus-visible` + 墨迹焦点环(2px var(--v-ink) outline-offset 2px) | P1 |
| 5 | skip-link 缺 :focus-visible | 补 `:focus-visible` 回归样式 | P1 |
| 6 | project_detail.html 双 `<h1>` | 副标题改 `<h2>` | P1 |
| 7 | Muse popup 缺 aria-live | 补 `aria-live="polite"`(本次不实施,记录) | P2 |
| 8 | `color-scheme` meta 缺失 | `<meta name="color-scheme" content="light dark">` | P2 |
| 9 | `-webkit-tap-highlight-color` | 全局 `* { -webkit-tap-highlight-color: transparent; }` | P3 |
| 10 | `touch-action` | 交互元素补 `touch-action: manipulation` | P3 |

---

## §7 实施阶段计划

### Phase 6.6.1 · 令牌层(基础)
- [ ] 新建 `archive-paper.css`(案卷纸令牌 + 纹理 + 材质)
- [ ] 替换 style.css :root 令牌(保留原令牌作 fallback)
- [ ] 引入 Noto Serif SC + JetBrains Mono(Google Fonts 或本地)
- [ ] 验证:Bridge 3 页加载无 404,令牌全覆盖

### Phase 6.6.2 · 组件层(索引卡 + 戳印 + 章节)
- [ ] KPI 卡片改索引卡(4 个 KPI + 戳印编号)
- [ ] 章节标题改 .v-section-title
- [ ] Logo 改戳印 .v-stamp
- [ ] 按钮改墨迹风
- [ ] 状态标签改印章风
- [ ] 验证:视觉一致性 spot-check

### Phase 6.6.3 · 图表层(毛笔笔触)
- [ ] 趋势线改毛笔笔触(stroke-width 3.5 + round)
- [ ] 热力图改盖章(border-radius 50%)
- [ ] 桑基图改墨迹流向(后续)
- [ ] 雷达图改毛笔边线(后续)
- [ ] 验证:图表数据可读性不下降

### Phase 6.6.4 · 动画层(混合)
- [ ] UI hover 改墨迹晕染(.v-ink-hover)
- [ ] 入场改 ink-fade-in
- [ ] 图表动画保留(仅色板替换)
- [ ] 空状态改水墨插画
- [ ] 验证:prefers-reduced-motion 生效

### Phase 6.6.5 · 褪色档案深色模式
- [ ] [data-theme="dark"] 新色板
- [ ] 切换器语义改"褪色/原色"
- [ ] 验证:深色模式对比度达标 WCAG AA

### Phase 6.6.6 · a11y 一并修
- [ ] P0:CLS 修复(img width/height + font-display)
- [ ] P1:transition / focus-visible / skip-link / 双 h1
- [ ] P2:color-scheme meta / aria-live
- [ ] 验证:Lighthouse 4 页 a11y ≥ 95 + CLS < 0.1

### Phase 6.6.7 · DRL 真循环收敛
- [ ] 触发 deep-review-loop skill
- [ ] 53/53 dogfood QA 选择器同步
- [ ] Lighthouse 4 页重测
- [ ] 423 passed 测试回归
- [ ] 收敛曲线记录

---

## §8 风险与回归测试

### 8.1 回归风险

| 风险 | 概率 | 影响 | 缓解 |
|------|:----:|:----:|------|
| 53/53 dogfood QA 选择器失效 | 高 | 中 | 同步更新 dogfood-qc.js 选择器 |
| 423 passed 测试回归 | 低 | 高 | 纯前端 CSS/HTML 改动,后端 0 影响 |
| Lighthouse 性能下降 | 中 | 中 | 字体加载用 font-display: swap + 预加载 |
| CLS 不降反升 | 低 | 高 | img width/height + 字体尺寸预留 |
| 深色模式对比度不达标 | 中 | 高 | check_contrast.py 自动校验 |

### 8.2 验收标准

- Lighthouse perf ≥ 90(4 页平均)
- Lighthouse a11y ≥ 95(4 页平均)
- CLS < 0.1(4 页全部)
- dogfood QA 53/53 PASS
- 423 passed 测试 0 回归
- WCAG AA 对比度全达标
- DRL 真收敛(连续 2 轮 0 新问题)

---

## §9 后续商业化候选(C · 现代档案)

C 方案色板与字体规格存档,后续商业化阶段启用:

```css
/* C · 现代档案(商业化候选) */
--v-paper:        #faf7f0;
--v-ink:          #2a3a28;
--v-primary:      #4a6a48;
--v-accent:       #8a9a88;
--v-radius:       4px;
--v-shadow:       0 1px 2px rgba(74, 106, 72, 0.08);
--v-font-body:    'Noto Serif SC', Georgia, serif;
```

C 方案特点:近白底 + 极简 + 现代感强,适合 SaaS 商业化场景,但叙事感弱于 B。

---

## §10 复利经验沉淀预留

Phase 6.6 完成后,以下经验需沉淀到 project_memory.md(编号 #283+):

- #283:激进路径视觉重设计的 brainstorm 决策流程(5 轮 mockup + 多选)
- #284:案卷纸纹理 CSS 实现(repeating-linear-gradient 横线纸)
- #285:褪色档案深色模式(老化纸张语义替换暗色)
- #286:毛笔笔触图表改造(stroke-width 3.5 + round + 印章数据点)
- #287:三材质区分的 CSS 背景纹理方案(方格本/宣纸/案卷)

---

**文档版本**:v1.0
**下一步**:待用户确认后进入 Phase 6.6.1 令牌层实施
