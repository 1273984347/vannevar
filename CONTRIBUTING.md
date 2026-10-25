# 贡献指南

感谢你愿意为 Vannevar 出力。本项目由小团队维护，**清晰的改动 + 可复现的验证**比大而全的补丁更受欢迎。

本文档描述如何参与。仓库的目录职责划分、AI/协作者工作约定见 [AGENTS.md](AGENTS.md)。

---

## 1. 上手流程

1. **Fork** 本仓库到你自己的 GitHub 账号。
2. **Clone** 你的 fork：
   ```bash
   git clone https://github.com/<你的用户名>/Vannevar.git
   cd Vannevar
   ```
3. **添加上游仓库**（便于同步）：
   ```bash
   git remote add upstream https://github.com/<上游组织>/Vannevar.git
   ```
4. **建分支**，不要直接在 `main` 上改动：
   ```bash
   git checkout -b feat/focus-snapshot-export
   ```
5. 提交改动 → 推送到你自己的 fork → 发起 Pull Request。

### 分支命名约定

| 前缀 | 用途 | 示例 |
|---|---|---|
| `feat/` | 新功能 | `feat/muse-mood-merge` |
| `fix/` | 缺陷修复 | `fix/bridge-backup-restore` |
| `docs/` | 文档与注释 | `docs/bridge-deploy-guide` |
| `refactor/` | 不改变行为的重构 | `refactor/bridge-router-split` |
| `chore/` | 构建、脚本、杂项 | `chore/pack-script-output-dir` |
| `test/` | 只增补测试 | `test/focus-sync-conflict` |

分支名用小写 + 连字符，尽量带一个可辨识的范围词（`focus` / `muse` / `bridge` / `shared` / `docs` / `scripts`）。

---

## 2. 开发环境要求

| 组件 | 版本 | 用途 |
|---|---|---|
| Python | 3.12 | Bridge 服务端与其测试 |
| Node.js | 24 | 扩展单元测试（`node:test`） |
| Chrome / Edge | 102+ | 手动加载 MV3 扩展验证 |

无需 `npm install`：两个扩展是**原生 JavaScript**，不依赖前端框架与打包器；Bridge 的 Python 依赖通过 `requirements.txt` 安装。

```bash
cd products/bridge-mvp
python -m pip install -r requirements.txt
```

Bridge 还需要两个环境变量才能以非调试模式启动（缺失会**拒绝启动**，这是有意的安全默认值）：

```bash
export BRIDGE_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export BRIDGE_ADMIN_PASS="你的密码"
```

本地开发也可以 `BRIDGE_DEBUG=1`：此时密钥与管理员口令随机生成并打印到 stderr。

---

## 3. 跑测试

**改动提交前，请至少跑通与你改动相关的那一组。**

```bash
# Bridge：全量单元/集成测试
cd products/bridge-mvp
python -m pytest tests/ -q --ignore=tests/e2e

# Focus：端到端加密
cd products/focus-mvp
node tests/test_crypto.mjs

# Focus：同步链路加密
node --import ./tests/loader.mjs tests/test_sync_crypto.mjs

# Muse：媒体会话
cd products/muse-mvp
node --import ./tests/loader.mjs tests/test_media_session.mjs
```

当前基线：Bridge 435 passed / 9 skipped / 0 failed；Focus 16/16 与 11/11；Muse 18/18。

Bridge 的 pytest 使用内存数据库（`sqlite:///:memory:`），不会写入你本地的 `bridge_mvp.db`。

### 代码检查

仓库**没有**配置 ESLint、Black、flake8、pre-commit 等独立 lint 工具链，请不要在 PR 里引入它们（会带来大量无关 diff）。目前的检查方式：

```bash
# JavaScript 语法检查（在仓库根目录执行）
node --check products/focus-mvp/background.js

# Python 语法检查（在仓库根目录执行）
python -m py_compile products/bridge-mvp/main.py

# Python 逻辑检查（等价于静态检查 + 行为验证）
# 注意：必须先进入 products/bridge-mvp，否则 pytest 找不到 tests/ 与 tests/conftest.py
cd products/bridge-mvp
python -m pytest tests/ -q --ignore=tests/e2e
```

> **`--ignore=tests/e2e` 不可省略**：`tests/e2e/test_bridge_e2e.py` 顶层 `import playwright`，
> 而 playwright 在 `requirements-dev.txt` 里默认被注释掉，未安装时 pytest 会在收集阶段直接报错
> （collection error），整轮测试无法开始。根 `README.md`、Bridge README 与 CI 用的是同一条命令。
>
> e2e 需要 Playwright + Chromium，本地按需安装（约 150MB 下载，不在日常与 CI 流程内）：
>
> ```bash
> pip install playwright && playwright install chromium
> ```

CI 会执行上述测试与语法检查。

---

## 4. 代码风格约定

**总原则：跟随所在文件的既有风格，局部一致性优先于个人偏好。**

- **JavaScript（Focus / Muse / shared）**
  - 原生 JS，不用框架、不用构建步骤、不引入 npm 运行时依赖。
  - 2 空格缩进，文件末尾换行，UTF-8 无 BOM，LF 换行。
  - 保持 `const` / `let` 优先，避免 `var`；不要在扩展上下文脚本中注入 DOM。
  - DOM 输出必须经转义（Muse 侧见 `products/muse-mvp/shared/escape.js`，Focus 侧为同类转义工具），不要直接拼接 HTML。
  - 新增权限要谨慎：`host_permissions` 默认保持为空，按需申请。
- **Python（Bridge）**
  - 沿用现有文件的风格：类型注解、docstring（中文说明与意图）、模块级常量集中在文件顶部。
  - 4 空格缩进，UTF-8 无 BOM，LF 换行。
  - 路由拆分到 `routers/`，数据模型集中在 `models.py`，数据库逻辑集中在 `database.py`。
  - 涉及凭据、令牌、URL 的代码走 `secrets.compare_digest` 等常量时间比较，不写日志明文。
- **共用层（shared/）**
  - `shared/vannevar-brand.css` 是设计令牌的唯一来源；产品内不要另起一套颜色变量。
  - `shared/vannevar-context-card.js` 定义 `.vcf` 卡片格式，改动需同步 Bridge 的 `context_card.py`。

### 行尾与编码

由 `.editorconfig` 与 `.gitattributes` 统一约束：UTF-8 无 BOM、LF 换行、去除行尾空白（Markdown 除外，其行尾双空格是合法换行语法）。请在编辑器中启用这两个文件的规则。

---

## 5. 提交信息（Conventional Commits）

格式：`<type>(<scope>): <subject>`，正文可选，破坏性变更用 `!` 标注并附 `BREAKING CHANGE:` 说明。

| type | 用途 |
|---|---|
| `feat` | 新功能 |
| `fix` | 缺陷修复 |
| `docs` | 文档 |
| `test` | 测试 |
| `refactor` | 重构（无行为变化） |
| `perf` | 性能 |
| `chore` | 构建/脚本/杂项 |
| `build` | 依赖或打包调整 |

`scope` 建议用：`focus`、`muse`、`bridge`、`shared`、`docs`、`scripts`。

示例：

```
feat(muse): 支持把同一天捕获的灵感合并为同一氛围

fix(bridge): 恢复备份前先清理 -wal/-shm 侧车文件

docs(readme): 更新 Muse 测试命令，补上 --import 参数
```

一条提交只做一件事；不要把格式化与功能改动混在一起。

---

## 6. Pull Request 流程

1. 推送分支到你的 fork，发起 PR（目标分支为 `main`）。
2. PR 描述必须包含：
   - **改了什么、为什么**；
   - **影响范围**（是否触及共享层、是否需要同步改文档）；
   - **验证证据**（实际跑过的命令 + 结果，例如 `435 passed / 9 skipped / 0 failed`）。
3. CI 通过后等待维护者查看。
4. 维护者可能提出修改意见；请在原分支追加提交，不要 force-push 已评审过的提交。
5. 通过后由维护者合并（ squash 合并为主）。

**改动产品代码时，请同步更新对应文档**：`products/focus-mvp/README.md`、`products/muse-mvp/README.md`，以及涉及用户可见行为时更新根目录 `README.md`。文档与代码不一致的 PR 不会被合并。

---

## 7. Issue 使用建议

请先搜索是否已有同类 Issue，避免重复。

**报告缺陷**，请附上：

- 复现步骤（1、2、3…）；
- 期望行为 vs 实际行为；
- 环境：操作系统、Chrome/Edge 版本、Python 版本；
- 相关错误信息（控制台/终端日志）。

**提出功能建议**，请说明：要解决什么场景问题、期望的交互、是否有替代方案。

**提交安全问题**，**不要开公开 Issue** —— 请走 [SECURITY.md](SECURITY.md) 的私下上报流程。

---

## 8. 行为准则

参与即表示同意 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

---

## 9. 许可

你的贡献将以 **MIT 协议**发布，与本仓库其余部分一致。提交即视为同意保留你原有版权归属。
