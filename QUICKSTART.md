# 30 秒快速上手

> 三个产品独立可用，按任一场景体验即可。完整加载步骤见 [LOADING-GUIDE.md](LOADING-GUIDE.md)。
>
> 品牌海报与开场视频见 [shared/media/](shared/media/)。

---

## Focus — 上下文管理扩展（效率工具）

**一句话**：保存当前所有标签页为一组「上下文」，关闭后一键恢复，附带 AI 摘要。

**30 秒体验**：
1. `chrome://extensions/` → 开启开发者模式 → 加载已解压扩展 → 选 `products/focus-mvp/`
2. 打开 2-3 个网页 → 点工具栏 Focus 图标 → 「保存当前上下文」
3. 关闭这些网页 → 再点 Focus 图标 → 选刚才的记录 → 「恢复」→ 网页全部重新打开
4. 点「详情」查看 AI 生成的上下文摘要（无 API key 时显示规则引擎摘要）

**截图**：`screenshots/vannevar-focus-screenshot.png`

---

## Muse — 灵感捕捉扩展（生活娱乐）

**一句话**：在任意网页选中文字保存为「灵感」，按氛围标签分类，时间线回顾。

**30 秒体验**：
1. 加载 `products/muse-mvp/`（同 Focus 步骤）
2. 在任意网页选中一段文字 → 点工具栏 Muse 图标
3. 输入备注、选氛围标签 → 「保存灵感」
4. 点 popup 中「打开灵感博物馆」→ 查看时间线、氛围分布图、来源域名聚合

**截图**：`screenshots/vannevar-muse-screenshot.png`

---

## Bridge — 公益交接平台（社会服务）

**一句话**：志愿者扫码查看项目任务与协作留言，管理员通过看板管理进度。

**30 秒体验**：
```bash
cd products/bridge-mvp
pip install -r requirements.txt
python main.py
# 终端会打印 [BRIDGE] Dev admin password: xxxx，用 admin + 该密码登录
```
1. 浏览器访问 `http://127.0.0.1:8000/` → 输入 `admin` + 终端打印的密码
2. 「新建项目」→ 进入项目详情 → 添加任务（拖拽看板）
3. 点「交接」→ 自动生成交接摘要
4. 访问「统计仪表盘」查看任务分布、趋势、贡献榜
5. 项目详情页二维码扫码或 `/p/{token}` 体验志愿者视角

**截图**：`screenshots/vannevar-bridge-screenshot.png`

---

## 常见问题

| 问题 | 答案 |
|------|------|
| Focus/Muse 需要 API key 吗？ | 不需要。无 key 时使用规则引擎，有 key 时升级为 AI 增强 |
| Bridge 密码是什么？ | 启动时自动生成并打印到终端，也可用 `BRIDGE_ADMIN_PASS` 指定 |
| 数据存储在哪？ | 全部本地：扩展用 SQLite WASM，Bridge 用本地 SQLite 文件 |
| 支持哪些浏览器？ | Chrome / Edge（Chromium 内核，Manifest V3） |
| 如何查看 API 文档？ | Bridge 启动后访问 `/docs`（仅 dev 模式） |
