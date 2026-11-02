# Muse — Chrome Web Store Listing 材料

> 对应扩展目录：`products/muse-mvp/`；打包产物：`dist/muse-mvp.zip`。

## Store name

`Vannevar Muse — 灵感捕捉与氛围收藏`（英文 locale：`Vannevar Muse — Mood-based Clippings`）

## Short description（≤132 字符）

**zh-CN**

> 选中网页文字即成灵感：按「氛围」而非文件夹组织，时间线回顾，Dashboard 看清创作脉络。

**en**

> Clip any webpage selection as an inspiration. Organize by mood, not folders — review your creative thread on a timeline.

（实测：zh 49 字符 / en 125 字符）

## Detailed description

**zh-CN**

```markdown
Vannevar Muse 把「刷到的那一下心动」存成看得见的灵感线。

收藏夹的问题：存进去 100 条，再也翻不出来。因为人记住的不是文件夹路径，是「当时的感觉」。

Muse 用「氛围」组织灵感：
① 捕捉 —— 任意网页选中文字 → 点扩展图标 → 备注 + 氛围标签，3 秒入库；
② 聚合 —— 温暖 / 冷峻 / 诗意 / 理性……同氛围的灵感自然聚成一面墙；
③ 回顾 —— Dashboard 时间线按天回看：灵感分布、高频来源、关键词，创作脉络一目了然。

隐私是默认项，不是选项：
- 灵感只存本机（chrome.storage.local + SQLite WASM）；
- 与你自架的 Bridge 同步时，端到端加密（AES-GCM-256 + PBKDF2），服务端只见密文；
- AI 增强完全可选：未配置 API 时先尝试 Chrome 端侧 AI（零配置、零上传），不可用则用内置规则引擎（基于频率统计），核心功能零外部依赖。

适合：写作者、设计师、研究者、所有「看到好东西想留住」的人。
```

**en**

```markdown
Vannevar Muse turns that "stop-the-scroll moment" into a visible inspiration thread.

Bookmark folders fail because you remember feelings, not paths.

Muse organizes by **mood**:
① Capture — select text on any page, click the icon, add a note + mood tag: saved in 3 seconds;
② Cluster — 温暖 / poetic / rational… same-mood inspirations naturally form a wall;
③ Review — the dashboard timeline shows daily inspirations, top sources and keywords: your creative thread at a glance.

Privacy is the default, not an option:
- Inspirations stay on-device (chrome.storage.local + SQLite WASM);
- Syncing to your self-hosted Bridge is end-to-end encrypted (AES-GCM-256 + PBKDF2);
- AI is optional: Chrome's built-in on-device AI first (zero config, zero upload), then a frequency-based rule engine — the core works with zero external dependencies.

For writers, designers, researchers — everyone who wants to keep the good bits.
```

## Single purpose（单一用途声明）

**zh**：捕捉网页选中文本为灵感卡片，按氛围标签在本机组织并提供时间线回顾。
**en**：Capture webpage selections as inspiration cards, organized locally by mood tags with a timeline review.

## Permission justifications（逐条）

| 权限 | 理由（可直接粘贴） |
|---|---|
| `storage` | 将灵感、氛围标签与扩展设置保存在本地。 |
| `unlimitedStorage` | 灵感含原文片段、备注与媒体元数据，长期积累超出默认 5MB 配额。 |
| `activeTab` | 用户点击扩展保存灵感时，读取当前页的标题、URL 与其选中的文本（用户显式动作的一部分）。 |
| `scripting` | 用户点击「保存灵感」时向当前页注入选择器脚本，以精确获取用户高亮的文本片段；仅在用户显式点击后执行。 |
| `contextMenus` | 提供右键「保存为灵感」入口，作为工具栏图标之外的等价捕捉方式。 |
| `alarms` | 定时触发自动同步（用户启用后）。 |
| `notifications` | 提示保存/同步结果与端到端加密状态（如跨设备口令不一致需用户处理）。 |

## Data usage（Privacy 标签页作答）

- Does this item collect or use personal data? — **No**（灵感仅存本机；同步经端到端加密，发往用户自架的 Bridge，开发者与第三方均无法读取）。
- Does this item use remote code? — **No**（CSP `'self'`，全部代码本地打包）。

## Screenshots（1280×800）

1. `screenshots/muse-dashboard.png`（主图：氛围分布 + 时间线 + 关键词）
2. `screenshots/muse-popup-saved.png`（弹窗：保存成功的灵感卡片）
3. `screenshots/muse-options.png`（设置页：E2E 加密与 AI 增强「可选」定位）
