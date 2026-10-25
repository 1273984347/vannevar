# verify_loading.py — 解压三个 MVP zip 并做加载前完整性验证
#
# Phase 6.6 更新（2026-07-21）：
#   - Bridge required 列表补全：新增 archive-paper.css / stats.css / story.css / sw.js / manifest.json /
#     ai.py / backup.py / context_card.py / moderation.py / templates.py + 全部 11 个 router
#   - 新增 Focus/Muse 核心 JS 文件 node --check 语法验证
#   - 新增 Bridge CSS 文件基础语法检查（大括号配对）
#   - 路径全部基于脚本自身位置，消除硬编码绝对路径
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()  # Vannevar/
PRODUCTS = ROOT / "products"
# 开源发布改造（2026-10-25）：分包输出统一到仓内 dist/（与 pack_mvps.py 的 out 一致）
DIST = ROOT / "dist"
TMP = ROOT / ".verify_tmp"


def unzip(name: str) -> Path:
    src = DIST / f"{name}.zip"
    if not src.exists():
        raise SystemExit(f"ERROR: {src} 不存在，请先运行 pack_mvps.py")
    dst = TMP / name
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    with zipfile.ZipFile(src, "r") as zf:
        zf.extractall(dst)
    return dst


def _check_js_syntax(d: Path, files: list, product: str) -> list:
    """对解压目录中的 JS 文件跑 node --check 语法验证。

    node 不可用时跳过（不阻断，仅警告），因为用户环境可能未装 Node。

    DRL R2 F2 修复：返回错误列表（空列表 = 无错误），由调用方收集后统一 raise，
    避免单文件语法错即崩溃导致后续文件未被检查。
    """
    node = shutil.which("node")
    if node is None:
        print(f"  ⚠ node 不可用，跳过 {product} JS 语法检查")
        return []
    errors = []
    for rel in files:
        f = d / rel
        if not f.exists():
            print(f"  ⚠ {product} JS 文件缺失：{rel}")
            continue
        result = subprocess.run(
            [node, "--check", str(f)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            errors.append(f"{product} JS 语法错误 ({rel}):\n{result.stderr}")
    return errors


def _check_css_brackets(d: Path, files: list, product: str) -> list:
    """CSS 基础语法检查：大括号配对 + 末尾分号缺失粗检。

    不做完整 CSS 解析（需第三方库），只查最常见的"括号不匹配"错误。

    DRL R2 F2 修复：返回错误列表（空列表 = 无错误），由调用方收集后统一 raise。
    """
    errors = []
    for rel in files:
        f = d / rel
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        # 去掉注释（避免注释中的括号干扰）
        text_no_comments = __import__("re").sub(r'/\*[\s\S]*?\*/', '', text)
        open_count = text_no_comments.count('{')
        close_count = text_no_comments.count('}')
        if open_count != close_count:
            errors.append(
                f"{product} CSS 括号不匹配 ({rel}): "
                f"{{ = {open_count}, }} = {close_count}"
            )
    return errors


def check_focus():
    d = unzip("focus-mvp")
    required = [
        # 扩展核心
        "manifest.json",
        "background.js",
        "popup.html", "popup.js", "popup.css",
        "options.html", "options.js", "options.css",
        "db.js", "snapshot.js", "ai.js", "resume.js",
        "crypto.js", "sync.js", "auto-sync.js",
        "bridge-link.js", "agent.js", "local-model.js",
        "ai-router.js", "auth.js", "task-watcher.js",
        # 三方库
        "lib/sql-wasm.js", "lib/sql-wasm.wasm", "lib/sql-wrapper.js",
        "lib/vannevar-context-card.js",
        # 共享
        "shared/toast.js", "shared/vannevar-shared.js",
        # 图标
        "icons/icon16.png", "icons/icon48.png", "icons/icon128.png",
        # 文档
        "privacy.html", "README.md",
    ]
    missing = [r for r in required if not (d / r).exists()]
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert manifest.get("manifest_version") == 3, "manifest_version 必须是 3"
    assert manifest.get("background", {}).get("type") == "module", "background.type 必须是 module"

    # Phase 6.6: 核心 JS 语法检查
    # DRL R2 F2 修复：收集所有错误后统一 raise，避免单文件错即中断后续检查
    syntax_errors = []
    syntax_errors += _check_js_syntax(d, [
        "background.js", "db.js", "snapshot.js", "ai.js", "resume.js",
        "crypto.js", "sync.js", "auto-sync.js", "popup.js", "options.js",
        "bridge-link.js", "agent.js", "local-model.js", "ai-router.js",
        "auth.js", "task-watcher.js",
        "lib/sql-wrapper.js", "lib/vannevar-context-card.js",
        "shared/toast.js", "shared/vannevar-shared.js",
    ], "Focus")

    # Phase 6.6 / DRL R2 F1 修复：Focus CSS 大括号配对检查
    syntax_errors += _check_css_brackets(d, ["popup.css", "options.css"], "Focus")

    if syntax_errors:
        raise AssertionError("\n---\n".join(syntax_errors))

    print(f"[focus-mvp] OK, missing={missing}")
    return missing


def check_muse():
    d = unzip("muse-mvp")
    required = [
        # 扩展核心
        "manifest.json",
        "core/background.js", "core/muse.js", "core/db.js",
        "core/ai.js", "core/resume.js", "core/bridge-link.js",
        "core/sync.js", "core/auto-sync.js", "core/agent.js",
        "core/local-model.js", "core/ai-router.js", "core/auth.js",
        "core/media-group.js",
        # popup
        "popup/popup.html", "popup/popup.js", "popup/popup.css",
        # dashboard
        "dashboard/dashboard.html", "dashboard/dashboard.js", "dashboard/dashboard.css",
        # options
        "options/options.html", "options/options.js", "options/options.css",
        # 三方库
        "lib/sql-wasm.js", "lib/sql-wasm.wasm", "lib/sql-wrapper.js",
        # 共享
        "shared/vannevar-brand.css", "shared/vannevar-shared.js",
        "shared/vannevar-context-card.js", "shared/toast.js",
        "shared/escape.js", "shared/moderation.js",
        # 图标
        "icons/icon16.png", "icons/icon48.png", "icons/icon128.png",
        # 文档
        "privacy.html", "README.md",
    ]
    missing = [r for r in required if not (d / r).exists()]
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert manifest.get("manifest_version") == 3, "manifest_version 必须是 3"
    assert manifest.get("background", {}).get("type") == "module", "background.type 必须是 module"

    # Phase 6.6: 核心 JS 语法检查
    # DRL R2 F2 修复：收集所有错误后统一 raise
    syntax_errors = []
    syntax_errors += _check_js_syntax(d, [
        "core/background.js", "core/muse.js", "core/db.js", "core/ai.js",
        "core/resume.js", "core/bridge-link.js", "core/sync.js",
        "core/auto-sync.js", "core/agent.js", "core/local-model.js",
        "core/ai-router.js", "core/auth.js", "core/media-group.js",
        "popup/popup.js", "dashboard/dashboard.js", "options/options.js",
        "lib/sql-wrapper.js", "shared/vannevar-context-card.js",
        "shared/vannevar-shared.js", "shared/toast.js",
        "shared/escape.js", "shared/moderation.js",
    ], "Muse")

    # Phase 6.6 / DRL R2 F1 修复：Muse CSS 大括号配对检查
    syntax_errors += _check_css_brackets(d, [
        "popup/popup.css", "dashboard/dashboard.css",
        "options/options.css", "shared/vannevar-brand.css",
    ], "Muse")

    if syntax_errors:
        raise AssertionError("\n---\n".join(syntax_errors))

    print(f"[muse-mvp] OK, missing={missing}")
    return missing


def check_bridge():
    d = unzip("bridge-mvp")
    required = [
        # 应用入口 + 核心模块
        "main.py", "auth.py", "database.py", "models.py", "schemas.py",
        "ai.py", "backup.py", "context_card.py", "moderation.py", "templates.py",
        # 全部 11 个 router
        "routers/__init__.py",
        "routers/pages.py", "routers/projects.py", "routers/tasks.py",
        "routers/events.py", "routers/agent_router.py", "routers/auth_router.py",
        "routers/chat_router.py", "routers/context_card_router.py",
        "routers/orgs_router.py", "routers/sync_router.py",
        "routers/_token_rate_limit.py",
        # 模板
        "templates/base.html", "templates/index.html", "templates/stats.html",
        "templates/project_detail.html", "templates/volunteer.html",
        "templates/error.html", "templates/demo.html", "templates/qr.html",
        "templates/story.html",
        # 静态资源（Phase 6.6: 新增 archive-paper.css / story.css）
        "static/style.css", "static/stats.css", "static/archive-paper.css",
        "static/story.css", "static/sw.js", "static/manifest.json",
        "static/privacy-policy.html",
        # 依赖
        "requirements.txt",
        # demo seed
        "scripts/seed_demo_data.py",
    ]
    missing = [r for r in required if not (d / r).exists()]

    # Python 语法检查（核心模块 + 全部 router）
    py_files = [
        "main.py", "auth.py", "database.py", "models.py", "schemas.py",
        "ai.py", "backup.py", "context_card.py", "moderation.py", "templates.py",
        "routers/__init__.py", "routers/pages.py", "routers/projects.py",
        "routers/tasks.py", "routers/events.py", "routers/agent_router.py",
        "routers/auth_router.py", "routers/chat_router.py",
        "routers/context_card_router.py", "routers/orgs_router.py",
        "routers/sync_router.py", "routers/_token_rate_limit.py",
        "scripts/seed_demo_data.py",
    ]
    for f in py_files:
        compile((d / f).read_text(encoding="utf-8"), f, "exec")

    # Phase 6.6: CSS 大括号配对检查
    # DRL R2 F2 修复：收集所有错误后统一 raise
    syntax_errors = []
    syntax_errors += _check_css_brackets(d, [
        "static/style.css", "static/stats.css", "static/archive-paper.css",
        "static/story.css",
    ], "Bridge")

    # Phase 6.6: sw.js 语法检查
    syntax_errors += _check_js_syntax(d, ["static/sw.js"], "Bridge")

    if syntax_errors:
        raise AssertionError("\n---\n".join(syntax_errors))

    print(f"[bridge-mvp] OK, missing={missing}")
    return missing


if __name__ == "__main__":
    if TMP.exists():
        shutil.rmtree(TMP)
    try:
        all_missing = check_focus() + check_muse() + check_bridge()
    finally:
        # 无论成功失败都清理临时目录
        if TMP.exists():
            shutil.rmtree(TMP)
    if all_missing:
        print(f"\nFAILED: {len(all_missing)} files missing")
        for m in all_missing:
            print(f"  - {m}")
        raise SystemExit(1)
    print("\nAll MVP zips passed loading-preload checks.")
