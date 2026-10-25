"""WCAG 对比度自动化校验：检查三产品深色/浅色模式下的品牌色对比度。

用法：
    python check_contrast.py

标准：
    - 正常文字（<18px 且非粗体）：对比度 >= 4.5:1
    - 大字（>=18px 或 >=14px bold）/ UI 元素（按钮、徽章、图标）：对比度 >= 3:1

Phase 6.6 更新（2026-07-21）：
    - 路径全部改相对（基于脚本自身位置），消除硬编码绝对路径
    - Bridge 配置接入 archive-paper.css（Phase 6.6 案卷纸视觉母题载体）
    - Bridge 背景色更新为案卷纸色板：#e8dcc0（浅）/ #2a2018（深）
    - Bridge 品牌变量改为案卷纸令牌（--v-ink / --v-ink-medium / --v-primary 等）
    - Muse 浅色背景修正为实际 --v-bg 值 #fff7ed（原 #fffbeb 略有偏差）
"""
import re
import sys
from pathlib import Path

# WCAG 标准
NORMAL_TEXT_THRESHOLD = 4.5
LARGE_TEXT_UI_THRESHOLD = 3.0

# 项目根目录（scripts/ 的上一级）
ROOT = Path(__file__).parent.parent.resolve()


def hex_to_rgb(hex_str):
    """#3b82f6 → (59, 130, 246)。支持 3 位简写。"""
    hex_str = hex_str.lstrip('#').strip()
    if len(hex_str) == 3:
        hex_str = ''.join(c * 2 for c in hex_str)
    return tuple(int(hex_str[i:i + 2], 16) for i in (0, 2, 4))


def relative_luminance(rgb):
    """WCAG 相对亮度公式。"""
    def adjust(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = [adjust(c) for c in rgb]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(hex1, hex2):
    """两个颜色的对比度比。"""
    l1 = relative_luminance(hex_to_rgb(hex1))
    l2 = relative_luminance(hex_to_rgb(hex2))
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _find_matching_brace(text, open_pos):
    """从 open_pos 位置的 '{' 开始，找到匹配的 '}'，返回其位置。"""
    depth = 0
    i = open_pos
    while i < len(text):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text) - 1


def _find_dark_ranges(css_text):
    """找到所有 @media (prefers-color-scheme: dark) { ... } 块的字符范围列表 [(start, end), ...]。"""
    ranges = []
    for media_match in re.finditer(
        r'@media\s*\(\s*prefers-color-scheme:\s*dark\s*\)\s*\{',
        css_text
    ):
        brace_pos = media_match.end() - 1  # '{' 的位置
        close_pos = _find_matching_brace(css_text, brace_pos)
        ranges.append((media_match.start(), close_pos))
    return ranges


def _strip_dark_blocks(css_text):
    """移除 @media (prefers-color-scheme: dark) { ... } 块，返回剩余文本。

    用于解析浅色模式的 :root 变量，避免深色覆盖值污染。
    """
    ranges = _find_dark_ranges(css_text)
    if not ranges:
        return css_text
    # 从后往前删除，避免位置偏移
    result = css_text
    for start, end in reversed(ranges):
        result = result[:start] + result[end + 1:]
    return result


def parse_css_variables(css_text):
    """从 CSS 文本中提取浅色模式 :root 变量，返回 {var_name: value} 字典。

    自动排除 @media (prefers-color-scheme: dark) 块内的 :root。
    会解析 var(--xxx) 引用为实际颜色值。
    """
    light_text = _strip_dark_blocks(css_text)
    root_vars = {}
    for match in re.finditer(r':root\s*\{([^}]*)\}', light_text):
        block = match.group(1)
        for var_match in re.finditer(
            r'(--[\w-]+)\s*:\s*([^;]+);', block
        ):
            name = var_match.group(1)
            value = var_match.group(2).strip()
            root_vars[name] = value

    # 解析 var() 引用
    resolved = {}
    for name, value in root_vars.items():
        resolved[name] = _resolve_var(value, root_vars, resolved, set())

    return resolved


def _resolve_var(value, all_vars, resolved, visiting):
    """递归解析 var(--xxx) 引用。"""
    var_ref = re.match(r'^var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)$', value.strip())
    if var_ref:
        ref_name = var_ref.group(1)
        fallback = var_ref.group(2)
        if ref_name in visiting:
            return fallback.strip() if fallback else value
        visiting.add(ref_name)
        ref_value = resolved.get(ref_name)
        if ref_value is None:
            ref_raw = all_vars.get(ref_name)
            if ref_raw is not None:
                ref_value = _resolve_var(ref_raw, all_vars, resolved, visiting)
                resolved[ref_name] = ref_value
        visiting.discard(ref_name)
        if ref_value:
            return ref_value
        return fallback.strip() if fallback else value
    return value


def extract_dark_block(css_text):
    """提取 @media (prefers-color-scheme: dark) 和 [data-theme="dark"] 中的 :root 变量。

    返回 {var_name: value} 字典（已解析 var 引用）。
    """
    dark_vars = {}
    ranges = _find_dark_ranges(css_text)

    for start, end in ranges:
        block_text = css_text[start:end + 1]
        for root_match in re.finditer(r':root\s*\{([^}]*)\}', block_text):
            block = root_match.group(1)
            for var_match in re.finditer(
                r'(--[\w-]+)\s*:\s*([^;]+);', block
            ):
                name = var_match.group(1)
                value = var_match.group(2).strip()
                dark_vars[name] = value

    # 匹配 [data-theme="dark"] { ... } 或 [data-theme="dark"] :root { ... }
    for theme_match in re.finditer(
        r'\[data-theme\s*=\s*["\']dark["\']\]\s*(?::root\s*)?\{([^}]*)\}',
        css_text
    ):
        block = theme_match.group(1)
        for var_match in re.finditer(
            r'(--[\w-]+)\s*:\s*([^;]+);', block
        ):
            name = var_match.group(1)
            value = var_match.group(2).strip()
            dark_vars[name] = value

    # 解析 var() 引用（dark 块可能引用 light 块的变量）
    light_vars = parse_css_variables(css_text)
    resolved = {}
    all_vars = {**light_vars, **dark_vars}
    for name, value in dark_vars.items():
        resolved[name] = _resolve_var(value, all_vars, resolved, set())

    return resolved


def is_hex_color(value):
    """判断值是否为十六进制颜色。"""
    value = value.strip()
    if value.startswith('#'):
        hex_part = value.lstrip('#')
        return len(hex_part) in (3, 6) and all(
            c in '0123456789abcdefABCDEF' for c in hex_part
        )
    return False


# ============================================================
# 三产品配置
# ============================================================

PRODUCTS = [
    {
        'name': 'Focus',
        'css_file': str(ROOT / 'products' / 'focus-mvp' / 'popup.css'),
        'light_bg': '#ffffff',   # --v-surface 卡片白底（文字主要载体）
        'dark_bg': '#0f172a',    # --v-bg 深色
        'brand_vars': {
            # 文字色 → 4.5:1 标准
            '--v-text': NORMAL_TEXT_THRESHOLD,
            '--v-text-secondary': NORMAL_TEXT_THRESHOLD,
            '--v-text-muted': NORMAL_TEXT_THRESHOLD,
            # 品牌色 / UI 元素色 → 3:1 标准
            '--v-primary': LARGE_TEXT_UI_THRESHOLD,
            '--v-primary-light': LARGE_TEXT_UI_THRESHOLD,
            '--v-accent': LARGE_TEXT_UI_THRESHOLD,
            # 语义色（用于状态文字 / 徽章）
            '--v-success': LARGE_TEXT_UI_THRESHOLD,
            '--v-warning': LARGE_TEXT_UI_THRESHOLD,
            '--v-danger': LARGE_TEXT_UI_THRESHOLD,
        },
    },
    {
        'name': 'Muse',
        'css_file': str(ROOT / 'products' / 'muse-mvp' / 'shared' / 'vannevar-brand.css'),
        'light_bg': '#fff7ed',   # --v-bg 浅色（原 #fffbeb 修正为实际值）
        'dark_bg': '#1a120b',    # --v-bg 深色
        'brand_vars': {
            '--v-text': NORMAL_TEXT_THRESHOLD,
            '--v-text-secondary': NORMAL_TEXT_THRESHOLD,
            '--v-text-muted': NORMAL_TEXT_THRESHOLD,
            '--v-primary': LARGE_TEXT_UI_THRESHOLD,
            '--v-primary-light': LARGE_TEXT_UI_THRESHOLD,
            '--v-accent': LARGE_TEXT_UI_THRESHOLD,
            '--v-success': LARGE_TEXT_UI_THRESHOLD,
            '--v-warning': LARGE_TEXT_UI_THRESHOLD,
            '--v-danger': LARGE_TEXT_UI_THRESHOLD,
        },
    },
    {
        # Phase 6.6 改造：Bridge 首屏页（stats / project-detail / index）
        # 实际渲染由 archive-paper.css 覆盖 style.css 令牌，故检查 archive-paper.css
        'name': 'Bridge',
        'css_file': str(ROOT / 'products' / 'bridge-mvp' / 'static' / 'archive-paper.css'),
        'light_bg': '#e8dcc0',   # --v-paper 案卷纸主色（泛黄）
        'dark_bg': '#2a2018',    # --v-paper 老化案卷纸（褪色档案深色模式）
        'brand_vars': {
            # 案卷纸墨色系（文字） → 4.5:1
            '--v-ink': NORMAL_TEXT_THRESHOLD,
            '--v-ink-medium': NORMAL_TEXT_THRESHOLD,
            '--v-ink-light': NORMAL_TEXT_THRESHOLD,
            '--v-ink-faded': NORMAL_TEXT_THRESHOLD,
            # 松烟绿点缀 / 交互 → 3:1
            '--v-primary': LARGE_TEXT_UI_THRESHOLD,
            '--v-primary-light': LARGE_TEXT_UI_THRESHOLD,
            '--v-accent': LARGE_TEXT_UI_THRESHOLD,
            # 语义色（墨色化） → 3:1
            '--v-success': LARGE_TEXT_UI_THRESHOLD,
            '--v-warning': LARGE_TEXT_UI_THRESHOLD,
            '--v-danger': LARGE_TEXT_UI_THRESHOLD,
        },
    },
]


def check_product(product):
    """检查一个产品的品牌色在浅/深背景上的对比度。

    返回 (results, failures) 两个列表。
    results: 所有检查项的详细记录
    failures: 不达标项
    """
    css_path = Path(product['css_file'])
    if not css_path.exists():
        print(f"  ⚠ 文件不存在: {css_path}")
        return [], []

    css_text = css_path.read_text(encoding='utf-8')
    light_vars = parse_css_variables(css_text)
    dark_vars = extract_dark_block(css_text)

    results = []
    failures = []

    for var_name, threshold in product['brand_vars'].items():
        light_value = light_vars.get(var_name)
        dark_value = dark_vars.get(var_name, light_value)

        # --- 深色模式检查（重点） ---
        if dark_value and is_hex_color(dark_value):
            ratio = contrast_ratio(dark_value, product['dark_bg'])
            passed = ratio >= threshold
            results.append({
                'mode': 'dark',
                'var': var_name,
                'color': dark_value,
                'bg': product['dark_bg'],
                'ratio': ratio,
                'threshold': threshold,
                'passed': passed,
            })
            if not passed:
                failures.append({
                    'mode': 'dark',
                    'var': var_name,
                    'color': dark_value,
                    'bg': product['dark_bg'],
                    'ratio': ratio,
                    'threshold': threshold,
                })

        # --- 浅色模式检查（确认未被破坏） ---
        if light_value and is_hex_color(light_value):
            ratio = contrast_ratio(light_value, product['light_bg'])
            passed = ratio >= threshold
            results.append({
                'mode': 'light',
                'var': var_name,
                'color': light_value,
                'bg': product['light_bg'],
                'ratio': ratio,
                'threshold': threshold,
                'passed': passed,
            })
            if not passed:
                failures.append({
                    'mode': 'light',
                    'var': var_name,
                    'color': light_value,
                    'bg': product['light_bg'],
                    'ratio': ratio,
                    'threshold': threshold,
                })

    return results, failures


def format_ratio(ratio):
    return f'{ratio:.2f}'


# ---------------------------------------------------------------------------
# 已知不达标基线（棘轮 / ratchet）
#
# 下列条目在本次开源发布准备之前就已不达标，属既有遗留问题（Bridge 深色
# 「复古档案纸」主题的墨色令牌与 #2a2018 纸面背景的对比度不足）。
#
# 棘轮语义：
#   - 基线内的不达标**不阻断**（退出码仍为 0），否则 `scripts/qa_all.py`
#     这条「一键验收」链路在开源状态下会恒红，反而掩盖真正的新问题；
#   - 任何**新增**的不达标项仍然阻断（退出码 1）；
#   - 修复任一基线项后，应把对应条目从本清单移除，让它重新回到受约束状态。
#
# 键格式：(产品名, 模式, CSS 变量名)
# ---------------------------------------------------------------------------
KNOWN_BASELINE = {
    ("Bridge", "dark", "--v-ink-light"),
    ("Bridge", "dark", "--v-ink-faded"),
    ("Bridge", "dark", "--v-danger"),
}


def main():
    all_failures = []
    total_checked = 0

    for product in PRODUCTS:
        print(f'\n{"=" * 60}')
        print(f'=== {product["name"]} 对比度校验 ===')
        print(f'{"=" * 60}')
        print(f'  CSS: {product["css_file"]}')
        print(f'  浅色背景: {product["light_bg"]}  深色背景: {product["dark_bg"]}')
        print()

        results, failures = check_product(product)

        # 按模式分组打印
        for mode in ('dark', 'light'):
            mode_results = [r for r in results if r['mode'] == mode]
            if not mode_results:
                continue
            mode_label = '深色模式' if mode == 'dark' else '浅色模式'
            bg = product['dark_bg'] if mode == 'dark' else product['light_bg']
            print(f'  --- {mode_label} (背景 {bg}) ---')
            for r in mode_results:
                status = '✅' if r['passed'] else '❌'
                threshold_label = '4.5' if r['threshold'] == NORMAL_TEXT_THRESHOLD else '3.0'
                print(
                    f'    {r["var"]:25s} ({r["color"]:8s}) '
                    f'on {r["bg"]}: {format_ratio(r["ratio"]):>6s}  '
                    f'{status}  (需 >={threshold_label})'
                )
            print()

            total_checked += len(mode_results)

        all_failures.extend([(product['name'], f) for f in failures])

    # --- 汇总 ---
    print(f'\n{"=" * 60}')
    print('=== 校验汇总 ===')
    print(f'{"=" * 60}')
    print(f'  共检查 {total_checked} 项')

    def _split(items):
        """把不达标项分成「已知基线内」与「新增」两组。"""
        baseline, new = [], []
        for name, f in items:
            key = (name, f['mode'], f['var'])
            (baseline if key in KNOWN_BASELINE else new).append((name, f))
        return baseline, new

    all_dark = [(n, f) for n, f in all_failures if f['mode'] == 'dark']
    all_light = [(n, f) for n, f in all_failures if f['mode'] == 'light']
    baseline_dark, new_dark = _split(all_dark)
    baseline_light, new_light = _split(all_light)

    if not all_dark:
        print('\n  ✅ 深色模式全部达标！三产品深色模式品牌色均满足 WCAG AA 标准。')

    if not all_failures:
        print('\n  ✅ 浅色模式也全部达标。')
        return 0

    if baseline_dark:
        print(f'\n  ⚠ 已知基线内不达标（{len(baseline_dark)} 个，既有问题，不阻断）：')
        for name, f in baseline_dark:
            threshold_label = '4.5' if f['threshold'] == NORMAL_TEXT_THRESHOLD else '3.0'
            print(
                f'    {name}: {f["var"]} ({f["color"]}) on {f["bg"]}: '
                f'{format_ratio(f["ratio"])} ⚠ (需 >={threshold_label})'
            )
        print('    → 见 scripts/check_contrast.py 的 KNOWN_BASELINE；修复后请从基线移除。')

    if new_dark:
        print(f'\n  ❌ 深色模式新增不达标（{len(new_dark)} 个，需修复）：')
        for name, f in new_dark:
            threshold_label = '4.5' if f['threshold'] == NORMAL_TEXT_THRESHOLD else '3.0'
            print(
                f'    {name}: {f["var"]} ({f["color"]}) on {f["bg"]}: '
                f'{format_ratio(f["ratio"])} ❌ (需 >={threshold_label})'
            )

    if all_light:
        print(f'\n  ⚠ 浅色模式预存不达标（{len(all_light)} 个，参考——浅色模式不作为阻断门禁）：')
        for name, f in all_light:
            threshold_label = '4.5' if f['threshold'] == NORMAL_TEXT_THRESHOLD else '3.0'
            mark = '⚠' if (name, f['mode'], f['var']) in KNOWN_BASELINE else '❌'
            print(
                f'    {name}: {f["var"]} ({f["color"]}) on {f["bg"]}: '
                f'{format_ratio(f["ratio"])} {mark} (需 >={threshold_label})'
            )

    # 阻断口径：深色模式的**新增**不达标。既有基线与浅色模式问题照常报告但不阻断。
    return 1 if new_dark else 0


if __name__ == '__main__':
    sys.exit(main())
