# Bridge MVP Playwright 端到端对抗测试
# 用法：python tests/e2e/test_bridge_e2e.py

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

# P1-2: 测试环境显式开启 dev 模式，允许使用默认密码
os.environ.setdefault("BRIDGE_DEBUG", "1")

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parents[2]

# 安全加固后 admin 页面要求 Basic Auth，e2e 需携带凭据
from auth import ADMIN_PASS, ADMIN_USER
ADMIN_HTTP_CREDENTIALS = {"username": ADMIN_USER, "password": ADMIN_PASS}


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(url, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            import urllib.request

            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def _start_server():
    port = _free_port()
    env = os.environ.copy()
    env["BRIDGE_DATABASE_URL"] = "sqlite:///:memory:"
    env["BRIDGE_BASE_URL"] = f"http://127.0.0.1:{port}/"
    # 修复：子进程必须使用父进程已生成的 admin 密码，否则 Basic Auth 凭据不匹配
    env["BRIDGE_ADMIN_PASS"] = ADMIN_PASS
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(BASE_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    if not _wait_for_server(f"{base_url}/health"):
        proc.terminate()
        raise RuntimeError("Bridge 服务启动超时")
    return proc, base_url


def _stop_server(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_bridge_e2e():
    proc, base_url = _start_server()
    errors = []
    dialogs = []

    def on_dialog(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    def expect_dialog(page, pattern, timeout=2000):
        # 兼容旧调用：原本检查 alert 弹窗，现在 alert 已替换为 toast。
        # 此函数仅作向后兼容占位，新代码应使用 expect_toast。
        deadline = time.time() + timeout / 1000
        while time.time() < deadline:
            for msg in list(dialogs):
                if pattern in msg:
                    dialogs.remove(msg)
                    return msg
            time.sleep(0.1)
        return None

    def expect_toast(page, pattern, timeout=5000):
        """等待并匹配包含 pattern 的 toast 文本。"""
        deadline = time.time() + timeout / 1000
        while time.time() < deadline:
            try:
                toasts = page.locator(".v-toast").all_text_contents()
            except Exception:
                toasts = []
            for t in toasts:
                if pattern in t:
                    return t
            time.sleep(0.1)
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                # 安全加固：admin 页面要求 Basic Auth，通过 context 预设凭据
                context = browser.new_context(http_credentials=ADMIN_HTTP_CREDENTIALS)
                page = context.new_page()
                page.on("dialog", on_dialog)
                page.on("pageerror", lambda err: errors.append(str(err)))

                # 1. 管理后台：创建项目时注入 XSS payload
                xss_payload = "<script>alert('xss-project')</script>"
                page.goto(f"{base_url}/")
                page.fill("#name", xss_payload)
                page.fill("#description", xss_payload)
                page.click("#createProjectForm button[type='submit']")
                # L11: 用 wait_for_url 替代固定超时
                page.wait_for_url(lambda url: "/admin/projects/" in url, timeout=10000)
                html = page.content()
                assert xss_payload not in html, "项目名中的 XSS 未被转义"
                assert "&lt;script&gt;alert('xss-project')&lt;/script&gt;" in html, "XSS payload 未正确转义为 HTML 实体"
                assert not dialogs, f"触发了意外弹窗: {dialogs}"

                # 2. 项目详情页：添加任务（空白标题应失败）— toast 反馈
                page.fill("#createTaskForm input[name='title']", "   ")
                page.click("#createTaskForm button")
                page.wait_for_timeout(1500)
                assert expect_toast(page, "添加任务失败", timeout=5000), "应弹出任务创建失败 toast"
                assert page.locator("#taskList li").count() == 0, "空白标题任务不应被创建"
                assert not dialogs, "空白任务提交不应触发 alert 弹窗"

                # 3. 添加正常任务
                page.fill("#createTaskForm input[name='title']", "采购物资")
                page.fill("#createTaskForm input[name='owner_name']", "小王")
                page.click("#createTaskForm button")
                page.wait_for_selector("#taskList li", state="attached", timeout=5000)
                assert page.locator("#taskList li").count() == 1, "正常任务应被创建"

                # 4. 记录交接：空白摘要应失败 — toast 反馈
                page.fill("#createHandoffForm textarea[name='summary']", "   ")
                page.click("#createHandoffForm button")
                page.wait_for_timeout(1500)
                assert expect_toast(page, "记录交接失败", timeout=5000), "应弹出交接记录失败 toast"

                # 记录正常交接
                page.fill("#createHandoffForm input[name='outgoing_volunteer']", "小李")
                page.fill("#createHandoffForm input[name='incoming_volunteer']", "小张")
                page.fill("#createHandoffForm textarea[name='summary']", "第一批物资已到位，注意防潮")
                page.click("#createHandoffForm button")
                page.wait_for_timeout(500)

                # 5. 协作留言 XSS
                msg_xss = "<img src=x onerror=alert('xss-msg')>"
                page.fill("#createMessageForm input[name='author']", "小张")
                page.fill("#createMessageForm textarea[name='content']", msg_xss)
                page.click("#createMessageForm button")
                page.wait_for_timeout(500)
                html = page.content()
                assert msg_xss not in html, "留言 XSS 未被转义"
                assert not dialogs, f"留言触发意外弹窗: {dialogs}"

                # 6. 二维码页
                qr_link = page.locator("a[href^='/qr/']").get_attribute("href")
                page.goto(f"{base_url}{qr_link}")
                page.wait_for_selector(".qr-image", state="visible", timeout=10000)
                assert "data:image/png;base64," in page.content()

                # 7. 志愿者 H5：通过二维码 token 访问
                share_path = page.locator(".qr-url").text_content()
                page.goto(share_path)
                page.wait_for_selector("text=志愿者项目视图", timeout=10000)
                vol_html = page.content()
                assert xss_payload not in vol_html, "志愿者页未转义项目 XSS"

                # 志愿者更新任务状态（按钮组替代 select）并留言
                done_btn = page.locator(".h5-task .status-btn[data-status='done']").first
                done_btn.click()
                page.wait_for_timeout(300)
                page.fill("#volunteerMessageForm input[name='author']", "小王")
                page.fill("#volunteerMessageForm textarea[name='content']", "任务已完成")
                page.click("#volunteerMessageForm button")
                page.wait_for_timeout(500)
                page.reload()
                assert page.locator("text=任务已完成").count() >= 1

                # 8. 搜索特殊字符
                page.goto(f"{base_url}/?q=%")
                page.wait_for_load_state("networkidle")
                assert page.locator("text=Internal Server Error").count() == 0, "搜索 % 不应报错"

                # 9. 404 兜底
                page.goto(f"{base_url}/admin/projects/99999")
                assert page.locator("text=项目不存在").count() >= 1 or page.locator("text=Not Found").count() >= 1

                page.goto(f"{base_url}/p/not-a-real-token")
                assert page.locator("text=项目不存在或链接已失效").count() >= 1 or page.locator("text=Not Found").count() >= 1
            finally:
                # L11: browser.close 移入 finally，避免浏览器泄漏
                browser.close()

        assert not errors, f"页面 JS 错误: {errors}"
        assert not dialogs, f"未处理的弹窗: {dialogs}"
        print("[bridge e2e] PASSED")
    finally:
        _stop_server(proc)


if __name__ == "__main__":
    test_bridge_e2e()
