# Phase 4 计划：跨产品联动 + 媒体集成 + 可视化 + E2E 加密

> **文档说明**：Phase 4 在 Phase 1-3 基础上，提升 Vannevar 三产品协同价值。分 4 个子阶段，每个子阶段完成后独立 DRL，最后整体 DRL 真循环收敛。

## 一、背景与目标

### 1.1 Phase 1-3 回顾

- **Phase 1**：统一账号体系（JWT）+ 数据协同（Bridge sync）+ 组织/团队/邀请
- **Phase 2**：Agent 主动服务 + 企业版 + 端侧 AI（WebLLM）+ 三级 fallback
- **Phase 3**：端到端用户旅程测试 + 性能基准 + 生产就绪（Docker）
- **DRL Round 1-10**：真收敛，303 passed / 0 failed，251 条复利经验

### 1.2 Phase 4 目标

| 子阶段 | 主题 | 核心价值 | 技术依赖 |
|--------|------|----------|----------|
| 4.1 | 跨产品联动 | Vannevar 三产品协同叙事（1+1+1>3） | Bridge sync 基础设施 |
| 4.2 | Muse 媒体会话集成 | 填充 Muse 核心缺口（氛围保存） | Media Session API |
| 4.3 | Bridge 数据可视化仪表盘 | 管理方可视化能力 | Bridge stats 端点 |
| 4.4 | Focus E2E 加密同步 | 隐私优先架构 | Web Crypto API |

### 1.3 GitHub 调研结论

- **TabHamster**（81 stars，2013）：Chrome 扩展会话管理+同步，老旧
- **Tab Session Manager**：Firefox/Chrome 开源，标签页会话保存恢复
- **Copy-History-Manager**（2026-04）：技术栈几乎完全一致（Chrome 扩展 + FastAPI + JWT + SQLite + 云同步）
- **结论**：GitHub 上无三产品协同定位的竞品，Vannevar 独特性在于 Focus+Muse+Bridge 三位一体 + 共享 Bridge 后端

---

## 二、Phase 4.1：跨产品联动

### 2.1 设计目标

Vannevar 三产品当前各自独立，Phase 4.1 打通产品边界：

1. **Focus → Bridge**：Focus 快照可一键推送到 Bridge 项目档案，作为项目上下文
2. **Muse → Bridge**：Muse 灵感可关联到 Bridge 项目，创作素材服务于公益项目
3. **Bridge → Focus**：Bridge 任务可反向触发 Focus 快照建议（"开始任务前保存上下文"）

### 2.2 技术方案

#### 2.2.1 Focus → Bridge 快照推送

**后端**（`routers/projects.py`）：
- 新增 `POST /api/projects/{project_id}/context-snapshots` 端点
- 接收 Focus 快照元数据（标题、URL 列表、AI 摘要、创建时间）
- 存储到新表 `project_context_snapshots`（project_id, snapshot_uuid, title, summary, urls_json, created_at）
- 权限：require_project_access（edit 级别）

**Focus 扩展**（`products/focus-mvp/`）：
- `bridge-link.js`（新建）：管理 Bridge 项目关联状态
- popup.js 增加"推送到 Bridge"按钮（登录后可见）
- 推送时调用 `POST /api/projects/{project_id}/context-snapshots`
- 仅推送元数据 + AI 摘要，不推送实际标签页内容（隐私）

#### 2.2.2 Muse → Bridge 灵感关联

**后端**（`routers/projects.py`）：
- 新增 `POST /api/projects/{project_id}/context-inspirations` 端点
- 接收 Muse 灵感元数据（标题、URL、笔记、氛围、创建时间）
- 存储到新表 `project_context_inspirations`

**Muse 扩展**（`products/muse-mvp/`）：
- `bridge-link.js`（新建）：管理 Bridge 项目关联
- popup.js 增加"关联到 Bridge 项目"按钮
- 推送时调用 `POST /api/projects/{project_id}/context-inspirations`

#### 2.2.3 Bridge → Focus 快照建议

**Bridge 后端**（`routers/agent_router.py`）：
- Agent 扫描任务时，生成"建议保存上下文"提醒
- 任务 status 从 `todo` 变为 `in_progress` 时，触发 chrome.notifications

**Focus 扩展**（`task-watcher.js`，由 `background.js` 启动）：
- 实施决策（MV3 限制）：Service Worker 长连接 SSE 不稳定，改用 HTTP 轮询替代原始 SSE 监听设计
- 每 5 分钟轮询 Bridge `/api/auth/me/recent-task-updates` 端点，水位线过滤已通知任务
- 收到状态变更后弹出 chrome.notifications："任务 X 开始了，是否保存当前上下文？"
- 用户点击通知 → 打开 popup → 一键保存快照
- 说明：Bridge 后端仍推送 `task_status_changed` SSE 事件（供 Bridge Web UI `project_detail.html` 通过 EventSource 订阅），Focus 扩展不直接消费 SSE

#### 2.2.4 Bridge 项目详情页整合

**前端**（`templates/project_detail.html`）：
- 新增"关联上下文"标签页，展示 Focus 快照 + Muse 灵感
- 按时间倒序排列，支持删除

### 2.3 数据模型

```python
# models.py 新增
class ProjectContextSnapshot(Base):
    __tablename__ = "project_context_snapshots"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    snapshot_uuid = Column(String, nullable=False)  # Focus 端 uuid
    title = Column(String, nullable=False)
    summary = Column(Text, default="")  # AI 摘要
    urls_json = Column(Text, default="[]")  # URL 列表 JSON
    tab_count = Column(Integer, default=0)
    created_at = Column(Integer, nullable=False)
    pushed_at = Column(Integer, nullable=False)

class ProjectContextInspiration(Base):
    __tablename__ = "project_context_inspirations"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    inspiration_uuid = Column(String, nullable=False)  # Muse 端 uuid
    title = Column(String, nullable=False)
    url = Column(String, nullable=False)
    note = Column(Text, default="")
    mood = Column(String, default="")
    created_at = Column(Integer, nullable=False)
    pushed_at = Column(Integer, nullable=False)
```

### 2.4 验收标准

- [x] Focus 登录后，popup 显示"推送到 Bridge"按钮（`products/focus-mvp/popup.js:renderDetailActions` 渲染于详情面板顶部；登录态在点击时校验，未登录则 toast 提示并跳转设置页）
- [x] 推送快照后，Bridge 项目详情页"关联上下文"标签可见（`products/bridge-mvp/templates/project_detail.html:493` 标签入口 + `:804` 渲染区块；数据由 `GET /api/projects/{id}/context-snapshots` 拉取）
- [x] Muse 登录后，popup 显示"关联到 Bridge 项目"按钮（实际文案为"推送到 Bridge"，aria-label 为"推送到 Bridge 项目档案"，`products/muse-mvp/popup/popup.js:348` 渲染于最近灵感列表项；登录态同样在点击时校验）
- [x] Bridge 任务状态变更时，Focus 收到通知（`products/focus-mvp/task-watcher.js:160`：轮询 `/api/auth/me/recent-task-updates` 后 `chrome.notifications.create` 弹出"任务状态变更：{状态}"）
- [x] 权限校验：非项目成员无法推送/查看（Bridge 侧 `require_project_access`；`products/bridge-mvp/tests/test_cross_product.py` 的 `test_non_owner_push_rejected` / `test_push_inspiration_non_owner_rejected` 断言 403）
- [x] 测试：新增 test_cross_product.py，覆盖推送/查询/权限/删除（`products/bridge-mvp/tests/test_cross_product.py`，800+ 行：推送/幂等 409/列表/删除/未认证 401/非成员 403/缺 `X-CSRF-Token` 403/项目删除级联/SSE 事件）

---

## 三、Phase 4.2：Muse 媒体会话集成

### 3.1 设计目标

填充 tech-spec 2.2 "氛围保存"核心缺口：捕获当前播放音乐，作为氛围的一部分。

### 3.2 技术方案

#### 3.2.1 Media Session API 捕获

**Muse 扩展**（`products/muse-mvp/core/media-session.js` 新建）：
- 使用 `navigator.mediaSession.metadata` 获取当前播放音乐
- 字段：title、artist、album、artwork
- 在 popup.js 中调用，保存灵感时附带 music 信息

#### 3.2.2 数据模型扩展

**Muse db.js**：
- `inspirations` 表新增 `music_title`、`music_artist`、`music_album` 列
- 兼容旧数据（ALTER TABLE，NULL 兜底）

**sync.js**：
- pushSync 附带 music 字段
- Bridge sync_router 接收并存储

**Bridge 后端**（`models.py`）：
- SyncItem 已有 payload JSON，music 字段直接存入 payload，无需新表

#### 3.2.3 灵感博物馆按音乐分组

**dashboard.js**：
- 新增"按音乐分组"视图
- 相同 artist/title 的灵感聚合展示
- 点击音乐名 → 筛选该音乐下的所有灵感

### 3.3 验收标准

- [x] 播放音乐时（如 YouTube/Spotify Web），保存灵感自动捕获音乐信息 — `core/muse.js:captureMediaMetadata` 通过 `chrome.scripting.executeScript` 读取 `navigator.mediaSession.metadata`，注入失败/无 metadata 时静默返回 null
- [x] 无音乐播放时，music 字段为空，不影响保存 — `saveInspiration` 默认参数 `mediaType/mediaArtist/mediaAlbum/mediaArtworkUrl = null`，旧数据 `ALTER TABLE` 增列后 NULL 兜底
- [x] 灵感博物馆"按音乐分组"视图可用 — `dashboard.html` 新增 `viewTabMusic` 视图切换按钮，`dashboard.js:renderMusicView` 渲染分组，纯函数逻辑提取到 `core/media-group.js` 便于单元测试
- [x] 同步：music 字段跨设备同步 — `sync.js` pushSync/pullSync 已附带 4 个 media 字段，Bridge `SyncItem.payload` JSON 直接存储
- [x] 测试：手动验证 + JS 语法检查 — `tests/test_media_session.mjs` 18/18 PASSED（saveInspiration 字段写入/NULL 兜底 + getInspirations 字段映射 + captureMediaMetadata 4 类 URL 推断 + groupByMusic 7 项纯函数测试），JS 语法检查 7 文件全通过

---

## 四、Phase 4.3：Bridge 数据可视化仪表盘

### 4.1 设计目标

Bridge 已有 `/api/stats` 端点，但缺乏可视化。Phase 4.3 新增可视化仪表盘页面。

### 4.2 技术方案

#### 4.2.1 后端 stats 端点扩展

**`routers/projects.py` stats_router**：
- `GET /api/stats/dashboard`：聚合数据
  - 项目状态分布（active/archived/done）
  - 近 30 天项目创建趋势
  - 近 30 天任务完成趋势
  - 志愿者活跃度热力图数据（按天）
  - 交接频率趋势（按周）

#### 4.2.2 前端仪表盘页面

**`templates/dashboard.html` 新建**：
- 使用原生 Canvas + CSS 绘制（不引入 Chart.js，保持依赖最小）
- 三栏布局：
  - 左：项目状态环形图 + 数字卡片
  - 中：30 天趋势折线图（项目创建 + 任务完成）
  - 右：志愿者活跃度热力图
- 底部：交接频率柱状图

> **实施路径偏差说明（2026-07-19）**：
> - 实际页面文件为 `templates/stats.html`（复用现有统计页），路径为 `/admin/stats`（非 `/admin/dashboard`）。
> - 实际使用原生 SVG + CSS Grid（非 Canvas），与 stats.html 既有图表技术栈一致。
> - 既有 stats.html 已实施 KPI 卡片 / 折线图 / 环形图 / 条形图 / 多折线对比图 / 贡献榜。Phase 4.3 补齐唯一缺失的「志愿者活跃度热力图」（GitHub 风格 30 格 CSS Grid + 5 级色阶）。
> - 后端 `compute_stats(db)` 输出 `activity_heatmap` 字段（近 30 天多源活动按天聚合，5 表大查询 + Python 端按天分组）。
> - **后端端点偏差**：§4.2.1 原写 `GET /api/stats/dashboard` 新端点，实际扩展既有 `GET /api/stats` 端点（在 `compute_stats` 返回字典中追加 `activity_heatmap` 字段），未新增 `/api/stats/dashboard` 路由。前端 `/admin/stats` 页面复用 `compute_stats(db)` 渲染（pages.py:140）。

#### 4.2.3 响应式设计

- 桌面端：三栏布局
- 移动端：单栏堆叠
- 暗色模式适配

### 4.3 验收标准

- [x] `/admin/stats` 页面可访问（plan 原写 `/admin/dashboard`，实际实施为 `/admin/stats`，见 §4.2.2 偏差说明）
- [x] 环形图/折线图/热力图/柱状图正确渲染
- [x] 数据实时反映后端 stats
- [x] 响应式：移动端单栏（依赖全局 style.css 媒体查询 + heatmap-wrap 水平滚动）
- [x] 暗色模式可用（依赖全局 style.css `prefers-color-scheme` + 设计令牌）
- [x] 测试：test_bridge.py 覆盖新端点（test_stats_phase4_3_extended_metrics / test_stats_activity_heatmap_reflects_context_push / test_admin_stats_page_renders_phase4_3_sections）

---

## 五、Phase 4.4：Focus E2E 加密同步

### 5.1 设计目标

tech-spec 1.5 "端到端加密云存储"落地：快照内容客户端加密，Bridge 只存密文。

### 5.2 技术方案

#### 5.2.1 密钥管理

**Focus 扩展**（`products/focus-mvp/crypto.js` 新建）：
- 使用 Web Crypto API（SubtleCrypto）
- 密钥派生：PBKDF2（用户密码 + 随机 salt → 256-bit AES-GCM 密钥）
- 密钥存储：chrome.storage.local（仅存派生后的密钥，不存密码）
- 盐值存储：chrome.storage.local + Bridge 用户配置（跨设备同步需要相同 salt）

#### 5.2.2 加密流程

**pushSync**：
1. 快照内容（urls 数组）序列化为 JSON
2. 生成随机 IV（12 bytes）
3. AES-GCM 加密 → ciphertext
4. 推送 `{ ciphertext, iv, salt }` 到 Bridge
5. Bridge 只存密文，无法解密

**pullSync**：
1. 拉取 `{ ciphertext, iv, salt }`
2. 用本地密钥解密
3. 还原 urls 数组

#### 5.2.3 兼容性

- 已有未加密快照：pushSync 时自动加密（迁移）
- pullSync 时检测是否密文（base64 + iv 字段），是则解密，否则按明文处理
- Bridge sync_router：payload 字段已是 JSON，加密后存为 `{ encrypted: true, ciphertext, iv }`

#### 5.2.4 密钥跨设备同步

**问题**：用户在设备 A 加密，设备 B 需要相同密钥才能解密。

**方案**：
- 用户首次启用 E2E 加密时，生成 salt 并同步到 Bridge（用户配置表）
- 设备 B 登录后拉取 salt，用本地密码派生相同密钥
- 密钥本身永不上云，只有 salt 上云

### 5.3 验收标准

- [x] 启用 E2E 加密后，pushSync 推送密文（`shared/vannevar-crypto.js` 写入 `{encrypted:true, alg:"AES-GCM-256", kdf:"PBKDF2-SHA256-100000", ciphertext, nonce, v:1}`；Focus `products/focus-mvp/sync.js`、Muse `products/muse-mvp/core/sync.js`）
- [x] Bridge 数据库中 urls 字段为密文（无法直接读取）（密文存于 SyncItem 的 `data` JSON，服务端只见 `ciphertext`/`nonce`；服务端既无 passphrase 也无派生密钥，无法解密任何同步内容）
- [x] pullSync 能正确解密（Focus `tests/test_crypto.mjs` 16/16 + `tests/test_sync_crypto.mjs` 12/12；Muse `tests/test_crypto.mjs` 24/24 + `tests/test_sync_crypto.mjs` 14/14）
- [x] 未启用加密时，兼容旧明文格式（pull 时以 `data.encrypted` 字段区分密文与明文，旧明文按原样处理，不做破坏性迁移）
- [x] 密钥跨设备同步：设备 B 能解密设备 A 加密的快照（本批落地：salt 作为公开参数经 `GET/PUT /api/auth/me/crypto-profile` 上云，档案另含 verification 密文用于在另一台设备反验 passphrase 是否一致；passphrase 永不上云）
- [x] 测试：加密/解密/兼容性 —— 实际为 `tests/test_crypto.mjs`（16）、`tests/test_sync_crypto.mjs`（12）、`tests/test_crypto_profile.py`（Bridge 端点）

### 5.4 实施偏差说明

**(a) 加密档案落库形态：用户配置表 → 独立表 + 独立端点**

- 原计划（§5.2.4）：「用户首次启用 E2E 加密时，生成 salt 并同步到 Bridge（用户配置表）」。
- 实际实现：独立表 `user_crypto_profiles`（用户 ↔ 档案 1:1，`products/bridge-mvp/models.py:147` 的 `class UserCryptoProfile`，`__tablename__` 在 `:159`）+ 独立端点 `GET/PUT /api/auth/me/crypto-profile`（`products/bridge-mvp/routers/auth_router.py:129` / `:157`），数据库迁移 `PRAGMA user_version` v11 → v12（`products/bridge-mvp/database.py:92` 的 `TARGET_SCHEMA_VERSION = 12`，迁移函数 `_run_migration_v12` 在 `:443`）。
- 理由：加密档案与账号 1:1，且未来可能扩展字段（KDF 参数、verification 版本、档案轮换历史等），独立表不污染 `users` 表，也便于后续做档案轮换与审计。
- 上云内容仅含公开参数：`salt`（PBKDF2 公开盐值，**不是密钥**）、`iterations`、`kdf`、`salt_fingerprint`、`verification`（密文 + nonce，仅用于在另一台设备上反验 passphrase 是否一致）。

**(b) 原计划未提及 Muse —— Muse 的端到端加密在本批补齐**

- 此前只有 Focus 有端到端加密实现：Muse 侧加密相关代码为零，云同步为明文上传，根 README 与 CHANGELOG 中「Muse 支持端到端加密」的声称当时并不成立；本批已补齐实现，该声称现在成立。
- Muse 新增 `core/crypto.js`（加密模块薄封装）、`core/crypto-profile.js`（档案同步层）、`shared/vannevar-crypto.js`（共享核心副本），设置页新增「E2E 加密同步」卡片（`products/muse-mvp/options/options.html` / `options.js`）。
- **顺带修复的真实缺陷**：Muse 的同步 push 此前不带 `X-CSRF-Token` 头，而 Bridge 写端点强制校验该头（须等于 JWT 的 `csrf_jti`），因此 Muse 云同步在生产模式下必然 403；本批改为统一使用 `getBridgeHeaders()` 注入（Focus 侧此前已有该修复并有专门用例覆盖）。
- 加密核心上收到共享层 `shared/vannevar-crypto.js`，以 `createCryptoModule({storeKey, verificationPlaintext})` 工厂导出参数化实现，Focus 与 Muse 共用同一份代码；运行副本分别为 `products/focus-mvp/lib/vannevar-crypto.js` 与 `products/muse-mvp/shared/vannevar-crypto.js`，由 `scripts/check_shared_sync.py` 门禁（STRICT 级，字节不一致即阻断）校验三者一致，并已接入 `scripts/qa_all.py`（shared_sync → pack_mvps → pack_final → verify_loading → contrast → links，共 6 步）。

---

## 六、整体验收标准

### 6.1 功能验收

- Phase 4.1：跨产品联动 4 条链路全部可用（验收项 6/6 已勾选，见 §2.4）
- Phase 4.2：媒体会话捕获 + 按音乐分组（验收项 5/5 已勾选，见 §3.3）
- Phase 4.3：仪表盘 4 类图表（验收项 6/6 已勾选，见 §4.3）
- Phase 4.4：E2E 加密 + 跨设备解密（验收项 6/6 已勾选，见 §5.3；跨设备 salt 上云于本批完成，实施偏差见 §5.4）

### 6.2 质量验收

- 全量测试通过（Bridge 全量基线见根 README 开发与测试章节）
- 扩展侧测试基线（本批实测）：Focus `node tests/test_crypto.mjs` 16 通过 / 0 失败；Focus `node --import ./tests/loader.mjs tests/test_sync_crypto.mjs` 12 通过 / 0 失败；Muse `node tests/test_crypto.mjs` 24 通过 / 0 失败；Muse `node --import ./tests/loader.mjs tests/test_sync_crypto.mjs` 14 通过 / 0 失败；Muse `node --import ./tests/loader.mjs tests/test_media_session.mjs` 18 / 18 PASSED
- 共享层一致性门禁：`scripts/check_shared_sync.py`（STRICT）校验 `shared/vannevar-crypto.js` 与两份运行副本字节一致，已接入 `scripts/qa_all.py`（shared_sync → pack_mvps → pack_final → verify_loading → contrast → links，共 6 步）
- JS 语法检查通过
- Python 语法检查通过
- DRL 真循环收敛（连续 N 轮无新问题）

### 6.3 演示路径不受影响

- Hard constraint：现有演示路径（rehearsal-demo.md）不被破坏
- Phase 4 功能为增量，不影响 Phase 1-3 演示

---

## 七、DRL 计划

### 7.1 每子阶段 DRL

- Phase 4.1 完成后：DRL Round 1（跨产品联动专项审查）
- Phase 4.2 完成后：DRL Round 2（媒体集成专项审查）
- Phase 4.3 完成后：DRL Round 3（可视化专项审查）
- Phase 4.4 完成后：DRL Round 4（E2E 加密专项审查）

### 7.2 整体 DRL 真循环

- Phase 4 全部完成后：整体 DRL Round 5+
- 每轮修复后必须重新审查（不许假收敛）
- 收敛曲线记录到 CHANGELOG
- 复利经验追加到 retrospective

### 7.3 真收敛标准

- 连续 N 轮重新审查均未发现新问题
- 问题数严格单调递减到 0
- 每轮问题数（P0/P1/P2）单独记录
