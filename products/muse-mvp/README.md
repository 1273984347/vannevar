# Vannevar · Muse MVP

面向生活与娱乐场景的最小可行产品：一个基于 Chrome Extension Manifest V3 的灵感捕获工具。浏览网页时，一键保存当前页面、选中的文字片段、备注和氛围标签，并在「灵感博物馆」仪表盘里按时间轴回顾与搜索。

## 功能

- **捕捉灵感**：保存当前标签页的标题、URL、选中文字
- **添加备注与氛围**：为每条灵感写备注，并打上「日常 / 夜晚 / 兴奋 / 忧郁 / 专注 / 放松」标签
- **快捷键捕获**：`Alt+Shift+M` 一键捕获当前页面+选中文本，无需打开 popup
- **灵感博物馆**：在扩展内打开仪表盘，查看时间轴、按氛围筛选、关键词搜索、删除
- **编辑灵感**：双击标题编辑，回车保存 / ESC 取消
- **后端搜索**：标题/备注/选中文字/氛围模糊匹配
- **灵感回顾（Resume）**：自动生成灵感库摘要（氛围分布、热门来源、关键词、下一步建议）
- **popup 历史列表**：弹窗内查看最近 5 条灵感 + 搜索
- **数据导出/导入**：JSON 格式备份与恢复，数据完全可控
- **数据损坏自动备份**：存储损坏时自动备份原数据，避免丢失
- **Options 页面**：数据管理 + 快捷键说明 + 隐私声明 + 氛围标签管理 + 关于
- **氛围标签自定义**：在 options 页面增删改氛围标签，自定义颜色
- **灵感收藏/置顶**：重要灵感置顶，置顶区独立展示
- **来源域名聚合视图**：按网站分组浏览灵感
- **本地 SQLite 存储**：数据以 SQLite 数据库形式保存在浏览器本地 storage 中

## 目录结构

```
muse-mvp/
├── manifest.json          # 扩展清单
├── core/
│   ├── background.js      # Service Worker + 消息路由
│   ├── db.js              # SQLite 存储封装
│   ├── muse.js            # 灵感数据模型与捕获逻辑
│   ├── media-group.js     # Phase 4.2 按媒体分组纯函数
│   ├── sync.js            # 云端同步（push/pull + LWW）
│   ├── auto-sync.js       # 自动同步调度（alarm + 去抖）
│   ├── auth.js            # JWT 鉴权
│   ├── agent.js           # Agent 主动建议
│   ├── ai.js / ai-router.js / local-model.js  # AI 推理
│   ├── resume.js          # 灵感回顾摘要
│   └── bridge-link.js     # Phase 4.1 跨产品联动
├── popup/
│   ├── popup.html         # 捕捉弹窗
│   ├── popup.css
│   └── popup.js
├── dashboard/
│   ├── dashboard.html     # 灵感博物馆（时间/来源/按音乐视图）
│   ├── dashboard.css
│   └── dashboard.js
├── options/
│   ├── options.html       # 数据管理 + AI 配置 + 氛围标签管理
│   ├── options.css
│   └── options.js
├── shared/                # 跨产品共享组件
│   ├── escape.js / toast.js / moderation.js
│   ├── vannevar-shared.js / vannevar-brand.css
│   └── vannevar-context-card.js
├── lib/                   # sql.js + sql-wrapper
├── icons/
└── tests/
    ├── e2e/               # Playwright 端到端测试
    ├── mocks/             # Node 单测 mock（db.js）
    ├── loader.mjs / loader-hooks.mjs  # ESM resolver 测试基础设施
    ├── requirements.txt
    └── test_media_session.mjs  # Phase 4.2 媒体会话单测
```

## 安装与运行

1. 打开 Chrome / Edge，进入 `chrome://extensions/`
2. 开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择仓库内的 `products/muse-mvp` 文件夹
5. 点击工具栏中的 Muse 图标即可使用

## 使用方式

1. 在任意网页上选中一段文字（可选）
2. 点击工具栏的 Muse 图标
3. 输入备注，选择氛围标签，点击「保存灵感」
4. 点击「打开灵感博物馆」查看所有记录

## 测试

### 端到端测试（Playwright + Python）

```bash
pip install -r tests/requirements.txt
playwright install chromium
python tests/e2e/test_muse.py
```

### 单元测试（Node.js + ESM resolver）

```bash
# Phase 4.2 媒体会话集成测试：captureMediaMetadata / saveInspiration / groupByMusic
# 使用 ESM resolver 把 muse.js 的 ./db.js 重定向到 tests/mocks/db.js（避免加载 SQLite WASM）
node --import ./tests/loader.mjs tests/test_media_session.mjs
```

> `--import ./tests/loader.mjs` **必须带**：loader 把 `core/muse.js` 的 `./db.js` 重定向到
> `tests/mocks/db.js`，绕开真实 `lib/sql-wrapper.js` / `lib/sql-wasm.js` 的 UMD 挂载
> （Node 没有浏览器全局 `self`）。
> 不带 loader 实测为 7/18 通过、11 失败（`ReferenceError: self is not defined`）——那是运行方式不对，不是缺陷。
> 同理，Focus 的 `test_sync_crypto.mjs` 也必须带 loader；而 Focus 的 `test_crypto.mjs` 不需要。

测试覆盖：
- `saveInspiration` 写入 / 读取 media 字段（mediaType/mediaArtist/mediaAlbum/mediaArtworkUrl）
- `captureMediaMetadata` URL 启发式 source 推断（YouTube→video / 喜马拉雅→podcast / 默认→music）
- `captureMediaMetadata` artwork 数组按 size 降序取最大（pageMetadata 模式实际调用注入 func）
- `groupByMusic` 纯函数分组逻辑（artist 优先 / title 兜底 / 无媒体归入"(未关联媒体)"组且始终在最后）

## 已知限制与设计取舍

### `optional_host_permissions` 包含 `https://*/*`

`manifest.json` 的 `optional_host_permissions` 声明了 `https://*/*`，这是有意保留的取舍，不是疏漏：
`captureInspiration` 需要读取任意页面内容（标题、选中文本、媒体元数据等），没有该权限就只能覆盖
用户主动打开的当前标签页，无法在后台标签页上捕获灵感。

它是**可选**权限：安装时**不默认启用**，需用户在安装时或扩展详情页按需手动授权。
`permissions` 中的 `activeTab` + `scripting` 已足够覆盖「捕获当前标签页」这一默认场景，
所以安装时不会提示「读取所有网站数据」。

未来可改为按用户实际访问的域名动态申请 `host_permissions`，进一步收窄授权范围。

---

## 技术说明

- 基于统一的扩展骨架（与 Focus 共享 db.js / snapshot.js 模式）扩展而来
- 使用 `chrome.scripting.executeScript` 在 `activeTab` 上获取选中文本
- 危险 URL scheme 与 Focus 保持一致，会被过滤
