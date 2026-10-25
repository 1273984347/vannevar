"""Dogfood screenshots: capture key pages with Playwright (with auth, dark mode, mobile)."""
import base64
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
# P0 修复（DRL R1b class-level）：移除硬编码 admin:admin123，改环境变量注入 + fail-fast
_ADMIN_PASS = os.getenv("BRIDGE_ADMIN_PASS")
if not _ADMIN_PASS:
    sys.stderr.write(
        "[FATAL] BRIDGE_ADMIN_PASS 环境变量未设置。\n"
        "        用法: BRIDGE_ADMIN_PASS=<your-pass> python dogfood_screenshots.py\n"
    )
    sys.exit(2)
AUTH = "Basic " + base64.b64encode(f"admin:{_ADMIN_PASS}".encode()).decode()
OUT_DIR = Path("dogfood_shots")
OUT_DIR.mkdir(exist_ok=True)


def new_page(browser, *, dark=False, reduced_motion=False, mobile=False):
    if mobile:
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            is_mobile=True,
            has_touch=True,
        )
    else:
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    # set auth header via extra HTTP headers + cookie injection for CSRF
    ctx.set_extra_http_headers({
        "Authorization": AUTH,
        "X-Requested-With": "BridgeAdmin",
    })
    page = ctx.new_page()
    # emulate media features on the page (BrowserContext doesn't have emulate_media in older versions)
    if dark or reduced_motion:
        features = []
        if dark:
            features.append({"name": "prefers-color-scheme", "value": "dark"})
        if reduced_motion:
            features.append({"name": "prefers-reduced-motion", "value": "reduce"})
        try:
            page.emulate_media(features=features)
        except TypeError:
            # older API: color_scheme / reduced_motion kwargs
            kwargs = {}
            if dark:
                kwargs["color_scheme"] = "dark"
            if reduced_motion:
                kwargs["reduced_motion"] = "reduce"
            page.emulate_media(**kwargs)
    # capture console + page errors
    console_logs = []
    page_errors = []
    page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    return page, ctx, console_logs, page_errors


def fetch_csrf_token(page):
    """Read bridge_csrf cookie value from the page context."""
    cookies = page.context.cookies()
    for c in cookies:
        if c.get("name") == "bridge_csrf":
            return c.get("value", "")
    return ""


def shot(page, name, *, full_page=True):
    path = OUT_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=full_page)
    print(f"  saved: {path}")
    return path


def run():
    console_summary = {}
    error_summary = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # 1. /demo desktop
        print("\n[1] /demo (desktop, light)")
        page, ctx, console, errors = new_page(browser)
        page.goto(f"{BASE}/demo", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(800)
        shot(page, "01_demo_desktop_light")
        console_summary["demo"] = console[:10]
        error_summary["demo"] = errors[:10]
        ctx.close()

        # 2. /demo/story desktop
        print("\n[2] /demo/story (desktop, light)")
        page, ctx, console, errors = new_page(browser)
        page.goto(f"{BASE}/demo/story", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1200)  # let story JS init
        shot(page, "02_story_desktop_light")
        # check kpi / chapter / seek elements (actual classes in story.html)
        kpi_count = page.locator(".story-kpi").count()
        chapter_count = page.locator(".chapter-card").count()
        seek_count = page.locator("#seekBar").count()
        play_btn = page.locator("#btnPlay").count()
        frame_total = page.locator("#frameTotal").count()
        narrative = page.locator("#narrativeText").count()
        print(f"  kpi: {kpi_count}, chapter: {chapter_count}, seek: {seek_count}, play: {play_btn}, frameTotal: {frame_total}, narrative: {narrative}")
        # try clicking play
        try:
            page.click("#btnPlay", timeout=2000)
            page.wait_for_timeout(2500)  # let it play a few frames
            shot(page, "02b_story_playing")
            frame_idx_text = page.locator("#frameIdx").inner_text(timeout=2000)
            print(f"  after 2.5s play: frameIdx = {frame_idx_text}")
            # pause
            page.click("#btnPlay", timeout=2000)
        except Exception as e:
            print(f"  play button click failed: {e}")
        console_summary["story"] = console[:10]
        error_summary["story"] = errors[:10]
        ctx.close()

        # 3. /admin/projects/1 desktop
        print("\n[3] /admin/projects/1 (desktop, light)")
        page, ctx, console, errors = new_page(browser)
        page.goto(f"{BASE}/admin/projects/1", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        shot(page, "03_project_detail_light")
        # check AI assistant section (use real selectors from project_detail.html)
        ai_section = page.locator("section:has-text('AI 助手'), [data-ai-assistant], .ai-assistant, #ai-assistant, #chat-panel").count()
        task_board = page.locator(".task-list, .tasks-grid, [data-task-list], .task-card, .tasks-section").count()
        chat_input = page.locator("textarea").count()
        send_btn = page.locator("button:has-text('发送'), button[type=submit]:has-text('问'), #askBtn, .ask-btn").count()
        print(f"  AI section: {ai_section}, task board: {task_board}, chat input: {chat_input}, send btn: {send_btn}")
        console_summary["project_detail"] = console[:10]
        error_summary["project_detail"] = errors[:10]
        ctx.close()

        # 4. /admin/stats desktop (where sankey/radar/network live)
        print("\n[4] /admin/stats (desktop, light)")
        page, ctx, console, errors = new_page(browser)
        page.goto(f"{BASE}/admin/stats", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1200)
        shot(page, "04_stats_light")
        sankey = page.locator(".sankey-chart, svg[aria-label*='桑基']").count()
        radar = page.locator(".radar-chart-wrap, svg[aria-label*='雷达']").count()
        network = page.locator(".network-chart-wrap, svg[aria-label*='网络图']").count()
        print(f"  sankey: {sankey}, radar: {radar}, network: {network}")
        console_summary["stats"] = console[:10]
        error_summary["stats"] = errors[:10]
        ctx.close()

        # 5. /admin/projects/1 dark mode
        print("\n[5] /admin/projects/1 (desktop, DARK)")
        page, ctx, console, errors = new_page(browser, dark=True)
        page.goto(f"{BASE}/admin/projects/1", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        shot(page, "05_project_detail_dark")
        # check theme toggle button presence
        theme_toggle = page.locator("[data-theme-toggle], .theme-toggle, button:has-text('深色'), button:has-text('浅色'), button:has-text('Dark'), button:has-text('Light')").count()
        print(f"  theme toggle buttons: {theme_toggle}")
        console_summary["project_detail_dark"] = console[:10]
        error_summary["project_detail_dark"] = errors[:10]
        ctx.close()

        # 6. /admin/projects/1 reduced motion
        print("\n[6] /admin/projects/1 (desktop, reduced-motion)")
        page, ctx, console, errors = new_page(browser, reduced_motion=True)
        page.goto(f"{BASE}/admin/projects/1", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        shot(page, "06_project_detail_reduced_motion")
        console_summary["project_detail_rm"] = console[:10]
        error_summary["project_detail_rm"] = errors[:10]
        ctx.close()

        # 7. /demo mobile
        print("\n[7] /demo (mobile)")
        page, ctx, console, errors = new_page(browser, mobile=True)
        page.goto(f"{BASE}/demo", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(800)
        shot(page, "07_demo_mobile")
        console_summary["demo_mobile"] = console[:10]
        error_summary["demo_mobile"] = errors[:10]
        ctx.close()

        # 8. /demo/story mobile
        print("\n[8] /demo/story (mobile)")
        page, ctx, console, errors = new_page(browser, mobile=True)
        page.goto(f"{BASE}/demo/story", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1200)
        shot(page, "08_story_mobile")
        console_summary["story_mobile"] = console[:10]
        error_summary["story_mobile"] = errors[:10]
        ctx.close()

        # 9. /admin/projects/1 mobile
        print("\n[9] /admin/projects/1 (mobile)")
        page, ctx, console, errors = new_page(browser, mobile=True)
        page.goto(f"{BASE}/admin/projects/1", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        shot(page, "09_project_detail_mobile")
        console_summary["project_detail_mobile"] = console[:10]
        error_summary["project_detail_mobile"] = errors[:10]
        ctx.close()

        # 10. /login page (or /)
        print("\n[10] / (admin index, desktop)")
        page, ctx, console, errors = new_page(browser)
        page.goto(f"{BASE}/", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1000)
        shot(page, "10_admin_index_light")
        console_summary["admin_index"] = console[:10]
        error_summary["admin_index"] = errors[:10]
        ctx.close()

        browser.close()

    # dump console + errors
    print("\n=== Console Logs Summary ===")
    for name, logs in console_summary.items():
        if logs:
            print(f"\n[{name}]")
            for line in logs:
                print(f"  {line}")
    print("\n=== Page Errors Summary ===")
    for name, errs in error_summary.items():
        if errs:
            print(f"\n[{name}]")
            for e in errs:
                print(f"  {e}")


if __name__ == "__main__":
    run()
