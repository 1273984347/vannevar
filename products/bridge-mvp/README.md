# Vannevar · Bridge MVP

面向公益团队的任务上下文共享与交接服务：一个基于 FastAPI 的 Web 应用，把「志愿者轮替导致的信息断档」变成可沉淀、可扫码接力、可核对的交接档案。

Bridge 是 Vannevar 产品族中负责**交接**环节的产品——Focus 捕捉工作上下文，Muse 整理灵感上下文，Bridge 让协作上下文在换人时不丢。

---

## 功能

- **项目看板**：三列看板管理任务（待办 / 进行中 / 已完成），支持拖拽与键盘切换状态
- **接力交接**：AI 基于未完成任务、近 7 天留言与历史交接生成结构化交接摘要（进度 / 负责人 / 下一步 / 注意事项）
- **协作留言**：留言可关联到具体任务，形成异步沟通闭环
- **扫码接力**：每个项目生成带权限隔离的二维码，扫码即可查看项目上下文并更新任务
- **多角色视角**：项目详情页可在志愿者 / 管理者 / 组织方视角间切换
- **数据看板**：`/admin/stats` 提供 9 项 KPI、热力图、桑基图、雷达图与网络图
- **AI 助手对话**：多轮对话 + SSE 流式输出，三层降级（LLM → 规则引擎 → 静态回复）
- **跨产品联动**：可导入 Focus 快照 / Muse 灵感作为交接背景（Context Card `.vcf` 格式）
- **实时推送**：SSE 广播任务状态变更与上下文变更
- **四种交接模板**：志愿者交接 / 正式交接 / 物资交付 / 开源接力

---

## 快速开始

### 环境要求

- Python 3.12（Windows 上若 `python` 不在 PATH，使用 Python Launcher `py`）
- 依赖见 `requirements.txt`

### 安装与运行

**Windows / PowerShell**

```powershell
cd products/bridge-mvp
python -m pip install -r requirements.txt
$env:BRIDGE_JWT_SECRET = python -c "import secrets; print(secrets.token_hex(32))"
$env:BRIDGE_ADMIN_PASS = "改成你的密码"
python main.py
```

**Linux / macOS**

```bash
cd products/bridge-mvp
python3 -m pip install -r requirements.txt
export BRIDGE_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export BRIDGE_ADMIN_PASS="改成你的密码"
python3 main.py
```

服务默认监听 `http://127.0.0.1:8000/`，管理后台账号为 `admin`，密码取自 `BRIDGE_ADMIN_PASS`。

> `BRIDGE_JWT_SECRET` 与 `BRIDGE_ADMIN_PASS` **均为必填**：缺失时服务会拒绝启动而不是带着弱凭据运行。

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `BRIDGE_PORT` | `8000` | 服务端口 |
| `BRIDGE_BIND_ADDR` | `127.0.0.1` | 绑定地址；公网访问需显式设为 `0.0.0.0` |
| `BRIDGE_ADMIN_USER` | `admin` | 管理员用户名 |
| `BRIDGE_ADMIN_PASS` | 无（必填） | 管理员密码；弱口令会被拒绝 |
| `BRIDGE_JWT_SECRET` | 无（必填） | JWT 签名密钥，生产必须为强随机值 |
| `BRIDGE_JWT_EXPIRE_DAYS` | `7` | JWT 有效期 |
| `BRIDGE_DATABASE_URL` | `sqlite:///bridge_mvp.db` | 数据库连接串；可换 `postgresql://…` |
| `BRIDGE_MAX_BACKUPS` | `7` | 滚动备份保留份数（范围 1–100） |
| `BRIDGE_AI_BASE_URL` | 空 | OpenAI 兼容 API 地址；留空则走本地规则引擎 |
| `BRIDGE_AI_API_KEY` | 空 | AI API Key |
| `BRIDGE_AI_MODEL` | 空 | 模型名 |
| `BRIDGE_DEBUG` | 空 | 设为 `1` 开启调试入口（`/demo` 等），生产务必关闭 |

完整示例见仓库根目录 `.env.example`。

---

## 目录结构

```
bridge-mvp/
├── main.py                # FastAPI 入口：路由注册 + SSE 白名单 + 限流 + 优雅降级
├── auth.py                # Basic Auth + CSRF + JWT + RBAC 依赖链
├── models.py              # SQLAlchemy 模型（含 version 乐观锁）
├── schemas.py             # Pydantic 校验（严格模式 extra="forbid"）
├── database.py            # SQLite + WAL + 滚动备份 + 迁移
├── backup.py              # 备份与恢复（原子替换 + 完整性校验）
├── ai.py                  # AI 增强：4 套 Prompt 模板 + 超时 + 降级
├── chat_router.py         # AI 助手对话（SSE 流式 + 三层降级）
├── context_card.py        # Context Card（.vcf）导入解析
├── templates.py           # 4 种交接模板
├── moderation.py          # 内容审核
├── routers/               # projects / tasks / pages / events / chat / auth / orgs / sync / agent
├── templates/             # Jinja2 页面模板
├── static/                # CSS + Service Worker + manifest
├── scripts/
│   └── seed_demo_data.py  # 演示数据集生成
├── lighthouse-reports/    # Lighthouse 性能测试与 dogfood QA 脚本
└── tests/                 # 单元测试 + e2e
```

---

## 测试

```bash
cd products/bridge-mvp
python -m pip install -r requirements.txt -r requirements-dev.txt

# 全量单元测试（当前基线 430 passed / 9 skipped）
python -m pytest tests/ -q --ignore=tests/e2e

# 仅数据库备份与恢复回归
python -m pytest tests/test_backup_recovery.py -v
```

测试通过 `BRIDGE_DATABASE_URL=sqlite:///:memory:` 使用**内存数据库**，不会读写或污染工作目录下的 `bridge_mvp.db`。

端到端测试（`tests/e2e/`）需要 Playwright + Chromium：

```bash
python -m pip install playwright
python -m playwright install chromium
python tests/e2e/test_bridge_e2e.py
```

---

## 安全模型

| 面向 | 机制 |
|---|---|
| 管理后台 | HTTP Basic Auth + CSRF Token + 角色权限（admin / user）+ 乐观锁版本校验 |
| 志愿者页 | 不可猜测的 `qr_token`，权限隔离到单个项目；Host 头注入防御；枚举限流 |
| 通用 | 严格 CSP、Pydantic 严格校验（拒绝未知字段）、内容审核、SQL 参数化 |
| 存储 | SQLite WAL + `busy_timeout=5000` + `foreign_keys=ON`；7 份滚动备份，损坏时自动从备份恢复 |
| AI | 交接摘要旁标注「AI 生成，仅供参考」免责提示；30s 超时后静默降级到规则引擎 |

---

## 数据存储

默认使用 SQLite（`bridge_mvp.db`，运行时生成，不入库）。数据库层已隔离在 `database.py` 边界内，切换 PostgreSQL 只需改环境变量，代码零改动：

```bash
export BRIDGE_DATABASE_URL="postgresql://user:pass@localhost:5432/vannevar"
```

---

## 常见问题

**启动报 `BRIDGE_JWT_SECRET 未设置`**
- 这是有意的安全校验。生成一个强随机密钥并设为环境变量即可。

**页面显示「Bridge 维护中」**
- 数据库初始化失败。检查 `bridge_mvp.db` 是否损坏；服务会在 `backups/` 保留最近 7 份备份，可手动复制回来：
  `copy backups\bridge_mvp_*.db bridge_mvp.db`

**忘记管理员密码**
- 重设 `BRIDGE_ADMIN_PASS` 后重启。已有 JWT 在过期前仍然有效。

**AI 功能没反应**
- 检查 `BRIDGE_AI_BASE_URL` / `BRIDGE_AI_API_KEY` / `BRIDGE_AI_MODEL` 是否配置。未配置时功能自动降级到本地规则引擎，核心能力不受影响，界面上的徽章会显示「规则引擎」。

---

## 许可

MIT，见仓库根目录 `LICENSE`。
