# pack_mvps.py — 打包三个 MVP 提交包（排除测试与临时文件）
#
# Phase 6.6（2026-07-21）改造：
#   - 接入 _pack_common 共享模块，消除与 pack_final.py 的逻辑重复
#   - 统一排除规则（之前 pack_mvps 用 exclude_patterns，pack_final 用 EXCLUDE_PATH_SEGMENTS，语义不一致）
#   - 新增 SHA256 校验和 + manifest.json 输出（用户可验证包完整性）
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _pack_common import pack_directory, COMMON_EXCLUDE_NAMES, COMMON_EXCLUDE_SUFFIXES

ROOT = Path(__file__).parent.parent.resolve()  # Vannevar/
PRODUCTS = ROOT / "products"

# MVP 分包排除：数据库文件 + 测试目录（与 pack_final 保持一致）
MVP_EXCLUDE_SUFFIXES = COMMON_EXCLUDE_SUFFIXES | {".db"}
MVP_EXCLUDE_PATH_SEGMENTS = {"tests", "e2e"}
MVP_EXCLUDE_FILES = {".gitignore", ".tmp_create.py", ".bandit.yml", ".dockerignore"}


def pack_mvp(name: str):
    src = PRODUCTS / name
    # 开源发布改造（2026-10-25）：输出到仓内 dist/，不再污染 products/ 目录。
    out = ROOT / "dist" / f"{name}.zip"
    return pack_directory(
        src=src,
        out=out,
        packager="pack_mvps",
        exclude_names=COMMON_EXCLUDE_NAMES,
        exclude_suffixes=MVP_EXCLUDE_SUFFIXES,
        exclude_files=MVP_EXCLUDE_FILES,
        exclude_path_segments=MVP_EXCLUDE_PATH_SEGMENTS,
        arcname_prefix="",  # MVP 包内路径直接从产品根开始，不加前缀
        extra_meta={"product": name},
    )


if __name__ == "__main__":
    results = []
    for name in ["focus-mvp", "muse-mvp", "bridge-mvp"]:
        _, items = pack_mvp(name)
        results.append((name, len(items)))

    print("\n--- summary ---")
    for name, count in results:
        print(f"{name}: {count} items")
