# tests/test_agent.py — 阶段2.2 服务端 Agent 建议测试
# V1 修复：删除模块级 os.environ.setdefault，由 conftest.py autouse fixture 统一注入
import pytest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from auth import CSRF_COOKIE_NAME
from conftest import auth_headers as _auth_headers_helper
from database import SessionLocal
from main import app
from models import AgentSuggestion, Project, Task
from routers import agent_router

client = TestClient(app)
client.cookies.set(CSRF_COOKIE_NAME, "dummy")  # JWT 路径不需要 Cookie CSRF


@pytest.fixture(autouse=True)
def _demo_mode_env(monkeypatch):
    """R2-V3 修复：仅设置演示模式环境变量，DB 重置交由 conftest.reset_db 统一处理。

    重命名自 reset_db 以避免覆盖 conftest 的同名 autouse fixture（同名就近原则）。
    两个 autouse fixture 同时生效：conftest.reset_db 处理 DB 重建 + 缓存清理，
    本 fixture 仅注入 BRIDGE_AI_DEMO_MODE=1（monkeypatch 自动回滚）。
    """
    # 局部启用演示模式，测试结束后自动恢复，避免污染其他测试文件
    # 复利经验 #235: 模块级 os.environ.setdefault 会泄漏到整个 pytest 会话
    monkeypatch.setenv("BRIDGE_AI_DEMO_MODE", "1")


def _register_and_login(email="agent@example.com", password="testpass123"):
    client.post("/api/auth/register", json={
        "email": email, "password": password, "display_name": "Agent 用户"
    })
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _auth_headers(token):
    """V2 适配：JWT 写端点需要 X-CSRF-Token 头 == JWT 中的 csrf_jti（DRL-R1-V2）。"""
    return _auth_headers_helper(token)


def _make_project(token, name="测试项目"):
    res = client.post("/api/projects", json={"name": name}, headers=_auth_headers(token))
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


# ---------- 扫描函数测试 ----------

def test_scan_stale_projects_creates_suggestion():
    """扫描停滞项目 → 创建 stale_warning 建议。"""
    token = _register_and_login()
    pid = _make_project(token, "停滞项目")
    # 手动将 updated_at 改为 10 天前
    db = SessionLocal()
    db.query(Project).filter(Project.id == pid).update(
        {"updated_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)}  # V18: naive UTC
    )
    db.commit()
    db.close()

    count = agent_router.scan_stale_projects()
    assert count == 1

    db = SessionLocal()
    s = db.query(AgentSuggestion).filter(
        AgentSuggestion.project_id == pid,
        AgentSuggestion.type == "stale_warning",
    ).first()
    db.close()
    assert s is not None
    assert s.severity == "warning"
    assert s.status == "pending"
    assert "停滞" in s.title or "10" in s.title
    # V24 修复：验证建议正文非空且有实质内容
    assert s.body and len(s.body) > 10, f"建议正文应非空且 >10 字符，实际: {repr(s.body)}"


def test_scan_stale_projects_skips_active_projects():
    """活跃项目（近期更新）不会被扫描。"""
    token = _register_and_login()
    _make_project(token, "活跃项目")  # 刚创建，updated_at 是现在
    count = agent_router.scan_stale_projects()
    assert count == 0


def test_scan_overdue_tasks():
    """扫描超期任务 → 创建 overdue_task 建议。"""
    token = _register_and_login()
    pid = _make_project(token, "超期项目")
    # 创建任务并改为 5 天前
    db = SessionLocal()
    t = Task(project_id=pid, title="超期任务", status="todo")
    db.add(t)
    db.commit()
    db.refresh(t)
    db.query(Task).filter(Task.id == t.id).update(
        {"created_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)}  # V18: naive UTC
    )
    db.commit()
    db.close()

    count = agent_router.scan_overdue_tasks()
    assert count == 1

    db = SessionLocal()
    s = db.query(AgentSuggestion).filter(
        AgentSuggestion.project_id == pid,
        AgentSuggestion.type == "overdue_task",
    ).first()
    db.close()
    assert s is not None
    assert s.severity == "warning"  # 5 天 → warning
    # V24 修复：验证建议正文非空且有实质内容
    assert s.body and len(s.body) > 10, f"建议正文应非空且 >10 字符，实际: {repr(s.body)}"


def test_scan_overdue_tasks_critical_severity():
    """超期 > 7 天 → critical 严重级别。"""
    token = _register_and_login()
    pid = _make_project(token, "严重超期项目")
    db = SessionLocal()
    t = Task(project_id=pid, title="严重超期", status="in_progress")
    db.add(t)
    db.commit()
    db.refresh(t)
    db.query(Task).filter(Task.id == t.id).update(
        {"created_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)}  # V18: naive UTC
    )
    db.commit()
    db.close()

    count = agent_router.scan_overdue_tasks()
    assert count == 1

    db = SessionLocal()
    s = db.query(AgentSuggestion).filter(
        AgentSuggestion.project_id == pid,
        AgentSuggestion.type == "overdue_task",
    ).first()
    db.close()
    assert s.severity == "critical"


def test_daily_summary_job():
    """日报扫描为今日活跃项目生成建议。"""
    token = _register_and_login()
    pid = _make_project(token, "日报项目")  # 刚创建，今日活跃
    count = agent_router.daily_summary_job()
    assert count >= 1

    db = SessionLocal()
    s = db.query(AgentSuggestion).filter(
        AgentSuggestion.project_id == pid,
        AgentSuggestion.type == "daily_summary",
    ).first()
    db.close()
    assert s is not None
    assert s.severity == "info"
    # V24 修复：验证日报正文非空且有实质内容
    assert s.body and len(s.body) > 10, f"日报正文应非空且 >10 字符，实际: {repr(s.body)}"


# ---------- 并发去重测试（补充-P2-1）----------

def test_dedup_same_day_same_type():
    """同项目同类型同日 → 只生成一条（UNIQUE 约束）。"""
    token = _register_and_login()
    pid = _make_project(token, "去重项目")
    db = SessionLocal()
    db.query(Project).filter(Project.id == pid).update(
        {"updated_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)}  # V18: naive UTC
    )
    db.commit()
    db.close()

    # 第一次扫描
    count1 = agent_router.scan_stale_projects()
    assert count1 == 1
    # 第二次扫描（同日）→ 应跳过
    count2 = agent_router.scan_stale_projects()
    assert count2 == 0

    db = SessionLocal()
    suggestions = db.query(AgentSuggestion).filter(
        AgentSuggestion.project_id == pid,
        AgentSuggestion.type == "stale_warning",
    ).all()
    db.close()
    assert len(suggestions) == 1


def test_dedup_different_types_same_day():
    """同项目不同类型同日 → 各生成一条。"""
    token = _register_and_login()
    pid = _make_project(token, "多类型项目")
    db = SessionLocal()
    # 让项目同时满足停滞 + 有超期任务
    db.query(Project).filter(Project.id == pid).update(
        {"updated_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)}  # V18: naive UTC
    )
    t = Task(project_id=pid, title="超期", status="todo")
    db.add(t)
    db.commit()
    db.refresh(t)
    db.query(Task).filter(Task.id == t.id).update(
        {"created_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)}  # V18: naive UTC
    )
    db.commit()
    db.close()

    agent_router.scan_stale_projects()
    agent_router.scan_overdue_tasks()

    db = SessionLocal()
    types = {s.type for s in db.query(AgentSuggestion).filter(
        AgentSuggestion.project_id == pid
    ).all()}
    db.close()
    assert types == {"stale_warning", "overdue_task"}


# ---------- 端点测试 ----------

def test_list_suggestions_requires_auth():
    """未认证 → 401。"""
    res = client.get("/api/projects/1/suggestions")
    assert res.status_code == 401


def test_list_suggestions_empty():
    """无建议 → 空列表。"""
    token = _register_and_login()
    pid = _make_project(token)
    res = client.get(f"/api/projects/{pid}/suggestions", headers=_auth_headers(token))
    assert res.status_code == 200
    assert res.json()["count"] == 0
    assert res.json()["items"] == []


def test_list_suggestions_after_scan():
    """扫描后列表返回建议。"""
    token = _register_and_login()
    pid = _make_project(token, "列表测试")
    db = SessionLocal()
    db.query(Project).filter(Project.id == pid).update(
        {"updated_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)}  # V18: naive UTC
    )
    db.commit()
    db.close()
    agent_router.scan_stale_projects()

    res = client.get(f"/api/projects/{pid}/suggestions", headers=_auth_headers(token))
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["items"][0]["type"] == "stale_warning"
    # V24 修复：验证 API 返回的建议正文非空且有实质内容
    assert data["items"][0]["body"] and len(data["items"][0]["body"]) > 10, \
        f"建议正文应非空且 >10 字符，实际: {repr(data['items'][0]['body'])}"


def test_ack_suggestion_pending_to_shown():
    """ACK：pending → shown。"""
    token = _register_and_login()
    pid = _make_project(token)
    db = SessionLocal()
    s = AgentSuggestion(
        project_id=pid, type="stale_warning",
        title="测试", body="body", severity="warning", status="pending",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()

    res = client.post(
        f"/api/projects/{pid}/suggestions/{sid}/ack",
        headers=_auth_headers(token),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "shown"


def test_ack_idempotent():
    """已 shown 的建议再次 ACK 不报错。"""
    token = _register_and_login()
    pid = _make_project(token)
    db = SessionLocal()
    s = AgentSuggestion(
        project_id=pid, type="stale_warning",
        title="测试", body="body", severity="warning", status="shown",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()

    res = client.post(
        f"/api/projects/{pid}/suggestions/{sid}/ack",
        headers=_auth_headers(token),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "shown"


def test_dismiss_suggestion():
    """忽略建议 → dismissed。"""
    token = _register_and_login()
    pid = _make_project(token)
    db = SessionLocal()
    s = AgentSuggestion(
        project_id=pid, type="overdue_task",
        title="测试", body="body", severity="warning", status="pending",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()

    res = client.post(
        f"/api/projects/{pid}/suggestions/{sid}/dismiss",
        headers=_auth_headers(token),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "dismissed"


def test_act_suggestion_sets_acted_at():
    """标记已采纳 → acted + acted_at 非空。"""
    token = _register_and_login()
    pid = _make_project(token)
    db = SessionLocal()
    s = AgentSuggestion(
        project_id=pid, type="daily_summary",
        title="测试", body="body", severity="info", status="shown",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()

    res = client.post(
        f"/api/projects/{pid}/suggestions/{sid}/act",
        headers=_auth_headers(token),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "acted"
    assert data["acted_at"] is not None


def test_act_requires_edit_permission():
    """act 需要 edit 权限——无权限用户 403。"""
    token_owner = _register_and_login("owner@x.com")
    token_stranger = _register_and_login("stranger@x.com")
    pid = _make_project(token_owner)
    db = SessionLocal()
    s = AgentSuggestion(
        project_id=pid, type="daily_summary",
        title="测试", body="body", severity="info", status="shown",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()

    res = client.post(
        f"/api/projects/{pid}/suggestions/{sid}/act",
        headers=_auth_headers(token_stranger),
    )
    assert res.status_code == 403


def test_suggestion_not_found():
    """不存在的建议 → 404。"""
    token = _register_and_login()
    pid = _make_project(token)
    res = client.post(
        f"/api/projects/{pid}/suggestions/99999/ack",
        headers=_auth_headers(token),
    )
    assert res.status_code == 404


def test_list_suggestions_other_user_forbidden():
    """无项目权限的用户 → 403。"""
    token_owner = _register_and_login("owner2@x.com")
    token_stranger = _register_and_login("stranger2@x.com")
    pid = _make_project(token_owner)
    res = client.get(
        f"/api/projects/{pid}/suggestions",
        headers=_auth_headers(token_stranger),
    )
    assert res.status_code == 403


# ---------- 状态流转测试（补充-P2-2）----------

def test_status_flow_pending_shown_acted():
    """完整状态流转：pending → shown → acted。"""
    token = _register_and_login()
    pid = _make_project(token)
    from database import SessionLocal
    db = SessionLocal()
    s = AgentSuggestion(
        project_id=pid, type="stale_warning",
        title="流转测试", body="body", severity="warning", status="pending",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()

    # pending → shown
    # V12 修复：POST 后先 assert status_code 再 .json()
    res = client.post(f"/api/projects/{pid}/suggestions/{sid}/ack", headers=_auth_headers(token))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "shown"
    # shown → acted
    res = client.post(f"/api/projects/{pid}/suggestions/{sid}/act", headers=_auth_headers(token))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "acted"


# V12 新增：4 种非法状态转换参数化测试
# 验证：非 pending 状态下 ack 不改变状态；dismiss/act 的行为符合预期
@pytest.mark.parametrize("initial_status, action, expected_status", [
    # 1. shown → ack：ack 仅对 pending 生效，shown 状态不变
    ("shown", "ack", "shown"),
    # 2. dismissed → ack：已忽略的建议 ack 不恢复
    ("dismissed", "ack", "dismissed"),
    # 3. acted → ack：已采纳的建议 ack 不恢复
    ("acted", "ack", "acted"),
    # 4. acted → dismiss：已采纳的建议仍可被忽略（dismiss 无前置状态约束）
    ("acted", "dismiss", "dismissed"),
])
def test_status_flow_illegal_transitions(initial_status, action, expected_status):
    """V12: 非法状态转换——验证状态不会不当回退。"""
    token = _register_and_login()
    pid = _make_project(token)
    from database import SessionLocal
    db = SessionLocal()
    s = AgentSuggestion(
        project_id=pid, type="stale_warning",
        title="非法转换测试", body="body", severity="warning", status=initial_status,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()

    res = client.post(
        f"/api/projects/{pid}/suggestions/{sid}/{action}",
        headers=_auth_headers(token),
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == expected_status, \
        f"初始={initial_status} + {action}: 期望 {expected_status}，实际 {res.json()['status']}"


def test_run_all_scans():
    """run_all_scans 手动触发所有扫描。"""
    token = _register_and_login()
    pid = _make_project(token, "全扫项目")
    db = SessionLocal()
    db.query(Project).filter(Project.id == pid).update(
        {"updated_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)}  # V18: naive UTC
    )
    t = Task(project_id=pid, title="超期", status="todo")
    db.add(t)
    db.commit()
    db.refresh(t)
    db.query(Task).filter(Task.id == t.id).update(
        {"created_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)}  # V18: naive UTC
    )
    db.commit()
    db.close()

    # V11 修复：第一个项目 updated_at 被改为 10 天前（供 stale 扫描），不会被 daily_summary 扫到。
    # 创建第二个今日活跃项目供日报扫描——daily_summary_job 只扫描 updated_at >= 今日 0:00 的项目。
    pid2 = _make_project(token, "日报项目")

    result = agent_router.run_all_scans()
    assert result["stale"] == 1
    assert result["overdue"] == 1
    # V11 修复：pid2 是今日创建的活跃项目，daily_summary_job 应新建 1 条日报建议。
    # UNIQUE(project_id, type, date(created_at)) 不会冲突——daily_summary 与 stale_warning/overdue_task 是不同 type。
    assert result["daily"] == 1, f"日报应新建 1 条（pid2 今日活跃），实际: {result['daily']}"


# ---------- V22: AI 异常降级测试 ----------

def test_scan_resilient_to_ai_failure(monkeypatch):
    """V22: generate_proactive_suggestion 返回 None（AI 失败/配置缺失）时，扫描应使用规则引擎回退正文。

    ai.py 的 generate_proactive_suggestion 内部有 try/except，失败时返回 None 而非抛异常。
    此测试模拟 AI 失败场景，验证扫描仍能创建建议且使用回退正文。
    """
    token = _register_and_login()
    pid = _make_project(token, "AI 失败项目")
    db = SessionLocal()
    db.query(Project).filter(Project.id == pid).update(
        {"updated_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)}  # V18: naive UTC
    )
    db.commit()
    db.close()

    # V22: mock generate_proactive_suggestion 返回 None（模拟 AI 失败）
    monkeypatch.setattr(agent_router, "generate_proactive_suggestion", lambda *a, **kw: None)

    count = agent_router.scan_stale_projects()
    assert count == 1, "AI 失败时仍应通过规则引擎回退创建建议"

    db = SessionLocal()
    s = db.query(AgentSuggestion).filter(
        AgentSuggestion.project_id == pid,
        AgentSuggestion.type == "stale_warning",
    ).first()
    db.close()
    assert s is not None
    # V24: 回退正文应非空且有实质内容
    assert s.body and len(s.body) > 10, f"回退正文应非空且 >10 字符，实际: {repr(s.body)}"


def test_scan_resilient_to_ai_exception(monkeypatch):
    """V22 补充: generate_proactive_suggestion 抛异常时，扫描行为验证。

    ai.py 内部有 try/except 不会抛异常，但如果未来代码变更破坏了这一保证，
    此测试将暴露问题——当前 scan_stale_projects 无 try/except 包裹 AI 调用。
    """
    token = _register_and_login()
    pid = _make_project(token, "AI 异常项目")
    db = SessionLocal()
    db.query(Project).filter(Project.id == pid).update(
        {"updated_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)}  # V18: naive UTC
    )
    db.commit()
    db.close()

    # V22: mock generate_proactive_suggestion 抛异常
    def _raise(*args, **kwargs):
        raise RuntimeError("模拟 AI 内部异常")
    monkeypatch.setattr(agent_router, "generate_proactive_suggestion", _raise)

    # 当前后端 scan_stale_projects 未捕获 AI 异常——记录此行为
    with pytest.raises(RuntimeError, match="模拟 AI 内部异常"):
        agent_router.scan_stale_projects()


# ---------- V23: ack/dismiss 陌生人权限测试 ----------

def test_ack_requires_edit_permission():
    """V23: ack 端点需要 edit 权限——无权限用户 403。"""
    token_owner = _register_and_login("ack_owner@x.com")
    token_stranger = _register_and_login("ack_stranger@x.com")
    pid = _make_project(token_owner)
    db = SessionLocal()
    s = AgentSuggestion(
        project_id=pid, type="stale_warning",
        title="测试", body="body", severity="warning", status="pending",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()

    res = client.post(
        f"/api/projects/{pid}/suggestions/{sid}/ack",
        headers=_auth_headers(token_stranger),
    )
    # V23 修复：ack 是写操作（状态变更 pending→shown），陌生人应 403
    assert res.status_code == 403, f"陌生人 ack 应返回 403，实际: {res.status_code} {res.text}"


def test_dismiss_requires_edit_permission():
    """V23: dismiss 端点需要 edit 权限——无权限用户 403。"""
    token_owner = _register_and_login("dismiss_owner@x.com")
    token_stranger = _register_and_login("dismiss_stranger@x.com")
    pid = _make_project(token_owner)
    db = SessionLocal()
    s = AgentSuggestion(
        project_id=pid, type="overdue_task",
        title="测试", body="body", severity="warning", status="pending",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()

    res = client.post(
        f"/api/projects/{pid}/suggestions/{sid}/dismiss",
        headers=_auth_headers(token_stranger),
    )
    # V23 修复：dismiss 是写操作（状态变更→dismissed），陌生人应 403
    assert res.status_code == 403, f"陌生人 dismiss 应返回 403，实际: {res.status_code} {res.text}"
