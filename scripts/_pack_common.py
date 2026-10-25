"""pack_final.py 与 pack_mvps.py 的共享逻辑。

抽离原因：两个脚本原本各自维护 EXCLUDE_NAMES / EXCLUDE_SUFFIXES / should_include，
逻辑重复且不一致（pack_final 硬编码 {"tests", "e2e"}，pack_mvps 用 exclude_patterns）。
统一抽到本模块，确保两边过滤规则一致 + 提供 SHA256 校验和与 manifest 生成。

Phase 6.6（2026-07-21）新增。
DRL R2 修复（2026-07-24）：
  - P0: 排除 .db-shm / .db-wal / .bak（SQLite 辅助文件含未提交事务/完整快照）
  - P2: pack_directory 加 src.exists() 检查 + out.parent.mkdir + write_manifest try/except
"""
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# 两脚本共用的排除目录名（pack_final 额外排除 archive，pack_mvps 不排除）
COMMON_EXCLUDE_NAMES = {
    "__pycache__", ".git", ".vscode", "node_modules", ".pytest_cache",
    ".verify_tmp", ".bandit",
}
COMMON_EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".pem", ".crx", ".bak"}
# .db 文件由各脚本自行决定是否排除（Bridge 的 bridge.db 不打包，但其他场景可能需要）
# P0 修复（DRL R2）：SQLite 辅助文件 .db-shm / .db-wal 含未提交事务/共享内存，必须排除
# pathlib .suffix 对 "bridge.db-shm" 只返回 ".shm"，故用 endswith 单独检查
COMMON_EXCLUDE_DB_AUX_SUFFIXES = (".db-shm", ".db-wal")


def should_include(rel: Path, exclude_names=None, exclude_suffixes=None,
                   exclude_files=None, exclude_path_segments=None) -> bool:
    """统一的文件包含判定。

    Args:
        rel: 相对于打包根目录的路径
        exclude_names: 排除的目录/文件名集合
        exclude_suffixes: 排除的后缀集合
        exclude_files: 排除的具体文件名集合（basename 精确匹配）
        exclude_path_segments: 排除的路径段（如 "tests" 会匹配任何路径中的 tests 段）
    """
    parts = rel.parts
    names = exclude_names or COMMON_EXCLUDE_NAMES
    if any(p in names for p in parts):
        return False
    if exclude_path_segments:
        if any(p in exclude_path_segments for p in parts):
            return False
    if rel.name in (exclude_files or set()):
        return False
    # P0 修复（DRL R2）：SQLite 辅助文件 .db-shm / .db-wal 排除
    if rel.name.endswith(COMMON_EXCLUDE_DB_AUX_SUFFIXES):
        return False
    suffixes = exclude_suffixes or COMMON_EXCLUDE_SUFFIXES
    if rel.suffix.lower() in suffixes:
        return False
    return True


def file_sha256(path: Path) -> str:
    """计算文件 SHA256（hex）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(zip_path: Path, items: list, packager: str, extra: dict = None) -> Path:
    """在 zip 同目录写 {zip_name}.manifest.json。

    items: list of {arcname, size, sha256}
    packager: 'pack_final' 或 'pack_mvps'
    extra: 额外的元数据字段
    """
    # P2 修复（DRL R2）：zip 未生成时 stat() 会二次崩溃，加 try/except
    try:
        zip_size = zip_path.stat().st_size
    except OSError as e:
        print(f"[{packager}] WARNING: 无法读取 zip 大小: {e}", file=sys.stderr)
        zip_size = 0
    manifest = {
        "packager": packager,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "zip_file": zip_path.name,
        "zip_size_bytes": zip_size,
        "item_count": len(items),
        "total_uncompressed_bytes": sum(i["size"] for i in items),
        "items": items,
    }
    if extra:
        manifest.update(extra)
    manifest_path = zip_path.with_suffix(".zip.manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def pack_directory(src: Path, out: Path, packager: str,
                   exclude_names=None, exclude_suffixes=None,
                   exclude_files=None, exclude_path_segments=None,
                   arcname_prefix: str = "", extra_meta: dict = None) -> tuple:
    """通用打包函数。

    Args:
        src: 要打包的源目录
        out: 输出 zip 路径
        packager: 打包器名称（写入 manifest）
        arcname_prefix: arcname 前缀（如 "Vannevar/"）
        extra_meta: 写入 manifest 的额外元数据
    Returns:
        (out_path, items) items 用于写 manifest
    """
    # P2 修复（DRL R2）：src 不存在时友好报错（os.walk 对不存在路径静默返回空）
    if not src.exists():
        raise FileNotFoundError(f"[{packager}] 源目录不存在: {src}")
    # P2 修复（DRL R2）：out 父目录不存在时自动创建
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    items = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(src):
            # 就地过滤被排除的目录，避免继续遍历
            names = exclude_names or COMMON_EXCLUDE_NAMES
            dirs[:] = [d for d in dirs if d not in names]
            for f in files:
                full = Path(root) / f
                rel = full.relative_to(src)
                if should_include(rel, exclude_names, exclude_suffixes,
                                  exclude_files, exclude_path_segments):
                    arcname = arcname_prefix + rel.as_posix()
                    zf.write(full, arcname=arcname)
                    items.append({
                        "arcname": arcname,
                        "size": full.stat().st_size,
                        "sha256": file_sha256(full),
                    })
    manifest_path = write_manifest(out, items, packager, extra_meta)
    print(f"[{packager}] {out.name}: {len(items)} items, "
          f"{out.stat().st_size} bytes, manifest -> {manifest_path.name}")
    return out, items
