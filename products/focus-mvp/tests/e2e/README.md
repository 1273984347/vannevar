# Focus MVP 端到端测试

## 安装依赖

```bash
cd products/focus-mvp
pip install -r tests/requirements.txt
playwright install chromium
```

## 运行测试

```bash
python tests/e2e/test_focus.py
python tests/e2e/test_adversarial.py
```

## 测试内容

### test_focus.py（常规链路）

1. 将扩展复制到临时目录，注入 RSA key 以获得固定扩展 ID。
2. 启动 Chromium 并加载扩展。
3. 等待 Service Worker 启动。
4. 打开一个普通网页，再打开 `chrome-extension://<id>/popup.html`。
5. 点击「保存当前上下文」，验证快照列表出现。
6. 点击「删除」，验证快照列表清空。
7. 检查 Service Worker 和 popup 控制台无 error。

### test_adversarial.py（对抗场景）

- 缺失 `contextMenus` 权限时核心保存功能仍可用。
- 危险 URL（`chrome://`、`chrome-extension://` 等）被过滤。
- 恢复 `javascript:` 等被阻止 URL 时不崩溃。
- 并发保存不会导致数据库重复初始化。
- 未知 action 返回 `ok: false` 而不是断开连接。

## 已知限制

- Playwright headless 模式目前无法稳定触发扩展 Service Worker，脚本会自动回退到 headed 模式。
- 测试需要联网访问 `https://example.com`，用于提供一个可被保存的普通标签页。
