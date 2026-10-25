# check_shared_sync.py — 共享层副本一致性校验
#
# 背景：仓库采用「shared/ 为源，产品目录内放副本」的发布方式
# （Chrome 扩展无法跨目录 import，Bridge 的静态资源也只服务自己的页面）。
# 副本一旦漂移，三个产品的行为就会**静默分叉**——这类缺陷不报错、
# 只在运行时表现为「同样的功能在 A 产品能用、B 产品不能」，
# 因此需要一道机器门禁把漂移卡在提交前。
#
# 两级判定：
#   STRICT（硬校验）：强约束副本组，漂移即 exit 1（阻断）
#   REPORT（如实报告）：历史遗留漂移，只报差异行数与哈希，不阻断，交由专项批次处理
#
# 为什么分级：合并历史漂移会改变产品实际渲染/行为，属于需要单独评估的
# 视觉与语义决策，不能混在功能批次里顺手改掉；但放着不管又会持续积累，
# 所以先让它「可见、可量化」，再专项处理。
#
# 用法：
#   python scripts/check_shared_sync.py            # 全量校验
#   python scripts/check_shared_sync.py --report-only  # 只报告，不阻断
import argparse
import difflib
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()  # Vannevar/

# STRICT 组：源文件 + 全部副本，字节必须完全一致
STRICT_GROUPS = [
    {
        "name": "端到端加密核心",
        "source": "shared/vannevar-crypto.js",
        "copies": [
            "products/focus-mvp/lib/vannevar-crypto.js",
            "products/muse-mvp/shared/vannevar-crypto.js",
        ],
        "note": "Focus 与 Muse 共用同一份加密实现；副本漂移 = 两端加密行为分叉",
    },
]

# REPORT 组：历史遗留漂移，只报告不阻断
REPORT_GROUPS = [
    {
        "name": "设计令牌",
        "source": "shared/vannevar-brand.css",
        "copies": ["products/muse-mvp/shared/vannevar-brand.css"],
        "note": "shared/ 供海报生成使用，产品目录副本供扩展实际加载，两者是不同用途的演进版本",
    },
    {
        "name": "Context Card 格式",
        "source": "shared/vannevar-context-card.js",
        "copies": [
            "products/focus-mvp/lib/vannevar-context-card.js",
            "products/muse-mvp/shared/vannevar-context-card.js",
        ],
        "note": "三产品交换格式，漂移会导致跨产品传递失败",
    },
    {
        "name": "共享运行时",
        "source": "products/focus-mvp/shared/vannevar-shared.js",
        "copies": [
            "products/muse-mvp/shared/vannevar-shared.js",
            "products/bridge-mvp/static/shared/vannevar-shared.js",
        ],
        "note": "Bridge 侧是 Web 运行时变体，与扩展侧本就不是同一份",
    },
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _diff_lines(src: Path, copy: Path) -> int:
    """返回两文件的不同行数（用于报告漂移规模）。"""
    a = src.read_text(encoding="utf-8").splitlines()
    b = copy.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(a, b, lineterm=""))
    # 去掉 ---/+++ 头与 @@ hunk 标记，只统计实际变更行
    return len([ln for ln in diff if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))])


def _check_group(group: dict, strict: bool) -> bool:
    """检查一个副本组。返回 True 表示该组通过（一致或仅报告）。"""
    src_rel = group["source"]
    src = ROOT / src_rel
    name = group["name"]
    print(f"\n[{name}] 源：{src_rel}")
    if not src.exists():
        print(f"  ✗ 源文件不存在：{src_rel}")
        return False

    src_hash = _sha256(src)
    print(f"  源 sha256：{src_hash[:16]}…")
    group_ok = True
    for copy_rel in group["copies"]:
        copy = ROOT / copy_rel
        if not copy.exists():
            print(f"  ✗ 副本缺失：{copy_rel}")
            group_ok = False
            continue
        copy_hash = _sha256(copy)
        if copy_hash == src_hash:
            print(f"  ✓ 一致：{copy_rel}")
            continue
        # 漂移
        try:
            n = _diff_lines(src, copy)
        except Exception:
            n = -1
        scope = "STRICT（阻断）" if strict else "REPORT（不阻断）"
        print(f"  ✗ 漂移：{copy_rel}")
        print(f"      sha256 {copy_hash[:16]}… 与源不同，差异约 {n} 行")
        print(f"      判定：{scope}")
        if not strict:
            print(f"      说明：{group.get('note', '')}")
        group_ok = False
    return group_ok


def main():
    parser = argparse.ArgumentParser(description="共享层副本一致性校验")
    parser.add_argument(
        "--report-only", action="store_true",
        help="只报告不阻断（STRICT 组降级为警告）",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("共享层副本一致性校验")
    print(f"ROOT: {ROOT}")
    print("=" * 70)

    strict_failed = []
    report_drift = []

    print("\n--- STRICT：强约束副本组（漂移即阻断） ---")
    for group in STRICT_GROUPS:
        if not _check_group(group, strict=True):
            strict_failed.append(group["name"])

    print("\n--- REPORT：历史遗留漂移（如实报告，不阻断） ---")
    for group in REPORT_GROUPS:
        ok = _check_group(group, strict=False)
        if not ok:
            report_drift.append(group["name"])

    print("\n" + "=" * 70)
    print("总结")
    print("=" * 70)
    print(f"  STRICT 组：{len(STRICT_GROUPS)} 个，失败 {len(strict_failed)} 个")
    for n in strict_failed:
        print(f"    ✗ {n}")
    print(f"  REPORT 组：{len(REPORT_GROUPS)} 个，存在漂移 {len(report_drift)} 个")
    for n in report_drift:
        print(f"    ! {n}（历史遗留，需专项批次处理）")

    if strict_failed and not args.report_only:
        print("\n结果：FAIL（STRICT 副本组漂移）")
        return 1
    if strict_failed:
        print("\n结果：WARN（--report-only 模式，未阻断）")
        return 0
    print("\n结果：PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
