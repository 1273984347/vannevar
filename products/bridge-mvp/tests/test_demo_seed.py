# tests/test_demo_seed.py — Phase 5.4 seed_demo_data.py + /demo 路由回归测试
"""
覆盖 Phase 5.1-5.3 的核心逻辑：
1. seed_demo_data() 播种 8 类数据 + 计数符合 schema 设计
2. 幂等性：重复调用返回 skipped
3. reset_demo_data() 清空所有表 + 重置 meta 标记
4. force=True 强制重新播种
5. AgentSuggestion handoff_gap 不同日期避开 UNIQUE 约束
6. /demo GET 页面渲染（BRIDGE_DEBUG=1）
7. /demo POST /demo/load + /demo/reset 端点
8. /demo 路由仅在 BRIDGE_DEBUG=1 时可用（生产返回 404）
9. 演示数据覆盖 30 天热力图 5 级色阶（h-0/h-1/h-2/h-3/h-4）
"""
import os

# R1a-C #5 修复：用 setdefault 而非 []=，遵循 conftest.py V1/V2 约定
# （conftest.py L13 已用 setdefault，测试文件不应强制覆盖，避免污染其他测试）
os.environ.setdefault("BRIDGE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BRIDGE_DEBUG", "1")

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from auth import ADMIN_PASS, ADMIN_USER, CSRF_COOKIE_NAME, CSRF_TOKEN
from conftest import auth_headers as _auth_headers
from database import Base, SessionLocal, engine
from main import app
from models import (
    AgentSuggestion,
    Handoff,
    Message,
    Project,
    ProjectContextInspiration,
    ProjectContextSnapshot,
    SyncItem,
    Task,
    VannevarMeta,
)

client = TestClient(app)
client.cookies.set(CSRF_COOKIE_NAME, CSRF_TOKEN)

ADMIN_AUTH = (ADMIN_USER, ADMIN_PASS)
CSRF_HEADERS = {"X-Requested-With": "BridgeAdmin", "X-CSRF-Token": CSRF_TOKEN}

# 加载 scripts/seed_demo_data.py（非 Python 包，用 importlib 加载）
_seed_path = Path(__file__).resolve().parent.parent / "scripts" / "seed_demo_data.py"
_spec = importlib.util.spec_from_file_location("seed_demo_data", _seed_path)
seed_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_mod)


# ============================================================
# 工具：验证数据库计数
# ============================================================

def _counts(db: Session) -> dict:
    return {
        "projects": db.query(Project).count(),
        "tasks": db.query(Task).count(),
        "messages": db.query(Message).count(),
        "handoffs": db.query(Handoff).count(),
        "snapshots": db.query(ProjectContextSnapshot).count(),
        "inspirations": db.query(ProjectContextInspiration).count(),
        "suggestions": db.query(AgentSuggestion).count(),
        "sync_items": db.query(SyncItem).count(),
    }


# ============================================================
# 1. seed_demo_data 直接调用测试
# ============================================================

def test_seed_demo_data_basic_counts():
    """播种后 8 类数据计数符合 schema 设计。"""
    db = SessionLocal()
    try:
        result = seed_mod.seed_demo_data(db, force=False)
        assert result.get("seeded") is True, f"播种失败: {result}"
        counts = result["counts"]
        assert counts["projects"] == 3, f"projects 应为 3，实际 {counts['projects']}"
        assert counts["tasks"] == 24, f"tasks 应为 24，实际 {counts['tasks']}"
        assert counts["messages"] == 15, f"messages 应为 15，实际 {counts['messages']}"
        assert counts["handoffs"] == 3, f"handoffs 应为 3，实际 {counts['handoffs']}"
        assert counts["snapshots"] == 10, f"snapshots 应为 10，实际 {counts['snapshots']}"
        assert counts["inspirations"] == 10, f"inspirations 应为 10，实际 {counts['inspirations']}"
        assert counts["suggestions"] == 8, f"suggestions 应为 8，实际 {counts['suggestions']}"
        assert counts["sync_items"] == 8, f"sync_items 应为 8，实际 {counts['sync_items']}"
    finally:
        db.close()


def test_seed_demo_data_idempotent():
    """已播种后再次调用应返回 skipped。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        result2 = seed_mod.seed_demo_data(db, force=False)
        assert result2.get("skipped") is True, f"应跳过，实际: {result2}"
        assert "already seeded" in result2.get("reason", ""), f"reason 应包含 already seeded: {result2}"
    finally:
        db.close()


def test_seed_demo_data_force_resets_and_reseeds():
    """force=True 时清空后重新播种。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        before = _counts(db)
        result = seed_mod.seed_demo_data(db, force=True)
        assert result.get("seeded") is True, f"force 后应重新播种: {result}"
        after = _counts(db)
        assert before == after, f"force 后数据应一致: before={before} after={after}"
    finally:
        db.close()


def test_reset_demo_data_clears_all_tables():
    """reset_demo_data 清空所有 8 类表 + meta 标记。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        result = seed_mod.reset_demo_data(db)
        assert result.get("reset") is True
        counts = _counts(db)
        for k, v in counts.items():
            assert v == 0, f"{k} 应清空，实际 {v}"
        # meta 标记也应清除
        meta = db.query(VannevarMeta).filter_by(key="demo_seeded").first()
        assert meta is None, "demo_seeded 标记应被清除"
        meta_seeded = db.query(VannevarMeta).filter_by(key="seeded").first()
        assert meta_seeded is None, "seeded 标记应被清除"
    finally:
        db.close()


# ============================================================
# 2. AgentSuggestion UNIQUE 约束避让测试
# ============================================================

def test_handoff_gap_suggestions_use_different_dates():
    """P3 的 2 个 handoff_gap 建议必须用不同日期避开 UNIQUE(project_id, type, date(created_at))。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        # P3 是第 3 个项目
        projects = db.query(Project).order_by(Project.id).all()
        assert len(projects) >= 3
        p3 = projects[2]
        gap_suggestions = (
            db.query(AgentSuggestion)
            .filter(AgentSuggestion.project_id == p3.id, AgentSuggestion.type == "handoff_gap")
            .order_by(AgentSuggestion.created_at)
            .all()
        )
        assert len(gap_suggestions) == 2, f"应有 2 个 handoff_gap 建议，实际 {len(gap_suggestions)}"
        # 校验日期不同（date(created_at) 不同）
        d1 = gap_suggestions[0].created_at.date()
        d2 = gap_suggestions[1].created_at.date()
        assert d1 != d2, f"两个 handoff_gap 建议日期必须不同: {d1} vs {d2}"
    finally:
        db.close()


def test_suggestions_cover_4_types_and_4_statuses():
    """8 个 AgentSuggestion 覆盖 4 种 type × 多种 status。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        suggestions = db.query(AgentSuggestion).all()
        types = {s.type for s in suggestions}
        statuses = {s.status for s in suggestions}
        assert types == {"stale_warning", "overdue_task", "handoff_gap", "daily_summary"}, \
            f"type 应覆盖 4 种，实际: {types}"
        # status 应至少覆盖 4 种（pending/shown/acted/dismissed）
        assert statuses == {"pending", "shown", "acted", "dismissed"}, \
            f"status 应覆盖 4 种，实际: {statuses}"
    finally:
        db.close()


# ============================================================
# 3. moderation_flagged + is_deleted 留言测试
# ============================================================

def test_messages_include_moderation_flagged_and_deleted():
    """12 条留言中应包含 1 条 moderation_flagged + 1 条 is_deleted。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        flagged = db.query(Message).filter(Message.moderation_flagged == True).all()  # noqa: E712
        deleted = db.query(Message).filter(Message.is_deleted == True).all()  # noqa: E712
        assert len(flagged) == 1, f"应有 1 条 moderation_flagged，实际 {len(flagged)}"
        assert len(deleted) == 1, f"应有 1 条 is_deleted，实际 {len(deleted)}"
    finally:
        db.close()


# ============================================================
# 4. /demo 路由测试
# ============================================================

def test_demo_get_page_requires_admin():
    """/demo GET 未认证返回 401。"""
    res = client.get("/demo")
    assert res.status_code == 401, f"未认证应 401，实际 {res.status_code}"


def test_demo_get_page_with_admin():
    """/demo GET 认证后返回 200 + 渲染 demo.html。"""
    res = client.get("/demo", auth=ADMIN_AUTH)
    assert res.status_code == 200, f"认证后应 200，实际 {res.status_code}"
    body = res.text
    assert "演示数据管理" in body, "页面应包含标题"
    assert "当前数据概览" in body, "页面应包含数据概览卡片"
    assert "demo-status" in body, "页面应包含状态徽章"


def test_demo_get_page_shows_zero_counts_when_empty():
    """空数据库时 /demo 显示 0 计数。"""
    res = client.get("/demo", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text
    # 空数据库时 is_seeded=False，状态徽章显示「演示数据未加载」
    assert "演示数据未加载" in body, "空数据库应显示未加载状态"
    # 8 个 count-value 中应有 is-zero 类
    assert body.count("is-zero") >= 8, "空数据库 8 个 count 都应为 0"


def test_demo_load_endpoint_seeds_data():
    """POST /demo/load 播种演示数据。"""
    res = client.post("/demo/load", auth=ADMIN_AUTH, headers=CSRF_HEADERS)
    assert res.status_code == 200, f"应 200，实际 {res.status_code}: {res.text}"
    data = res.json()
    assert data.get("seeded") is True, f"应返回 seeded=True: {data}"
    counts = data["counts"]
    assert counts["projects"] == 3
    assert counts["tasks"] == 24


def test_demo_load_endpoint_idempotent():
    """POST /demo/load 已播种后返回 skipped。"""
    # 第一次播种
    res1 = client.post("/demo/load", auth=ADMIN_AUTH, headers=CSRF_HEADERS)
    assert res1.status_code == 200
    assert res1.json().get("seeded") is True
    # 第二次应跳过
    res2 = client.post("/demo/load", auth=ADMIN_AUTH, headers=CSRF_HEADERS)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2.get("skipped") is True, f"第二次应 skipped: {data2}"


def test_demo_reset_endpoint_clears_data():
    """POST /demo/reset 清空数据。"""
    # 先播种
    client.post("/demo/load", auth=ADMIN_AUTH, headers=CSRF_HEADERS)
    # 再清空（R1b P2-4：需传 confirm=True）
    res = client.post("/demo/reset", auth=ADMIN_AUTH, headers=CSRF_HEADERS,
                      json={"confirm": True})
    assert res.status_code == 200, f"reset 应 200，实际 {res.status_code}: {res.text}"
    data = res.json()
    assert data.get("reset") is True, f"应返回 reset=True: {data}"
    # 验证数据库已清空
    db = SessionLocal()
    try:
        assert db.query(Project).count() == 0, "reset 后 projects 应为 0"
    finally:
        db.close()


def test_demo_load_requires_csrf_header():
    """POST /demo/load 缺少 CSRF 头应被拒绝。"""
    # 只带 Basic Auth，不带 X-Requested-With
    res = client.post("/demo/load", auth=ADMIN_AUTH)
    # require_admin 校验 CSRF 头，缺失返回 403
    assert res.status_code == 403, f"缺 CSRF 头应 403，实际 {res.status_code}"


def test_demo_get_page_after_seed_shows_ready_status():
    """播种后 /demo 页面显示「演示数据已就绪」。"""
    client.post("/demo/load", auth=ADMIN_AUTH, headers=CSRF_HEADERS)
    res = client.get("/demo", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text
    assert "演示数据已就绪" in body, "播种后应显示已就绪"
    assert "已加载演示数据" in body, "按钮文案应变为已加载"


# ============================================================
# 5. 热力图 5 级色阶覆盖测试
# ============================================================

def test_heatmap_covers_5_levels():
    """播种后 30 天热力图应覆盖 h-0/h-1/h-2/h-3/h-4 全部 5 级色阶。

    通过模拟 compute_stats 的 activity_heatmap 聚合逻辑验证。
    R1b P1-2 修复：用 date() 直接比较，避免 timedelta.days 跨小时 flake
    （原实现 (now_naive - p.created_at).days 在当前 UTC 小时 < created_at.hour 时返回 -1，
    导致 D0 活动被丢弃；与 compute_stats 的 _to_utc_day 对齐，直接取 UTC date）。
    R1a-B Finding #2 修复：色阶阈值与 stats.html 生产实现一致（≤3/≤6/≤9/10+）。
    """
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        # 收集近 30 天每天的活动总数（5 类活动：projects/tasks/messages/snapshots/inspirations）
        today_utc = datetime.now(timezone.utc).date()
        from collections import defaultdict
        daily_counts = defaultdict(int)

        def _days_ago_from_dt(dt_naive: datetime) -> int:
            """naive UTC datetime → 距今天数（用 date 比较，避免 timedelta.days 跨小时 flake）。"""
            return (today_utc - dt_naive.date()).days

        def _days_ago_from_ms(ms: int) -> int:
            """ms 时间戳 → 距今天数。"""
            dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            return (today_utc - dt.date()).days

        # projects: created_at (naive UTC datetime)
        for p in db.query(Project).all():
            days_ago = _days_ago_from_dt(p.created_at)
            if 0 <= days_ago < 30:
                daily_counts[days_ago] += 1

        # tasks: created_at (naive UTC datetime)
        for t in db.query(Task).all():
            days_ago = _days_ago_from_dt(t.created_at)
            if 0 <= days_ago < 30:
                daily_counts[days_ago] += 1

        # messages: created_at (naive UTC datetime)
        for m in db.query(Message).all():
            days_ago = _days_ago_from_dt(m.created_at)
            if 0 <= days_ago < 30:
                daily_counts[days_ago] += 1

        # snapshots: created_at (ms 时间戳)
        for s in db.query(ProjectContextSnapshot).all():
            days_ago = _days_ago_from_ms(s.created_at)
            if 0 <= days_ago < 30:
                daily_counts[days_ago] += 1

        # inspirations: created_at (ms 时间戳)
        for i in db.query(ProjectContextInspiration).all():
            days_ago = _days_ago_from_ms(i.created_at)
            if 0 <= days_ago < 30:
                daily_counts[days_ago] += 1

        # 计算每天的 level（阈值与 stats.html 生产实现一致：≤3 h-1 / ≤6 h-2 / ≤9 h-3 / 10+ h-4）
        levels = set()
        for day, count in daily_counts.items():
            if count == 0:
                levels.add("h-0")
            elif count <= 3:
                levels.add("h-1")
            elif count <= 6:
                levels.add("h-2")
            elif count <= 9:
                levels.add("h-3")
            else:
                levels.add("h-4")

        # 加上 0 活动的天数（如果有些天没活动）
        all_days = set(range(30))
        active_days = set(daily_counts.keys())
        inactive_days = all_days - active_days
        if inactive_days:
            levels.add("h-0")

        # 应覆盖全部 5 级（h-0/h-1/h-2/h-3/h-4）
        # 注：根据 schema 设计，h-3 由 D0（7 个活动）覆盖，h-4 由 D12（11 个活动）覆盖
        assert "h-0" in levels, f"应覆盖 h-0: {levels}"
        assert "h-1" in levels, f"应覆盖 h-1: {levels}"
        assert "h-2" in levels, f"应覆盖 h-2: {levels}"
        assert "h-3" in levels, f"应覆盖 h-3: {levels}（D0 应有 7 个活动）"
        assert "h-4" in levels, f"应覆盖 h-4: {levels}（D12 应有 11 个活动）"
    finally:
        db.close()


# ============================================================
# 6. SyncItem 分布测试
# ============================================================

def test_sync_items_distribution():
    """8 个 SyncItem 应为 5 focus + 3 muse。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        focus_count = db.query(SyncItem).filter(SyncItem.app == "focus").count()
        muse_count = db.query(SyncItem).filter(SyncItem.app == "muse").count()
        assert focus_count == 5, f"focus 应为 5，实际 {focus_count}"
        assert muse_count == 3, f"muse 应为 3，实际 {muse_count}"
        # item_type 分布：5 snapshot + 3 inspiration
        snapshot_count = db.query(SyncItem).filter(SyncItem.item_type == "snapshot").count()
        inspiration_count = db.query(SyncItem).filter(SyncItem.item_type == "inspiration").count()
        assert snapshot_count == 5, f"snapshot 应为 5，实际 {snapshot_count}"
        assert inspiration_count == 3, f"inspiration 应为 3，实际 {inspiration_count}"
    finally:
        db.close()


# ============================================================
# 7. 项目状态分布测试
# ============================================================

def test_project_status_distribution():
    """3 个项目应为 2 active + 1 done。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        active = db.query(Project).filter(Project.status == "active").count()
        done = db.query(Project).filter(Project.status == "done").count()
        archived = db.query(Project).filter(Project.status == "archived").count()
        assert active == 2, f"active 应为 2，实际 {active}"
        assert done == 1, f"done 应为 1，实际 {done}"
        assert archived == 0, f"archived 应为 0，实际 {archived}"
    finally:
        db.close()


def test_task_status_distribution_per_project():
    """24 个任务按项目分布：P1=2todo+3in_progress+3done / P2=3todo+2in_progress+3done / P3=8done。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        projects = db.query(Project).order_by(Project.id).all()
        for p in projects:
            tasks = db.query(Task).filter(Task.project_id == p.id).all()
            todo = sum(1 for t in tasks if t.status == "todo")
            in_progress = sum(1 for t in tasks if t.status == "in_progress")
            done = sum(1 for t in tasks if t.status == "done")
            assert len(tasks) == 8, f"P{p.id} 应有 8 个任务，实际 {len(tasks)}"
            if p.status == "done":
                # P3: 0 todo + 0 in_progress + 8 done
                assert todo == 0, f"P{p.id}（done）todo 应为 0，实际 {todo}"
                assert in_progress == 0, f"P{p.id}（done）in_progress 应为 0，实际 {in_progress}"
                assert done == 8, f"P{p.id}（done）done 应为 8，实际 {done}"
            else:
                # active 项目总任务 8，todo+in_progress+done=8
                assert todo + in_progress + done == 8, f"P{p.id} 任务总数应为 8"
    finally:
        db.close()


# ============================================================
# 8. R1a-B #1 修复：BRIDGE_DEBUG=0 时 /demo 返回 404（生产隐藏入口）
# ============================================================

def test_demo_returns_404_when_debug_disabled(monkeypatch):
    """R1a-B #1: BRIDGE_DEBUG=0 时 /demo 应返回 404 隐藏入口。

    _require_debug_or_404() 在请求时读取 os.getenv("BRIDGE_DEBUG")，
    monkeypatch.setenv 可注入临时覆盖，测试后自动恢复。
    """
    monkeypatch.setenv("BRIDGE_DEBUG", "0")
    # 认证后仍应返回 404（_require_debug_or_404 在 require_admin 之后执行）
    res = client.get("/demo", auth=ADMIN_AUTH)
    assert res.status_code == 404, f"BRIDGE_DEBUG=0 时应 404，实际 {res.status_code}"

    # POST /demo/load 也应 404
    res2 = client.post("/demo/load", auth=ADMIN_AUTH, headers=CSRF_HEADERS)
    assert res2.status_code == 404, f"BRIDGE_DEBUG=0 时 POST /demo/load 应 404，实际 {res2.status_code}"

    # POST /demo/reset 也应 404
    res3 = client.post("/demo/reset", auth=ADMIN_AUTH, headers=CSRF_HEADERS,
                       json={"confirm": True})
    assert res3.status_code == 404, f"BRIDGE_DEBUG=0 时 POST /demo/reset 应 404，实际 {res3.status_code}"


# ============================================================
# 9. R1b P2-3 修复：seed_demo_data(force=False) 不自动清空已有数据
# ============================================================

def test_seed_demo_data_does_not_delete_existing_data():
    """R1b P2-3: force=False 且数据库已有非 demo 数据时，返回 needs_reset 而非自动清空。

    危害场景：用户在 /admin/ 手动建项目后点 /demo 加载按钮，
    原 bug 会触发 reset_demo_data 清空所有真实数据。
    修复后应返回 needs_reset=True，让用户显式决定是否清空。
    """
    db = SessionLocal()
    try:
        # 模拟用户手动建项目（demo_seeded 未设置）
        from models import Project
        user_project = Project(
            name="用户手动建的项目",
            description="这是真实数据，不应被清空",
            status="active",
            qr_token="manual-token-12345",
        )
        db.add(user_project)
        db.commit()

        # 调用 seed_demo_data(force=False) — 不应清空已有数据
        result = seed_mod.seed_demo_data(db, force=False)
        assert result.get("needs_reset") is True, \
            f"已有数据时应返回 needs_reset=True，实际: {result}"
        assert "already contains project data" in result.get("reason", ""), \
            f"reason 应说明已有数据，实际: {result}"

        # 验证原项目仍存在（未被清空）
        saved = db.query(Project).filter(Project.qr_token == "manual-token-12345").first()
        assert saved is not None, "原项目数据应保留，不应被清空"
        assert saved.name == "用户手动建的项目"

        # force=True 时才允许清空
        result2 = seed_mod.seed_demo_data(db, force=True)
        assert result2.get("seeded") is True, f"force=True 后应重新播种: {result2}"
        # 原项目应已被清空（demo 数据覆盖）
        deleted = db.query(Project).filter(Project.qr_token == "manual-token-12345").first()
        assert deleted is None, "force=True 后原项目应被清空"
    finally:
        db.close()


# ============================================================
# 10. R1b P2-4 修复：POST /demo/reset 服务端 confirm 校验
# ============================================================

def test_demo_reset_requires_confirm_payload():
    """R1b P2-4: POST /demo/reset 缺少 confirm=True 应返回 400。

    服务端强制校验 payload.confirm=True，防 CSRF 误触发或 curl 直接调用。
    """
    # 不带 body
    res1 = client.post("/demo/reset", auth=ADMIN_AUTH, headers=CSRF_HEADERS)
    assert res1.status_code == 400, f"缺 confirm 应 400，实际 {res1.status_code}: {res1.text}"
    data1 = res1.json()
    assert data1["detail"]["code"] == "confirm_required", \
        f"应返回 confirm_required code，实际: {data1}"

    # 带 confirm=False
    res2 = client.post("/demo/reset", auth=ADMIN_AUTH, headers=CSRF_HEADERS,
                       json={"confirm": False})
    assert res2.status_code == 400, f"confirm=False 应 400，实际 {res2.status_code}"

    # 带 confirm=True — 应成功
    # 先播种一些数据
    client.post("/demo/load", auth=ADMIN_AUTH, headers=CSRF_HEADERS)
    res3 = client.post("/demo/reset", auth=ADMIN_AUTH, headers=CSRF_HEADERS,
                       json={"confirm": True})
    assert res3.status_code == 200, f"confirm=True 应 200，实际 {res3.status_code}: {res3.text}"
    assert res3.json().get("reset") is True


# ============================================================
# Phase 6.K1: 桑基图（志愿者跨项目流向）回归测试
# ============================================================

def test_sankey_flows_cross_project_volunteers():
    """Phase 6.K1: 桑基图应识别跨项目志愿者（小李 P3+P1，小张 P1+P2）。

    复用 Phase 6.1 叙事数据：
    - 小李：P3 法律咨询登记表设计 → P1 老人探访（法律视角带入）
    - 小张：P1 物资 + P2 物流（跨项目物资协调者）
    其余志愿者（小王/小周/小吴/小钱）仅在单一项目内活动。
    """
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        from routers.projects import compute_stats
        stats = compute_stats(db)
        vf = stats["volunteer_flows"]

        # 基本结构
        assert "volunteers" in vf, "volunteer_flows 缺少 volunteers 字段"
        assert "projects" in vf, "volunteer_flows 缺少 projects 字段"
        assert "flows" in vf, "volunteer_flows 缺少 flows 字段"
        assert vf["total_tasks"] > 0, "total_tasks 应 > 0"

        # 跨项目志愿者数应为 2（小李 + 小张）
        assert vf["cross_project_volunteers"] == 2, \
            f"跨项目志愿者应为 2（小李+小张），实际 {vf['cross_project_volunteers']}"

        # 小李应出现在 P1 和 P3
        xiaoli = next(v for v in vf["volunteers"] if v["name"] == "小李")
        assert "周末探访老人院" in xiaoli["projects"], "小李应参与 P1"
        assert "社区无偿法律援助" in xiaoli["projects"], "小李应参与 P3"
        assert len(xiaoli["projects"]) == 2, f"小李应参与 2 个项目，实际 {len(xiaoli['projects'])}"

        # 小张应出现在 P1 和 P2
        xiaozhang = next(v for v in vf["volunteers"] if v["name"] == "小张")
        assert "周末探访老人院" in xiaozhang["projects"], "小张应参与 P1"
        assert "山区图书角募捐" in xiaozhang["projects"], "小张应参与 P2"
        assert len(xiaozhang["projects"]) == 2, f"小张应参与 2 个项目，实际 {len(xiaozhang['projects'])}"

        # 桑基图坐标预算：每个 volunteer 和 project 应有 y_start/y_end/y_center
        for v in vf["volunteers"]:
            assert "y_start" in v, f"志愿者 {v['name']} 缺 y_start"
            assert "y_end" in v, f"志愿者 {v['name']} 缺 y_end"
            assert "y_center" in v, f"志愿者 {v['name']} 缺 y_center"
            assert v["y_end"] > v["y_start"], f"志愿者 {v['name']} y_end 应 > y_start"
        for p in vf["projects"]:
            assert "y_start" in p, f"项目 {p['name']} 缺 y_start"
            assert "y_end" in p, f"项目 {p['name']} 缺 y_end"
            assert "y_center" in p, f"项目 {p['name']} 缺 y_center"

        # 每个 flow 应有 y1/y2/stroke_w
        for f in vf["flows"]:
            assert "y1" in f, "flow 缺 y1"
            assert "y2" in f, "flow 缺 y2"
            assert "stroke_w" in f, "flow 缺 stroke_w"
            assert f["stroke_w"] >= 1, f"flow stroke_w 应 ≥1，实际 {f['stroke_w']}"

        # viewBox 尺寸
        assert vf["viewBox_w"] == 800, f"viewBox_w 应为 800，实际 {vf['viewBox_w']}"
        assert vf["viewBox_h"] == 360, f"viewBox_h 应为 360，实际 {vf['viewBox_h']}"
    finally:
        db.close()


def test_sankey_stats_page_renders_flow_svg():
    """Phase 6.K1: /admin/stats 页面应渲染桑基图 SVG 元素。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
    finally:
        db.close()

    res = client.get("/admin/stats", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text

    # 桑基图区段标题
    assert "志愿者跨项目流向" in body, "桑基图标题未渲染"
    # SVG 元素
    assert "sankey-chart" in body, "桑基图容器未渲染"
    assert "sankey-flow" in body, "桑基图流向曲线未渲染"
    assert "sankey-node" in body, "桑基图节点未渲染"
    assert "sankey-vol" in body, "志愿者节点未渲染"
    assert "sankey-proj" in body, "项目节点未渲染"
    assert "sankey-label" in body, "桑基图标签未渲染"
    assert "sankey-axis" in body, "桑基图列标题未渲染"
    # 跨项目洞察
    assert "人跨项目" in body, "跨项目志愿者计数 pill 未渲染"
    assert "chart-insight" in body, "桑基图洞察提示未渲染"
    # 无障碍表格
    assert "志愿者跨项目流向数据" in body, "桑基图无障碍数据表未渲染"
    # 关键志愿者名应出现在标签中
    assert "小李" in body, "志愿者小李未渲染"
    assert "小张" in body, "志愿者小张未渲染"


# ============================================================
# Phase 6.K2: 雷达图（项目健康度 5 维评分）回归测试
# ============================================================

def test_radar_project_health_scores():
    """Phase 6.K2: 3 个项目应生成 5 维健康度评分 + 雷达图坐标。

    验证：
    - 3 个项目均出现在 project_health.projects
    - 5 维评分（0-100）+ polygon_points 顶点字符串
    - 公共结构：axis_endpoints（5 个）/ grid_rings（5 级）/ axis_labels（5 个）
    """
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        from routers.projects import compute_stats
        stats = compute_stats(db)
        ph = stats["project_health"]

        # 公共结构
        assert len(ph["axis_endpoints"]) == 5, "应有 5 个轴端点"
        assert len(ph["grid_rings"]) == 5, "应有 5 级网格"
        assert len(ph["axis_labels"]) == 5, "应有 5 个轴标签"
        assert ph["cx"] == 200 and ph["cy"] == 200, "中心点应为 (200, 200)"
        assert ph["r"] == 150, "半径应为 150"
        assert ph["viewBox_w"] == 400 and ph["viewBox_h"] == 400

        # 3 个项目
        assert len(ph["projects"]) == 3, f"应有 3 个项目健康度，实际 {len(ph['projects'])}"

        # 项目名验证
        project_names = [p["name"] for p in ph["projects"]]
        assert "周末探访老人院" in project_names
        assert "山区图书角募捐" in project_names
        assert "社区无偿法律援助" in project_names

        # 每个项目的 5 维评分 + polygon_points
        score_keys = ["activity", "completion", "handoff_quality", "message_density", "agent_action"]
        for p in ph["projects"]:
            assert "scores" in p, f"{p['name']} 缺 scores"
            assert "polygon_points" in p, f"{p['name']} 缺 polygon_points"
            for k in score_keys:
                assert k in p["scores"], f"{p['name']} 缺 score {k}"
                score = p["scores"][k]
                assert 0 <= score <= 100, f"{p['name']} {k} 评分应在 0-100，实际 {score}"
            # polygon_points 应有 5 个顶点（10 个数字）
            pts_count = len(p["polygon_points"].split())
            assert pts_count == 5, f"{p['name']} polygon 应有 5 个顶点，实际 {pts_count}"

        # P3 社区无偿法律援助：8 任务全 done → completion=100
        p3 = next(p for p in ph["projects"] if p["name"] == "社区无偿法律援助")
        assert p3["scores"]["completion"] == 100, \
            f"P3 完成率应为 100（8 任务全 done），实际 {p3['scores']['completion']}"
        # P3 有 1 个 confirmed handoff（共 1 个）→ handoff_quality=100
        assert p3["scores"]["handoff_quality"] == 100, \
            f"P3 交接质量应为 100（1 confirmed/1 total），实际 {p3['scores']['handoff_quality']}"

        # P2 山区图书角募捐：8 任务中 3 done → completion = round(3/8*100) = 38
        p2 = next(p for p in ph["projects"] if p["name"] == "山区图书角募捐")
        assert p2["scores"]["completion"] == 38, \
            f"P2 完成率应为 38（3 done/8 total），实际 {p2['scores']['completion']}"
    finally:
        db.close()


def test_radar_stats_page_renders_svg():
    """Phase 6.K2: /admin/stats 页面应渲染雷达图 SVG 元素。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
    finally:
        db.close()

    res = client.get("/admin/stats", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text

    # 雷达图标题
    assert "项目健康度雷达图" in body, "雷达图标题未渲染"
    # SVG 元素
    assert "radar-chart-wrap" in body, "雷达图容器未渲染"
    assert "radar-grid" in body, "雷达图网格未渲染"
    assert "radar-axis" in body, "雷达图轴线未渲染"
    assert "radar-polygon" in body, "雷达图评分多边形未渲染"
    assert "radar-proj-1" in body, "项目 1 多边形未渲染"
    assert "radar-proj-2" in body, "项目 2 多边形未渲染"
    assert "radar-proj-3" in body, "项目 3 多边形未渲染"
    assert "radar-axis-label" in body, "雷达图轴标签未渲染"
    # 5 维标签
    for label in ("活跃度", "完成率", "交接质量", "留言密度", "Agent 干预"):
        assert label in body, f"雷达图轴标签 [{label}] 未渲染"
    # 图例
    assert "radar-legend" in body, "雷达图图例未渲染"
    # 无障碍表格
    assert "项目健康度雷达图数据" in body, "雷达图无障碍数据表未渲染"
    # 3 个项目名应出现在图例
    assert "周末探访老人院" in body, "P1 项目名未渲染"
    assert "山区图书角募捐" in body, "P2 项目名未渲染"
    assert "社区无偿法律援助" in body, "P3 项目名未渲染"


# ============================================================
# Phase 6.K3: 网络图（项目协作网络）回归测试
# ============================================================

def test_network_project_collaboration():
    """Phase 6.K3: 项目协作网络应识别共享志愿者边。

    验证：
    - 3 个项目节点呈三角形布局
    - 2 条边：P1-P2（小张共享）+ P1-P3（小李共享）
    - P2-P3 无共享志愿者（无边）
    - 2 个跨项目志愿者
    """
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
        from routers.projects import compute_stats
        stats = compute_stats(db)
        pn = stats["project_network"]

        # 基本结构
        assert "nodes" in pn and "edges" in pn and "shared_volunteers" in pn
        assert len(pn["nodes"]) == 3, f"应有 3 个项目节点，实际 {len(pn['nodes'])}"
        assert len(pn["shared_volunteers"]) == 2, \
            f"应有 2 个跨项目志愿者，实际 {len(pn['shared_volunteers'])}"

        # 节点应有 x/y/radius 坐标
        for node in pn["nodes"]:
            assert "x" in node and "y" in node and "radius" in node
            assert node["radius"] >= 20, f"节点 {node['name']} 半径应 ≥20"

        # 边应有 2 条（P1-P2 小张，P1-P3 小李）
        assert len(pn["edges"]) == 2, f"应有 2 条协作边，实际 {len(pn['edges'])}"

        # 每条边应有 source/target/volunteers/weight
        for e in pn["edges"]:
            assert "source" in e and "target" in e
            assert "volunteers" in e and "weight" in e
            assert "source_xy" in e and "target_xy" in e
            assert e["weight"] >= 1

        # 共享志愿者应为小李和小张
        sv_names = {sv["name"] for sv in pn["shared_volunteers"]}
        assert sv_names == {"小李", "小张"}, f"跨项目志愿者应为小李+小张，实际 {sv_names}"

        # 验证边包含小张（P1-P2）和小李（P1-P3）
        all_edge_volunteers = set()
        for e in pn["edges"]:
            all_edge_volunteers.update(e["volunteers"])
        assert "小张" in all_edge_volunteers, "小张应在某条边上"
        assert "小李" in all_edge_volunteers, "小李应在某条边上"

        # viewBox 尺寸
        assert pn["viewBox_w"] == 400 and pn["viewBox_h"] == 360
    finally:
        db.close()


def test_network_stats_page_renders_svg():
    """Phase 6.K3: /admin/stats 页面应渲染网络图 SVG 元素。"""
    db = SessionLocal()
    try:
        seed_mod.seed_demo_data(db, force=False)
    finally:
        db.close()

    res = client.get("/admin/stats", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text

    # 网络图标题
    assert "项目协作网络" in body, "网络图标题未渲染"
    # SVG 元素
    assert "network-chart-wrap" in body, "网络图容器未渲染"
    assert "network-node" in body, "网络图节点未渲染"
    assert "network-edge" in body, "网络图边未渲染"
    assert "network-node-label" in body, "网络图节点标签未渲染"
    assert "network-edge-label" in body, "网络图边标签未渲染"
    # 图例
    assert "network-legend" in body, "网络图图例未渲染"
    assert "跨项目志愿者" in body, "网络图图例标题未渲染"
    # 无障碍表格
    assert "项目协作网络数据" in body, "网络图无障碍数据表未渲染"
    # 共享志愿者名应出现在边标签
    assert "小李" in body, "小李应在网络图中渲染"
    assert "小张" in body, "小张应在网络图中渲染"
    # 3 个项目名应出现在节点标签
    assert "周末探访老人院" in body
    assert "山区图书角募捐" in body
    assert "社区无偿法律援助" in body
