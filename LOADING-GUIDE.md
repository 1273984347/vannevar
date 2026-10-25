# Vannevar 加载测试指南

本指南用于在本地测试或验证时，快速加载并检查三个 MVP 产物。

---

## 1. Focus（浏览器扩展）

### 方式 A：直接加载已解压源码（开发调试）

1. 打开 Chrome / Edge，访问 `chrome://extensions/`
2. 开启右上角「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择仓库内的 `products/focus-mvp` 文件夹
5. 工具栏出现 Vannevar 图标后即可使用

### 方式 B：从 zip 包加载（发布分发）

> 仓库**不保留 zip 包**：`.gitignore` 同时排除了 `dist/` 与 `*.zip`，所以 clone 下来的工作树里
> 没有任何 zip，需要自己先生成。

在**仓库根目录**执行打包脚本：

```bash
python scripts/pack_mvps.py
```

产物在仓库根目录的 `dist/` 下：

| 产物 | 内容 |
|---|---|
| `dist/focus-mvp.zip` | Focus 扩展包 |
| `dist/muse-mvp.zip` | Muse 扩展包 |
| `dist/bridge-mvp.zip` | Bridge 服务端包 |

每个 zip 同名还会生成一份 `*.zip.manifest.json`（含 SHA256 校验和），可用于校验包完整性。
`dist/` 同样是 gitignored 的本地产物，不随仓库分发。

加载步骤：

1. 解压 `dist/focus-mvp.zip` 到任意文件夹（如 `focus-mvp/`）
2. 按方式 A 的第 1-3 步进入开发者模式
3. 选择解压后的 `focus-mvp/` 文件夹
4. 检查扩展详情页是否显示「Vannevar · Focus MVP v0.1.0」

> 正式发布后也可直接从 GitHub Releases 下载打包好的 zip，无需自行生成。

### 快速验证

1. 打开 3 个网页（如 GitHub、MDN、Stack Overflow）
2. 点击工具栏 Focus 图标 → 点击「保存当前上下文」
3. 关闭这 3 个网页
4. 再次点击 Focus 图标 → 找到刚才的记录 → 点击「恢复」
5. 观察是否重新打开刚才的 3 个网页
6. 点击记录右侧「详情」→ 查看 Resume 摘要（场景、关键词、建议）

---

## 2. Muse（浏览器扩展）

### 方式 A：直接加载已解压源码

1. 打开 `chrome://extensions/`，开启「开发者模式」
2. 点击「加载已解压的扩展程序」
3. 选择仓库内的 `products/muse-mvp` 文件夹

### 方式 B：从 zip 包加载

zip 包需自行生成（仓库不保留 zip，原因与产物清单见上文 Focus 的方式 B）：

```bash
# 在仓库根目录执行
python scripts/pack_mvps.py
```

1. 解压 `dist/muse-mvp.zip` 到 `muse-mvp/` 文件夹
2. 按方式 A 加载该文件夹
3. 检查扩展详情页是否显示「Vannevar · Muse MVP v0.1.0」

> **权限说明**：Muse 默认仅请求 `activeTab + scripting` 即可捕获当前标签页灵感，安装时不会提示「读取所有网站数据」。
> `optional_host_permissions` 里的 `https://*/*` 需用户在安装时按需手动授权，非默认启用（详见产品 README 的「已知限制与设计取舍」）。
> 若未来需要后台标签页捕获，可在扩展详情页手动授予可选权限。

### 快速验证

1. 在任意网页选中一段文字
2. 点击工具栏 Muse 图标
3. 输入备注，选择氛围标签，点击「保存灵感」
4. 点击 popup 中「打开灵感博物馆」
5. 在仪表盘查看时间线、氛围分布、来源域名聚合
6. 点击顶部「生成回顾」查看 Resume 摘要

---

## 3. Bridge（Web 服务）

Bridge 无需浏览器扩展，直接运行 Python 服务即可。

### 本地启动

Bridge 在**非调试模式**下必须同时设置 `BRIDGE_JWT_SECRET` 与 `BRIDGE_ADMIN_PASS`，
缺任意一个服务都会**直接拒绝启动**（抛 `RuntimeError`）。二选一：

**方式一（推荐）：显式设置两个环境变量**

```powershell
# Windows PowerShell
cd products/bridge-mvp
py -m pip install -r requirements.txt
$env:BRIDGE_JWT_SECRET = python -c "import secrets; print(secrets.token_hex(32))"
$env:BRIDGE_ADMIN_PASS = "改成你的密码"
python main.py
```

```bash
# Linux / macOS
cd products/bridge-mvp
pip install -r requirements.txt
export BRIDGE_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export BRIDGE_ADMIN_PASS="改成你的密码"
python main.py
```

**方式二：本地开发用 `BRIDGE_DEBUG=1`**（JWT 密钥与管理员密码随机生成并打印到 stderr）

```powershell
# Windows PowerShell
cd products/bridge-mvp
py -m pip install -r requirements.txt
$env:BRIDGE_DEBUG = "1"
python main.py
# 启动后终端（stderr）会打印 [BRIDGE] Dev admin password: xxxx
```

```bash
# Linux / macOS
cd products/bridge-mvp
pip install -r requirements.txt
export BRIDGE_DEBUG=1
python main.py
# 启动后终端（stderr）会打印 [BRIDGE] Dev admin password: xxxx
```

默认监听 `http://127.0.0.1:8000/`。

### 默认凭据

- 管理员账号：`admin`（可用 `BRIDGE_ADMIN_USER` 覆盖）
- 管理员密码：`BRIDGE_ADMIN_PASS` 指定的值。**非调试模式下该变量必填**，缺失或仍为内置弱口令时服务拒绝启动
- 随机 dev 密码：仅当 `BRIDGE_DEBUG=1` 且**未**设置 `BRIDGE_ADMIN_PASS` 时，服务启动时在 stderr 打印一行
  `[BRIDGE] Dev admin password: xxxx` —— 只有这一种情况下才会有这行输出
- 志愿者入口：在项目详情页扫描二维码或访问 `/p/{qr_token}`

### 快速验证

1. 访问 `http://127.0.0.1:8000/`，输入管理员凭据
2. 点击「新建项目」，填写项目名称与描述
3. 进入项目详情 → 添加任务（待办/进行中/已完成）
4. 添加一条协作留言
5. 点击「交接」→「自动生成交接摘要」
6. 访问「统计仪表盘」，查看任务分布、近 7 天趋势、志愿者贡献榜
7. 用志愿者二维码链接访问 `/p/{token}`，更新任务状态并发表留言

---

## 4. 常见问题

### 扩展加载时提示「无法加载」

- 检查 `manifest.json` 是否在解压后的根目录
- 检查 `lib/sql-wasm.wasm` 文件是否存在（约 1MB）
- 检查 `icons/` 下是否有 `icon16.png`、`icon48.png`、`icon128.png`
- 不要直接加载 zip 文件，必须先解压

### 点击保存后没有反应

- 右键点击扩展图标 →「检查弹出内容」打开 Console
- 查看是否有红色报错，常见原因是 CSP 限制或 sql.js 初始化失败

### Bridge 端口被占用

- 使用其他端口：`uvicorn main:app --host 127.0.0.1 --port 8080`
- 若用 `python main.py` 启动，也可改环境变量 `BRIDGE_PORT=8080`（该变量**只在 `python main.py` 下生效**；
  用 `uvicorn main:app --host ... --port ...` 直接启动时，监听地址与端口由 uvicorn 参数决定）
- 部署到自定义域名时设置 `BRIDGE_BASE_URL=https://your-domain/`

---

## 5. 交付文件清单

| 产物 | 文件 | 加载方式 |
|------|------|----------|
| Focus | `products/focus-mvp/` 目录，或自行打包得到的 `dist/focus-mvp.zip` | 目录直接加载；zip 需先解压 |
| Muse | `products/muse-mvp/` 目录，或自行打包得到的 `dist/muse-mvp.zip` | 目录直接加载；zip 需先解压 |
| Bridge | `products/bridge-mvp/` 目录 | `python main.py`（需先设置 `BRIDGE_JWT_SECRET` + `BRIDGE_ADMIN_PASS`，或设 `BRIDGE_DEBUG=1`） |
