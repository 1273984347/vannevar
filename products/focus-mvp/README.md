# Vannevar · Focus MVP

面向学习与工作场景的最小可行产品：一个基于 Chrome Extension Manifest V3 的浏览器插件，使用 SQLite（sql.js）本地存储用户的工作上下文（浏览器标签页），支持一键保存与恢复。

> **术语说明**：本文档中"上下文"与"快照"指同一对象——某时刻浏览器标签页集合的一次保存。代码层（数据库表名 `snapshots`、消息 action `*_SNAPSHOT`）保留"snapshot"命名以兼容历史数据；用户面向文案统一称为"上下文"。

## 功能

- **保存当前上下文**：捕获当前窗口所有标签页的 URL 和标题
- **恢复上下文**：一键重新打开保存的标签页，带进度反馈
- **上下文搜索**：按标题关键词搜索历史上下文
- **编辑标题**：双击上下文标题自定义命名
- **标签页预览**：展开上下文查看包含的标签页列表，可直接打开
- **Resume 摘要**：自动生成 session 摘要（场景识别、热门站点、关键词、下一步建议）
- **快捷键**：`Alt+Shift+S` 保存、`Alt+Shift+R` 恢复最近
- **数据导出/导入**：JSON 格式备份与恢复，数据完全可控
- **数据损坏自动备份**：存储损坏时自动备份原数据，避免丢失
- **删除记录**：二次确认清理不需要的上下文
- **本地 SQLite 存储**：数据以 SQLite 数据库形式保存在浏览器本地 storage 中

## 目录结构

```
focus-mvp/
├── manifest.json          # 扩展清单
├── popup.html / popup.css / popup.js   # 弹出界面
├── background.js          # Service Worker 后台逻辑
├── db.js                  # SQLite 存储封装
├── snapshot.js            # 标签页捕获与恢复
├── sync.js                # 云端同步（push/pull + LWW）
├── auto-sync.js           # 自动同步调度（alarm + 去抖）
├── crypto.js              # Phase 4.4 E2E 加密（AES-GCM-256 + PBKDF2）
├── crypto-profile.js      # Phase 4.4 加密档案同步（跨设备解密）
├── auth.js                # JWT 鉴权
├── agent.js               # Agent 主动建议
├── ai.js / ai-router.js / local-model.js  # AI 推理
├── resume.js              # Session 摘要
├── task-watcher.js        # Phase 4.1 Bridge 任务变更通知（HTTP 轮询）
├── bridge-link.js         # Phase 4.1 跨产品联动
├── options.html / options.css / options.js  # 配置页
├── privacy.html
├── shared/                # 跨产品共享组件（toast / vannevar-shared）
├── lib/
│   ├── sql-wasm.js / sql-wasm.wasm     # sql.js
│   ├── sql-wrapper.js
│   ├── vannevar-crypto.js  # Phase 4.4 E2E 加密核心（与 shared/ 源字节一致，门禁校验）
│   └── vannevar-context-card.js
├── icons/
└── tests/
    ├── e2e/               # Playwright 端到端测试
    ├── mocks/             # Node 单测 mock（auth.js / db.js）
    ├── loader.mjs / loader-hooks.mjs    # ESM resolver 测试基础设施
    ├── requirements.txt
    ├── test_crypto.mjs               # crypto.js 单测
    └── test_sync_crypto.mjs          # sync.js + crypto.js 集成测试
```

## 安装与运行

1. 打开 Chrome / Edge 浏览器，进入 `chrome://extensions/`
2. 开启右上角「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择仓库内的 `products/focus-mvp` 文件夹
5. 点击工具栏中的 Vannevar 图标即可使用

## 使用方式

1. 打开一组工作相关的标签页
2. 点击浏览器工具栏的 Vannevar 图标
3. 点击「保存当前上下文」
4. 需要恢复时，再次点击图标，找到对应记录并点击「恢复」

## 手动测试步骤

如果加载后想验证功能是否正常：

1. 打开 3 个任意网页（比如百度搜索、知乎、GitHub）
2. 点击 Vannevar 图标 → 点击「保存当前上下文」
3. 关闭这 3 个网页，只留下一个空白标签页
4. 再次点击 Vannevar 图标 → 找到刚才保存的记录 → 点击「恢复」
5. 观察是否重新打开了刚才的 3 个网页

如果恢复成功，说明 SQLite 存储、标签页捕获、标签页恢复三条链路都通了。

## E2E 加密同步

设置页（Options）提供「E2E 加密同步」卡片（`options.html` / `options.js`），可为云端同步启用端到端加密。

- **算法与参数**：AES-GCM-256 加密内容；PBKDF2-SHA256 派生密钥，迭代 100,000 次；随机 16 字节 salt + 12 字节 nonce
- **加密时机**：启用后，快照在**本地加密完成后**才上传，Bridge 只存得到 `ciphertext` + `nonce`，永远看不到明文
- **passphrase 处理**：仅存在于内存，不写入 `chrome.storage`、不上传 Bridge、不参与任何网络请求；扩展 / Service Worker 重启后密钥即丢失，需重新输入 passphrase 解锁
- **跨设备解密**：在另一台设备上登录同一账号后，用**同一个 passphrase** 解锁，即可解密该设备拉取的、此前由本机推送的密文 —— PBKDF2 的 salt 是**公开参数（不是密钥）**，经 Bridge 档案端点 `GET/PUT /api/auth/me/crypto-profile` 同步；档案另含 verification 密文，用于反验 passphrase 是否一致。**passphrase 永不落盘、永不上云**，服务端既无法反推 passphrase，也无法解密任何同步内容
- **不可恢复**：passphrase 遗忘后云端密文**无法恢复**，请自行存入密码管理器或离线介质

> 加密核心与 Muse 共用同一份实现：共享层源文件 `shared/vannevar-crypto.js`，Focus 运行副本 `products/focus-mvp/lib/vannevar-crypto.js`，由 `scripts/check_shared_sync.py` 门禁校验三者字节一致（STRICT 级，漂移即阻断）。

## 测试

### 端到端测试（Playwright + Python）

```bash
pip install -r tests/requirements.txt
playwright install chromium
python tests/e2e/test_focus.py
```

### 单元测试（Node.js + ESM resolver）

```bash
# crypto.js 单测：AES-GCM-256 加密 / PBKDF2 派生 / 密钥不出边界
# crypto.js 是真实模块，不需要 mock 重定向 → 不带 --import
node tests/test_crypto.mjs
# 实测：16 通过 / 0 失败

# sync.js + crypto.js 集成测试：加密锁定时跳过 push / pullSync 解密 / 跨上下文 unlock
# / push 成功后把加密档案同步到 Bridge（跨设备前提）
# 必须带 --import ./tests/loader.mjs
node --import ./tests/loader.mjs tests/test_sync_crypto.mjs
# 实测：12 通过 / 0 失败
```

> `test_crypto.mjs` **不带** `--import` 即可（实测 16 通过 / 0 失败）；而 `test_sync_crypto.mjs`
> 与 Muse 的 `test_media_session.mjs` **必须**带 `--import ./tests/loader.mjs`——否则 mock 缺失，
> 加载真实 `lib/sql-wrapper.js` 时会抛 `ReferenceError: self is not defined`（Node 没有浏览器全局 `self`），
> 导致大量用例失败。
> `test_sync_crypto.mjs` 的 12 条用例中，最后一条为「B-P1-4: push 成功后把加密档案同步到 Bridge（跨设备前提）」，
> 覆盖上文「E2E 加密同步」所述的跨设备解密前提（salt 档案上云）。

ESM resolver（`tests/loader-hooks.mjs`）把 `sync.js` 内部的 `./auth.js` / `./db.js` 重定向到 `tests/mocks/`，避免加载 SQLite WASM 和真实 auth 模块。crypto.js 保持原样加载（真实模块，需 WebCrypto API）。

## 常见问题

**扩展加载时提示「无法加载」**
- 检查 `lib/sql-wasm.wasm` 文件是否存在（约 1MB）
- 检查 `icons/` 目录下是否有 `icon16.png`、`icon48.png`、`icon128.png`
- 检查 `manifest.json` 中的 `content_security_policy` 是否只包含 `'self'` 和 `'wasm-unsafe-eval'`，不要包含 `'unsafe-eval'`（Manifest V3 不允许）

**点击保存后没有反应**
- 按 `F12` 打开 popup 的开发者工具（右键点击图标 → 检查弹出内容）
- 查看 Console 是否有红色报错
- 把报错内容贴出来即可定位问题

## 已知限制与设计取舍

### CSP 的 `connect-src` 允许任意 HTTPS 源

`manifest.json` 的 `content_security_policy.extension_pages` 写作
`script-src 'self' 'wasm-unsafe-eval'; object-src 'self'; connect-src 'self' https: http://localhost:* http://127.0.0.1:*; ...`，
其中 **`connect-src` 允许任意 HTTPS 源**。

这是有意保留的取舍，不是疏漏：产品需要支持用户在 Options 页**自配置的 AI 端点**（可以是任意 HTTPS API URL），
一旦收紧 `connect-src`，用户填的自定义端点会被 CSP 直接拦掉。

未来可通过 `activeTab` 权限或动态 `host_permissions` 进一步细化收紧（例如按用户实际填写的端点域名申请权限）。

---

## 技术说明

- **SQLite 实现**：使用 [sql.js](https://sql.js.org/) 在浏览器环境中运行 SQLite，数据库以 Uint8Array 形式导出并存储到 `chrome.storage.local`
- **本地优先**：所有数据默认保存在本地，不上传任何服务器
- **权限最小化**（以 `manifest.json` 为准）：
  - `host_permissions` 为**空** —— 不申请任何站点范围的访问权
  - `permissions`：`tabs`、`storage`、`unlimitedStorage`、`activeTab`、`windows`、`alarms`、`notifications`
  - `optional_permissions`：`contextMenus` —— 属**可选**权限，需用户在扩展详情页按需手动授予；
    代码里对应逻辑统一用 `if (chrome.contextMenus)` 做了存在性判断，未授权时自动跳过

## 后续优化方向

- 接入大模型生成更智能的 session 摘要（Resume 进阶）
- 支持保存本地文件路径（配合桌面端）
- 端到端加密的**密钥轮换**：更换 passphrase 时目前会生成新 salt，历史密文因此永久不可解密；理想做法是保留旧 salt 的解密能力（多密钥并存）
- 新增 onboarding 引导与删除撤销
- 支持标签页分组与跨窗口管理
