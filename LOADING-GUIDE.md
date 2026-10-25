# Vannevar 加载测试指南

本指南用于在本地测试或验证时，快速加载并检查三个 MVP 产物。

---

## 1. Focus（浏览器扩展）

### 方式 A：直接加载已解压源码（开发调试）

1. 打开 Chrome / Edge，访问 `chrome://extensions/`
2. 开启右上角「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择 `Vannevar/products/focus-mvp` 文件夹
5. 工具栏出现 Vannevar 图标后即可使用

### 方式 B：从 zip 包加载（发布分发）

1. 解压 `products/focus-mvp.zip` 到任意文件夹（如 `focus-mvp/`）
2. 按方式 A 的第 1-3 步进入开发者模式
3. 选择解压后的 `focus-mvp/` 文件夹
4. 检查扩展详情页是否显示「Vannevar · Focus MVP v0.1.0」

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
3. 选择 `Vannevar/products/muse-mvp` 文件夹

### 方式 B：从 zip 包加载

1. 解压 `products/muse-mvp.zip` 到 `muse-mvp/` 文件夹
2. 按方式 A 加载该文件夹
3. 检查扩展详情页是否显示「Vannevar · Muse MVP v0.1.0」

> **权限说明**：Muse 默认仅请求 `activeTab + scripting` 即可捕获当前标签页灵感，安装时不会提示「读取所有网站数据」。若未来需要后台标签页捕获，可在扩展详情页手动授予可选权限。

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

```bash
cd Vannevar/products/bridge-mvp
pip install -r requirements.txt
python main.py
```

默认监听 `http://127.0.0.1:8000/`。

### 默认凭据

- 管理员账号：`admin`，密码在服务启动时自动生成并打印到终端（stderr），形如 `[BRIDGE] Dev admin password: xxxx`
- 如需固定密码：启动前设置环境变量 `BRIDGE_ADMIN_PASS=你的密码`
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
- 部署到自定义域名时设置 `BRIDGE_BASE_URL=https://your-domain/`

---

## 5. 交付文件清单

| 产物 | 文件 | 加载方式 |
|------|------|----------|
| Focus | `products/focus-mvp.zip` | 解压后加载已解压扩展 |
| Muse | `products/muse-mvp.zip` | 解压后加载已解压扩展 |
| Bridge | `products/bridge-mvp/` 目录 | `python main.py` |
