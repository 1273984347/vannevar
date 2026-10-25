<!--
  Pull Request 模板 —— Vannevar monorepo
  三个产品共用本模板：Focus（扩展）/ Muse（扩展）/ Bridge（服务）。
  请按实际情况勾选，未涉及的项留空即可。
-->

## 变更类型 / Change type

- [ ] 修复 / Fix
- [ ] 新功能 / Feature
- [ ] 文档 / Documentation
- [ ] 测试 / Test
- [ ] 构建 / Build
- [ ] 其他 / Other（请在下方说明）

## 关联 Issue / Related issue

<!-- 例：Closes #123；若为多个，用 Closes #123, #124 -->
Closes #

## 变更说明 / Description

<!-- 说明改了什么、为什么这样改、影响哪些模块。若含破坏性变更请单独标注。 -->

## 测试证据 / Testing evidence

<!-- 请勾选实际执行过的验证项，并在括号里补充结果数字。 -->

- [ ] Bridge · `pytest tests/ -q --ignore=tests/e2e`（结果：______ passed / ______ skipped / ______ failed）
- [ ] Focus · `node tests/test_crypto.mjs`（结果：______ 通过 / ______ 失败）
- [ ] Focus · `node --import ./tests/loader.mjs tests/test_sync_crypto.mjs`（结果：______ 通过 / ______ 失败）
- [ ] Muse · `node --import ./tests/loader.mjs tests/test_media_session.mjs`（结果：______ 通过 / ______ 失败）
- [ ] JS 语法 · `node --check`（三产品全部 .js，已排除 vendored 文件）
- [ ] 手动验证 / Manual verification（说明方式：______）

## 自检清单 / Self-check

- [ ] 未引入新依赖 / No new dependencies（新增依赖请同步更新 `requirements*.txt` 并说明理由）
- [ ] 未提交密钥与数据库文件 / No secrets or database files committed（`.env`、`*.db`、`backups/` 均不入库）
- [ ] 未改动无关文件 / No unrelated changes（改动范围限定在本次需求内）
- [ ] 文档同步更新 / Docs updated（用户可见的行为变更已同步到对应文档）

<!--
补充说明（可选）：
- 截图 / 录屏：
- 已知限制：
- 后续跟进：
-->
