# qa_all.py — 一键全量 QA：串联打包 → 完整性 → 对比度 → 链接 → (压力测试)
#
# Phase 6.6（2026-07-24）新增：
#   - 把分散的 QA 工具（pack_mvps / pack_final / verify_loading / check_contrast /
#     check_links / stress_test_storage）串联成一条命令，输出统一 PASS/FAIL 报告
#   - 发布级 N=0：任一步骤失败立即非 0 退出，便于 CI / 用户一键验收
#   - 支持 --skip-pack（复用已打好的包）/ --full（含压力测试）/ --only STEP
#
# 用法：
#   python scripts/qa_all.py              # 全量（不含压力测试）
#   python scripts/qa_all.py --full       # 全量（含压力测试）
#   python scripts/qa_all.py --skip-pack  # 跳过打包，直接验证已有 zip
#   python scripts/qa_all.py --only contrast  # 只跑对比度
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()  # Vannevar/
SCRIPTS = Path(__file__).parent.resolve()

# 步骤定义：(key, 描述, 命令列表, 是否默认启用)
STEPS = [
    ("shared_sync", "共享层副本一致性", [sys.executable, str(SCRIPTS / "check_shared_sync.py")], True),
    ("pack_mvps", "打包三个 MVP 分包", [sys.executable, str(SCRIPTS / "pack_mvps.py")], True),
    ("pack_final", "打包总提交包", [sys.executable, str(SCRIPTS / "pack_final.py")], True),
    ("verify_loading", "MVP zip 完整性 + JS/CSS 语法", [sys.executable, str(SCRIPTS / "verify_loading.py")], True),
    ("contrast", "WCAG 对比度校验", [sys.executable, str(SCRIPTS / "check_contrast.py")], True),
    ("links", "Markdown 链接完整性", [sys.executable, str(SCRIPTS / "check_links.py")], True),
    ("stress", "存储压力测试（100/500/1000 条）", [sys.executable, str(SCRIPTS / "stress_test_storage.py")], False),
]


def run_step(key: str, desc: str, cmd: list, cwd: Path) -> tuple:
    """跑一个步骤，返回 (passed: bool, elapsed: float, output: str)。"""
    print(f"\n{'=' * 70}")
    print(f"[{key}] {desc}")
    print(f"{'=' * 70}")
    start = time.perf_counter()
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=300,
        )
        elapsed = time.perf_counter() - start
        # 把 stdout/stderr 透传给用户（保留各工具原有输出格式）
        if result.stdout:
            print(result.stdout, end='')
        if result.stderr:
            print(result.stderr, end='', file=sys.stderr)
        passed = result.returncode == 0
        status = 'PASS' if passed else f'FAIL (exit={result.returncode})'
        print(f"\n[{key}] {status}  ({elapsed:.1f}s)")
        return passed, elapsed, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - start
        print(f"\n[{key}] TIMEOUT (>300s)  ({elapsed:.1f}s)")
        return False, elapsed, ''
    except Exception as e:
        elapsed = time.perf_counter() - start
        print(f"\n[{key}] ERROR: {e}  ({elapsed:.1f}s)")
        return False, elapsed, str(e)


def main():
    parser = argparse.ArgumentParser(description='Vannevar 一键全量 QA')
    parser.add_argument('--full', action='store_true',
                        help='启用压力测试（默认跳过，耗时较长）')
    parser.add_argument('--skip-pack', action='store_true',
                        help='跳过 pack_mvps / pack_final（复用已有 zip）')
    parser.add_argument('--only', choices=[k for k, _, _, _ in STEPS],
                        help='只跑指定步骤')
    args = parser.parse_args()

    print('=' * 70)
    print('Vannevar 全量 QA 报告')
    print(f'ROOT: {ROOT}')
    print(f'模式: {"--full" if args.full else "默认"} '
          f'{"--skip-pack" if args.skip_pack else ""} '
          f'{f"--only {args.only}" if args.only else ""}'.strip())
    print('=' * 70)

    results = []
    for key, desc, cmd, default_on in STEPS:
        # 跳过逻辑
        if args.only and key != args.only:
            continue
        if not args.only:
            if args.skip_pack and key.startswith('pack_'):
                print(f"\n[{key}] SKIP (--skip-pack)")
                results.append((key, 'SKIP', 0.0))
                continue
            if key == 'stress' and not args.full:
                print(f"\n[{key}] SKIP (非 --full 模式)")
                results.append((key, 'SKIP', 0.0))
                continue
            if not default_on and not args.full:
                continue
        passed, elapsed, _ = run_step(key, desc, cmd, ROOT)
        results.append((key, 'PASS' if passed else 'FAIL', elapsed))
        # 任一失败立即停止（fail-fast），避免后续步骤依赖坏包
        if not passed and not args.only:
            print(f"\n[qa_all] {key} 失败，终止后续步骤（fail-fast）")
            break

    # ---------------- 总结 ----------------
    print('\n' + '=' * 70)
    print('QA 总结')
    print('=' * 70)
    total = len([r for r in results if r[1] != 'SKIP'])
    passed = len([r for r in results if r[1] == 'PASS'])
    failed = len([r for r in results if r[1] == 'FAIL'])
    skipped = len([r for r in results if r[1] == 'SKIP'])
    for key, status, elapsed in results:
        print(f'  {status:>4}  {key:<18} ({elapsed:>5.1f}s)')
    print(f'\n  {passed}/{total} passed, {failed} failed, {skipped} skipped')

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
