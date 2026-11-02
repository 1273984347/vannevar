# 变更日志

本文件记录 Vannevar 项目所有值得注意的变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **Chrome Web Store 上架套件（T-11）**：新增 `docs/webstore/`（提交步骤清单 + Focus / Muse 双语 listing 材料：短描述、详述、单一用途声明、逐权限理由、数据披露），上架最终步骤（开发者账号与上传）为发布负责人手动动作。
- **Bridge 公开截图重制（T-11）**：`screenshots/bridge-*.png` 四张以官方演示数据集 + playwright 实拍更新，移除旧图中的「截图测试项目」字样并首次反映当前档案纸感 UI；过程脚本记录见 T-11 报告。

### Fixed

- **端侧 AI 增强层（Focus / Muse，T-10）**：新增共享适配层 `vannevar-builtin-ai.js`（双副本字节一致，入 STRICT 门禁），接入 Chrome 内置 Prompt API（`LanguageModel`）。降级链升级为：**已配置的云端 API 优先（尊重显式选择）→ 未配置云端时尝试端侧 AI（零配置、零上传，模型由 Chrome 管理）→ 本地规则引擎**。可用性判定采取保守策略：仅在模型已就绪（`available`）时启用，`downloadable`/`downloading` 状态不静默触发数百 MB 的模型下载；端侧输出经宽松 JSON 提取后走与云端一致的结果解析路径；任何失败静默降级，不阻断主流程。既有 WebLLM 本地层（600MB、默认关闭、无调用方）保持现状，后续评估合并。

### Fixed

- **退出登录现在会真正撤销服务端 token（Focus / Muse）**：扩展此前 logout 只清除本地凭据，Bridge 端已签发 JWT 在到期前仍然有效。现 logout 先尽力调用 `POST /api/auth/logout`（携带 Bearer 与 `X-CSRF-Token`，即 JWT 的 `csrf_jti`）触发服务端 `token_revoked_at` 撤销并失效缓存，再清除本地状态；服务端不可达时不阻塞本地登出（此时旧 token 只能等自然过期，属无状态 JWT 的固有限制）。Bridge 端撤销与缓存失效机制此前已有测试覆盖，本批补齐的是调用侧。
- **跨设备口令不一致不再静默（Focus / Muse）**：自动同步遇到「云端加密档案的 salt 与本机不一致」时，此前返回值被静默丢弃（T-02 P2-1），用户无从得知为何另一台设备解不开。现统一弹出系统通知（24 小时节流防打扰），说明「未覆盖云端档案，请到设置页解锁或采用云端档案」；档案恢复一致后自动复位。手动解锁路径的报错提示此前已存在，本批补齐的是自动同步路径。

### Changed

- **共享层副本门禁收紧（T-09）**：`privacy-policy.html` 单源化（shared/ 为唯一源，Bridge static 副本字节一致并随之获得样式引用），Bridge static 新增 `vannevar-brand.css` 副本随政策页使用；`check_shared_sync.py` 门禁扩为 **STRICT 5 组**（加密核心、Context Card、隐私政策、Bridge 静态令牌、扩展运行时对，漂移即阻断），Context Card 与扩展运行时对自 REPORT 升入 STRICT（当前字节一致，升级零成本）；设计令牌（海报源 vs 扩展副本）与 Bridge Web 运行时变体明确标注为「刻意分叉」，留在 REPORT 如实报告。T-01 遗留 R-10（隐私政策多副本漂移）就此关闭。

### Testing

- 新增 Focus / Muse 镜像用例 `tests/test_logout_revoke.mjs`（各 7 条，无需 loader）：撤销请求头完整性（Bearer + `csrf_jti`）、网络失败容忍、未登录零请求；salt-mismatch 通知、24h 节流、成功复位、其余 reason 静默。
- 新增 Focus / Muse 镜像用例 `tests/test_builtin_ai.mjs`（Focus 8 条 / Muse 9 条，无需 loader）：端侧 JSON 采纳、围栏输出宽松提取、`unavailable`/`downloadable` 不可用、首次 `create()` 抛错静默、云端配置优先且端侧零调用、流式一次性回调、mood 推荐数组解析（Muse）。测试基线：Focus 16 + 14 + 7 + **8**；Muse 24 + 16 + 18 + 7 + **9**；Bridge 449 / 9（未改 Bridge 代码）。

## [0.2.0] - 2026-11-03

> 发布说明与可下载产物：<https://github.com/1273984347/vannevar/releases/tag/v0.2.0>

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

### Dependencies

- Bridge 依赖随 dependabot 升级，每条均经 CI 全量验证、合并后叠加组合再经本地全新环境实测（449 passed / 9 skipped / 0 failed）：`fastapi` 0.159.1 → 0.162.0、`sqlalchemy` 2.0.50 → 2.1.6、`python-jose[cryptography]` 3.3.0 → 3.5.0、`apscheduler` 3.10.4 → 3.11.3；测试依赖 `pytest` 8.4.2 → 9.2.0（#10 ~ #14）。

### Testing

- 新增 `products/muse-mvp/tests/test_crypto.mjs`（24 条，含两个独立模块实例互解的跨设备用例）、`products/muse-mvp/tests/test_sync_crypto.mjs`（16 条）、`products/bridge-mvp/tests/test_crypto_profile.py`（12 条）。
- 新增门禁 `scripts/check_shared_sync.py`（共享层副本一致性，已接入 `scripts/qa_all.py`，强约束副本组漂移即阻断）。
- 测试基线：Bridge **449 passed / 9 skipped / 0 failed**；Focus 16 + 14；Muse 24 + 16 + 18。

### Documentation

- 根 `README.md` 补齐跨设备解密说明、加密档案的安全边界、修正后的测试基线与代码体量统计。
- `products/muse-mvp/README.md` 与 `products/focus-mvp/README.md` 补齐 E2E 加密与跨设备解密章节。
- 根 `README.md` 的测试一节补齐 `requirements-dev.txt` 安装前提（pytest 属测试依赖，不随运行时安装），新用户照文档跑测试不再卡在 `No module named pytest`；`QUICKSTART.md` 的 Windows 命令统一为 `py`；根 `README.md` / `AGENTS.md` 测试基线数字校准为实测值（T-03 新用户首航自测发现，F-1/F-2/F-3）。
- 展示面打磨（T-05）：根 `README.md` 首屏新增三产品真实 UI 截图卡并校准徽章区（新增 Release 徽章、移除过时的静态徽章注释）；`QUICKSTART.md` 截图改指真实产品 UI；移除 3 张带「TRAE AI 创造力大赛」烙印的旧提案海报（`screenshots/vannevar-*-screenshot.png`，参赛烙印清除）。
- 新增英文版根 README（`README.en.md`，内容与中文版对应当前状态），两版首行互设双语切换入口。

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
