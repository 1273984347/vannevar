# AGENTS.md —— Vannevar 仓库工作约定

面向在本仓库工作的 AI Agent、自动化脚本与协作者。人类贡献者请先读 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 1. 项目定位

**一句话**：Vannevar 是一套「上下文管理」产品族 —— 让信息在恰当的时机，出现在恰当的地方。

三个产品覆盖同一条上下文生命周期的三个阶段：

| 产品 | 目录 | 阶段 | 形态 |
|---|---|---|---|
| **Focus** | `products/focus-mvp/` | 捕捉 | Chrome MV3 扩展（原生 JS + sql.js/WASM + chrome.storage） |
| **Muse** | `products/muse-mvp/` | 整理 | Chrome MV3 扩展（原生 JS + sql.js/WASM + chrome.storage） |
| **Bridge** | `products/bridge-mvp/` | 交接 | FastAPI + SQLAlchemy + SQLite(WAL) + Jinja2 + SSE |

共享层：`shared/vannevar-brand.css`（设计令牌）、`shared/vannevar-context-card.js`（`.vcf` 上下文卡片格式）、`products/*/shared/vannevar-shared.js`。

---

## 2. 硬约束（违反即回退）

1. **唯一工作根是 `D:\Vannevar`。** 旧的 `D:\1\Vannevar` 工作副本路径已废弃，**禁止再使用**（读写、打包输出、文档引用、脚本内硬编码一律不得出现）。所有新文档引用路径优先用**仓库相对路径**。
   > 本行是**禁令声明本身**，必须写出被禁用的具体路径才有意义，是全仓唯一允许出现该路径的位置；除此之外，代码、配置、产品文档与 CI 中该路径的命中数必须为 0。
2. **不引入新前端框架。** Focus / Muse 保持原生 JavaScript，不加打包器、不加 npm 运行时依赖、不加 UI 框架。需要新能力时优先扩展 `shared/`。
3. **数据本地优先，用户上下文不上传。** 扩展侧默认只存本机；同步内容必须经端到端加密（AES-GCM-256 + PBKDF2-SHA256 100,000 次迭代）。不得把用户上下文、密钥、口令写入日志或提交进仓库。
4. **提交必须带显式 pathspec。** `git commit` 时显式列出文件，不要用 `git add -A` / `git commit -a`：
   ```bash
   git commit -m "fix(bridge): 恢复前清理 WAL 侧车文件" -- products/bridge-mvp/backup.py products/bridge-mvp/tests/test_backup_recovery.py
   ```
   原因：多 Agent 并行时，全量提交会把别人的在途改动一起带进去。
5. **动手前先读 `docs/opensource/` 下的既有报告。** 其中记录了已确认的事实、已定的决策与已知的坑；不要重复调查已结论的问题，也不要推翻已记录的决策而不留理由。

---

## 3. 目录职责

| 路径 | 负责什么 | 谁能改 |
|---|---|---|
| `products/focus-mvp/` | Focus 扩展全部代码、manifest、图标 | Focus 方向负责人 |
| `products/muse-mvp/` | Muse 扩展全部代码、manifest、图标 | Muse 方向负责人 |
| `products/bridge-mvp/` | FastAPI 服务、模型、路由、模板、测试 | Bridge 方向负责人 |
| `products/*/lib/` | 第三方 SQL.js WASM 运行时（原样引入） | **不要手改**；升级需同步更新 `NOTICE` |
| `products/*/shared/` | 产品内共享脚本（`vannevar-shared.js`、`escape.js` 等） | 对应产品负责人 |
| `shared/` | 设计令牌、Context Card 格式、品牌素材 | 共享层负责人；**改动需通知三个产品方向** |
| `docs/` | 用户指南、部署指南、OpenAPI | 文档负责人 |
| `docs/opensource/` | 开源发布过程报告（计划 / 执行记录 / 验收 / 复盘） | 发布流程执行者 |
| `assets/`、`diagrams/`、`screenshots/` | 说明材料、架构图、产品截图 | 文档负责人 |
| `demo/` | 演示素材 | 文档负责人 |
| `scripts/` | 打包、截图、链接与对比度检查脚本 | 工具链负责人 |
| 根级 `README.md` / `LICENSE` / `NOTICE` / `CONTRIBUTING.md` / `CODE_OF_CONDUCT.md` / `SECURITY.md` / `CHANGELOG.md` / `AGENTS.md` / `.editorconfig` / `.gitattributes` | 发布与社区面文档 | 发布负责人（并行工作时按文件独占分配，勿交叉修改） |

**并行工作约定**：多个 Agent 同时开工前，先声明自己将要写哪些文件；同一文件只由一个 Agent 写入。改他人已写入文件时，先说明原因并让对方确认。

---

## 4. 测试命令速查

```bash
# Bridge：全量单元/集成测试（内存库，不污染本地数据）
cd products/bridge-mvp
py -m pytest tests/ -q --ignore=tests/e2e
# 基线：435 passed / 9 skipped / 0 failed

# Focus：端到端加密
cd products/focus-mvp
node tests/test_crypto.mjs                 # 16 / 0

# Focus：同步链路加密
node --import ./tests/loader.mjs tests/test_sync_crypto.mjs   # 11 / 0

# Muse：媒体会话
cd products/muse-mvp
node --import ./tests/loader.mjs tests/test_media_session.mjs  # 18 / 0
```

注意：

- Muse 的测试**必须**带 `--import ./tests/loader.mjs`，否则 mock 缺失导致大量失败。
- 仓库**没有** ESLint / Black / flake8 / pre-commit 配置，不要引入。JS 用 `node --check <file>`，Python 用 `python -m py_compile <file>` 加 `pytest`。
- 改 Bridge 行为后跑全量 pytest；改 Focus/Muse 共享逻辑时跑对应产品的 node 测试。

---

## 5. 工作方法

### 5.1 标准闭环

**执行前计划 → 执行记录 → 验收 → 复盘 → 经验沉淀**

1. **执行前计划**：先写清目标、范围（做/不做）、验收判据（可布尔或计数判定）、风险与回退方案；计划落盘到 `docs/opensource/` 下再动手。
2. **执行记录**：按步骤留下可追溯的日志（命令、输入、输出、结论），区分**事实**与**假设**，假设必须标待验证。
3. **验收**：只依据**实际跑过的命令输出**下结论，不凭推测宣布成功。
4. **复盘**：记录哪里偏离预期、根因是什么。
5. **经验沉淀**：把可复用的结论写回 `docs/opensource/` 的报告与本文件，避免同一个坑踩两次。

### 5.2 大改动后的自查

跨多文件 / 多目录的批量改动，交付前除机器判据外，还要做两件事：

- **独立盲审**：由未参与实现的审阅方在无作者上下文下审阅完整 diff，重点找逻辑缺陷与遗漏。
- **可执行行为复核**：实跑关键命令，必要时做改动前后的对照。

### 5.3 清理类任务的验收口径

清理、迁移、去重、删词类任务**不按「改了多少」验收，按「0 命中」验收**：

- 逐条列出待清除的目标（路径、关键词、文件类型）；
- 对**全仓 + 全项目目录**做检索，确认命中数为 0；
- 历史段、归档面、本地工地同样要清；
- 报告中给出命中数（清理前 / 清理后），不写「大致清理完毕」。

### 5.4 文档纪律

- 结论必须带数据、证据与显式假设；禁止「应该没问题」这类模糊表述。
- 建议必须含量化指标 + 责任人 + 时间 + 验收方式。
- 交付物要能脱离本次会话独立阅读（自包含）。
- 关键决策留痕：写明备选方案、选择理由与可复用结论。

---

## 6. 文档与代码的对应关系

改了产品代码，文档要同步：

| 改动类型 | 必须同步更新 |
|---|---|
| Focus 行为变化 | `products/focus-mvp/README.md` + 根 `README.md`（能力/命令/截图说明） |
| Muse 行为变化 | `products/muse-mvp/README.md` + 根 `README.md` |
| Bridge 行为、接口或环境变量变化 | 根 `README.md` + `docs/deploy-guide.md` + `docs/openapi.json`（如接口有变） |
| 共享层令牌或 `.vcf` 格式变化 | `shared/vannevar-brand.css` / `shared/vannevar-context-card.js` 对应说明 + 三个产品文档 + `NOTICE`（如涉第三方） |
| 新增依赖或第三方资产 | `NOTICE` + `requirements.txt` / 引入位置说明 |
| 用户可见行为变化 | `CHANGELOG.md` 的 `Unreleased` 段 |

> 说明：三个产品均有产品级 README（`products/focus-mvp/README.md`、`products/muse-mvp/README.md`、`products/bridge-mvp/README.md`），行为变更需三者同步。

反向要求也成立：README 里写的命令、路径、端点，必须在仓库里真实存在 —— 写完文档要自己核对一遍。

---

## 7. 收官检查清单

提交前逐条确认：

- [ ] 改动范围内的测试全部实跑通过，输出已记录；
- [ ] 没有引入新前端框架 / 新 npm 运行时依赖；
- [ ] 没有把密钥、口令、用户上下文写入代码或日志；
- [ ] `git commit` 带显式 pathspec，未夹带他人文件；
- [ ] 相关 README / CHANGELOG 已同步；
- [ ] 清理类任务的全仓命中数为 0；
- [ ] 计划、执行记录、验收、复盘已落到 `docs/opensource/`。
