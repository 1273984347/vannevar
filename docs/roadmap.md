# 路线图 / Roadmap

> **English TL;DR**: Vannevar evolves in three lanes — **Now** (Chrome Web Store listing, UI i18n, on-device AI tier, logout token revocation), **Next** (shared-layer single-sourcing, docker-compose deploy, asset re-creation), **Later** (an idea pool, not promises). The project ships in small verified batches; the sequence below is an ordering, not a calendar. Feedback is welcome via [Issues](https://github.com/1273984347/vannevar/issues) or [Discussions](https://github.com/1273984347/vannevar/discussions).

本路线图只排**顺序**，不承诺**日历时间**——单人项目按批次推进，每个批次走「计划 → 执行 → 验收 → 复盘」闭环（见 [AGENTS.md](../AGENTS.md) 与 [docs/opensource/](opensource/) 的过程报告）。反过来说：出现在「Later」不等于会做，只是排在后面。

## 三条不变的原则

1. **本地优先不动摇**：用户上下文默认不出本机；同步内容端到端加密。
2. **AI 永远是可选增强**：任何 AI 能力缺失时，核心功能必须完整可用。
3. **无遥测**：不做任何形式的数据回传，用户声音只经 Issue / Discussions 显式表达。

## Now —— 进行中（目标：v0.3）

| 事项 | 说明 | 状态 |
|---|---|---|
| Chrome Web Store 上架准备（Focus / Muse） | 上架材料、权限说明、隐私政策入口；上架后安装从「开发者模式加载目录」变为一次点击 | 准备中 |
| 扩展 UI 的 i18n（中英双语） | 上架后流量是全球的，popup / options 界面需要双语 | 排队 |
| `logout()` 服务端 token 撤销 | 修复「退出登录只清本地、服务端 token 仍有效」的安全缺口 | ✅ 2026-11-03 |
| salt-mismatch 的用户可见提示 | 跨设备口令不一致时明确告知，而不是静默保留远端 | ✅ 2026-11-03 |
| 端侧 AI 增强层（Chrome 内置 AI） | 降级链升级为：端侧 AI（零配置、零上传）→ 远程 OpenAI 兼容 API → 本地规则引擎 | 排队 |
| 反馈回路 | Discussions 已开启；feedback 模板与路线图即本批产物 | ✅ 2026-11-03 |

## Next —— 接下来（Now 完成后按序）

| 事项 | 说明 | 来源 |
|---|---|---|
| 共享层单源化 | `privacy-policy.html` 四副本收敛为单一源（STRICT 门禁）；设计令牌与 Bridge 侧运行时副本漂移评估处置 | T-02/T-05 遗留 |
| Bridge 一键部署 | `docker-compose.yml` + 最小配置示例，降低自托管门槛 | 部署反馈预期 |
| 演示素材重制 | 重造干净演示数据后重截 `bridge-detail/home` 等截图；重录品牌视频（去参赛痕迹） | T-01 R-05 / T-05 |
| QUICKSTART / deploy-guide 英文化 | 文档层双语覆盖第二梯队 | v0.2.0 后评估 |

## Later —— 想法池（不构成承诺）

- **`.vcf`（Context Card）作为跨产品互操作格式生态化**：让第三方工具也能读写上下文卡片；
- **Focus 恢复保真度增强**：更多会话状态（表单内容、滚动栈、分组窗口）的凝固与还原；
- **Bridge 协作深化**：更细粒度的角色与通知策略；
- **志愿者移动端体验**：扫码接力页面的移动端适配打磨。

## 已完成的里程碑

| 时间 | 里程碑 |
|---|---|
| 2026-10-25 | **v0.1.0** 三产品完整开源发布（修复 Bridge 恢复缺陷、补 CI、发布卫生） |
| 2026-11-03 | **v0.2.0** Muse 端到端加密补齐 + 跨设备解密（加密档案 salt 上云）+ 依赖升级 + 展示面打磨 + 英文 README；新用户首航自测通过（详见 [CHANGELOG](../CHANGELOG.md) 与 docs/opensource/ 报告） |
| 2026-11-03 | 反馈回路建立（Discussions + feedback 模板 + 本路线图） |

---

反馈入口：[Bug 报告](https://github.com/1273984347/vannevar/issues/new?template=bug_report.yml) · [产品反馈](https://github.com/1273984347/vannevar/issues/new?template=feedback.yml) · [讨论区](https://github.com/1273984347/vannevar/discussions) · 安全问题请走 [SECURITY.md](../SECURITY.md) 私下上报。
