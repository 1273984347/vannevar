# 变更日志

本文件记录 Vannevar 项目所有值得注意的变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

**共享层**

- 新增 `shared/vannevar-crypto.js`：参数化的端到端加密核心（`createCryptoModule` 工厂），Focus 与 Muse 共用同一份实现，避免两套加密代码各自演化。

**Muse —— 补齐端到端加密**

- Muse 的灵感与氛围标签现在经 **AES-GCM-256 + PBKDF2-SHA256（100,000 次）** 加密后再上传，Bridge 服务端只能看到密文。
- Muse 设置页新增「E2E 加密同步」区：启用 / 解锁 / 锁定 / 禁用；加密已启用但未解锁时，自动同步会暂停并通过系统通知提醒，而不是推送明文或反复失败。

**跨设备解密**

- 新增加密档案端点 `GET` / `PUT` `/api/auth/me/crypto-profile` 与数据表 `user_crypto_profiles`（数据库迁移 `user_version` v11 → v12）。PBKDF2 salt 作为**公开参数**随档案同步，使另一台设备用**同一个口令**即可解密历史密文；口令本身永不上云、永不落盘。
- Focus 与 Muse 的设置页均支持「采用 Bridge 上的加密档案」解锁，并在 push / pull 成功后自动补齐云端档案（老用户首次同步即自动完成）。

### Fixed

- **Muse 的云同步在生产模式下必然 403**：Muse 的 push 请求此前不带 `X-CSRF-Token` 头，而 Bridge 的写端点强制校验该头（须等于 JWT 的 `csrf_jti`）。现已统一通过 `getBridgeHeaders()` 注入；Focus 侧此前已修复并有专门用例覆盖，Muse 侧本批补齐。
- **文档与实现失真**：`docs/phase4-plan.md` 中 Phase 4.1 的 6 条验收项实际早已实现（后端端点、扩展接入、UI、800+ 行测试齐备）却从未回填，显示为「未完成」；`tech/tech-spec.md` 的端到端加密云存储仍标为「规划中」；`demo/designs/visual-redesign-spec.md` 状态仍为「待实施」（实际已落地）。以上均已校准。
- 说明：`0.1.0` 发布说明中「Muse —— 与 Bridge 的双向同步，同样支持端到端加密」一句，在当时的实现下并不成立（Muse 同步实为明文）。按发布记录不追溯改写的惯例，此处在 `Unreleased` 留痕，实际能力于本次补齐。

### Security

- Muse 同步内容由明文改为密文上行，消除「用户以为数据已加密、实际服务端可直接读取」的隐私缺口；此前状态违反 `AGENTS.md` §2.3「同步内容必须经端到端加密」。

### Testing

- 新增 `products/muse-mvp/tests/test_crypto.mjs`（24 条，含两个独立模块实例互解的跨设备用例）、`products/muse-mvp/tests/test_sync_crypto.mjs`（14 条）、`products/bridge-mvp/tests/test_crypto_profile.py`（12 条）。
- 新增门禁 `scripts/check_shared_sync.py`（共享层副本一致性，已接入 `scripts/qa_all.py`，强约束副本组漂移即阻断）。
- 测试基线：Bridge **447 passed / 9 skipped / 0 failed**；Focus 16 + 12；Muse 24 + 14 + 18。

### Documentation

- 根 `README.md` 补齐跨设备解密说明、加密档案的安全边界、修正后的测试基线与代码体量统计。
- `products/muse-mvp/README.md` 与 `products/focus-mvp/README.md` 补齐 E2E 加密与跨设备解密章节。

## [0.1.0] - 2026-10-25

> 发布说明与可下载产物：<https://github.com/1273984347/vannevar/releases/tag/v0.1.0>

首个公开版本。三个产品（Focus / Muse / Bridge）以同一套内核完成首次完整发布。

### Added

**Focus —— 捕捉（Chrome MV3 扩展）**

- 标签页快照：保存标签页集合、标题、URL 与选中片段，并支持一键恢复。
- AI 上下文标签：自动为快照标注「深度专注 / 多线并行 / 需尽快处理」等状态。
- 快捷键：`Alt+Shift+S` 保存当前上下文，`Alt+Shift+R` 恢复最近一次。
- Options 设置页：AI 增强配置（API Base URL / API Key / 模型）与端侧本地模型选择。
- 与 Bridge 的双向同步：上下文经端到端加密后上行。

**Muse —— 整理（Chrome MV3 扩展）**

- 选中片段捕获：保存网页中被选中的文本与来源链接。
- 氛围（mood）标签体系：按「当时的感觉」而非文件夹组织灵感，同氛围内容自动聚合。
- Dashboard 时间线：按天回顾捕获过的灵感。
- 媒体元数据捕获：识别 YouTube / 喜马拉雅等来源的媒体类型与封面信息。
- 与 Bridge 的双向同步，同样支持端到端加密。

**Bridge —— 交接（FastAPI Web 服务）**

- 项目看板：项目、任务、留言、交接记录的完整数据模型与页面。
- AI 交接摘要：基于任务、留言与历史交接一键生成交接摘要，支持 SSE 流式输出。
- 二维码扫码接力：生成项目二维码，志愿者扫码即可接手当前进度。
- 实时推送：SSE 事件流，向在线端点推送任务与项目更新。
- AI 对话助手：项目内的上下文问答入口。
- 数据层：SQLAlchemy ORM + SQLite（WAL 模式 + 滚动备份），`BRIDGE_DATABASE_URL` 可切换至 PostgreSQL。
- 运维脚本：备份与恢复、演示数据播种、dogfood 截图与 SSE 自检脚本。

**共享层与跨产品能力**

- 设计系统：`shared/vannevar-brand.css` 设计令牌，三个产品共用同一套视觉语言。
- Context Card（`.vcf`）格式：`shared/vannevar-context-card.js` 与 Bridge 的 `context_card.py`，用于跨产品传递上下文条目。
- 统一 AI 降级策略：优先调用 OpenAI 兼容 API（超时上限 30 秒），未配置或调用失败时自动降级到本地规则引擎，核心功能零外部依赖。
- 本地优先存储：扩展侧使用 sql.js（SQLite WASM）+ `chrome.storage.local`，用户上下文默认不离开本机。

### Security

- 端到端加密：同步数据在离开浏览器前加密，算法为 **AES-GCM-256**，密钥经 **PBKDF2-SHA256（100,000 次迭代）** 派生，派生密钥为 non-extractable、仅存在于内存，用户口令不落盘、不上传。
- Bridge 认证与授权：HTTP Basic Auth（常量时间比较）、CSRF 双重提交令牌并校验 `X-Requested-With` 头、RBAC 与资源归属校验（防 IDOR）。
- 响应安全头：Content-Security-Policy 等安全响应头按部署模式下发。
- 扫码凭据防护：`qr_token` 采用凭据校验 + 失败限流 + 常量时间比较三层防护；SSE 端点强制鉴权。
- 扩展侧加固：URL scheme 白名单（仅 `http:` / `https:`）、富文本统一 HTML 转义防 XSS、`host_permissions` 保持为空、CSP 限制脚本源为 `'self'`。
- 启动期安全默认值：非调试模式下未设置 `BRIDGE_JWT_SECRET` 或管理员口令仍为弱口令时，服务拒绝启动。
- 仓库卫生：源码与历史中不含任何密钥、令牌或个人凭据。

### Documentation

- 重写 `README.md`：补齐产品定位、三产品速览、快速开始、AI 降级策略、架构与数据流、安全模型、测试基线与项目结构。
- 新增 `LICENSE`（MIT）、`NOTICE`（第三方 SQLite WASM 与自有素材归属说明）。
- 新增 `CONTRIBUTING.md`（上手流程、分支命名、测试命令、代码风格、Conventional Commits、PR 流程）。
- 新增 `CODE_OF_CONDUCT.md`（社区行为准则）、`SECURITY.md`（漏洞上报流程与响应时限）。
- 新增 `AGENTS.md`（AI Agent 与协作者的仓库工作约定、目录职责与验证要求）。
- 新增 `CHANGELOG.md`（本文件）、`.editorconfig` 与 `.gitattributes`（统一 UTF-8 无 BOM、LF 换行与缩进约定）。
- 产品文档：`products/focus-mvp/README.md`、`products/muse-mvp/README.md`。
- 过程文档：`docs/user-guide.md`、`docs/deploy-guide.md`、`docs/openapi.json`，以及 `docs/opensource/` 下的开源发布过程报告。
