# Focus — Chrome Web Store Listing 材料

> 对应扩展目录：`products/focus-mvp/`；打包产物：`dist/focus-mvp.zip`。

## Store name

`Vannevar Focus — 上下文快照与恢复`（英文 locale：`Vannevar Focus — Context Snapshots`）

## Short description（≤132 字符）

**zh-CN**

> 一键保存浏览器完整工作现场：标签页、标题、URL 与选中片段。关闭后再一键恢复，AI 自动生成上下文摘要。

**en**

> Snapshot your work session: tabs, titles, URLs, selections. Restore in one click, with an AI context summary.

（实测：zh 63 字符 / en 118 字符，均 ≤132 上限）

## Detailed description

**zh-CN**

```markdown
Vannevar Focus 把「此刻的工作现场」凝固成可恢复的快照。

你是否也遇到过：开了 20 个标签页做调研，第二天（或被一个会议打断后）再也拼不回当时的上下文？

Focus 一键完成三件事：
① 快照 —— 当前窗口所有标签页的标题、URL、你的选中片段，一次存档；
② 恢复 —— 一键重建完整工作现场（快捷键 Alt+Shift+S 保存 / Alt+Shift+R 恢复最近一次）；
③ AI 上下文标签 —— 自动标注「深度专注 / 多线并行 / 需尽快处理」，回到任务前 3 秒看懂当时在做什么。

隐私是默认项，不是选项：
- 快照只存本机（chrome.storage.local + SQLite WASM）；
- 与你自架的 Bridge 服务同步时，数据先经 AES-GCM-256 端到端加密；
- AI 增强完全可选：未配置任何 API 时，先尝试 Chrome 端侧 AI（零配置、零上传），不可用则使用内置规则引擎 —— 核心功能零外部依赖。

适合：深度工作者、多项目并行者、研究型阅读者。
```

**en**

```markdown
Vannevar Focus freezes "your current work context" into restorable snapshots.

Ever lost 20 research tabs to a meeting or a reboot — and never rebuilt the context?

Focus does three things:
① Snapshot — every tab's title, URL and your text selections, archived in one click;
② Restore — rebuild the full session instantly (Alt+Shift+S save / Alt+Shift+R restore latest);
③ AI context tags — "deep focus / multi-track / urgent", so you re-enter the task in 3 seconds.

Privacy is the default, not an option:
- Snapshots stay on-device (chrome.storage.local + SQLite WASM);
- Syncing to your self-hosted Bridge is end-to-end encrypted (AES-GCM-256);
- AI is optional: with nothing configured, Focus tries Chrome's built-in on-device AI (zero config, zero upload), then falls back to a local rule engine — the core works with zero external dependencies.

For deep workers, multi-project switchers and research readers.
```

## Single purpose（单一用途声明）

**zh**：保存与恢复浏览器标签页工作现场，并提供本机生成的上下文摘要与标签。
**en**：Save and restore the browser tab session as context snapshots, with locally generated summaries and tags.

## Permission justifications（逐条）

| 权限 | 理由（可直接粘贴） |
|---|---|
| `tabs` | 核心功能：读取当前窗口标签页的标题与 URL 以创建/恢复快照；恢复快照需要重新打开标签页。 |
| `storage` | 将快照与扩展设置保存在本地。 |
| `unlimitedStorage` | 快照包含大量标签页元数据与用户选中文本，超出默认 5MB 本地存储配额。 |
| `activeTab` | 在用户点击扩展时读取当前页选中片段（用户显式保存动作的一部分），不读常规浏览行为。 |
| `windows` | 恢复快照时按原始窗口分组重新打开标签页。 |
| `alarms` | 定时触发自动同步（用户启用后），无用户交互的后台保活。 |
| `notifications` | 提示同步结果（成功/失败）与跨设备加密档案口令不一致等需要用户处理的状态。 |

## Data usage（Privacy 标签页作答）

- Does this item collect or use personal data? — **No**（快照仅存本机；用户显式配置并启用同步时，数据经端到端加密后传输到**用户自己部署**的 Bridge 服务，开发者与第三方均无法读取）。
- Does this item use remote code? — **No**（CSP 限定 `'self'`，全部代码本地打包）。
- 卖点补充：无遥测、无账号体系（可选连接用户自架服务）。

## Screenshots（1280×800）

1. `screenshots/focus-popup-expanded.png`（主图：弹窗内已保存上下文 + AI 摘要 + 标签）
2. `screenshots/focus-options.png`（设置页：AI 增强「可选」定位）
