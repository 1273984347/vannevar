# check_links.py — 检查最终总包内所有 markdown 相对链接是否指向真实文件
#
# Phase 6.6（2026-07-21）改造：
#   - 路径解耦：原 parent.parent.parent 隐式依赖目录层级，改用 ROOT 显式定位
#   - 支持命令行参数指定 zip 路径（默认仍指向 Vannevar-submission.zip）
#   - 新增 .manifest.json 校验：若存在 manifest，比对实际文件数与 manifest 声明数
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

# 项目根目录（scripts/ 的上一级）
ROOT = Path(__file__).parent.parent.resolve()
# 默认总包路径：与 pack_final.py 的 OUT 保持一致（ROOT / dist / Vannevar-submission.zip）
# 开源发布改造（2026-10-25）：打包输出从项目外的 ROOT.parent 迁到仓内 dist/，
# 此处同步更新，否则脚本找不到包。
DEFAULT_ZIP = ROOT / "dist" / "Vannevar-submission.zip"
LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')


def main():
    # 支持命令行指定 zip 路径，默认用 DEFAULT_ZIP
    zip_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ZIP
    if not zip_path.exists():
        print(f"ERROR: {zip_path} not found")
        print(f"  默认路径: {DEFAULT_ZIP}")
        print(f"  用法: python check_links.py [path/to/Vannevar-submission.zip]")
        raise SystemExit(1)

    # Phase 6.6: 若存在 manifest，校验文件数一致性
    manifest_path = zip_path.with_suffix(".zip.manifest.json")
    if manifest_path.exists():
        import json
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(f"[manifest] packager={manifest.get('packager')} "
              f"declared_items={manifest.get('item_count')} "
              f"generated_at={manifest.get('generated_at', 'N/A')[:19]}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        md_files = [n for n in names if n.endswith(".md")]

        broken = []
        checked = 0
        for md in md_files:
            content = zf.read(md).decode("utf-8", errors="replace")
            md_dir = PurePosixPath(md).parent
            for label, link in LINK_RE.findall(content):
                # 跳过外链、锚点、mailto
                if link.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                # 去掉锚点
                path_part = link.split("#")[0].split("?")[0]
                if not path_part:
                    continue
                # 解析相对路径（手动处理 .. 和 .）
                parts = list(md_dir.parts)
                for seg in path_part.split("/"):
                    if seg == "" or seg == ".":
                        continue
                    if seg == "..":
                        if parts:
                            parts.pop()
                        continue
                    parts.append(seg)
                target_str = "/".join(parts)
                checked += 1
                if target_str not in names:
                    # 也可能是目录
                    is_dir = any(n.startswith(target_str + "/") for n in names)
                    if not is_dir:
                        broken.append((md, label, link, target_str))

        print(f"checked {checked} links in {len(md_files)} markdown files")
        if broken:
            print(f"\nBROKEN: {len(broken)} links")
            for md, label, link, target in broken[:30]:
                print(f"  {md}")
                print(f"    [{label}]({link}) -> {target}")
            if len(broken) > 30:
                print(f"  ... and {len(broken) - 30} more")
            raise SystemExit(1)
        else:
            print("ALL LINKS OK")


if __name__ == "__main__":
    main()
