# Vannevar

[![CI](https://github.com/1273984347/vannevar/actions/workflows/ci.yml/badge.svg)](https://github.com/1273984347/vannevar/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Chrome MV3](https://img.shields.io/badge/Chrome-Manifest%20V3-4285F4.svg)](products/focus-mvp/manifest.json)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB.svg)](products/bridge-mvp/requirements.txt)
[![Node](https://img.shields.io/badge/Node-24-5FA04E.svg)](products/focus-mvp/manifest.json)

> 让信息在恰当的时机，出现在恰当的地方。

Vannevar 是一套 **上下文管理（Context Management）产品族**，致敬 Vannevar Bush 在 1945 年《As We May Think》中提出的 Memex 概念。它不替你「存更多东西」，而是让**当下的工作、灵感和协作能接得上**。

同一个产品族覆盖同一条上下文生命周期的三个阶段：

| 产品 | 一句话 | 阶段 |
|---|---|---|
| **Focus** | 一键保存、恢复浏览器的完整工作现场，AI 自动打上下文标签 | 捕捉 |
| **Muse** | 捕获网页选中片段，用「氛围」而不是文件夹组织灵感，Dashboard 时间线回顾 | 整理 |
| **Bridge** | 公益项目看板 + AI 交接摘要 + 二维码扫码接力，让协作不断档 | 交接 |

<!--
徽章说明：以上为静态徽章，均不指向 CI 服务。
CI 徽章请在 GitHub Actions 首次成功运行后，从 Actions 页面复制真实徽章 URL 补入此处，
不要预先写入任何尚未启用的工作流地址。
-->

---

## 为什么做

我们不缺存储工具，缺的是**上下文不断**的能力。信息在三种最常见的形态下悄悄丢失：

| 形态 | 现场表现 | 根因 | Vannevar 的对应 |
|---|---|---|---|
| **工作断片** | 开了 20 个标签页，关掉电脑后第二天全找不到；浏览器标签、搜索词、滚动位置一起消失 | 上下文只存在于浏览器会话里，没有被「凝固」下来 | **Focus**：把标签页 + 标题 + URL + 选中片段固化成快照，可一键恢复；AI 自动标注「深度专注 / 多线并行 / 需尽快处理」 |
| **灵感沉底** | 刷到一段话、一个配色、一首歌，想不起时已经翻不动了；收藏夹变成垃圾场 | 组织维度错了 —— 用户记不住「文件夹路径」，只记得「当时的感觉」 | **Muse**：用「氛围（mood）」而非文件夹组织灵感，同一氛围的内容自然聚合；Dashboard 时间线按天回顾 |
| **交接断档** | 公益项目换班时，前一个人脑子里有 30% 信息没写下来；志愿者扫码接手后只能从头问 | 交接依赖「写总结」，而总结是额外负担，没写就等于丢失 | **Bridge**：看板沉淀任务与留言，AI 基于任务/留言/历史交接一键生成交接摘要，扫码即接力 |

三条线共用一套内核：**本地优先存储、统一 AI 降级策略、共享设计系统、Context Card 交换格式**。

---

## 三产品速览

| 产品 | 形态 | 典型场景 | 核心能力 | 目录 |
|---|---|---|---|---|
| **Focus** | Chrome MV3 扩展 | 深度工作、多线并行研究、临时中断恢复 | 标签页快照与恢复、AI 上下文标签、本地加密同步到 Bridge | [`products/focus-mvp/`](products/focus-mvp/) |
| **Muse** | Chrome MV3 扩展 | 灵感捕捉、素材归档、创作回顾 | 选中片段捕获、氛围标签与分组、Dashboard 时间线、媒体元数据、端到端加密同步 | [`products/muse-mvp/`](products/muse-mvp/) |
| **Bridge** | FastAPI Web 服务 | 公益团队协作、志愿者交接、跨人接力 | 项目看板与任务、Basic Auth + RBAC、AI 交接摘要、SSE 实时推送、扫码接力 | [`products/bridge-mvp/`](products/bridge-mvp/) |

技术形态一览：

- **Focus / Muse**：原生 JavaScript（无前端框架）、Chrome Manifest V3、Service Worker + sql.js（SQLite WASM）+ `chrome.storage.local`。
- **Bridge**：FastAPI + SQLAlchemy + SQLite（WAL 模式 + 滚动备份）+ Jinja2 + SSE；通过 `BRIDGE_DATABASE_URL` 可切换到 PostgreSQL。

---

## 快速开始

### Focus / Muse（浏览器扩展）

无需构建、无需 npm install —— 两者都是纯原生 JS，直接以「已解压的扩展程序」加载：

1. 打开 `chrome://extensions/`（Edge 为 `edge://extensions/`）。
2. 右上角开启 **开发者模式**。
3. 点击 **加载已解压的扩展程序**。
4. 选择仓库中的产品目录：
   - Focus → `products/focus-mvp`
   - Muse → `products/muse-mvp`
5. 建议把扩展固定到工具栏；Focus 的快捷键默认为 `Alt+Shift+S`（保存上下文）与 `Alt+Shift+R`（恢复最近一次）。

首次使用建议：打开扩展的 **Options 页**，确认 AI 增强为「未配置 / 本地规则引擎」状态即可正常使用（详见下一节）。

> 扩展默认只请求 `tabs`/`storage`/`activeTab`/`windows`/`alarms`/`notifications` 等必要权限，`host_permissions` 为空，不读取你的浏览历史以外的内容。

### Bridge（Web 服务）

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

> 若系统里只有 Python Launcher（Windows 常见），把上面的 `python` 换成 `py` 即可，例如 `py -m pip install -r requirements.txt`。

启动后：

| 项 | 值 |
|---|---|
| 管理后台 | <http://127.0.0.1:8000/> |
| 账号 | `admin`（可用 `BRIDGE_ADMIN_USER` 覆盖） |
| 密码 | 由 `BRIDGE_ADMIN_PASS` 指定 |
| 健康检查 | <http://127.0.0.1:8000/health> → `{"ok": true}` |

**必须设置的两个环境变量**：生产模式下（未设 `BRIDGE_DEBUG=1`）若不设置 `BRIDGE_JWT_SECRET`，服务会**直接启动失败**并提示配置强密钥；若 `BRIDGE_ADMIN_PASS` 缺失或仍是内置弱口令，同样**拒绝启动**。这是有意的安全默认值，不要绕过。

开发调试时可用 `BRIDGE_DEBUG=1`：此时 JWT 密钥与管理员密码会随机生成并打印到 stderr，省去手动设置。

---

## AI 增强与降级

三个产品共用**同一条降级链路**，设计原则是：**AI 是可选增强，不是依赖**。

```
用户触发 AI 功能
      │
      ├─ 已配置 OpenAI 兼容 API？ ──是──▶ 调用（超时上限 30s）
      │                                        │
      │                                  成功 ─┴─ 失败/超时
      │                                   │            │
      │                                 返回结果      降级 ↓
      └─ 未配置 ─────────────────────────────────▶ 本地规则引擎
                                                    （核心功能零外部依赖）
```

- **Focus / Muse**：在扩展 **Options 页**的「AI 增强（可选）」区域填写 API Base URL、API Key、模型名。API Key 只写入本地 `chrome.storage.local`，不会上传到任何服务器；不配置则使用内置规则引擎（Muse 的氛围标签基于频率统计，而非模型推荐）。
- **Bridge**：通过环境变量配置。

| 变量 | 作用 | 示例 |
|---|---|---|
| `BRIDGE_AI_BASE_URL` | OpenAI 兼容端点（需含完整路径） | `https://api.openai.com/v1/chat/completions` |
| `BRIDGE_AI_API_KEY` | API Key | `sk-…` |
| `BRIDGE_AI_MODEL` | 模型名，缺省 `gpt-4o-mini` | `gpt-4o-mini` |
| `BRIDGE_AI_DEMO_MODE` | 演示模式，跳过真实调用并返回预设结果 | `1` |

未配置时，Bridge 的交接摘要、状态建议、对话助手依然可用 —— 走的是内置规则引擎。

---

## 架构与数据流

```
   ┌────────────────────┐        ┌────────────────────┐
   │  Focus (MV3 扩展)  │        │  Muse (MV3 扩展)   │
   │  Service Worker    │        │  Service Worker    │
   │  sql.js (SQLite)   │        │  sql.js (SQLite)   │
   │  chrome.storage    │        │  chrome.storage    │
   └─────────┬──────────┘        └─────────┬──────────┘             本地优先
             │                                │                 用户上下文默认只留本机
             │  POST /api/sync/push          │
             │  GET  /api/sync/pull?app=…    │
             └───────────────┬────────────────┘
                             │  HTTP(S) + Bearer / CSRF
                             ▼
                ┌──────────────────────────────┐
                │      Bridge (FastAPI)        │
                │  sync 网关 · LWW 冲突合并     │
                │  projects/tasks/handoffs     │
                │  RBAC · CSRF · SSE            │
                └──────────────┬───────────────┘
                               │ SQLAlchemy ORM
                               ▼
        ┌──────────────────────────────────────────┐
        │  SQLite（WAL + 滚动备份）                │
        │  或 PostgreSQL（BRIDGE_DATABASE_URL）    │
        └──────────────────────────────────────────┘

   共享层：shared/vannevar-brand.css（设计令牌）
           shared/vannevar-context-card.js（.vcf 上下文卡片格式）
           products/*/shared/vannevar-shared.js
```

关键约定：

| 主题 | 约定 |
|---|---|
| 同步方向 | 扩展 `push` 上行 + `pull` 下行，双向；`app=focus` / `app=muse` 区分来源 |
| 冲突合并 | **LWW（Last-Write-Wins）**，以记录自身的 `updated_at` 判定新旧 |
| 扩展侧存储 | `chrome.storage.local` + sql.js（SQLite WASM）本地库 |
| 服务端存储 | SQLite WAL 模式 + 滚动备份；`BRIDGE_DATABASE_URL` 可切 PostgreSQL |
| 交换格式 | **Context Card（`.vcf`）**，三产品之间传递上下文条目的统一 JSON 格式 |

### 端到端加密（扩展侧）

Focus 与 Muse 的同步数据在离开浏览器前即加密：

- 算法：**AES-GCM-256**
- 密钥派生：**PBKDF2-SHA256，100,000 次迭代**（随机 salt，固定复用）
- 派生出的 `CryptoKey` 为 **non-extractable**，仅存在于内存
- **用户口令本身不落盘、不上传**；服务端只见到密文、nonce 与 salt

因此 Bridge 无法读取未解密的上行内容；忘记口令即无法恢复（这是设计取舍，不是缺陷）。

> Muse 的同步此前是明文上传，且其 push 请求缺少 Bridge 写端点强制校验的 `X-CSRF-Token`（生产模式下必然 403）。两处均已修复，现在 Muse 与 Focus 走同一条加密链路。

### 跨设备解密

加密逻辑由共享层 `shared/vannevar-crypto.js` 统一提供（Focus 与 Muse 使用同一份实现，副本一致性由 `scripts/check_shared_sync.py` 门禁保证）。

- **PBKDF2 salt 是公开参数，不是密钥**：它随加密档案经 `GET/PUT /api/auth/me/crypto-profile` 同步到 Bridge，供其它设备派生同一把密钥。
- **口令永不上云、永不落盘**：档案里另存一份 verification 密文，用于在另一台设备上反验口令是否一致。
- 因此在设备 B 输入**同一个口令**即可解密设备 A 推送的密文；口令不一致会被明确拒绝，而不是推出一堆解不开的数据。

---

## 安全模型

| 层面 | 机制 |
|---|---|
| Bridge 管理员入口 | HTTP Basic Auth，用户名/密码均用 `secrets.compare_digest` 常量时间比较 |
| Bridge 写操作 | CSRF 双重提交：启动期生成随机 token → Cookie + `X-CSRF-Token` 头校验；并要求 `X-Requested-With` 头 |
| Bridge 资源授权 | RBAC + 资源归属校验，防止越权访问他人项目（IDOR 防护） |
| Bridge 响应头 | Content-Security-Policy 等安全响应头；生产模式下静态资源长缓存 |
| 志愿者扫码 | `qr_token` 凭据 + 失败限流 + 常量时间比较；SSE 端点强制鉴权（`qr_token` 或管理员 Basic Auth） |
| 扩展 URL 处理 | URL scheme 白名单，仅允许 `http:` / `https:`，显式排除 `javascript:` / `data:` / 浏览器内部 scheme |
| 扩展渲染 | 富文本统一走 HTML 转义，防 XSS |
| 扩展权限 | `host_permissions` 为空，按需申请 `activeTab`；CSP 限制脚本源为 `'self'` |
| 本地数据 | 扩展侧 SQLite 存于 `chrome.storage.local`；**Focus 与 Muse** 的同步内容均端到端加密（见上） |
| 加密档案 | PBKDF2 salt（公开参数，非密钥）经 `/api/auth/me/crypto-profile` 同步，用于跨设备派生同一密钥；口令与派生密钥永不出现在服务端 |
| 加密边界 | 端到端加密覆盖的是**数据同步**（`/api/sync/*`）。「推送到 Bridge 项目档案」（`/api/projects/{id}/context-*`）**不加密** —— Bridge 需要读取标题、链接与摘要来生成交接摘要与统计图表 |
| 密钥与口令 | 依赖不落盘（仅 `chrome.storage.local` / 环境变量），仓库内不提交任何密钥 |

发现问题请按 [SECURITY.md](SECURITY.md) 的流程私下上报，不要开公开 Issue。

---

## 开发与测试

### 环境要求

| 组件 | 版本 | 用途 |
|---|---|---|
| Python | 3.12 | Bridge 服务与测试 |
| Node.js | 24 | 扩展单元测试（`node:test`） |
| Chrome | 102+ | 运行 MV3 扩展 |

### 测试命令与当前基线

```bash
# Bridge：全量单元/集成测试（不跑 e2e）
cd products/bridge-mvp
py -m pytest tests/ -q --ignore=tests/e2e
# 基线：447 passed / 9 skipped / 0 failed（用例数随新功能递增）

# Focus：端到端加密
cd products/focus-mvp
node tests/test_crypto.mjs
# 结果：16 通过 / 0 失败

# Focus：同步链路加密
node --import ./tests/loader.mjs tests/test_sync_crypto.mjs
# 结果：12 通过 / 0 失败

# Muse：端到端加密
cd products/muse-mvp
node tests/test_crypto.mjs
# 结果：24 通过 / 0 失败（不触碰 SQLite WASM，无需 loader）

# Muse：同步链路加密
node --import ./tests/loader.mjs tests/test_sync_crypto.mjs
# 结果：14 通过 / 0 失败

# Muse：媒体会话
node --import ./tests/loader.mjs tests/test_media_session.mjs
# 结果：18 通过 / 0 失败
```

> Muse 的测试依赖 `tests/loader.mjs` 提供的 mock 装载钩子，**必须带 `--import` 参数**，否则多数用例会因缺少 mock 而失败。

Bridge 的 pytest 使用内存库（`tests/conftest.py` 中 `BRIDGE_DATABASE_URL` 默认为 `sqlite:///:memory:`），**不会污染**本地 `bridge_mvp.db`。

### 代码检查

仓库目前没有引入独立的 linter 工具链，校验以运行时检查为准：

```bash
# JavaScript 语法检查
node --check products/focus-mvp/background.js

# Python 语法检查
python -m py_compile products/bridge-mvp/main.py
```

> Windows 上若 `python` 指向 Microsoft Store 占位程序，请改用 Python Launcher：`py -m py_compile ...`、`py -m pytest ...`。

### 一键验收

仓库自带一条串联式 QA 流水线，把打包、完整性、对比度、链接检查串成一条命令：

```bash
python scripts/qa_all.py              # 全量（不含压力测试）
python scripts/qa_all.py --full       # 含存储压力测试
python scripts/qa_all.py --only links # 只跑某一环
```

当前状态：**6/6 通过**（`shared_sync` / `pack_mvps` / `pack_final` / `verify_loading` / `contrast` / `links`）。

其中 `shared_sync` 是共享层副本一致性门禁：校验 `shared/vannevar-crypto.js` 与两个产品目录副本**字节完全一致**（漂移即阻断），同时如实报告历史遗留的设计令牌与 Bridge 侧运行时副本漂移（不阻断，留待专项批次处理）。

打包产物统一输出到仓库内的 `dist/`（该目录已加入 `.gitignore`，不入库）。

---

## 项目结构

```
Vannevar/
├── products/
│   ├── focus-mvp/          # Focus · Chrome MV3 扩展（捕捉）
│   │   ├── background.js   #   Service Worker
│   │   ├── crypto.js       #   E2E 加密（薄封装，核心见 shared/vannevar-crypto.js）
│   │   ├── crypto-profile.js # 加密档案同步（跨设备解密）
│   │   ├── db.js           #   sql.js 本地库
│   │   ├── sync.js         #   与 Bridge 的 push/pull 同步
│   │   ├── options.html    #   设置页（AI 增强等）
│   │   ├── lib/            #   第三方 SQL.js WASM 运行时 + 共享层副本
│   │   └── tests/          #   node:test 用例
│   ├── muse-mvp/           # Muse · Chrome MV3 扩展（整理）
│   │   ├── core/           #   muse.js / db.js / sync.js / ai*.js / crypto*.js
│   │   ├── dashboard/      #   Dashboard 时间线回顾
│   │   ├── popup/          #   扩展弹窗
│   │   ├── options/        #   设置页
│   │   └── tests/          #   node:test 用例
│   └── bridge-mvp/         # Bridge · FastAPI 服务（交接）
│       ├── main.py         #   应用入口
│       ├── auth.py         #   Basic Auth / CSRF / JWT
│       ├── ai.py           #   AI 增强与降级
│       ├── backup.py       #   滚动备份与恢复
│       ├── models.py       #   SQLAlchemy 模型
│       ├── routers/        #   projects / tasks / events / sync / …
│       ├── templates/      #   Jinja2 模板
│       ├── static/         #   静态资源
│       └── tests/          #   pytest 用例
├── shared/                 # 三产品共享层
│   ├── vannevar-brand.css  #   设计令牌
│   ├── vannevar-context-card.js  # .vcf 上下文卡片格式
│   ├── vannevar-crypto.js  #   端到端加密核心（Focus / Muse 共用）
│   ├── media/              #   品牌海报与演示视频
│   └── privacy-policy.html #   隐私政策页
├── demo/                   # 演示素材
├── docs/                   # 用户指南、部署指南、OpenAPI、阶段计划
│   └── opensource/         #   开源发布过程报告
├── tech/                   # 三产品技术方案
├── assets/                 # 一页纸、竞品矩阵等说明材料
├── diagrams/               # 架构图（HTML 源文件 + PNG 导出）
├── screenshots/            # 产品截图
├── scripts/                # 打包、截图、链接、对比度检查与一键验收脚本
├── QUICKSTART.md           # 30 秒快速上手
├── LOADING-GUIDE.md        # 扩展加载与本地验证指南
├── LICENSE / NOTICE / CONTRIBUTING.md / CODE_OF_CONDUCT.md
├── SECURITY.md / CHANGELOG.md / AGENTS.md
└── .editorconfig / .gitattributes
```

代码体量（实测，按产品目录源码文件统计）：focus-mvp 37 文件 / 10,312 行，muse-mvp 45 文件 / 13,935 行，bridge-mvp 63 文件 / 31,554 行，合计 145 文件 / 55,801 行。

---

## 贡献

欢迎参与。流程、环境准备与提交规范见 **[CONTRIBUTING.md](CONTRIBUTING.md)**；参与社区的共同约定见 **[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)**。

给后续 AI Agent 与协作者的仓库工作约定写在 **[AGENTS.md](AGENTS.md)**。

想了解「为什么这么设计」——每个技术取舍的理由、**代价**与影响面，见 **[docs/architecture-decisions.md](docs/architecture-decisions.md)**（架构决策记录）。改动既有决策时请在该文件的「决策变更日志」里追加，而不是直接改写历史。

---

## 许可

本项目以 **MIT 协议**发布，完整条款见 [LICENSE](LICENSE)。

仓库内的第三方组件（各产品 `lib/` 目录下的 SQLite WASM 运行时）与自有素材的品牌归属说明见 **[NOTICE](NOTICE)**。

---

## 致谢

- **Vannevar Bush**（1890–1974）与 1945 年的《As We May Think》、**Memex** 概念 —— 本项目的名字与出发点。
- 所有为 Focus、Muse、Bridge 提过问题、报过 bug、写过文档的贡献者。

本项目最初来源于一次独立的产品创意实践：从真实场景里「上下文不断丢」的痛点出发，把三个工具从第一天就按**可长期使用、可被他人接管**的标准来写 —— 这也是它今天以开源形式完整发布的原因。
