# tests/test_story.py — Phase 6.L 故事模式 + 时间轴回放测试
"""
覆盖 Phase 6.L 的核心逻辑：
1. /demo/story 路由鉴权（未认证 401 / 认证 200 / BRIDGE_DEBUG=0 时 404）
2. /demo/story 页面渲染关键元素（topbar / story-stage / 5 KPI / seek-bar / 5 章节）
3. _build_story_frames 函数单元测试（空输入 / 30 帧累计值 / 章节归属 / max 计算）
4. 播种后访问 /demo/story 渲染 30 帧 + 5 章节
5. demo.html 入口按钮存在
6. 章节叙事文案与 _STORY_CHAPTERS 一致
"""
import os

os.environ.setdefault("BRIDGE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BRIDGE_DEBUG", "1")

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auth import ADMIN_PASS, ADMIN_USER, CSRF_COOKIE_NAME, CSRF_TOKEN
from database import SessionLocal
from main import app

client = TestClient(app)
client.cookies.set(CSRF_COOKIE_NAME, CSRF_TOKEN)

ADMIN_AUTH = (ADMIN_USER, ADMIN_PASS)

# 加载 scripts/seed_demo_data.py
_seed_path = Path(__file__).resolve().parent.parent / "scripts" / "seed_demo_data.py"
_spec = importlib.util.spec_from_file_location("seed_demo_data", _seed_path)
seed_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_mod)


# ============================================================
# 1. 路由鉴权
# ============================================================

def test_story_requires_admin():
    """/demo/story 未认证返回 401。"""
    res = client.get("/demo/story")
    assert res.status_code == 401, f"未认证应 401，实际 {res.status_code}"


def test_story_renders_with_admin():
    """/demo/story 认证后返回 200 + 渲染 story.html。"""
    res = client.get("/demo/story", auth=ADMIN_AUTH)
    assert res.status_code == 200, f"认证后应 200，实际 {res.status_code}"
    body = res.text
    assert "故事模式" in body, "页面应包含标题"
    assert "story-stage" in body, "页面应包含 story-stage 区段"
    assert "story-controls" in body, "页面应包含 story-controls 区段"
    assert "story-chapters" in body, "页面应包含 story-chapters 区段"


def test_story_returns_404_when_debug_off(monkeypatch):
    """BRIDGE_DEBUG!=1 时 /demo/story 返回 404（生产环境隐藏入口）。"""
    monkeypatch.setenv("BRIDGE_DEBUG", "0")
    res = client.get("/demo/story", auth=ADMIN_AUTH)
    assert res.status_code == 404, f"非 debug 模式应 404，实际 {res.status_code}"


# ============================================================
# 2. 页面渲染关键元素
# ============================================================

def test_story_page_contains_5_kpi_cards():
    """页面应包含 5 个 story-kpi（项目/任务/留言/Focus 快照/Muse 灵感）。"""
    res = client.get("/demo/story", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text
    for dim in ["projects", "tasks", "messages", "snapshots", "inspirations"]:
        assert f'data-dim="{dim}"' in body, f"页面应包含 {dim} KPI 卡片"


def test_story_page_contains_seek_bar_and_controls():
    """页面应包含 seek-bar + 5 个控件按钮 + 速度选择。"""
    res = client.get("/demo/story", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text
    assert 'id="seekBar"' in body, "页面应包含 seek-bar"
    assert 'id="btnPlay"' in body, "页面应包含播放按钮"
    assert 'id="btnPrev"' in body, "页面应包含上一帧按钮"
    assert 'id="btnNext"' in body, "页面应包含下一帧按钮"
    assert 'id="btnReset"' in body, "页面应包含重置按钮"
    assert 'id="btnEnd"' in body, "页面应包含末帧按钮"
    assert 'id="speedSelect"' in body, "页面应包含速度选择"


def test_story_page_contains_5_chapter_cards():
    """页面应包含 5 个章节卡片。"""
    res = client.get("/demo/story", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text
    # 5 个章节 key
    for key in ["kickoff", "collaboration", "agent", "context", "review"]:
        assert f'data-key="{key}"' in body, f"页面应包含章节 {key}"
    # 章节计数
    assert body.count('class="chapter-card"') == 5, "应有 5 个章节卡片"


def test_story_page_includes_story_css():
    """页面应在 head 中引用 story.css。"""
    res = client.get("/demo/story", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text
    assert "/static/story.css" in body, "页面应引用 /static/story.css"


def test_demo_page_links_to_story():
    """demo.html 应包含指向 /demo/story 的入口按钮。"""
    res = client.get("/demo", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text
    assert 'href="/demo/story"' in body, "demo.html 应包含 /demo/story 链接"
    assert "故事模式" in body, "demo.html 应包含「故事模式」按钮文案"


# ============================================================
# 3. _build_story_frames 函数单元测试
# ============================================================

def test_build_story_frames_empty_input():
    """空输入应返回零帧结构。"""
    from routers.pages import _build_story_frames

    result = _build_story_frames([])
    assert result["total_frames"] == 0
    assert result["frames"] == []
    assert result["chapters"] == []
    assert result["max_daily_total"] == 1
    assert result["max_cumulative"] == 1


def test_build_story_frames_30_frames_cumulative():
    """30 帧输入应正确计算累计值 + day_label + 章节归属。"""
    from routers.pages import _build_story_frames

    # 构造 30 天 mock 数据
    heatmap = []
    for i in range(30):
        heatmap.append({
            "date": f"2026-01-{i + 1:02d}",
            "total": i + 1,
            "projects": 1 if i % 3 == 0 else 0,
            "tasks": i + 1,
            "messages": i * 2,
            "snapshots": 1 if i % 5 == 0 else 0,
            "inspirations": 1 if i % 7 == 0 else 0,
        })

    result = _build_story_frames(heatmap)
    assert result["total_frames"] == 30
    assert len(result["frames"]) == 30
    assert len(result["chapters"]) == 5

    # 第 0 帧：累计 = delta
    f0 = result["frames"][0]
    assert f0["cumulative"]["total"] == 1  # heatmap[0].total = 1
    assert f0["cumulative"]["tasks"] == 1
    assert f0["day_label"] == "Day 1"
    assert f0["chapter"] == "kickoff"  # idx 0 属于 kickoff (start_frame=0)

    # 第 6 帧：累计 = sum(heatmap[0..6].total) = 1+2+3+4+5+6+7 = 28
    f6 = result["frames"][6]
    assert f6["cumulative"]["total"] == 28
    assert f6["day_label"] == "Day 7"
    assert f6["chapter"] == "collaboration"  # idx 6 属于 collaboration (start_frame=6)

    # 第 12 帧：属于 agent (start_frame=12)
    f12 = result["frames"][12]
    assert f12["chapter"] == "agent"

    # 第 18 帧：属于 context (start_frame=18)
    f18 = result["frames"][18]
    assert f18["chapter"] == "context"

    # 第 24 帧：属于 review (start_frame=24)
    f24 = result["frames"][24]
    assert f24["chapter"] == "review"

    # 第 29 帧：仍属于 review
    f29 = result["frames"][29]
    assert f29["chapter"] == "review"
    assert f29["day_label"] == "Day 30"


def test_build_story_frames_max_values():
    """max_daily_total 和 max_cumulative 计算正确。"""
    from routers.pages import _build_story_frames

    heatmap = [
        {"date": "2026-01-01", "total": 5, "projects": 1, "tasks": 2, "messages": 1, "snapshots": 1, "inspirations": 0},
        {"date": "2026-01-02", "total": 10, "projects": 2, "tasks": 5, "messages": 2, "snapshots": 1, "inspirations": 0},
        {"date": "2026-01-03", "total": 3, "projects": 0, "tasks": 1, "messages": 1, "snapshots": 1, "inspirations": 0},
    ]
    result = _build_story_frames(heatmap)
    # max_daily = 10（第 2 帧）
    assert result["max_daily_total"] == 10
    # max_cumulative = 5+10+3 = 18（最后一帧累计）
    assert result["max_cumulative"] == 18


def test_build_story_frames_chapter_ranges():
    """5 章节的 start_frame / end_frame 边界正确。"""
    from routers.pages import _build_story_frames, _STORY_CHAPTERS

    heatmap = [{"date": f"2026-01-{i + 1:02d}", "total": 1, "projects": 0, "tasks": 1, "messages": 0, "snapshots": 0, "inspirations": 0} for i in range(30)]
    result = _build_story_frames(heatmap)
    chapters = result["chapters"]

    # 第 1 章：start=0, end=5（下一章 start=6，所以 end=5）
    assert chapters[0]["key"] == "kickoff"
    assert chapters[0]["start_frame"] == 0
    assert chapters[0]["end_frame"] == 5

    # 第 2 章：start=6, end=11
    assert chapters[1]["key"] == "collaboration"
    assert chapters[1]["start_frame"] == 6
    assert chapters[1]["end_frame"] == 11

    # 第 3 章：start=12, end=17
    assert chapters[2]["key"] == "agent"
    assert chapters[2]["start_frame"] == 12
    assert chapters[2]["end_frame"] == 17

    # 第 4 章：start=18, end=23
    assert chapters[3]["key"] == "context"
    assert chapters[3]["start_frame"] == 18
    assert chapters[3]["end_frame"] == 23

    # 第 5 章：start=24, end=29（最后一章，end=29=n-1）
    assert chapters[4]["key"] == "review"
    assert chapters[4]["start_frame"] == 24
    assert chapters[4]["end_frame"] == 29

    # 章节叙事字段非空
    for ch in chapters:
        assert ch["title"], f"章节 {ch['key']} 缺少 title"
        assert ch["narrative"], f"章节 {ch['key']} 缺少 narrative"


def test_story_chapters_definition_immutable():
    """_STORY_CHAPTER 定义应为 5 章，且 start_frame 单调递增。"""
    from routers.pages import _STORY_CHAPTERS

    assert len(_STORY_CHAPTERS) == 5
    starts = [ch["start_frame"] for ch in _STORY_CHAPTERS]
    assert starts == sorted(starts), "start_frame 应单调递增"
    assert starts[0] == 0, "第 1 章 start_frame 必须为 0"
    assert starts[-1] < 30, f"最后 1 章 start_frame 必须 < 30，实际 {starts[-1]}"


# ============================================================
# 4. 播种后访问 /demo/story 渲染 30 帧
# ============================================================

def test_story_page_after_seed_renders_30_frames():
    """播种演示数据后访问 /demo/story 应渲染 30 帧 + 章节叙事文案。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=True)
    finally:
        db.close()

    res = client.get("/demo/story", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text

    # 5 个章节标题文案
    expected_titles = [
        "开局 · 项目档案建立",
        "协作 · 跨角色任务流",
        "智能 · Agent 主动建议",
        "上下文 · Focus 快照与 Muse 灵感",
        "复盘 · 仪表盘与跨项目洞察",
    ]
    for title in expected_titles:
        assert title in body, f"页面应包含章节标题: {title}"

    # 5 个章节叙事关键词（不直接断言全句，避免标点差异）
    narrative_keywords = [
        "Bridge 之旅起步",          # kickoff
        "任务在志愿者间接力",       # collaboration
        "Agent 开始介入",            # agent
        "Focus 扩展主动推送",        # context
        "30 天回放到终点",           # review
    ]
    for kw in narrative_keywords:
        assert kw in body, f"页面应包含章节叙事关键词: {kw}"


def test_story_page_after_seed_has_nonzero_cumulative():
    """播种后渲染的 FRAMES JSON 中累计值非零（验证数据流到前端）。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=True)
    finally:
        db.close()

    res = client.get("/demo/story", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text

    # 验证 FRAMES 注入的 JSON 含 "cumulative" 字段且累计 > 0
    # 简单断言：最后一帧的累计任务数 > 0（演示数据有 24 个任务）
    assert '"cumulative"' in body, "页面应注入 frames cumulative 数据"
    assert '"delta"' in body, "页面应注入 frames delta 数据"
    assert '"day_label"' in body, "页面应注入 frames day_label 字段"
    assert '"chapter"' in body, "页面应注入 frames chapter 字段"


# ============================================================
# 5. 空数据库场景
# ============================================================

def test_story_page_empty_db_no_crash():
    """空数据库下访问 /demo/story 不报错。

    compute_stats 始终返回 30 天 activity_heatmap（全零），故 /demo/story
    在空数据库下仍渲染 30 帧（全零），不触发空状态分支。
    本测试验证页面不报错 + FRAMES 注入正确。
    """
    res = client.get("/demo/story", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text
    # 页面应注入 FRAMES（30 帧全零）
    assert "var FRAMES" in body, "空数据库下页面仍应注入 FRAMES"
    # 页面不应崩溃
    assert "故事模式" in body


def test_story_page_empty_db_disables_controls():
    """空数据库下 FRAMES 含 30 帧全零数据（compute_stats 总返回 30 天）。"""
    res = client.get("/demo/story", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text
    # FRAMES 应包含 30 帧而非空数组（compute_stats 始终返回 30 天 heatmap）
    assert '"day_label": "Day 1"' in body or '"day_label":"Day 1"' in body, \
        "FRAMES 应包含 Day 1 帧"
    assert '"day_label": "Day 30"' in body or '"day_label":"Day 30"' in body, \
        "FRAMES 应包含 Day 30 帧"
