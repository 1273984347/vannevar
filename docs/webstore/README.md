# Chrome Web Store 上架套件（T-11）

本目录是 Focus / Muse 两个扩展上架 Chrome Web Store 的**全套材料**。商店账号注册、$5 注册费与最终上传是**发布负责人手动步骤**；本套件保证其余一切「拿来即用」。

## 材料清单

| 文件 | 内容 |
|---|---|
| [listing-focus.md](listing-focus.md) | Focus 商店条目：名称、中英短描述（≤132 字符）、中英详述、单一用途声明、逐权限理由、数据披露 |
| [listing-muse.md](listing-muse.md) | Muse 商店条目（同上结构） |

## 提交步骤（手动，约 30 分钟 + 审核等待）

1. **注册开发者账号**：<https://chrome.google.com/webstore/devconsole>（一次性 $5 注册费）。
2. **打包**：仓库根目录跑 `python scripts/qa_all.py`（6/6 PASS），取 `dist/focus-mvp.zip` 与 `dist/muse-mvp.zip` —— zip 内 manifest 位于根目录，`verify_loading` 门禁已校验完整性。
3. **版本号**：上传前确认 `manifest.json` 的 `version` 严格递增（CWS 不允许同版本重复上传）。
4. **上传**：开发者后台 → New item → 上传 zip。
5. **Store listing**：逐字段粘贴对应 listing 文件内容（主语言建议 zh-CN，同时填英文 locale）。
6. **Privacy 标签页**：
   - Single purpose：粘贴 listing 文件中的「单一用途声明」；
   - Permission justifications：逐条粘贴对应权限理由；
   - Data usage：按「数据披露」一节作答 —— **全部选 No**（数据不离开本机；同步数据端到端加密后发往用户自架的 Bridge，开发者无法读取）；
   - Privacy policy URL：`https://github.com/1273984347/vannevar/blob/main/shared/privacy-policy.html`（仓库内公开页面，随版本演进更新）。
7. **截图**：1280×800，直接使用仓库 `screenshots/` 下的对应图（Focus：`focus-popup-expanded.png`；Muse：`muse-dashboard.png`）；可再补 2-4 张（Muse 可加 `muse-popup-saved.png`）。
8. **Distribution**：Public；地区默认全部。
9. **Submit for review**。审核通常数小时至数天；`host_permissions` 为空 + 无远程代码 + 权限理由充分，属低风险组合。

## 审核友好要点（已具备）

- ✅ `host_permissions` 为空（最小权限组合）；
- ✅ 无远程托管代码（CSP `'self'`，全部本地资源）；
- ✅ URL scheme 白名单 + 富文本转义（无 `eval` / 远程脚本注入面）；
- ✅ 隐私政策页面随仓库公开；
- ✅ 每条权限都有与实现对应的用途说明（见两个 listing 文件）。
