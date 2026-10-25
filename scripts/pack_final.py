# pack_final.py — 打包 Vannevar 最终总提交包
#
# Phase 6.6（2026-07-21）改造：
#   - 接入 _pack_common 共享模块，消除与 pack_mvps.py 的逻辑重复
#   - 新增 SHA256 校验和 + manifest.json 输出（用户可验证包完整性）
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _pack_common import pack_directory, COMMON_EXCLUDE_NAMES, COMMON_EXCLUDE_SUFFIXES

ROOT = Path(__file__).parent.parent.resolve()  # Vannevar/
# 开源发布改造（2026-10-25）：输出到仓内 dist/。
# 原先输出到 ROOT.parent（项目目录之外），导致仓内那份交付包永远不会被刷新——
# 脚本产出与实际交付物长期脱节。dist/ 已在 .gitignore 中排除，不会入库。
OUT = ROOT / "dist" / "Vannevar-submission.zip"

# 最终总包额外排除（用户无需查看历史归档 / 打包脚本本身）
EXTRA_EXCLUDE_NAMES = COMMON_EXCLUDE_NAMES | {"archive", "dist"}
EXTRA_EXCLUDE_SUFFIXES = COMMON_EXCLUDE_SUFFIXES | {".db"}
EXTRA_EXCLUDE_FILES = {
    "pack_mvps.py", "pack_final.py", "verify_loading.py",
    "_pack_common.py", "qa_all.py", ".tmp_create.py",
    ".gitignore", "bandit-report.txt",
}
# 最终包保留 zip 和文档即可，排除源码中的测试目录
EXTRA_EXCLUDE_PATH_SEGMENTS = {"tests", "e2e"}


def main():
    items_extra = {
        "source_root": str(ROOT),
        "excludes": {
            "names": sorted(EXTRA_EXCLUDE_NAMES),
            "suffixes": sorted(EXTRA_EXCLUDE_SUFFIXES),
            "files": sorted(EXTRA_EXCLUDE_FILES),
            "path_segments": sorted(EXTRA_EXCLUDE_PATH_SEGMENTS),
        },
    }
    pack_directory(
        src=ROOT,
        out=OUT,
        packager="pack_final",
        exclude_names=EXTRA_EXCLUDE_NAMES,
        exclude_suffixes=EXTRA_EXCLUDE_SUFFIXES,
        exclude_files=EXTRA_EXCLUDE_FILES,
        exclude_path_segments=EXTRA_EXCLUDE_PATH_SEGMENTS,
        arcname_prefix="Vannevar/",
        extra_meta=items_extra,
    )
    print(f"[Vannevar submission] -> {OUT}")


if __name__ == "__main__":
    main()
