# 变更日志

本文件记录 Vannevar 项目所有值得注意的变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

暂无。尚未发布的改动将在合并到 `main` 后归入此节。

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
