# 架构决策记录（ADR）

> 本文件记录 Vannevar 中**不易从代码直接读出**的关键取舍：每个决策是什么、为什么这么选、**代价是什么**、以及影响到的其他部分。
>
> 格式：决策 → 理由 → 代价 → 现状。修改既有决策请在下方「决策变更日志」追加，不要原地改写历史。

---

## 1. Manifest V3 + Service Worker + ES Module

- **决策**：扩展使用 MV3，`background.service_worker.type: "module"`，后台脚本用 ES `import` 拆分模块（`db.js` / `snapshot.js` / `crypto.js` / `sync.js` …）。
- **理由**：MV2 已被 Chrome 淘汰；ES Module 让扩展代码可以像普通前端项目一样按职责拆文件，而不是堆在一个巨大的 Service Worker 里。
- **代价**：
  - Service Worker 中**动态 `import()` 被规范禁止**（只能静态 import），第三方 UMD 库需要打补丁才能在 Service Worker 里挂载 —— 这也是仓库里 `lib/sql-wasm.js` 需要手工模块化的原因。
  - MV3 的 Service Worker 会被浏览器随时终止，所有状态必须落盘、不能只放内存。
- **影响**：Focus 与 Muse 共享同一套 `manifest.json` 骨架与 `sql-wasm` 兼容补丁。

## 2. SQLite 本地存储：sql.js + `chrome.storage.local`

- **决策**：在浏览器里用 sql.js（SQLite 的 WebAssembly 构建）跑 SQLite，数据库二进制序列化后存进 `chrome.storage.local`。
- **理由**：需要结构化查询、快照历史、按时间排序这类能力（纯 IndexedDB / localStorage 写起来成本高）；同时保持"数据完全本地、不上传"的产品前提。
- **代价**：
  - 需要加载约 1 MB 的 wasm。
  - **`chrome.storage.local` 约有 5 MB 容量上限**（未授予 `unlimitedStorage` 时）—— 数据量大时需要迁移到 IndexedDB 或申请无限存储权限。
  - 每次读写都要做「二进制 ↔ Base64」的转换。
- **影响**：Focus 与 Muse 用同一套存储模式；Bridge 走服务端 SQLite，两者的取舍不同（见 §6）。

## 3. 权限最小化，但保留必要的入口

- **决策**：按需申请权限，**不放宽到"能拿就拿"**。
  - Focus 实际申请：`tabs`、`storage`、`unlimitedStorage`、`activeTab`、`windows`、`alarms`、`notifications`；`contextMenus` 放在 `optional_permissions`。
  - Muse 实际申请：`storage`、`unlimitedStorage`、`activeTab`、`scripting`、`contextMenus`、`alarms`、`notifications`；`optional_host_permissions` 含 `https://*/*`。
  - 两者的 `host_permissions` 均为**空**。
- **理由**：每多一个权限，商店审核与用户信任成本都上升。
- **代价**：
  - Muse 的 `optional_host_permissions` 含 `https://*/*`（捕捉灵感需要读取任意页面内容），安装时需用户手动授权、非默认启用。
  - Focus 的 CSP `connect-src` 必须允许任意 HTTPS（用户自配 AI 端点），这削弱了 CSP 的收敛度。
- **防御性设计**：右键菜单调用统一包在 `if (chrome.contextMenus)` 里 —— 权限被撤销时，popup 里的核心保存功能仍然可用。

## 4. 不引入前端框架

- **决策**：两个扩展的 popup / dashboard / options 全部用原生 HTML / CSS / JS 编写。
- **理由**：扩展不需要构建步骤，能省掉一整条工具链；产品状态简单，框架的收益不足以抵消复杂度。
- **代价**：状态管理偏原始，组件复用靠手写。
- **变更可能**：若某个界面状态复杂度显著上升，可单独引入框架 —— 这不与"不引入"冲突。

## 5. 上下文恢复策略：替换空白标签页 vs 开新窗口

- **决策**：若当前窗口只剩 `chrome://newtab`，就替换它；否则**打开新窗口**恢复所有标签页。
- **理由**：避免用户眼前突然涌入一堆标签页盖掉正在进行的工作；只有空白页时才直接复用当前窗口。
- **影响**：同样适用于 Bridge 那种需要一次打开多个协作页面的场景。

## 6. Bridge 的存储层：SQLite 起步，但边界划在 `database.py`

- **决策**：Bridge 默认用本地 SQLite（WAL 模式），所有持久化访问收敛在 `database.py`，通过 `BRIDGE_DATABASE_URL` 可切换到 PostgreSQL。
- **理由**：公益场景的部署环境不可控，零外部依赖的 SQLite 最稳；同时把数据库方言锁在一层里，将来换库不改业务代码。
- **代价**：
  - SQLite 的并发写能力有限，因此启用 WAL + `busy_timeout=5000` + `foreign_keys=ON`。
  - 单机部署，无法横向扩展。
- **重要约束**：**备份目录必须与数据库文件同级**。早期实现里备份目录固定在代码目录下，导致用 `BRIDGE_DATABASE_URL` 指向别处时（典型如 Docker 部署）备份体系整体失效。现在 `backup.py` 从 engine 的实际 URL 推导库路径，备份落在库同级的 `backups/`（Docker 下即 `/app/data/backups`）。

## 7. 数据库恢复：原子替换，而不是覆盖写

- **决策**：恢复流程固定为七步 —— 校验备份本身 → 释放连接池 → 保留损坏库审计副本 → 复制到临时文件 → 校验临时文件 → `os.replace` 原子替换 → 清除旧库的 WAL 侧车 → 用**独立连接**复核。
- **理由**：早期实现直接覆盖目标文件，在 Windows 上因为连接池仍持有句柄，rename 失败后转而"直接覆盖"一个仍被打开的文件，产生**报告成功但结果仍损坏**的库；且从不清理属于旧库的 `-wal` / `-shm` 侧车，也不校验备份自身是否完好。
- **代价**：流程比"直接 copy"长，但对管理员操作（低频）完全可接受。
- **配套要求**：实现里的每个关键分支都必须有**反向验证**（故意破坏该分支，确认测试会变红），否则"测试全绿"可能只是底层库顺带行为的假象。

## 8. 对比度门禁用棘轮，不用"一票否决"

- **决策**：`scripts/check_contrast.py` 的判定分两类 —— **新增**的不达标项直接阻断退出码；**已登记在 `KNOWN_BASELINE` 里的历史遗留项**照常完整报告但不阻断。
- **理由**：若不区分，一次性把所有历史不达标项都算作失败，CI 会恒红，反而掩盖真正的新问题；基线修好一项就移除一项，形成单向棘轮。
- **代价**：基线需要有人主动维护，否则会退化成"永久豁免"。

---

## 决策变更日志

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-10-25 | 补充 §6 的备份目录约束 | 修复了「数据库路径与备份路径分叉」导致 Docker 部署下备份体系整体失效的缺陷 |
| 2026-10-25 | 补充 §7 的恢复流程与反向验证要求 | 修复了「恢复报告成功但结果仍损坏」的缺陷，并发现其中一条清理逻辑因文件名拼接错误**从未生效** |
| 2026-10-25 | §3 权限清单按实际 `manifest.json` 更新 | 原记录写于开发早期，与最终实现已有出入 |
| 2026-10-25 | 补充 §8 对比度门禁的棘轮机制 | 避免"门禁恒红"掩盖真实新问题 |
