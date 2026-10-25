"""
生成 Vannevar 品牌视觉资产：
- 三产品独立主视觉海报（shared/media/poster-*.png）
- 10 秒品牌开场视频（shared/media/vannevar-brand-intro-extended.mp4）

依赖：Playwright、ffmpeg、Python 3.8+
用法：python scripts/capture_assets.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "shared"
MEDIA = SHARED / "media"
FRAMES_DIR = SHARED / "frames-extended"

POSTERS = [
    ("Focus", SHARED / "poster-focus.html", MEDIA / "poster-focus.png", 1200, 1600),
    ("Muse", SHARED / "poster-muse.html", MEDIA / "poster-muse.png", 1200, 1600),
    ("Bridge", SHARED / "poster-bridge.html", MEDIA / "poster-bridge.png", 1200, 1600),
]

VIDEO_HTML = SHARED / "brand-showcase-extended.html"
VIDEO_OUT = MEDIA / "vannevar-brand-intro-extended.mp4"
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
FPS = 25
DURATION = 10


def file_url(path: Path) -> str:
    return path.as_uri()


def capture_posters():
    print("[capture] 开始截取三产品海报...")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 1600})
        for name, html_path, out_path, width, height in POSTERS:
            if not html_path.exists():
                raise FileNotFoundError(f"海报源文件不存在: {html_path}")
            page.set_viewport_size({"width": width, "height": height})
            page.goto(file_url(html_path), wait_until="networkidle")
            # 等待字体加载完成
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out_path), full_page=False)
            print(f"  [OK] {name} 海报 -> {out_path}")
        browser.close()


def capture_video_frames():
    print("[capture] 开始生成品牌视频帧序列...")
    if FRAMES_DIR.exists():
        shutil.rmtree(FRAMES_DIR)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    total_frames = FPS * DURATION

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": VIDEO_WIDTH, "height": VIDEO_HEIGHT})
        page.goto(file_url(VIDEO_HTML), wait_until="networkidle")
        page.wait_for_timeout(1200)

        # 接管时间轴，确保首帧为空白
        page.evaluate("""
            window.__brandManual = true;
            window.__brandTime = 0;
            window.__updateBrandFrame();
        """)
        page.wait_for_timeout(100)

        for frame_idx in range(total_frames):
            t = frame_idx / FPS
            page.evaluate(
                """
                (t) => {
                    window.__brandTime = t;
                    window.__updateBrandFrame();
                }
                """,
                t,
            )
            # 给一帧时间让浏览器完成渲染
            page.wait_for_timeout(5)
            frame_path = FRAMES_DIR / f"frame-{frame_idx:04d}.png"
            page.screenshot(path=str(frame_path), full_page=False)
            if (frame_idx + 1) % 50 == 0:
                print(f"  [progress] {frame_idx + 1}/{total_frames} 帧")

        browser.close()
    print(f"  [OK] 共生成 {total_frames} 帧 -> {FRAMES_DIR}")


def build_video():
    print("[capture] 开始用 ffmpeg 合成 10s 视频...")
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate", str(FPS),
        "-i", str(FRAMES_DIR / "frame-%04d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
        str(VIDEO_OUT),
    ]
    subprocess.run(cmd, check=True)
    print(f"  [OK] 视频 -> {VIDEO_OUT}")


def main():
    MEDIA.mkdir(parents=True, exist_ok=True)
    try:
        capture_posters()
        capture_video_frames()
        build_video()
        print("[capture] 全部资产生成完成。")
        return 0
    except Exception as e:
        print(f"[capture] 失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
