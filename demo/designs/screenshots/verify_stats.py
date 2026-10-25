"""Phase 6.6.2 server-side rendered HTML 静态验证"""
import re
import sys
from pathlib import Path

html_path = Path(__file__).resolve().parent / "stats-rendered.html"
if not html_path.exists():
    print(f"FAIL: {html_path} not found")
    sys.exit(1)

content = html_path.read_text(encoding="utf-8")
print(f"html size: {len(content)}")

checks = [
    ("data-archive-no", r'data-archive-no', 9, "9 KPI stamps"),
    ("archive-paper.css link", r'archive-paper\.css', 1, "CSS include"),
    ("stat-kpi", r'class="stat-kpi', 9, "9 KPI cards"),
    ("stats-kpi-grid", r'class="stats-kpi-grid"', 2, "2 KPI grids"),
    ("heatmap-cell", r'class="heatmap-cell h-\d', None, "heatmap cells >0"),
    ("chart-group-title", r'class="chart-group-title"', None, "section titles >0"),
    ("group-tag", r'class="group-tag"', None, "group-tag stamps >0"),
    ("kpi-archive-1", r'data-archive-no="壹"', 1, "1st KPI stamp"),
    ("kpi-archive-9", r'data-archive-no="玖"', 1, "9th KPI stamp"),
    ("topbar", r'class="topbar"', 1, "topbar"),
]

print()
all_pass = True
for name, pattern, expected, desc in checks:
    matches = re.findall(pattern, content)
    count = len(matches)
    if expected is None:
        ok = count > 0
        expect_str = ">0"
    else:
        ok = count == expected
        expect_str = str(expected)
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"  [{status}] {name}: {count} (expect {expect_str}) - {desc}")

print()
print(f"OVERALL: {'PASS' if all_pass else 'FAIL'}")
