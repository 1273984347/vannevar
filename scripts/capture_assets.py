"""
生成 Vannevar 品牌视觉资产：
- 三产品独立主视觉海报（shared/media/poster-*.png）
- 品牌海报（shared/media/vannevar-brand-poster.png）
- 5 秒品牌开场视频（shared/media/vannevar-brand-intro.mp4）
- 10 秒品牌开场视频（shared/media/vannevar-brand-intro-extended.mp4）

依赖：Playwright、ffmpeg、Python 3.8+
用法：
    python scripts/capture_assets.py            # 全部
    python scripts/capture_assets.py --posters  # 只重出海报
    python scripts/capture_assets.py --videos   # 只重出视频

注：ffmpeg 需在 PATH 中；若用 winget 安装后当前终端仍找不到，
新开一个终端，或把 ffmpeg 的 bin 目录加进 PATH。
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
FRAMES_DIR_SHORT = SHARED / "frames-short"

POSTERS = [
    ("Focus", SHARED / "poster-focus.html", MEDIA / "poster-focus.png", 1200, 1600),
    ("Muse", SHARED / "poster-muse.html", MEDIA / "poster-muse.png", 1200, 1600),
    ("Bridge", SHARED / "poster-bridge.html", MEDIA / "poster-bridge.png", 1200, 1600),
]

# 品牌总海报（由 brand-showcase.html 渲染）
BRAND_POSTER = ("Brand", SHARED / "brand-showcase.html", MEDIA / "vannevar-brand-poster.png", 1200, 800)

# 两个开场视频：短版来自 brand-showcase.html，长版来自 brand-showcase-extended.html
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
FPS = 25
VIDEOS = [
    {
        "name": "5s",
        "html": SHARED / "brand-showcase.html",
        "frames": FRAMES_DIR_SHORT,
        "out": MEDIA / "vannevar-brand-intro.mp4",
        "duration": 5,
    },
    {
        "name": "10s",
        "html": SHARED / "brand-showcase-extended.html",
        "frames": FRAMES_DIR,
        "out": MEDIA / "vannevar-brand-intro-extended.mp4",
        "duration": 10,
    },
]


def file_url(path: Path) -> str:
    return path.as_uri()


def capture_posters():
    print("[capture] 开始截取海报...")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 1600})
        for name, html_path, out_path, width, height in POSTERS + [BRAND_POSTER]:
            if not html_path.exists():
                raise FileNotFoundError(f"海报源文件不存在: {html_path}")
            page.set_viewport_size({"width": width, "height": height})
            page.goto(file_url(html_path), wait_until="networkidle")
            # 等待字体加载完成
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out_path), full_page=False)
            print(f"  [OK] {name} 海报 -> {out_path}")
        browser.close()


def capture_video_frames(spec):
    frames_dir = spec["frames"]
    print(f"[capture] 开始生成 {spec['name']} 品牌视频帧序列...")
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    total_frames = FPS * spec["duration"]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": VIDEO_WIDTH, "height": VIDEO_HEIGHT})
        page.goto(file_url(spec["html"]), wait_until="networkidle")
        page.wait_for_timeout(1200)

        # 接管时间轴，确保首帧为空白
        page.evaluate(
            """
            window.__brandManual = true;
            window.__brandTime = 0;
            window.__updateBrandFrame();
        """
        )
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
            frame_path = frames_dir / f"frame-{frame_idx:04d}.png"
            page.screenshot(path=str(frame_path), full_page=False)
            if (frame_idx + 1) % 50 == 0:
                print(f"  [progress] {spec['name']} {frame_idx + 1}/{total_frames} 帧")

        browser.close()
    print(f"  [OK] 共生成 {total_frames} 帧 -> {frames_dir}")


def build_video(spec):
    print(f"[capture] 开始用 ffmpeg 合成 {spec['name']} 视频...")
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate", str(FPS),
        "-i", str(spec["frames"] / "frame-%04d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
        str(spec["out"]),
    ]
    subprocess.run(cmd, check=True)
    print(f"  [OK] 视频 -> {spec['out']}")


def main():
    args = set(sys.argv[1:])
    only_posters = "--posters" in args
    only_videos = "--videos" in args
    if only_posters and only_videos:
        print("[capture] --posters 与 --videos 不能同时使用", file=sys.stderr)
        return 2

    MEDIA.mkdir(parents=True, exist_ok=True)
    try:
        if not only_videos:
            capture_posters()
        if not only_posters:
            for spec in VIDEOS:
                capture_video_frames(spec)
                build_video(spec)
        print("[capture] 全部资产生成完成。")
        return 0
    except Exception as e:
        print(f"[capture] 失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
