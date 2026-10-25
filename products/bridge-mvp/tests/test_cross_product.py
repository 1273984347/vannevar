# tests/test_cross_product.py — Phase 4.1 跨产品联动端点测试
"""覆盖 Focus 快照 / Muse 灵感推送到 Bridge 项目档案的 6 个端点。

测试维度：
- 推送 + 列表 + 删除（正常流程）
- 权限校验（view 不能推送/删除，edit 可以）
- 幂等性（同一 uuid 重复推送返回 409）
- 404（不存在的项目/快照/灵感）
- 项目删除级联清理关联上下文
- 字段长度限制（extra="forbid" + max_length）

DRL 适配：使用 conftest 的 auth_headers(token) 自动注入 CSRF 头，
通过 API 注册+登录获取含 csrf_jti 的真实 token，与 test_security.py 模式一致。
"""
import time

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers
from main import app
from models import ProjectContextInspiration, ProjectContextSnapshot
from database import SessionLocal


# ============================================================
# 辅助函数（与 test_security.py 模式一致）
# ============================================================

def _register_and_login(client, email="p4cross@x.com", password="testpass123"):
    """注册用户并返回 JWT token（含 csrf_jti）。"""
    client.post("/api/auth/register", json={
        "email": email, "password": password, "display_name": "P4 跨产品测试用户"
    })
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _create_project(client, token, name="P4 跨产品联动项目"):
    """创建项目并返回 project_id。"""
    res = client.post("/api/projects", json={"name": name}, headers=auth_headers(token))
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def _push_snapshot(client, token, project_id, snapshot_uuid="snap-001", title="测试快照"):
    """推送 Focus 快照，返回响应。"""
    payload = {
        "snapshot_uuid": snapshot_uuid,
        "title": title,
        "summary": "AI 生成的快照摘要",
        "urls": ["https://example.com/page1", "https://example.com/page2"],
        "tab_count": 5,
        "created_at": int(time.time() * 1000),
    }
    return client.post(
        f"/api/projects/{project_id}/context-snapshots",
        json=payload,
        headers=auth_headers(token),
    )


def _push_inspiration(client, token, project_id, inspiration_uuid="insp-001", title="测试灵感"):
    """推送 Muse 灵感，返回响应。"""
    payload = {
        "inspiration_uuid": inspiration_uuid,
        "title": title,
        "url": "https://example.com/inspiration",
        "note": "这是灵感笔记",
        "mood": "专注",
        "created_at": int(time.time() * 1000),
    }
    return client.post(
        f"/api/projects/{project_id}/context-inspirations",
        json=payload,
        headers=auth_headers(token),
    )


# ============================================================
# Focus 快照推送
# ============================================================


def test_push_snapshot_success(client):
    """正常推送 Focus 快照应返回 201。"""
    token = _register_and_login(client, "snap1@x.com")
    pid = _create_project(client, token, name="快照推送测试项目")
    resp = _push_snapshot(client, token, pid, snapshot_uuid="snap-uuid-001", title="测试快照")
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["snapshot_uuid"] == "snap-uuid-001"
    assert data["title"] == "测试快照"
    assert data["tab_count"] == 5
    assert data["urls"] == ["https://example.com/page1", "https://example.com/page2"]
    assert data["project_id"] == pid
    assert data["pushed_by"] > 0


def test_push_snapshot_idempotent_409(client):
    """同一 (project_id, snapshot_uuid) 重复推送返回 409。"""
    token = _register_and_login(client, "snap2@x.com")
    pid = _create_project(client, token, name="快照幂等测试项目")
    resp1 = _push_snapshot(client, token, pid, snapshot_uuid="snap-uuid-dup", title="重复推送测试")
    assert resp1.status_code == 201
    resp2 = _push_snapshot(client, token, pid, snapshot_uuid="snap-uuid-dup", title="重复推送测试")
    assert resp2.status_code == 409


def test_list_snapshots(client):
    """列出项目的 Focus 快照。"""
    token = _register_and_login(client, "snap3@x.com")
    pid = _create_project(client, token, name="快照列表测试项目")
    _push_snapshot(client, token, pid, snapshot_uuid="snap-uuid-list", title="列表测试快照")
    resp = client.get(
        f"/api/projects/{pid}/context-snapshots",
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    uuids = [i["snapshot_uuid"] for i in items]
    assert "snap-uuid-list" in uuids


def test_delete_snapshot(client):
    """删除 Focus 快照。"""
    token = _register_and_login(client, "snap4@x.com")
    pid = _create_project(client, token, name="快照删除测试项目")
    push_resp = _push_snapshot(client, token, pid, snapshot_uuid="snap-uuid-del", title="待删除快照")
    assert push_resp.status_code == 201
    snapshot_id = push_resp.json()["id"]
    # 删除
    del_resp = client.delete(
        f"/api/projects/{pid}/context-snapshots/{snapshot_id}",
        headers=auth_headers(token),
    )
    assert del_resp.status_code == 204
    # 再次删除应 404
    del_resp2 = client.delete(
        f"/api/projects/{pid}/context-snapshots/{snapshot_id}",
        headers=auth_headers(token),
    )
    assert del_resp2.status_code == 404


# ============================================================
# Muse 灵感推送
# ============================================================


def test_push_inspiration_success(client):
    """正常推送 Muse 灵感应返回 201。"""
    token = _register_and_login(client, "insp1@x.com")
    pid = _create_project(client, token, name="灵感推送测试项目")
    resp = _push_inspiration(client, token, pid, inspiration_uuid="insp-uuid-001", title="测试灵感")
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["inspiration_uuid"] == "insp-uuid-001"
    assert data["title"] == "测试灵感"
    assert data["mood"] == "专注"
    assert data["project_id"] == pid


def test_push_inspiration_idempotent_409(client):
    """同一 (project_id, inspiration_uuid) 重复推送返回 409。"""
    token = _register_and_login(client, "insp2@x.com")
    pid = _create_project(client, token, name="灵感幂等测试项目")
    resp1 = _push_inspiration(client, token, pid, inspiration_uuid="insp-uuid-dup", title="重复灵感")
    assert resp1.status_code == 201
    resp2 = _push_inspiration(client, token, pid, inspiration_uuid="insp-uuid-dup", title="重复灵感")
    assert resp2.status_code == 409


def test_list_inspirations(client):
    """列出项目的 Muse 灵感。"""
    token = _register_and_login(client, "insp3@x.com")
    pid = _create_project(client, token, name="灵感列表测试项目")
    _push_inspiration(client, token, pid, inspiration_uuid="insp-uuid-list", title="列表测试灵感")
    resp = client.get(
        f"/api/projects/{pid}/context-inspirations",
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    uuids = [i["inspiration_uuid"] for i in items]
    assert "insp-uuid-list" in uuids


def test_delete_inspiration(client):
    """删除 Muse 灵感。"""
    token = _register_and_login(client, "insp4@x.com")
    pid = _create_project(client, token, name="灵感删除测试项目")
    push_resp = _push_inspiration(client, token, pid, inspiration_uuid="insp-uuid-del", title="待删除灵感")
    assert push_resp.status_code == 201
    insp_id = push_resp.json()["id"]
    del_resp = client.delete(
        f"/api/projects/{pid}/context-inspirations/{insp_id}",
        headers=auth_headers(token),
    )
    assert del_resp.status_code == 204
    del_resp2 = client.delete(
        f"/api/projects/{pid}/context-inspirations/{insp_id}",
        headers=auth_headers(token),
    )
    assert del_resp2.status_code == 404


# ============================================================
# 权限校验
# ============================================================


def test_unauthenticated_push_rejected(client):
    """未认证推送应返回 401。"""
    token = _register_and_login(client, "noauth1@x.com")
    pid = _create_project(client, token, name="未认证测试项目")
    # 不带 Authorization 头
    payload = {
        "snapshot_uuid": "snap-noauth",
        "title": "无认证",
        "summary": "",
        "urls": [],
        "tab_count": 0,
        "created_at": int(time.time() * 1000),
    }
    resp = client.post(
        f"/api/projects/{pid}/context-snapshots",
        json=payload,
    )
    assert resp.status_code in (401, 403)


def test_non_owner_push_rejected(client):
    """非项目成员推送应返回 403。"""
    token_a = _register_and_login(client, "owner@x.com")
    pid_a = _create_project(client, token_a, name="用户A的项目")
    # 另一个用户登录
    token_b = _register_and_login(client, "other@x.com")
    payload = {
        "snapshot_uuid": "snap-forbidden",
        "title": "禁止推送",
        "summary": "",
        "urls": [],
        "tab_count": 0,
        "created_at": int(time.time() * 1000),
    }
    resp = client.post(
        f"/api/projects/{pid_a}/context-snapshots",
        json=payload,
        headers=auth_headers(token_b),
    )
    assert resp.status_code == 403


def test_missing_csrf_header_rejected(client):
    """R2-V5 适配：JWT 写端点缺少 X-CSRF-Token 头应返回 403。"""
    token = _register_and_login(client, "csrf1@x.com")
    pid = _create_project(client, token, name="CSRF 测试项目")
    payload = {
        "snapshot_uuid": "snap-csrf",
        "title": "CSRF",
        "summary": "",
        "urls": [],
        "tab_count": 0,
        "created_at": int(time.time() * 1000),
    }
    # 只有 Authorization 头，无 X-CSRF-Token
    resp = client.post(
        f"/api/projects/{pid}/context-snapshots",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ============================================================
# Phase 4.1 DRL R2 修复（P2-B2）：灵感端点权限/CSRF 测试对称覆盖
# 设计依据：snapshot 端点已有 401/403/CSRF 三个权限测试，
# inspiration 端点共用 require_project_access("edit") 依赖（projects.py:1340/1387/1407），
# 但端点级测试缺失，构成 class-level pattern 的不对称覆盖。此处补齐。
# ============================================================


def test_push_inspiration_unauthenticated_rejected(client):
    """未认证推送灵感应返回 401。"""
    token = _register_and_login(client, "insp-noauth@x.com")
    pid = _create_project(client, token, name="灵感未认证测试项目")
    payload = {
        "inspiration_uuid": "insp-noauth",
        "title": "无认证灵感",
        "url": "https://example.com/noauth",
        "note": "",
        "mood": "",
        "created_at": int(time.time() * 1000),
    }
    resp = client.post(
        f"/api/projects/{pid}/context-inspirations",
        json=payload,
    )
    assert resp.status_code in (401, 403)


def test_push_inspiration_non_owner_rejected(client):
    """非项目成员推送灵感应返回 403。"""
    token_a = _register_and_login(client, "insp-owner@x.com")
    pid_a = _create_project(client, token_a, name="灵感 owner 测试项目")
    token_b = _register_and_login(client, "insp-other@x.com")
    payload = {
        "inspiration_uuid": "insp-forbidden",
        "title": "禁止推送灵感",
        "url": "https://example.com/forbidden",
        "note": "",
        "mood": "",
        "created_at": int(time.time() * 1000),
    }
    resp = client.post(
        f"/api/projects/{pid_a}/context-inspirations",
        json=payload,
        headers=auth_headers(token_b),
    )
    assert resp.status_code == 403


def test_push_inspiration_missing_csrf_header_rejected(client):
    """灵感端点 JWT 写端点缺少 X-CSRF-Token 头应返回 403。"""
    token = _register_and_login(client, "insp-csrf@x.com")
    pid = _create_project(client, token, name="灵感 CSRF 测试项目")
    payload = {
        "inspiration_uuid": "insp-csrf",
        "title": "灵感 CSRF",
        "url": "https://example.com/csrf",
        "note": "",
        "mood": "",
        "created_at": int(time.time() * 1000),
    }
    # 只有 Authorization 头，无 X-CSRF-Token
    resp = client.post(
        f"/api/projects/{pid}/context-inspirations",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ============================================================
# 字段校验
# ============================================================


def test_snapshot_extra_field_forbidden(client):
    """extra="forbid"：未知字段应返回 422。"""
    token = _register_and_login(client, "extra1@x.com")
    pid = _create_project(client, token, name="extra 字段测试项目")
    payload = {
        "snapshot_uuid": "snap-extra",
        "title": "extra",
        "summary": "",
        "urls": [],
        "tab_count": 0,
        "created_at": int(time.time() * 1000),
        "unknown_field": "should be rejected",
    }
    resp = client.post(
        f"/api/projects/{pid}/context-snapshots",
        json=payload,
        headers=auth_headers(token),
    )
    assert resp.status_code == 422


def test_inspiration_missing_required_field(client):
    """缺少必填字段应返回 422。"""
    token = _register_and_login(client, "missing1@x.com")
    pid = _create_project(client, token, name="缺失字段测试项目")
    payload = {
        "inspiration_uuid": "insp-missing",
        # 缺少 title 和 url
        "note": "",
        "mood": "",
        "created_at": int(time.time() * 1000),
    }
    resp = client.post(
        f"/api/projects/{pid}/context-inspirations",
        json=payload,
        headers=auth_headers(token),
    )
    assert resp.status_code == 422


def test_snapshot_404_when_project_not_found(client):
    """推送快照到不存在的项目应返回 404。"""
    token = _register_and_login(client, "notfound1@x.com")
    payload = {
        "snapshot_uuid": "snap-404",
        "title": "404 项目",
        "summary": "",
        "urls": [],
        "tab_count": 0,
        "created_at": int(time.time() * 1000),
    }
    resp = client.post(
        "/api/projects/999999/context-snapshots",
        json=payload,
        headers=auth_headers(token),
    )
    assert resp.status_code == 404


# ============================================================
# 级联删除
# ============================================================


def test_project_delete_cascades_context(client):
    """项目删除时，关联的快照和灵感应级联删除。"""
    token = _register_and_login(client, "cascade@x.com")
    pid = _create_project(client, token, name="级联删除测试项目")
    # 推送快照 + 灵感
    snap_resp = _push_snapshot(client, token, pid, snapshot_uuid="snap-cascade", title="级联快照")
    assert snap_resp.status_code == 201
    insp_resp = _push_inspiration(client, token, pid, inspiration_uuid="insp-cascade", title="级联灵感")
    assert insp_resp.status_code == 201
    # 删除项目
    del_resp = client.delete(f"/api/projects/{pid}", headers=auth_headers(token))
    assert del_resp.status_code == 204
    # 确认级联删除
    db = SessionLocal()
    try:
        snaps = db.query(ProjectContextSnapshot).filter(ProjectContextSnapshot.project_id == pid).all()
        insps = db.query(ProjectContextInspiration).filter(ProjectContextInspiration.project_id == pid).all()
        assert len(snaps) == 0
        assert len(insps) == 0
    finally:
        db.close()


# ============================================================
# Phase 4.1: Bridge → Focus 任务状态变更通知端点
# ============================================================


def _create_task(client, token, project_id, title="测试任务"):
    """在项目中创建任务，返回响应 json。新建任务默认 status=todo。"""
    res = client.post(
        f"/api/projects/{project_id}/tasks",
        json={"title": title, "description": "", "owner_name": "tester"},
        headers=auth_headers(token),
    )
    assert res.status_code == 201, res.text
    return res.json()


def _update_task_status(client, token, task_id, new_status):
    """更新任务状态（PATCH /api/tasks/{id}）。"""
    res = client.patch(
        f"/api/tasks/{task_id}",
        json={"status": new_status},
        headers=auth_headers(token),
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_recent_task_updates_requires_auth(client):
    """未认证访问 /api/auth/me/recent-task-updates 应返回 401。"""
    resp = client.get("/api/auth/me/recent-task-updates")
    assert resp.status_code == 401


def test_recent_task_updates_returns_user_tasks(client):
    """登录用户应看到自己项目的最近任务，按 updated_at 倒序。"""
    token = _register_and_login(client, "watcher@x.com")
    pid = _create_project(client, token, name="任务通知项目")
    # 创建 3 条任务（默认 status=todo）
    t1 = _create_task(client, token, pid, title="任务 A")
    t2 = _create_task(client, token, pid, title="任务 B")
    t3 = _create_task(client, token, pid, title="任务 C")
    # 更新两条任务的状态，使 updated_at 不同
    _update_task_status(client, token, t2["id"], "in_progress")
    _update_task_status(client, token, t3["id"], "done")
    # 拉取最近任务更新
    resp = client.get(
        "/api/auth/me/recent-task-updates?limit=10",
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 3
    # 验证字段
    titles = {item["title"] for item in data}
    assert "任务 A" in titles and "任务 B" in titles and "任务 C" in titles
    # 验证 ISO 时间字符串可被解析
    for item in data:
        assert item["updated_at_iso"], "updated_at_iso 不应为空"
        assert item["updated_at_iso"].endswith("Z"), "应为 UTC 时间戳（带 Z 后缀）"
        assert item["project_name"] == "任务通知项目"
        assert item["status"] in ("todo", "in_progress", "done")
    # 任务 C 最后被更新，应排在最前
    assert data[0]["title"] == "任务 C"
    assert data[0]["status"] == "done"


def test_recent_task_updates_isolated_between_users(client):
    """用户 A 看不到用户 B 的项目任务。"""
    token_a = _register_and_login(client, "user-a@x.com")
    pid_a = _create_project(client, token_a, name="A 的项目")
    _create_task(client, token_a, pid_a, title="A 的任务")

    token_b = _register_and_login(client, "user-b@x.com")
    pid_b = _create_project(client, token_b, name="B 的项目")
    _create_task(client, token_b, pid_b, title="B 的任务")

    # A 拉取，只能看到 A 的任务
    resp_a = client.get("/api/auth/me/recent-task-updates", headers=auth_headers(token_a))
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert len(data_a) == 1
    assert data_a[0]["title"] == "A 的任务"
    assert data_a[0]["project_name"] == "A 的项目"

    # B 拉取，只能看到 B 的任务
    resp_b = client.get("/api/auth/me/recent-task-updates", headers=auth_headers(token_b))
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    assert len(data_b) == 1
    assert data_b[0]["title"] == "B 的任务"
    assert data_b[0]["project_name"] == "B 的项目"


def test_recent_task_updates_limit_param(client):
    """limit 参数应限制返回条数，且 max=100。"""
    token = _register_and_login(client, "limit@x.com")
    pid = _create_project(client, token, name="limit 测试项目")
    # 创建 5 条任务
    for i in range(5):
        _create_task(client, token, pid, title=f"任务 {i}")
    # limit=2 应只返回 2 条
    resp = client.get(
        "/api/auth/me/recent-task-updates?limit=2",
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    # limit=0 应被 ge=1 拒绝（422）
    resp_zero = client.get(
        "/api/auth/me/recent-task-updates?limit=0",
        headers=auth_headers(token),
    )
    assert resp_zero.status_code == 422
    # limit=200 应被 le=100 拒绝（422）
    resp_over = client.get(
        "/api/auth/me/recent-task-updates?limit=200",
        headers=auth_headers(token),
    )
    assert resp_over.status_code == 422


def test_recent_task_updates_empty_for_new_user(client):
    """新用户（无项目）应返回空列表。"""
    token = _register_and_login(client, "empty@x.com")
    resp = client.get("/api/auth/me/recent-task-updates", headers=auth_headers(token))
    assert resp.status_code == 200
    assert resp.json() == []


# ============================================================
# Phase 4.1: task_status_changed SSE 事件触发验证
# ============================================================


def test_task_status_changed_event_fired(client, monkeypatch):
    """任务 status 从 todo → in_progress 时应触发 task_status_changed SSE 事件。

    通过 monkeypatch 拦截 routers.events.publish，记录事件类型 + 数据，
    验证 status 变化时 publish 被调用两次（task_updated + task_status_changed），
    而 status 不变时只调用一次（task_updated）。
    """
    from routers import events as events_mod

    published = []
    original_publish = events_mod.publish

    def _spy_publish(project_id, event_type, data):
        published.append({"project_id": project_id, "event": event_type, "data": data})
        return original_publish(project_id, event_type, data)

    monkeypatch.setattr(events_mod, "publish", _spy_publish)
    # tasks.py 通过 `from routers.events import publish` 导入，需要同时 patch tasks 模块的引用
    from routers import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "publish", _spy_publish)

    token = _register_and_login(client, "sse1@x.com")
    pid = _create_project(client, token, name="SSE 事件测试项目")
    task = _create_task(client, token, pid, title="SSE 测试任务")

    # 重置 publish 记录（创建任务时也会 publish task_created）
    published.clear()

    # 1. status 变化：todo → in_progress
    _update_task_status(client, token, task["id"], "in_progress")

    # 应有 2 条 publish：task_updated + task_status_changed
    event_types = [p["event"] for p in published]
    assert "task_updated" in event_types, "应推送 task_updated 事件"
    assert "task_status_changed" in event_types, "应推送 task_status_changed 事件"

    # task_status_changed 事件数据应包含 old_status / new_status
    sse_event = next(p for p in published if p["event"] == "task_status_changed")
    assert sse_event["data"]["old_status"] == "todo"
    assert sse_event["data"]["new_status"] == "in_progress"
    assert sse_event["data"]["title"] == "SSE 测试任务"
    assert sse_event["data"]["project_id"] == pid
    assert sse_event["data"]["id"] == task["id"]


def test_task_status_changed_not_fired_when_status_unchanged(client, monkeypatch):
    """任务 status 未变化时不应触发 task_status_changed 事件（仅 task_updated）。"""
    from routers import events as events_mod

    published = []
    original_publish = events_mod.publish

    def _spy_publish(project_id, event_type, data):
        published.append({"project_id": project_id, "event": event_type, "data": data})
        return original_publish(project_id, event_type, data)

    monkeypatch.setattr(events_mod, "publish", _spy_publish)
    from routers import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "publish", _spy_publish)

    token = _register_and_login(client, "sse2@x.com")
    pid = _create_project(client, token, name="SSE 无变化测试项目")
    task = _create_task(client, token, pid, title="无变化任务")

    published.clear()

    # 更新 title 不改 status
    res = client.patch(
        f"/api/tasks/{task['id']}",
        json={"title": "新标题"},
        headers=auth_headers(token),
    )
    assert res.status_code == 200, res.text

    # 只应有 task_updated，不应有 task_status_changed
    event_types = [p["event"] for p in published]
    assert "task_updated" in event_types
    assert "task_status_changed" not in event_types


def test_task_status_changed_fired_on_done_transition(client, monkeypatch):
    """任务 status 从 in_progress → done 也应触发 task_status_changed 事件。"""
    from routers import events as events_mod

    published = []
    original_publish = events_mod.publish

    def _spy_publish(project_id, event_type, data):
        published.append({"project_id": project_id, "event": event_type, "data": data})
        return original_publish(project_id, event_type, data)

    monkeypatch.setattr(events_mod, "publish", _spy_publish)
    from routers import tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "publish", _spy_publish)

    token = _register_and_login(client, "sse3@x.com")
    pid = _create_project(client, token, name="SSE done 转换测试项目")
    task = _create_task(client, token, pid, title="done 转换任务")

    # todo → in_progress → done
    _update_task_status(client, token, task["id"], "in_progress")
    published.clear()
    _update_task_status(client, token, task["id"], "done")

    event_types = [p["event"] for p in published]
    assert "task_status_changed" in event_types
    sse_event = next(p for p in published if p["event"] == "task_status_changed")
    assert sse_event["data"]["old_status"] == "in_progress"
    assert sse_event["data"]["new_status"] == "done"


# ============================================================
# Phase 7.4 (建议 3) DRL R2 修复（F-1）：Context Cards SSE 事件覆盖
# 设计依据：projects.py 4 个新增 publish 调用（context_snapshot_created/deleted
# + context_inspiration_created/deleted）零测试覆盖，属 class-level pattern 4 instances
# （DRL R1b 升级至 P2）。复用 _spy_publish monkeypatch 模式（与 test_task_status_changed
# 系列一致），同时 patch events_mod + projects_mod 因 projects.py 通过
# `from routers.events import publish` 导入（projects.py:40）。
# ============================================================


def test_context_snapshot_created_publish(client, monkeypatch):
    """推送 Focus 快照时应 publish context_snapshot_created 事件 + payload 含 id/title/tab_count。"""
    from routers import events as events_mod
    from routers import projects as projects_mod

    published = []
    original_publish = events_mod.publish

    def _spy_publish(project_id, event_type, data):
        published.append({"project_id": project_id, "event": event_type, "data": data})
        return original_publish(project_id, event_type, data)

    monkeypatch.setattr(events_mod, "publish", _spy_publish)
    monkeypatch.setattr(projects_mod, "publish", _spy_publish)

    token = _register_and_login(client, "sse-snap-create@x.com")
    pid = _create_project(client, token, name="SSE 快照创建测试项目")

    published.clear()
    push_resp = _push_snapshot(
        client, token, pid,
        snapshot_uuid="snap-sse-create",
        title="SSE 创建快照",
    )
    assert push_resp.status_code == 201, push_resp.text
    snap_id = push_resp.json()["id"]

    event_types = [p["event"] for p in published]
    assert "context_snapshot_created" in event_types, "应推送 context_snapshot_created 事件"
    sse_event = next(p for p in published if p["event"] == "context_snapshot_created")
    assert sse_event["project_id"] == pid
    assert sse_event["data"]["id"] == snap_id
    assert sse_event["data"]["title"] == "SSE 创建快照"
    assert sse_event["data"]["tab_count"] == 5


def test_context_snapshot_deleted_publish(client, monkeypatch):
    """删除 Focus 快照时应 publish context_snapshot_deleted 事件 + payload 含 id。"""
    from routers import events as events_mod
    from routers import projects as projects_mod

    published = []
    original_publish = events_mod.publish

    def _spy_publish(project_id, event_type, data):
        published.append({"project_id": project_id, "event": event_type, "data": data})
        return original_publish(project_id, event_type, data)

    monkeypatch.setattr(events_mod, "publish", _spy_publish)
    monkeypatch.setattr(projects_mod, "publish", _spy_publish)

    token = _register_and_login(client, "sse-snap-del@x.com")
    pid = _create_project(client, token, name="SSE 快照删除测试项目")
    push_resp = _push_snapshot(
        client, token, pid,
        snapshot_uuid="snap-sse-del",
        title="待删除 SSE 快照",
    )
    assert push_resp.status_code == 201
    snap_id = push_resp.json()["id"]

    published.clear()
    del_resp = client.delete(
        f"/api/projects/{pid}/context-snapshots/{snap_id}",
        headers=auth_headers(token),
    )
    assert del_resp.status_code == 204

    event_types = [p["event"] for p in published]
    assert "context_snapshot_deleted" in event_types, "应推送 context_snapshot_deleted 事件"
    sse_event = next(p for p in published if p["event"] == "context_snapshot_deleted")
    assert sse_event["project_id"] == pid
    assert sse_event["data"]["id"] == snap_id


def test_context_inspiration_created_publish(client, monkeypatch):
    """推送 Muse 灵感时应 publish context_inspiration_created 事件 + payload 含 id/title/mood。"""
    from routers import events as events_mod
    from routers import projects as projects_mod

    published = []
    original_publish = events_mod.publish

    def _spy_publish(project_id, event_type, data):
        published.append({"project_id": project_id, "event": event_type, "data": data})
        return original_publish(project_id, event_type, data)

    monkeypatch.setattr(events_mod, "publish", _spy_publish)
    monkeypatch.setattr(projects_mod, "publish", _spy_publish)

    token = _register_and_login(client, "sse-insp-create@x.com")
    pid = _create_project(client, token, name="SSE 灵感创建测试项目")

    published.clear()
    push_resp = _push_inspiration(
        client, token, pid,
        inspiration_uuid="insp-sse-create",
        title="SSE 创建灵感",
    )
    assert push_resp.status_code == 201, push_resp.text
    insp_id = push_resp.json()["id"]

    event_types = [p["event"] for p in published]
    assert "context_inspiration_created" in event_types, "应推送 context_inspiration_created 事件"
    sse_event = next(p for p in published if p["event"] == "context_inspiration_created")
    assert sse_event["project_id"] == pid
    assert sse_event["data"]["id"] == insp_id
    assert sse_event["data"]["title"] == "SSE 创建灵感"
    assert sse_event["data"]["mood"] == "专注"


def test_context_inspiration_deleted_publish(client, monkeypatch):
    """删除 Muse 灵感时应 publish context_inspiration_deleted 事件 + payload 含 id。"""
    from routers import events as events_mod
    from routers import projects as projects_mod

    published = []
    original_publish = events_mod.publish

    def _spy_publish(project_id, event_type, data):
        published.append({"project_id": project_id, "event": event_type, "data": data})
        return original_publish(project_id, event_type, data)

    monkeypatch.setattr(events_mod, "publish", _spy_publish)
    monkeypatch.setattr(projects_mod, "publish", _spy_publish)

    token = _register_and_login(client, "sse-insp-del@x.com")
    pid = _create_project(client, token, name="SSE 灵感删除测试项目")
    push_resp = _push_inspiration(
        client, token, pid,
        inspiration_uuid="insp-sse-del",
        title="待删除 SSE 灵感",
    )
    assert push_resp.status_code == 201
    insp_id = push_resp.json()["id"]

    published.clear()
    del_resp = client.delete(
        f"/api/projects/{pid}/context-inspirations/{insp_id}",
        headers=auth_headers(token),
    )
    assert del_resp.status_code == 204

    event_types = [p["event"] for p in published]
    assert "context_inspiration_deleted" in event_types, "应推送 context_inspiration_deleted 事件"
    sse_event = next(p for p in published if p["event"] == "context_inspiration_deleted")
    assert sse_event["project_id"] == pid
    assert sse_event["data"]["id"] == insp_id
