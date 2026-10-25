# tests/test_bridge.py — Bridge MVP 接口与页面测试
# B-P2-5: 测试用 create_all 不走 init_db，种子/迁移逻辑需独立测试（见 test_seed_data）。
# 即测试路径（create_all）与生产路径（init_db 含迁移+种子）不等价，这是 MVP 阶段的已知分叉。
import os

# R3 class-level fix: 用 setdefault 而非 []=，遵循 conftest.py 约定（允许 CI/命令行覆盖）
os.environ.setdefault("BRIDGE_DATABASE_URL", "sqlite:///:memory:")
# P1-2: 测试环境显式开启 dev 模式，允许使用默认密码
os.environ.setdefault("BRIDGE_DEBUG", "1")

import pytest
from fastapi.testclient import TestClient

from auth import ADMIN_PASS, ADMIN_USER, CSRF_COOKIE_NAME, CSRF_TOKEN
from conftest import auth_headers as _auth_headers
from database import Base, engine
from main import app

client = TestClient(app)
# B-SEC-02: 预置 CSRF Cookie，使双重提交 Cookie 校验通过
client.cookies.set(CSRF_COOKIE_NAME, CSRF_TOKEN)

# 管理后台 API 请求所需的 Basic Auth 凭据
ADMIN_AUTH = (ADMIN_USER, ADMIN_PASS)
# M1: 管理员写端点所需的 CSRF 校验头（含 X-Requested-With 与 X-CSRF-Token）
CSRF_HEADERS = {"X-Requested-With": "BridgeAdmin", "X-CSRF-Token": CSRF_TOKEN}
# P1-13: 志愿者写端点使用独立的 CSRF 校验头
VOLUNTEER_CSRF_HEADERS = {"X-Requested-With": "BridgeVolunteer"}


def _create_project(name="测试项目", description="测试描述"):
    """辅助：创建并返回一个项目。"""
    res = client.post(
        "/api/projects",
        json={"name": name, "description": description},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_create_and_get_project():
    res = client.post(
        "/api/projects",
        json={"name": "社区老人关怀", "description": " test"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "社区老人关怀"
    assert data["description"] == "test"
    assert data["qr_token"]

    project_id = data["id"]
    # P0-SEC: 项目详情需认证
    assert client.get(f"/api/projects/{project_id}").status_code == 401
    detail = client.get(f"/api/projects/{project_id}", auth=ADMIN_AUTH).json()
    assert detail["id"] == project_id
    assert detail["tasks"] == []
    # 志愿者凭 qr_token 也可访问
    volunteer_detail = client.get(
        f"/api/projects/{project_id}?qr_token={data['qr_token']}",
        headers=VOLUNTEER_CSRF_HEADERS,
    ).json()
    assert volunteer_detail["id"] == project_id


def test_list_and_search_projects():
    # M6: 创建专属项目，断言基于该项目，不依赖其他测试
    client.post(
        "/api/projects",
        json={"name": "河流保护专属", "description": "unique-desc-12345"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )

    res = client.get("/api/projects?q=河流保护专属", auth=ADMIN_AUTH)
    assert res.status_code == 200
    results = res.json()
    assert len(results) == 1
    assert results[0]["name"] == "河流保护专属"

    # 搜索描述中的唯一字符串
    res = client.get("/api/projects?q=unique-desc-12345", auth=ADMIN_AUTH)
    assert len(res.json()) == 1


def test_list_projects_pagination():
    # M2: 分页参数
    for i in range(3):
        _create_project(name=f"分页项目{i}")

    res = client.get("/api/projects?skip=1&limit=1", auth=ADMIN_AUTH)
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1

    # limit 上限校验
    res = client.get("/api/projects?limit=200", auth=ADMIN_AUTH)
    assert res.status_code == 422


def test_task_lifecycle():
    project = _create_project(name="任务测试项目")
    pid = project["id"]

    task = client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": " 联系社区 ", "owner_name": "小王"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()
    assert task["title"] == "联系社区"
    assert task["owner_name"] == "小王"
    assert task["status"] == "todo"

    updated = client.patch(
        f"/api/tasks/{task['id']}",
        json={"status": "in_progress"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()
    assert updated["status"] == "in_progress"

    assert client.delete(
        f"/api/tasks/{task['id']}",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).status_code == 204
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404


def test_handoff_and_message():
    project = _create_project(name="交接测试项目")
    pid = project["id"]

    task = client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "采购物资"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()

    handoff = client.post(
        f"/api/projects/{pid}/handoffs",
        json={
            "summary": "第一批物资已到位",
            "outgoing_volunteer": "小李",
            "incoming_volunteer": "小张",
        },
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()
    assert handoff["summary"] == "第一批物资已到位"

    msg = client.post(
        f"/api/projects/{pid}/messages",
        json={"content": "收到，明天继续", "author": "小张", "task_id": task["id"]},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()
    assert msg["content"] == "收到，明天继续"


def test_volunteer_and_qr_pages():
    project = _create_project(name="H5 测试项目")
    token = project["qr_token"]

    res = client.get(f"/p/{token}")
    assert res.status_code == 200
    assert "志愿者项目视图" in res.text

    res = client.get(f"/qr/{token}")
    assert res.status_code == 200
    assert "data:image/png;base64," in res.text

    # P1-SEC: /qr/{token}/image 需有效 token，返回 PNG
    res = client.get(f"/qr/{token}/image")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content.startswith(b"\x89PNG")

    # 无效 token → 404
    assert client.get("/qr/not-a-real-token/image").status_code == 404


def test_invalid_inputs():
    res = client.post(
        "/api/projects",
        json={"name": ""},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422

    project = _create_project(name="校验测试")
    pid = project["id"]

    res = client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": ""},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422

    res = client.post(
        f"/api/projects/{pid}/messages",
        json={"content": ""},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422

    res = client.patch(
        f"/api/tasks/{pid}",
        json={"status": "unknown"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422


def test_not_found():
    assert client.get("/api/projects/99999").status_code == 404
    assert client.get("/p/not-a-real-token").status_code == 404


def test_xss_escape_in_html():
    payload = "<script>alert(1)</script>"
    project = client.post(
        "/api/projects",
        json={"name": payload, "description": payload},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()

    # 安全加固：admin 页面现在要求 Basic Auth，需携带凭据访问
    res = client.get(f"/admin/projects/{project['id']}", auth=ADMIN_AUTH)
    assert res.status_code == 200
    assert payload not in res.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in res.text


def test_csrf_protection():
    """M1: 管理员写端点缺少 X-Requested-With 头应返回 403。"""
    res = client.post(
        "/api/projects",
        json={"name": "无CSRF项目"},
        auth=ADMIN_AUTH,
    )
    assert res.status_code == 403


def test_project_patch():
    """L9: 项目 PATCH 端点。"""
    project = _create_project(name="原始名称", description="原始描述")
    pid = project["id"]

    res = client.patch(
        f"/api/projects/{pid}",
        json={"name": "更新名称", "description": "更新描述"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "更新名称"
    assert data["description"] == "更新描述"


def test_like_wildcard_escape():
    """M5: LIKE 通配符 % 和 _ 应被转义，不报错。"""
    _create_project(name="100%完成", description="test")
    # 搜索 % 不应匹配所有记录也不应报错
    res = client.get("/api/projects?q=%", auth=ADMIN_AUTH)
    assert res.status_code == 200
    # 搜索 _ 不应报错
    res = client.get("/api/projects?q=_", auth=ADMIN_AUTH)
    assert res.status_code == 200


def test_404_paths():
    """L10: 补充 404 路径覆盖。"""
    # PATCH /api/tasks/99999 → 404
    assert client.patch(
        "/api/tasks/99999",
        json={"status": "done"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).status_code == 404

    # DELETE /api/tasks/99999 → 404
    assert client.delete(
        "/api/tasks/99999",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).status_code == 404

    # DELETE /api/projects/99999 → 404
    assert client.delete(
        "/api/projects/99999",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).status_code == 404

    # POST /api/projects/99999/tasks → 404
    assert client.post(
        "/api/projects/99999/tasks",
        json={"title": "测试"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).status_code == 404

    # POST /api/projects/99999/handoffs → 404
    assert client.post(
        "/api/projects/99999/handoffs",
        json={"summary": "测试"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).status_code == 404

    # POST /api/projects/99999/messages → 404
    assert client.post(
        "/api/projects/99999/messages",
        json={"content": "测试"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).status_code == 404


# ---------- 第二批升级测试 ----------

def test_admin_pages_require_auth():
    """安全加固：admin 页面要求 Basic Auth，未认证返回 401。"""
    # 首页
    assert client.get("/").status_code == 401
    # 项目详情页
    project = _create_project(name="鉴权测试项目")
    assert client.get(f"/admin/projects/{project['id']}").status_code == 401
    # 携带凭据后正常访问
    assert client.get("/", auth=ADMIN_AUTH).status_code == 200
    assert client.get(f"/admin/projects/{project['id']}", auth=ADMIN_AUTH).status_code == 200


def test_project_status_default_and_filter():
    """项目状态字段：默认 active、可筛选。"""
    p1 = _create_project(name="状态筛选-进行中")
    p2 = _create_project(name="状态筛选-归档")
    # 把 p2 改为 archived
    client.patch(
        f"/api/projects/{p2['id']}",
        json={"status": "archived"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )

    # 默认创建为 active
    assert p1["status"] == "active"

    # 筛选 active
    res = client.get("/api/projects?status=active&q=状态筛选", auth=ADMIN_AUTH)
    assert res.status_code == 200
    active_list = res.json()
    assert all(p["status"] == "active" for p in active_list)
    assert any(p["id"] == p1["id"] for p in active_list)
    assert not any(p["id"] == p2["id"] for p in active_list)

    # 筛选 archived
    res = client.get("/api/projects?status=archived&q=状态筛选", auth=ADMIN_AUTH)
    assert res.status_code == 200
    archived_list = res.json()
    assert all(p["status"] == "archived" for p in archived_list)
    assert any(p["id"] == p2["id"] for p in archived_list)

    # 非法 status 返回 422
    assert client.get("/api/projects?status=invalid", auth=ADMIN_AUTH).status_code == 422


def test_project_create_with_status():
    """创建项目时可指定 status。"""
    res = client.post(
        "/api/projects",
        json={"name": "直接归档项目", "description": "", "status": "archived"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "archived"

    # 非法 status 返回 422
    res = client.post(
        "/api/projects",
        json={"name": "非法状态", "status": "unknown"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422


def test_volunteer_create_task_via_qr_token():
    """志愿者凭 qr_token 可创建任务。"""
    project = _create_project(name="志愿者建任务测试")
    pid = project["id"]
    token = project["qr_token"]

    # qr_token 创建任务（P1-3: 志愿者侧写端点也需要 X-Requested-With 头）
    res = client.post(
        f"/api/projects/{pid}/tasks?qr_token={token}",
        json={"title": "志愿者记录的待办", "description": "需要跟进", "owner_name": "志愿者小张"},
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 201
    task = res.json()
    assert task["title"] == "志愿者记录的待办"
    assert task["owner_name"] == "志愿者小张"
    assert task["status"] == "todo"

    # P1-3: qr_token 但缺 X-Requested-With → 403
    res = client.post(
        f"/api/projects/{pid}/tasks?qr_token={token}",
        json={"title": "缺 CSRF 头"},
    )
    assert res.status_code == 403

    # 错误的 qr_token → 403
    res = client.post(
        f"/api/projects/{pid}/tasks?qr_token=wrong-token",
        json={"title": "非法操作"},
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 403

    # DF-2: 无 qr_token 且无 admin auth → 401（先认证后 CSRF，避免泄露端点存在 CSRF 防护）
    res = client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "无权操作"},
    )
    assert res.status_code == 401

    # 带 CSRF 头但无 auth → 401
    res = client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "无权操作"},
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 401

    # admin 仍可创建
    res = client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "管理员创建"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201


# ---------- P0/P1 安全加固测试 ----------

def test_stats_requires_admin():
    """P0-1: /api/stats 必须管理员认证。"""
    # 未认证 → 401
    assert client.get("/api/stats").status_code == 401
    # 错误凭据 → 401
    assert client.get("/api/stats", auth=("admin", "wrong-pass")).status_code == 401
    # 正确凭据 → 200
    res = client.get("/api/stats", auth=ADMIN_AUTH)
    assert res.status_code == 200
    data = res.json()
    assert "projects" in data
    assert "tasks" in data


def test_stats_phase4_3_extended_metrics():
    """Phase 4.3: /api/stats 返回扩展指标（用户/SyncItem/Context/Agent/多维趋势）。

    覆盖所有新增字段：
    - users.total / users.active
    - sync.total / sync.by_app / sync.by_item_type / sync.active_users
    - context.snapshots_total / context.inspirations_total
    - agent.total / agent.by_type / agent.by_status
    - daily_projects / daily_messages / daily_snapshots / daily_inspirations
    """
    res = client.get("/api/stats", auth=ADMIN_AUTH)
    assert res.status_code == 200
    data = res.json()

    # 1. users 指标
    assert "users" in data
    assert "total" in data["users"]
    assert "active" in data["users"]
    assert isinstance(data["users"]["total"], int)
    assert isinstance(data["users"]["active"], int)
    assert data["users"]["active"] <= data["users"]["total"]

    # 2. sync 指标
    assert "sync" in data
    sync = data["sync"]
    assert "total" in sync
    assert "by_app" in sync
    assert sync["by_app"].keys() == {"focus", "muse"}
    assert "by_item_type" in sync
    assert sync["by_item_type"].keys() == {"snapshot", "inspiration", "mood"}
    assert "active_users" in sync

    # 3. context 指标
    assert "context" in data
    ctx = data["context"]
    assert "snapshots_total" in ctx
    assert "inspirations_total" in ctx

    # 4. agent 指标
    assert "agent" in data
    agent = data["agent"]
    assert "total" in agent
    assert "by_type" in agent
    assert agent["by_type"].keys() == {"stale_warning", "overdue_task", "handoff_gap", "daily_summary"}
    assert "by_status" in agent
    assert agent["by_status"].keys() == {"pending", "shown", "dismissed", "acted"}

    # 5. 多维趋势（每个 7 天）
    for key in ("daily_projects", "daily_messages", "daily_snapshots", "daily_inspirations"):
        assert key in data
        assert len(data[key]) == 7
        for item in data[key]:
            assert "date" in item
            assert "count" in item
            assert isinstance(item["count"], int)

    # 6. Phase 4.3: 志愿者活跃度热力图（近 30 天）
    assert "activity_heatmap" in data
    heatmap = data["activity_heatmap"]
    assert isinstance(heatmap, list)
    assert len(heatmap) == 30, f"热力图应为 30 天，实际 {len(heatmap)}"
    for cell in heatmap:
        assert "date" in cell
        assert "total" in cell
        assert "projects" in cell
        assert "tasks" in cell
        assert "messages" in cell
        assert "snapshots" in cell
        assert "inspirations" in cell
        assert isinstance(cell["total"], int)
        assert isinstance(cell["projects"], int)
        assert isinstance(cell["tasks"], int)
        assert isinstance(cell["messages"], int)
        assert isinstance(cell["snapshots"], int)
        assert isinstance(cell["inspirations"], int)
        # total 应等于 5 个分项之和
        assert cell["total"] == (
            cell["projects"] + cell["tasks"] + cell["messages"]
            + cell["snapshots"] + cell["inspirations"]
        )
    # 日期格式 YYYY-MM-DD
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", heatmap[0]["date"]), f"日期格式错误: {heatmap[0]['date']}"
    # 日期应按时间升序（最早 → 今日）
    assert heatmap[-1]["date"] > heatmap[0]["date"], "热力图日期未升序"


def test_stats_phase4_3_reflects_actual_data():
    """Phase 4.3: 推送 Focus 快照和 Muse 灵感后，stats 应反映对应计数。"""
    from datetime import datetime, timezone

    # 注册一个用户并推送 1 条 focus snapshot + 1 条 muse inspiration
    reg = client.post("/api/auth/register", json={
        "email": "phase43user@example.com",
        "password": "testpass123",
        "display_name": "Phase43 用户",
    })
    token = reg.json()["access_token"]
    auth_h = _auth_headers(token)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    client.post("/api/sync/push", json={"items": [{
        "app": "focus",
        "item_type": "snapshot",
        "client_uuid": "phase43-snap-1",
        "data": {"title": "Phase 4.3 测试快照", "tabs": []},
        "client_created_at": now_ms,
        "client_updated_at": now_ms,
    }]}, headers=auth_h)
    client.post("/api/sync/push", json={"items": [{
        "app": "muse",
        "item_type": "inspiration",
        "client_uuid": "phase43-insp-1",
        "data": {"title": "Phase 4.3 测试灵感", "url": "https://example.com"},
        "client_created_at": now_ms,
        "client_updated_at": now_ms,
    }]}, headers=auth_h)

    res = client.get("/api/stats", auth=ADMIN_AUTH)
    data = res.json()

    # sync.by_app 应反映推送数据
    assert data["sync"]["by_app"]["focus"] >= 1
    assert data["sync"]["by_app"]["muse"] >= 1
    # sync.by_item_type 应反映推送数据
    assert data["sync"]["by_item_type"]["snapshot"] >= 1
    assert data["sync"]["by_item_type"]["inspiration"] >= 1
    # 至少有 1 个活跃用户（推送数据的 owner）
    assert data["sync"]["active_users"] >= 1
    # 用户总数至少 1
    assert data["users"]["total"] >= 1


def test_stats_activity_heatmap_reflects_context_push():
    """Phase 4.3: 推送 context snapshot + inspiration 后，热力图今日格应反映活动。

    热力图聚合 ProjectContextSnapshot/ProjectContextInspiration 表（不是 SyncItem），
    故需通过 /api/projects/{pid}/context-snapshots 和 /context-inspirations 端点推送。

    防flake设计：用 today_iso 锚定匹配 cell 而非 heatmap[-1]，避免 UTC 午夜跨越时
    推送时刻（T0）与 stats 查询时刻（T1）落在不同 UTC 日导致 heatmap[-1] 不是今日格。
    """
    from datetime import datetime, timezone

    project = _create_project(name="热力图测试项目")
    pid = project["id"]
    # 记录推送时刻的 UTC 日期，用于事后锚定热力图格子（避免 heatmap[-1] 假定）
    push_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # 推送 1 条 context snapshot + 1 条 context inspiration
    snap_res = client.post(
        f"/api/projects/{pid}/context-snapshots",
        json={
            "snapshot_uuid": "heat-snap-1",
            "title": "热力图测试快照",
            "summary": "",
            "urls": [],
            "tab_count": 0,
            "created_at": now_ms,
        },
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert snap_res.status_code == 201, snap_res.text

    insp_res = client.post(
        f"/api/projects/{pid}/context-inspirations",
        json={
            "inspiration_uuid": "heat-insp-1",
            "title": "热力图测试灵感",
            "url": "https://example.com/heat",
            "note": "",
            "mood": "",
            "created_at": now_ms,
        },
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert insp_res.status_code == 201, insp_res.text

    res = client.get("/api/stats", auth=ADMIN_AUTH)
    data = res.json()

    heatmap = data["activity_heatmap"]
    # 防flake：用 push_iso 锚定匹配 cell，而非假定 heatmap[-1] 是今日
    # （若 UTC 午夜跨越推送与查询，heatmap[-1] 可能是次日格，导致断言失败）
    matched = [c for c in heatmap if c["date"] == push_iso]
    assert matched, f"热力图中找不到推送日期 {push_iso} 的格子（可能 UTC 午夜跨越）"
    target_cell = matched[0]
    # 推送格应反映：1 个项目创建 + 1 个 snapshot + 1 个 inspiration = total ≥ 3
    assert target_cell["projects"] >= 1, f"{push_iso} 热力图项目数应 ≥1，实际 {target_cell['projects']}"
    assert target_cell["snapshots"] >= 1, f"{push_iso} 热力图快照数应 ≥1，实际 {target_cell['snapshots']}"
    assert target_cell["inspirations"] >= 1, f"{push_iso} 热力图灵感数应 ≥1，实际 {target_cell['inspirations']}"
    assert target_cell["total"] >= 3, f"{push_iso} 热力图总数应 ≥3，实际 {target_cell['total']}"


def test_admin_stats_page_renders_phase4_3_sections():
    """Phase 4.3: /admin/stats 页面渲染应包含所有新增的 KPI 卡片和图表区段。

    覆盖模板渲染路径（Jinja2 + SVG），确保新增字段在 HTML 中可见。
    预置 1 个项目 + 1 个任务触发 daily_tasks 趋势有数据，验证多折线图完整渲染。
    Phase 6.2 修复：CSS 抽取到外部 stats.css 后，chart-label 仅在 d.count > 0 时
    作为 <text class="chart-label"> 渲染，必须创建 task 让 daily_tasks 有数据。
    """
    # 预置 1 个项目 + 1 个无 owner_name 的任务：
    # - daily_tasks 有数据 → 触发 chart-label 条件渲染（d.count > 0）
    # - top_owners 为空 → 触发 chart-line-2-fill 空榜插画渲染（owner_name 过滤）
    # 两者渲染条件互斥，仅当 task 存在但 owner_name 为空时同时满足
    project = _create_project(name="Phase 4.3 渲染测试项目")
    client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "渲染测试任务"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )

    res = client.get("/admin/stats", auth=ADMIN_AUTH)
    assert res.status_code == 200
    body = res.text

    # 新增 KPI 卡片标签
    for label in ("注册用户", "云端同步项", "Focus 快照", "Muse 灵感", "Agent 建议"):
        assert label in body, f"KPI 卡片 [{label}] 未渲染"

    # 新增图表标题
    for title in ("跨产品活动趋势对比", "按产品分布", "按类型分布", "Agent 建议 · 按类型", "志愿者活跃度热力图"):
        assert title in body, f"图表标题 [{title}] 未渲染"

    # SVG 多折线图元素（有数据时渲染）
    assert "multi-line-chart" in body, "多折线图容器未渲染"
    assert "line-legend" in body, "折线图图例未渲染"
    assert "line-path" in body, "折线 SVG path 未渲染"

    # Phase 4.3: 热力图 DOM 元素（30 格 CSS Grid + 5 级色阶 class + 图例）
    assert "heatmap-grid" in body, "热力图网格容器未渲染"
    assert "heatmap-cell" in body, "热力图单元格未渲染"
    assert "heatmap-legend" in body, "热力图图例未渲染"
    # 至少应出现 h-0（今日或空白天）和有数据的 h-1+ 色阶（创建项目后今日格应有活动）
    assert "h-0" in body or "h-1" in body or "h-2" in body or "h-3" in body or "h-4" in body, "热力图色阶 class 未渲染"
    # 30 个热力图格子（每格 1 天）。精确匹配 'class="heatmap-cell h-' 前缀，
    # 避免把 CSS 规则（.heatmap-cell { ... }）误计入字符串命中数。
    cell_count = body.count('class="heatmap-cell h-')
    assert cell_count == 30, f"热力图格子数应为 30，实际 {cell_count}"
    # 今日高亮标记
    assert "today" in body, "热力图今日高亮未渲染"

    # visually-hidden 表格（无障碍）应包含新字段
    assert "近 7 天跨产品活动趋势数据" in body, "跨产品趋势数据表未渲染"
    assert "SyncItem 按产品分布数据" in body, "SyncItem 产品分布数据表未渲染"
    assert "SyncItem 按类型分布数据" in body, "SyncItem 类型分布数据表未渲染"
    assert "Agent 建议按类型分布数据" in body, "Agent 建议类型分布数据表未渲染"
    assert "近 30 天志愿者活跃度热力图数据" in body, "热力图无障碍数据表未渲染"

    # P1-DOG-I 回归断言：stats.html SVG 图表应使用 chart-* CSS class 而非硬编码颜色
    # 检查 5 条折线（chart-line-2 ~ chart-line-6）+ 主图 chart-line-1 + 数据点 chart-dot-N
    # + 网格线 chart-grid-line + 坐标轴文字 chart-axis-text + 区域填充 chart-area
    # + 空榜插画 path fill（chart-line-2-fill / chart-line-3-fill）（DRL R1b P3-F1 修复）
    for cls in (
        "chart-grid-line", "chart-axis-text", "chart-area", "chart-label",
        "chart-line-1", "chart-dot-1",
        "chart-line-2", "chart-dot-2", "chart-line-2-bg", "chart-line-2-fill",
        "chart-line-3", "chart-dot-3", "chart-line-3-fill",
        "chart-line-4", "chart-dot-4", "chart-line-4-bg",
        "chart-line-5", "chart-dot-5", "chart-line-5-bg",
        "chart-line-6", "chart-dot-6", "chart-line-6-bg",
    ):
        assert cls in body, f"P1-DOG-I: chart-* class [{cls}] 未渲染"
    # 反向断言：不应残留硬编码颜色（line/dot/circle/text 元素的 fill=/stroke= 属性）
    import re
    hardcoded = re.findall(r'<(?:line|circle|polyline|polygon|path|text)\b[^>]*\b(?:fill|stroke)="#', body)
    assert not hardcoded, f"P1-DOG-I: 仍有 SVG 元素使用硬编码颜色: {hardcoded[:3]}"


def test_project_endpoints_require_auth_and_do_not_leak_qr_token():
    """P0-2: 项目读写端点需认证，且响应中不暴露 qr_token。"""
    project = _create_project(name="端点泄露测试")
    pid = project["id"]

    # 未认证访问列表 → 401
    assert client.get("/api/projects?q=端点泄露测试").status_code == 401
    # 未认证访问详情 → 401
    assert client.get(f"/api/projects/{pid}").status_code == 401

    # 管理员认证后，列表端点不含 qr_token
    res = client.get("/api/projects?q=端点泄露测试", auth=ADMIN_AUTH)
    assert res.status_code == 200
    results = res.json()
    assert len(results) == 1
    assert "qr_token" not in results[0]
    assert results[0]["id"] == pid

    # 管理员认证后，详情端点不含 qr_token
    res = client.get(f"/api/projects/{pid}", auth=ADMIN_AUTH)
    assert res.status_code == 200
    detail = res.json()
    assert "qr_token" not in detail
    assert detail["id"] == pid
    assert "tasks" in detail
    assert "handoffs" in detail
    assert "messages" in detail

    # 管理员创建端点仍返回 qr_token（供管理员分发）
    assert "qr_token" in project


def test_sse_requires_auth():
    """P0-3: SSE 端点必须鉴权。

    仅测试鉴权失败场景（返回普通 JSON 错误）。
    成功的 SSE 是无限流，TestClient 无法直接读取（会阻塞），
    但鉴权逻辑在 StreamingResponse 创建之前执行，失败时抛 HTTPException。
    """
    project = _create_project(name="SSE 鉴权测试")
    pid = project["id"]

    # 未认证 → 401（DRL-R1-V8: SSE 端点先校验 Origin 头，需传入匹配 Host 的 Origin）
    SSE_HEADERS = {"Origin": "http://testserver"}
    res = client.get(f"/api/projects/{pid}/events", headers=SSE_HEADERS)
    assert res.status_code == 401
    assert "需要认证" in res.json()["detail"]

    # 错误 qr_token → 403
    res = client.get(f"/api/projects/{pid}/events?qr_token=wrong-token", headers=SSE_HEADERS)
    assert res.status_code == 403
    assert "无权操作" in res.json()["detail"]

    # 错误的 Basic Auth 凭据 → 401
    res = client.get(f"/api/projects/{pid}/events", auth=("admin", "wrong-pass"), headers=SSE_HEADERS)
    assert res.status_code == 401

    # 不存在的项目 → 404
    assert client.get("/api/projects/99999/events", headers=SSE_HEADERS).status_code == 404


def test_confirm_handoff_rejects_duplicate():
    """P1-8: 已确认的交接不允许重复确认，返回 409。"""
    project = _create_project(name="重复确认测试")
    pid = project["id"]
    token = project["qr_token"]

    # 创建交接（管理员）
    handoff = client.post(
        f"/api/projects/{pid}/handoffs",
        json={"summary": "第一批物资已到位", "outgoing_volunteer": "小李", "incoming_volunteer": "小张"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()
    hid = handoff["id"]

    # 首次确认 → 200
    res = client.post(
        f"/api/projects/{pid}/handoffs/{hid}/confirm?qr_token={token}",
        json={"confirmed_by": "小张"},
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "confirmed"

    # 重复确认 → 409
    res = client.post(
        f"/api/projects/{pid}/handoffs/{hid}/confirm?qr_token={token}",
        json={"confirmed_by": "小张"},
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 409


def test_volunteer_message_and_confirm_require_csrf_header():
    """P1-3: 志愿者侧 message/confirm 端点也需要 X-Requested-With 头。"""
    project = _create_project(name="志愿者 CSRF 测试")
    pid = project["id"]
    token = project["qr_token"]

    handoff = client.post(
        f"/api/projects/{pid}/handoffs",
        json={"summary": "交接内容"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()

    # message 缺 CSRF 头 → 403
    res = client.post(
        f"/api/projects/{pid}/messages?qr_token={token}",
        json={"content": "测试留言"},
    )
    assert res.status_code == 403

    # message 带 CSRF 头 → 201
    res = client.post(
        f"/api/projects/{pid}/messages?qr_token={token}",
        json={"content": "测试留言"},
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 201

    # confirm 缺 CSRF 头 → 403
    res = client.post(
        f"/api/projects/{pid}/handoffs/{handoff['id']}/confirm?qr_token={token}",
        json={"confirmed_by": "小张"},
    )
    assert res.status_code == 403

    # task update 缺 CSRF 头 → 403
    task = client.post(
        f"/api/projects/{pid}/tasks?qr_token={token}",
        json={"title": "测试任务"},
        headers=VOLUNTEER_CSRF_HEADERS,
    ).json()
    res = client.patch(
        f"/api/tasks/{task['id']}?qr_token={token}",
        json={"status": "done"},
    )
    assert res.status_code == 403

    # task update 带 CSRF 头 → 200
    res = client.patch(
        f"/api/tasks/{task['id']}?qr_token={token}",
        json={"status": "done"},
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 200


# ---------- import / export / auto-generate 核心功能测试 ----------

def test_export_project_structure():
    """P0: 导出 JSON 结构完整，含 tasks/handoffs/messages 且 task_index 正确。"""
    project = _create_project(name="导出测试项目", description="导出用")
    pid = project["id"]

    # 建两个任务 + 一条交接 + 两条留言（其中一条关联任务）
    t1 = client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "任务一", "owner_name": "小王"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()
    t2 = client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "任务二", "owner_name": "小李"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()
    # TaskCreate 不接受 status 字段，用 PATCH 更新
    client.patch(
        f"/api/tasks/{t2['id']}",
        json={"status": "done"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    client.post(
        f"/api/projects/{pid}/handoffs",
        json={"summary": "首批交接", "outgoing_volunteer": "老张", "incoming_volunteer": "小王"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    client.post(
        f"/api/projects/{pid}/messages",
        json={"content": "关联任务一", "author": "小王", "task_id": t1["id"]},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    client.post(
        f"/api/projects/{pid}/messages",
        json={"content": "普通留言", "author": "小李"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )

    res = client.get(f"/api/projects/{pid}/export", auth=ADMIN_AUTH)
    assert res.status_code == 200
    data = res.json()

    assert data["name"] == "导出测试项目"
    assert data["description"] == "导出用"
    assert "exported_at" in data
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["title"] == "任务一"
    assert data["tasks"][1]["status"] == "done"
    assert len(data["handoffs"]) == 1
    assert data["handoffs"][0]["summary"] == "首批交接"
    assert len(data["messages"]) == 2
    # 第一条留言关联任务 0（t1），第二条无关联
    msg_indexes = [m["task_index"] for m in data["messages"]]
    assert 0 in msg_indexes
    assert None in msg_indexes

    # 导出不包含 qr_token
    assert "qr_token" not in data

    # 未认证 → 401
    assert client.get(f"/api/projects/{pid}/export").status_code == 401
    # 不存在的项目 → 404
    assert client.get("/api/projects/99999/export", auth=ADMIN_AUTH).status_code == 404


def test_import_project_round_trip():
    """P0: 导入 JSON 重建项目，task_index 关联正确，再导出可还原。"""
    payload = {
        "name": "导入测试项目",
        "description": "验证导入",
        "tasks": [
            {"title": "任务A", "owner_name": "小张", "status": "todo"},
            {"title": "任务B", "owner_name": "小王", "status": "done"},
        ],
        "handoffs": [
            {"summary": "初始交接", "outgoing_volunteer": "老李", "incoming_volunteer": "小张", "status": "confirmed"},
        ],
        "messages": [
            {"author": "小张", "content": "关联任务A", "task_index": 0},
            {"author": "匿名", "content": "无关联留言", "task_index": None},
        ],
    }

    res = client.post(
        "/api/projects/import",
        json=payload,
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    imported = res.json()
    pid = imported["id"]

    # 验证关联：详情中第一条留言的 task_id 指向任务A
    detail = client.get(f"/api/projects/{pid}", auth=ADMIN_AUTH).json()
    assert len(detail["tasks"]) == 2
    assert detail["tasks"][0]["title"] == "任务A"
    assert len(detail["handoffs"]) == 1
    assert detail["handoffs"][0]["status"] == "confirmed"
    assert len(detail["messages"]) == 2
    # 找到关联任务的留言
    linked = [m for m in detail["messages"] if m.get("task_id")]
    assert len(linked) == 1
    assert linked[0]["task_id"] == detail["tasks"][0]["id"]

    # 再次导出，结构应与原始 payload 一致
    exported = client.get(f"/api/projects/{pid}/export", auth=ADMIN_AUTH).json()
    assert exported["name"] == payload["name"]
    assert len(exported["tasks"]) == 2
    assert len(exported["handoffs"]) == 1
    assert len(exported["messages"]) == 2
    # task_index 往返一致
    linked_msg = [m for m in exported["messages"] if m["task_index"] is not None][0]
    assert linked_msg["task_index"] == 0

    # 未认证 → 401
    assert client.post("/api/projects/import", json=payload).status_code == 401
    # 缺 CSRF → 403
    assert client.post("/api/projects/import", json=payload, auth=ADMIN_AUTH).status_code == 403


def test_import_rejects_invalid_task_index():
    """P0: task_index 越界应返回 422 并回滚事务。"""
    payload = {
        "name": "越界测试",
        "tasks": [{"title": "仅一个任务"}],
        "handoffs": [],
        "messages": [{"content": "关联不存在的任务", "task_index": 99}],
    }

    res = client.post(
        "/api/projects/import",
        json=payload,
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422
    assert "task_index" in res.json()["detail"]
    # 事务回滚：项目不应存在
    assert client.get("/api/projects?q=越界测试", auth=ADMIN_AUTH).json() == []


def test_auto_generate_handoff():
    """P0: 自动生成交接摘要，包含未完成任务数和近 7 天留言。"""
    project = _create_project(name="自动交接测试", description="AI 摘要")
    pid = project["id"]

    # 建两个任务（一个待办、一个已完成）+ 一条留言
    t1 = client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "待办任务", "owner_name": "小张"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()
    t2 = client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "已完成任务", "owner_name": "小王"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()
    # TaskCreate 不接受 status，用 PATCH 标记完成
    client.patch(
        f"/api/tasks/{t2['id']}",
        json={"status": "done"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    client.post(
        f"/api/projects/{pid}/messages",
        json={"content": "项目进展顺利", "author": "小张"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )

    # 不带请求体 → 201，使用默认空字符串
    res = client.post(
        f"/api/projects/{pid}/handoffs/auto-generate",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    handoff = res.json()
    assert handoff["status"] == "draft"
    assert "待办任务" in handoff["summary"]
    assert "1/2" in handoff["summary"]  # 1 个已完成 / 共 2 个

    # 带请求体 → 201，记录交接人
    res = client.post(
        f"/api/projects/{pid}/handoffs/auto-generate",
        json={"outgoing_volunteer": "老张", "incoming_volunteer": "小王"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    handoff2 = res.json()
    assert handoff2["outgoing_volunteer"] == "老张"
    assert handoff2["incoming_volunteer"] == "小王"

    # 未认证 → 401
    assert client.post(f"/api/projects/{pid}/handoffs/auto-generate").status_code == 401
    # 不存在的项目 → 404
    assert client.post(
        "/api/projects/99999/handoffs/auto-generate",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).status_code == 404


def test_ai_status_endpoint():
    """P0-AI: /api/ai-status 返回 AI 配置状态（不泄露凭据）。"""
    res = client.get("/api/ai-status")
    assert res.status_code == 200
    data = res.json()
    assert "ai_enabled" in data
    assert isinstance(data["ai_enabled"], bool)
    # 测试环境未配置 AI → False
    assert data["ai_enabled"] is False


def test_auto_generate_falls_back_to_rule_engine_without_ai():
    """P0-AI: 未配置 AI 时，auto-generate 降级到规则引擎，摘要含结构化标记。"""
    project = _create_project(name="无AI降级测试")
    pid = project["id"]
    client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "待跟进任务", "owner_name": "小张"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )

    res = client.post(
        f"/api/projects/{pid}/handoffs/auto-generate",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    summary = res.json()["summary"]
    # 规则引擎输出含【自动生成于】标记
    assert "【自动生成于" in summary
    assert "待跟进任务" in summary


def test_prompt_template_selection():
    """Prompt 模板化：关键词自动检测模板选择。"""
    from ai import detect_template, PROMPT_TEMPLATES

    # 4 个基础模板 + 4 个 Agent 主动建议模板（阶段2.2）均已定义且含 role/focus/sections 三要素
    assert set(PROMPT_TEMPLATES.keys()) == {
        "volunteer", "handover", "delivery", "default",
        "proactive_stale", "proactive_overdue", "proactive_handoff_gap", "daily_summary",
    }
    for name, tpl in PROMPT_TEMPLATES.items():
        assert tpl["role"], f"{name} 缺 role"
        assert tpl["focus"], f"{name} 缺 focus"
        assert tpl["sections"] and len(tpl["sections"]) >= 1, f"{name} 缺 sections"

    # volunteer 关键词：招募 / 志愿者
    assert detect_template({"name": "志愿者招募计划", "description": ""}) == "volunteer"
    assert detect_template({"name": "项目", "description": "需要招募人手"}) == "volunteer"

    # handover 关键词：交接 / 换班 / 离职
    assert detect_template({"name": "交接班记录", "description": ""}) == "handover"
    assert detect_template({"name": "项目", "description": "负责人离职"}) == "handover"

    # delivery 关键词：交付 / 验收 / 结项
    assert detect_template({"name": "成果交付", "description": ""}) == "delivery"
    assert detect_template({"name": "项目", "description": "等待验收"}) == "delivery"

    # 无关键词 → default
    assert detect_template({"name": "普通项目", "description": "日常工作"}) == "default"

    # 关键词出现在任务标题 / 留言内容中也能识别
    assert detect_template({
        "name": "项目", "description": "",
        "tasks": [{"title": "招募志愿者"}],
    }) == "volunteer"
    assert detect_template({
        "name": "项目", "description": "",
        "messages": [{"content": "下周开始换班"}],
    }) == "handover"


def test_prompt_template_manual_override():
    """Prompt 模板化：手动指定 template_name 覆盖自动检测。"""
    from ai import build_prompt, detect_template, PROMPT_TEMPLATES

    # 项目数据含「招募」关键词 → 自动检测为 volunteer
    project_data = {"name": "志愿者招募", "description": "需要招募新成员", "tasks": [], "messages": []}
    assert detect_template(project_data) == "volunteer"

    # 手动指定 delivery：build_prompt 应使用 delivery 而非 volunteer
    context = {"project_name": "志愿者招募", "total_tasks": 5, "done_tasks": 2}
    prompt = build_prompt("delivery", context)
    assert PROMPT_TEMPLATES["delivery"]["role"] in prompt
    for section in PROMPT_TEMPLATES["delivery"]["sections"]:
        assert section in prompt
    # 不应包含 volunteer 模板的 role（证明覆盖生效）
    assert PROMPT_TEMPLATES["volunteer"]["role"] not in prompt

    # 未识别的模板名 → 回退到 default
    prompt_default = build_prompt("unknown_template", context)
    assert PROMPT_TEMPLATES["default"]["role"] in prompt_default

    # 端点层：手动指定 template_name 应被接受；不传时向后兼容
    project = _create_project(name="手动模板测试项目", description="AI 摘要")
    pid = project["id"]

    # 显式指定 delivery 模板 → 201
    res = client.post(
        f"/api/projects/{pid}/handoffs/auto-generate",
        json={"template_name": "delivery"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201

    # 非法 template_name → 422
    res = client.post(
        f"/api/projects/{pid}/handoffs/auto-generate",
        json={"template_name": "not-a-real-template"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422

    # 不传 template_name（向后兼容）→ 201
    res = client.post(
        f"/api/projects/{pid}/handoffs/auto-generate",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201


# ---------- 内容审核 + 管理员软删除测试 ----------

def test_moderation_detects_spam():
    """留言含敏感词（"加微信"）时 flagged=True，但不阻止提交。"""
    project = _create_project(name="审核-垃圾词测试")
    pid = project["id"]

    res = client.post(
        f"/api/projects/{pid}/messages",
        json={"content": "快加微信领取免费资料", "author": "测试员"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["moderation_flagged"] is True
    assert data["moderation_warning"]  # 非空提示
    assert "加微信" in data["moderation_warning"]
    # 内容仍然保存（不阻止提交）
    assert data["content"] == "快加微信领取免费资料"


def test_moderation_allows_clean_text():
    """正常留言 flagged=False，warning 为 None。"""
    project = _create_project(name="审核-正常文本测试")
    pid = project["id"]

    res = client.post(
        f"/api/projects/{pid}/messages",
        json={"content": "项目进展顺利，明天继续跟进", "author": "小张"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["moderation_flagged"] is False
    assert data["moderation_warning"] is None


def test_message_soft_delete():
    """管理员软删除留言：is_deleted=True，志愿者视图不显示。"""
    project = _create_project(name="软删除测试项目")
    pid = project["id"]
    token = project["qr_token"]

    # 管理员创建一条留言
    msg = client.post(
        f"/api/projects/{pid}/messages",
        json={"content": "需要删除的留言", "author": "管理员"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()
    msg_id = msg["id"]
    assert msg["is_deleted"] is False

    # 管理员软删除
    res = client.delete(
        f"/api/projects/{pid}/messages/{msg_id}",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 204

    # 管理员视图：仍能看到，但 is_deleted=True
    detail = client.get(f"/api/projects/{pid}", auth=ADMIN_AUTH).json()
    deleted_msgs = [m for m in detail["messages"] if m["id"] == msg_id]
    assert len(deleted_msgs) == 1
    assert deleted_msgs[0]["is_deleted"] is True

    # 志愿者视图：不显示已删除留言
    vol_detail = client.get(
        f"/api/projects/{pid}?qr_token={token}",
        headers=VOLUNTEER_CSRF_HEADERS,
    ).json()
    assert all(m["id"] != msg_id for m in vol_detail["messages"])

    # 重复软删除应仍返回 204（幂等）
    res = client.delete(
        f"/api/projects/{pid}/messages/{msg_id}",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 204

    # 志愿者不能软删除（无管理员凭据）→ 401
    res = client.delete(
        f"/api/projects/{pid}/messages/{msg_id}",
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 401

    # 不存在的 message_id → 404
    res = client.delete(
        f"/api/projects/{pid}/messages/99999",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 404


# W087b: 数据库备份管理测试
def test_backup_endpoints_require_auth():
    """备份管理端点未认证返回 401。"""
    assert client.get("/api/backups").status_code == 401
    assert client.post("/api/backups").status_code == 401
    assert client.post("/api/backups/foo.db/restore").status_code == 401


def _backup_sandbox(tmp_path, monkeypatch):
    """把 backup 模块指向临时沙箱，使测试不依赖仓库内是否残留 bridge_mvp.db。

    开源发布改造（2026-10-25）：bridge_mvp.db 属于运行时数据，已被 .gitignore 排除，
    全新 checkout 里并不存在。原测试依赖该文件存在，在 CI 上必然失败。
    """
    import sqlite3

    import sqlalchemy

    import backup

    db_path = tmp_path / "bridge_mvp.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE sandbox (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO sandbox (v) VALUES ('ok')")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(backup, "DB_PATH", db_path)
    monkeypatch.setattr(backup, "BACKUP_DIR", backup_dir)
    eng = sqlalchemy.create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(backup, "engine", eng)
    return {"db": db_path, "backups": backup_dir}


def test_backup_create_and_list(tmp_path, monkeypatch):
    """管理员可手动触发备份并列出备份列表。"""
    _backup_sandbox(tmp_path, monkeypatch)

    # 触发备份
    res = client.post("/api/backups", auth=ADMIN_AUTH, headers=CSRF_HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert "backup" in data
    assert data["backup"].startswith("bridge_mvp_")
    assert data["size"] > 0

    # 列出备份
    res = client.get("/api/backups", auth=ADMIN_AUTH)
    assert res.status_code == 200
    backups = res.json()["backups"]
    assert len(backups) >= 1
    assert all("name" in b and "size" in b and "mtime" in b for b in backups)


def test_backup_restore_rejects_invalid_name():
    """恢复端点拒绝不在备份列表中的文件名（防路径遍历）。"""
    res = client.post(
        "/api/backups/../../etc/passwd/restore",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    # 无效文件名应返回 400（而非 404，因为路径遍历被 normalize 后不匹配任何备份）
    assert res.status_code in (400, 404)


# W088a: qr_token 权限隔离测试——验证跨项目不可访问
def test_qr_token_project_isolation():
    """志愿者凭项目 A 的 qr_token 不能访问项目 B 的数据。"""
    project_a = _create_project(name="项目A")
    project_b = _create_project(name="项目B")
    token_a = project_a["qr_token"]
    token_b = project_b["qr_token"]
    pid_b = project_b["id"]

    # 1. 用项目 A 的 token 访问项目 B 的详情 → 403
    res = client.get(
        f"/api/projects/{pid_b}?qr_token={token_a}",
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 403

    # 2. 用项目 A 的 token 在项目 B 下创建任务 → 403
    res = client.post(
        f"/api/projects/{pid_b}/tasks?qr_token={token_a}",
        json={"title": "越权任务"},
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 403

    # 3. 用项目 B 的 token 访问项目 B 的详情 → 200（对照组）
    res = client.get(
        f"/api/projects/{pid_b}?qr_token={token_b}",
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["id"] == pid_b

    # 4. token 不匹配（随机字符串）→ 403
    res = client.get(
        f"/api/projects/{pid_b}?qr_token=invalid_random_token_xxx",
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 403


# W088c: qr_token 失败限流测试——防枚举扫描
def test_qr_token_fail_rate_limit():
    """连续 5 次 token 校验失败后，第 6 次返回 429（临时封禁）。"""
    # DRL-R1-V17: 限流存储已提取到 routers._token_rate_limit 共享模块
    from routers._token_rate_limit import _qr_token_fail_store

    # 重置限流存储，避免受前面测试影响
    _qr_token_fail_store.clear()

    project = _create_project(name="限流测试项目")
    pid = project["id"]

    # 连续 5 次用错误 token 访问 → 全部 403
    for i in range(5):
        res = client.get(
            f"/api/projects/{pid}?qr_token=wrong_token_{i}",
            headers=VOLUNTEER_CSRF_HEADERS,
        )
        assert res.status_code == 403, f"第 {i+1} 次失败应返回 403，实际 {res.status_code}"

    # 第 6 次 → 429（已被临时封禁）
    res = client.get(
        f"/api/projects/{pid}?qr_token=wrong_token_6",
        headers=VOLUNTEER_CSRF_HEADERS,
    )
    assert res.status_code == 429

    # 清理限流存储，避免影响后续测试
    _qr_token_fail_store.clear()


# W091b: Bridge 并发场景测试——两个志愿者同时更新同一任务
def test_concurrent_task_update_no_corruption(tmp_path):
    """并发更新同一任务——WAL 模式下不应数据损坏，最后写入获胜。

    设计决策（为什么用数据库层而非 API 层）：
    1. :memory: SQLite 用 StaticPool（单连接），多线程访问同一连接抛 InterfaceError；
       无法用 TestClient + :memory: 测试真正的 API 层并发。
    2. WAL 模式只对文件数据库生效，:memory: 无 WAL——要验证 WAL 并发必须用文件数据库。
    3. API 层并发最终落到数据库层，数据库层并发测试更贴近 SQLite 并发的本质。
    4. 用 tmp_path 临时文件数据库，测试结束自动清理。

    断言：5 线程并发更新同一任务 → 无异常 + 至少 1 个成功 + 最终值一致 + 完整性 OK。
    """
    import threading
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from models import Base, Project, Task

    # 1. 创建临时文件数据库 + 启用 WAL（模拟生产环境）
    db_path = tmp_path / "concurrent_test.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with test_engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=5000"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # 2. 创建项目和任务
    session = TestSession()
    project = Project(name="并发测试项目", description="", qr_token="concurrent_test_token")
    session.add(project)
    session.commit()
    session.refresh(project)
    task = Task(project_id=project.id, title="并发任务", status="todo", owner_name="")
    session.add(task)
    session.commit()
    session.refresh(task)
    task_id = task.id
    session.close()

    # 3. 并发更新：5 个线程同时更新同一任务（改标题）
    results = []
    errors = []
    lock = threading.Lock()

    def update_task(idx):
        s = TestSession()
        try:
            t = s.query(Task).filter(Task.id == task_id).first()
            if t:
                t.title = f"并发更新_{idx}"
                s.commit()
            with lock:
                results.append((idx, "ok"))
        except Exception as e:
            with lock:
                errors.append((idx, str(e)))
            try:
                s.rollback()
            except Exception:
                pass
        finally:
            s.close()

    threads = [threading.Thread(target=update_task, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # 4. 断言：无异常（WAL + busy_timeout=5s 应串行化 5 个并发写）
    assert len(errors) == 0, f"并发更新产生异常: {errors}"

    # 5. 断言：至少 1 个成功
    success_count = sum(1 for _, status in results if status == "ok")
    assert success_count >= 1, f"至少 1 个更新应成功，实际结果: {results}"

    # 6. 断言：任务最终标题是某个并发更新的值（最后写入获胜）
    session = TestSession()
    final_task = session.query(Task).filter(Task.id == task_id).first()
    session.close()
    assert final_task.title in [f"并发更新_{i}" for i in range(5)], \
        f"最终标题应为某个并发更新值，实际: {final_task.title}"

    # 7. 断言：数据库完整性未损坏（WAL 模式下并发写不应破坏文件）
    with test_engine.connect() as conn:
        integrity = conn.execute(text("PRAGMA integrity_check")).fetchone()[0]
    assert integrity == "ok", f"并发写后数据库完整性检查应通过，实际: {integrity}"

    test_engine.dispose()


# 乐观锁：version 字段 + 冲突检测，解决并发更新覆盖风险
def test_optimistic_lock_conflict():
    """乐观锁冲突检测：客户端 B 用过期 version 更新应返回 409，重读后更新成功。

    场景模拟：
    1. 客户端 A 和 B 同时读取任务（version=1）
    2. 客户端 A 更新成功（version→2）
    3. 客户端 B 用旧 version=1 更新 → 409 Conflict + 当前 version
    4. 客户端 B 重新读取（version=2）后更新成功（version→3）
    5. 顺便验证：不传 version 时保持向后兼容（不冲突，直接更新 + version 自增）
    """
    project = _create_project(name="乐观锁测试项目")
    pid = project["id"]

    # 创建任务
    task = client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "原始任务", "owner_name": "小王"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    ).json()
    task_id = task["id"]

    # 1. 两个客户端同时读取任务（version=1）
    client_a_view = client.get(f"/api/tasks/{task_id}", auth=ADMIN_AUTH).json()
    client_b_view = client.get(f"/api/tasks/{task_id}", auth=ADMIN_AUTH).json()
    assert client_a_view["version"] == 1
    assert client_b_view["version"] == 1

    # 2. 客户端 A 更新成功（version→2）
    res_a = client.patch(
        f"/api/tasks/{task_id}",
        json={"title": "A 改的标题", "version": client_a_view["version"]},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res_a.status_code == 200, res_a.text
    updated_a = res_a.json()
    assert updated_a["title"] == "A 改的标题"
    assert updated_a["version"] == 2, "A 更新成功后 version 应自增到 2"

    # 3. 客户端 B 用旧 version=1 更新 → 409 Conflict
    res_b = client.patch(
        f"/api/tasks/{task_id}",
        json={"title": "B 改的标题", "version": client_b_view["version"]},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res_b.status_code == 409, f"过期 version 更新应返回 409，实际: {res_b.status_code} {res_b.text}"
    detail = res_b.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["current_version"] == 2, "409 响应应包含当前 version=2"
    assert detail["your_version"] == 1, "409 响应应包含客户端提交的 version=1"
    # 409 时任务标题不应被 B 覆盖
    fresh = client.get(f"/api/tasks/{task_id}", auth=ADMIN_AUTH).json()
    assert fresh["title"] == "A 改的标题", "B 的更新被拒绝，标题应仍是 A 的值"
    assert fresh["version"] == 2

    # 4. 客户端 B 重新读取（version=2）后更新成功（version→3）
    client_b_view2 = client.get(f"/api/tasks/{task_id}", auth=ADMIN_AUTH).json()
    assert client_b_view2["version"] == 2
    res_b2 = client.patch(
        f"/api/tasks/{task_id}",
        json={"title": "B 重读后改的标题", "version": client_b_view2["version"]},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res_b2.status_code == 200, res_b2.text
    updated_b2 = res_b2.json()
    assert updated_b2["title"] == "B 重读后改的标题"
    assert updated_b2["version"] == 3, "B 重读后更新成功，version 应自增到 3"

    # 5. 向后兼容：不传 version 时行为不变（不冲突，直接更新 + version 自增）
    res_compat = client.patch(
        f"/api/tasks/{task_id}",
        json={"status": "done"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res_compat.status_code == 200, res_compat.text
    updated_compat = res_compat.json()
    assert updated_compat["status"] == "done"
    assert updated_compat["version"] == 4, "不传 version 也应自增，version→4"


def test_optimistic_lock_project_conflict():
    """项目级乐观锁：同样支持 version 冲突检测，端到端验证 Project PATCH。"""
    project = _create_project(name="项目锁测试")
    pid = project["id"]
    assert project["version"] == 1

    # 第一次更新成功（version→2）
    res1 = client.patch(
        f"/api/projects/{pid}",
        json={"name": "第一次更新", "version": 1},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res1.status_code == 200, res1.text
    assert res1.json()["version"] == 2

    # 用旧 version=1 再次更新 → 409
    res2 = client.patch(
        f"/api/projects/{pid}",
        json={"name": "冲突更新", "version": 1},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res2.status_code == 409, f"过期 version 应返回 409，实际: {res2.status_code}"
    assert res2.json()["detail"]["current_version"] == 2

    # 不传 version 时向后兼容
    res3 = client.patch(
        f"/api/projects/{pid}",
        json={"description": "无锁兼容更新"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res3.status_code == 200, res3.text
    assert res3.json()["version"] == 3


# W091c: SQLite 数据库文件损坏恢复路径测试
def test_database_corruption_recovery(tmp_path, monkeypatch):
    """数据库文件损坏后，auto_backup_on_startup 应检测并从备份恢复。"""
    import os

    from backup import auto_backup_on_startup, list_backups, backup_db

    # 沙箱化：开源发布改造（2026-10-25）——不依赖仓库内是否残留 bridge_mvp.db
    sandbox = _backup_sandbox(tmp_path, monkeypatch)
    db_path = sandbox["db"]

    # 1. 先正常创建一个备份（确保有可恢复的备份）
    backup_db()
    backups = list_backups()
    assert len(backups) >= 1, "应至少有 1 个备份"

    # 2. 模拟数据库文件损坏——写入垃圾数据
    original_size = os.path.getsize(db_path)
    with open(db_path, "wb") as f:
        f.write(b"CORRUPTED_DATA_NOT_A_VALID_SQLITE_FILE" * 100)

    # 3. 调用 auto_backup_on_startup——应检测到损坏并从备份恢复
    result = auto_backup_on_startup()

    # 4. 断言恢复成功
    assert result is True or result is None, f"恢复应成功或跳过，结果: {result}"

    # 5. 断言数据库文件已恢复（不再是垃圾数据）
    recovered_size = os.path.getsize(db_path)
    assert recovered_size != original_size or recovered_size > 100, \
        f"恢复后文件大小应合理，原 {original_size}，恢复后 {recovered_size}"

    # 6. 断言数据库可正常读写（integrity_check 通过）
    #    用沙箱 engine 校验——恢复后的真实文件是沙箱里的那个
    from sqlalchemy import text

    import backup

    with backup.engine.connect() as conn:
        result = conn.execute(text("PRAGMA integrity_check"))
        integrity = result.fetchone()[0]
        assert integrity == "ok", f"数据库完整性检查应通过，实际: {integrity}"


# B-P2-3/B-P2-4: 种子数据与停滞提醒测试
def test_seed_data():
    """种子数据测试：用 with TestClient(app) 触发 lifespan → init_db → _seed_demo_data。

    验证：
    1. 种子项目"周末探访老人院"存在（B-P2-3）
    2. 有 3 个任务（B-P2-3）
    3. 有 1 条交接记录（B-P2-3）
    4. in_progress 任务（updated_at=7天前）的 HTML 含"已 7 天未更新"停滞提醒（B-P2-4）

    注意：此测试用 with TestClient(app) 触发 lifespan，与其它测试用 create_all 不同。
    """
    with TestClient(app) as c:
        # B-P2-3: 验证种子项目存在
        res = c.get("/api/projects?q=周末探访老人院", auth=ADMIN_AUTH)
        assert res.status_code == 200
        projects = res.json()
        assert len(projects) == 1, f"应只有 1 个种子项目，实际 {len(projects)}"
        seed_project = projects[0]
        pid = seed_project["id"]
        assert seed_project["name"] == "周末探访老人院"

        # B-P2-3: 验证有 3 个任务
        detail = c.get(f"/api/projects/{pid}", auth=ADMIN_AUTH).json()
        assert len(detail["tasks"]) == 3, f"应有 3 个任务，实际 {len(detail['tasks'])}"

        # B-P2-3: 验证有 1 条交接记录
        assert len(detail["handoffs"]) == 1, f"应有 1 条交接记录，实际 {len(detail['handoffs'])}"

        # B-P2-4: 验证停滞提醒（in_progress 任务 updated_at=7天前，>3 天阈值）
        res = c.get(f"/admin/projects/{pid}", auth=ADMIN_AUTH)
        assert res.status_code == 200
        assert "已 7 天未更新" in res.text, "in_progress 任务应显示停滞提醒"


# ---------- Phase 4.4: E2E 加密同步契约测试 ----------
# Bridge 后端不参与加密/解密，仅作为密文存储与转发中介。
# 这些测试验证：客户端推送的密文容器（data.encrypted=true）能被原样拉取回来，
# Bridge 不修改、不解析、不剥离密文字段。这是 E2E 加密成立的核心契约。

def test_sync_push_encrypted_payload_preserved_on_pull():
    """Phase 4.4: push 加密容器后 pull 应原样返回，Bridge 不解析 data 字段。

    模拟客户端推送一条密文 SyncItem，再 pull 验证：
    - data.encrypted == True
    - data.alg / data.kdf / data.ciphertext / data.nonce / data.v 全部原样保留
    - Bridge 未剥离任何字段
    """
    from datetime import datetime, timezone
    reg = client.post("/api/auth/register", json={
        "email": "phase44user@example.com",
        "password": "testpass123",
        "display_name": "Phase44 用户",
    })
    token = reg.json()["access_token"]
    auth_h = _auth_headers(token)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # 模拟 crypto.js encryptData() 的输出格式
    encrypted_data = {
        "encrypted": True,
        "alg": "AES-GCM-256",
        "kdf": "PBKDF2-SHA256-100000",
        "ciphertext": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "nonce": "AAAAAAAAAAAAAAAA",
        "v": 1,
    }

    push_res = client.post("/api/sync/push", json={"items": [{
        "app": "focus",
        "item_type": "snapshot",
        "client_uuid": "phase44-encrypted-1",
        "data": encrypted_data,
        "client_created_at": now_ms,
        "client_updated_at": now_ms,
    }]}, headers=auth_h)
    assert push_res.status_code == 200, push_res.text

    pull_res = client.get("/api/sync/pull?app=focus", headers=auth_h)
    assert pull_res.status_code == 200
    items = pull_res.json()["items"]
    matched = [i for i in items if i["client_uuid"] == "phase44-encrypted-1"]
    assert len(matched) == 1, "拉取应返回推送的密文条目"

    pulled_data = matched[0]["data"]
    # 关键契约：所有密文容器字段原样保留
    assert pulled_data["encrypted"] is True
    assert pulled_data["alg"] == "AES-GCM-256"
    assert pulled_data["kdf"] == "PBKDF2-SHA256-100000"
    assert pulled_data["ciphertext"] == encrypted_data["ciphertext"]
    assert pulled_data["nonce"] == encrypted_data["nonce"]
    assert pulled_data["v"] == 1


def test_sync_push_mixed_plaintext_and_encrypted_preserved():
    """Phase 4.4: 同一用户可同时存在明文与密文 SyncItem（向后兼容场景）。

    模拟用户启用加密前推送的明文 + 启用后推送的密文，pull 时应能同时返回两类。
    """
    from datetime import datetime, timezone
    reg = client.post("/api/auth/register", json={
        "email": "phase44mixed@example.com",
        "password": "testpass123",
        "display_name": "Phase44 混合",
    })
    token = reg.json()["access_token"]
    auth_h = _auth_headers(token)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # 1 条明文（旧数据）
    plaintext_data = {"title": "明文快照", "tabs": [], "createdAt": now_ms}
    client.post("/api/sync/push", json={"items": [{
        "app": "focus",
        "item_type": "snapshot",
        "client_uuid": "phase44-plain-1",
        "data": plaintext_data,
        "client_created_at": now_ms,
        "client_updated_at": now_ms,
    }]}, headers=auth_h)

    # 1 条密文（新启用加密后的数据）
    encrypted_data = {
        "encrypted": True,
        "alg": "AES-GCM-256",
        "kdf": "PBKDF2-SHA256-100000",
        "ciphertext": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        "nonce": "CCCCCCCCCCCCCCCC",
        "v": 1,
    }
    client.post("/api/sync/push", json={"items": [{
        "app": "focus",
        "item_type": "snapshot",
        "client_uuid": "phase44-cipher-1",
        "data": encrypted_data,
        "client_created_at": now_ms + 1,
        "client_updated_at": now_ms + 1,
    }]}, headers=auth_h)

    pull_res = client.get("/api/sync/pull?app=focus", headers=auth_h)
    items = pull_res.json()["items"]
    by_uuid = {i["client_uuid"]: i for i in items}

    assert "phase44-plain-1" in by_uuid, "明文条目应可拉取"
    assert "phase44-cipher-1" in by_uuid, "密文条目应可拉取"

    # 明文条目 data 字段保留原结构（无 encrypted 标记）
    assert by_uuid["phase44-plain-1"]["data"]["title"] == "明文快照"
    assert "encrypted" not in by_uuid["phase44-plain-1"]["data"]

    # 密文条目 data 字段保留密文结构
    assert by_uuid["phase44-cipher-1"]["data"]["encrypted"] is True
    assert by_uuid["phase44-cipher-1"]["data"]["ciphertext"] == encrypted_data["ciphertext"]


def test_sync_push_encrypted_lww_conflict_resolution():
    """Phase 4.4: LWW 冲突解决对密文容器同样适用。

    同一 client_uuid 推送两次（密文不同，client_updated_at 不同），后推送的应覆盖。
    """
    from datetime import datetime, timezone
    reg = client.post("/api/auth/register", json={
        "email": "phase44lww@example.com",
        "password": "testpass123",
        "display_name": "Phase44 LWW",
    })
    token = reg.json()["access_token"]
    auth_h = _auth_headers(token)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # 第一次推送：旧密文
    client.post("/api/sync/push", json={"items": [{
        "app": "focus",
        "item_type": "snapshot",
        "client_uuid": "phase44-lww-1",
        "data": {
            "encrypted": True, "alg": "AES-GCM-256", "kdf": "PBKDF2-SHA256-100000",
            "ciphertext": "OLD_CIPHERTEXT", "nonce": "OLD_NONCE", "v": 1,
        },
        "client_created_at": now_ms,
        "client_updated_at": now_ms,
    }]}, headers=auth_h)

    # 第二次推送：新密文（同 uuid，更新时间戳）
    client.post("/api/sync/push", json={"items": [{
        "app": "focus",
        "item_type": "snapshot",
        "client_uuid": "phase44-lww-1",
        "data": {
            "encrypted": True, "alg": "AES-GCM-256", "kdf": "PBKDF2-SHA256-100000",
            "ciphertext": "NEW_CIPHERTEXT", "nonce": "NEW_NONCE", "v": 1,
        },
        "client_created_at": now_ms,
        "client_updated_at": now_ms + 1000,
    }]}, headers=auth_h)

    pull_res = client.get("/api/sync/pull?app=focus", headers=auth_h)
    items = pull_res.json()["items"]
    matched = [i for i in items if i["client_uuid"] == "phase44-lww-1"]
    assert len(matched) == 1, "LWW 后应只有 1 条记录"
    # 新密文应覆盖旧密文
    assert matched[0]["data"]["ciphertext"] == "NEW_CIPHERTEXT"
    assert matched[0]["data"]["nonce"] == "NEW_NONCE"

