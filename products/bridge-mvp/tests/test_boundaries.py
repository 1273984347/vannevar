# tests/test_boundaries.py — DRL Round 1 边界输入测试
# 覆盖：V8（空值/零/负数/超大输入/Unicode/超长字符串/JSON 类型混淆）
# V1 修复：由 conftest.py autouse fixture 统一注入环境变量
# 核心验证：所有边界输入应返回 422/400（Pydantic 校验），不应返回 500（服务器错误）
import time

import pytest

from conftest import ADMIN_AUTH, CSRF_HEADERS, auth_headers


# ============================================================
# 辅助函数
# ============================================================

def _register_and_login(client, email="bound@x.com", password="testpass123"):
    """注册用户并返回 JWT token。"""
    client.post("/api/auth/register", json={
        "email": email, "password": password, "display_name": "边界测试用户"
    })
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _create_project(client, token, name="边界测试项目"):
    """创建项目并返回 project_id。"""
    res = client.post("/api/projects", json={"name": name}, headers=auth_headers(token))
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


# ============================================================
# V8: 边界输入测试
# ============================================================

class TestV8Boundaries:
    """V8: 边界输入测试——验证 422/400 而非 500。"""

    # ---------- 空值 ----------

    def test_empty_project_name(self, client):
        """V8-1: 空项目名 → 422（min_length=1）。"""
        token = _register_and_login(client, "b1@x.com")
        res = client.post("/api/projects", json={"name": ""}, headers=auth_headers(token))
        assert res.status_code == 422, f"空项目名应 422，实际: {res.status_code}"
        assert "traceback" not in res.text.lower()
        # R2-V8: 422 响应应包含字段级错误详情
        assert "name" in str(res.json()["detail"])

    def test_empty_task_title(self, client):
        """V8-2: 空任务标题 → 422（min_length=1）。"""
        token = _register_and_login(client, "b2@x.com")
        pid = _create_project(client, token, "空任务测试")
        res = client.post(
            f"/api/projects/{pid}/tasks",
            json={"title": ""},
            headers=auth_headers(token),
        )
        assert res.status_code == 422, f"空任务标题应 422，实际: {res.status_code}"

    def test_empty_message_content(self, client):
        """V8-3: 空留言内容 → 422（min_length=1）。"""
        token = _register_and_login(client, "b3@x.com")
        pid = _create_project(client, token, "空留言测试")
        res = client.post(
            f"/api/projects/{pid}/messages",
            json={"content": ""},
            headers=auth_headers(token),
        )
        assert res.status_code == 422, f"空留言应 422，实际: {res.status_code}"

    # ---------- 零/负数 ----------

    def test_zero_project_id(self, client):
        """V8-4: project_id=0 → 404（项目不存在）。"""
        token = _register_and_login(client, "b4@x.com")
        res = client.get("/api/projects/0", headers=auth_headers(token))
        assert res.status_code == 404, f"project_id=0 应 404，实际: {res.status_code}"

    def test_negative_project_id(self, client):
        """V8-5: project_id=-1 → 422（路径参数 int 不接受负数由路由处理）。"""
        token = _register_and_login(client, "b5@x.com")
        # FastAPI 路径参数 int 类型：-1 会被解析为 int 但路由匹配可能不同
        res = client.get("/api/projects/-1", headers=auth_headers(token))
        assert res.status_code in (404, 422, 403), f"project_id=-1 应 404/422/403，实际: {res.status_code}"
        assert "traceback" not in res.text.lower()

    # ---------- 超大输入 ----------

    def test_huge_project_name(self, client):
        """V8-6: 项目名超长（10000 字符）→ 422（max_length=120）。"""
        token = _register_and_login(client, "b6@x.com")
        res = client.post(
            "/api/projects",
            json={"name": "A" * 10000},
            headers=auth_headers(token),
        )
        assert res.status_code == 422, f"超长项目名应 422，实际: {res.status_code}"
        assert "name" in str(res.json()["detail"])  # R2-V8: 错误应指明字段

    def test_huge_task_description(self, client):
        """V8-7: 任务描述超长（100000 字符）→ 422（max_length=5000）。"""
        token = _register_and_login(client, "b7@x.com")
        pid = _create_project(client, token, "超大描述测试")
        res = client.post(
            f"/api/projects/{pid}/tasks",
            json={"title": "正常标题", "description": "B" * 100000},
            headers=auth_headers(token),
        )
        assert res.status_code == 422, f"超长描述应 422，实际: {res.status_code}"

    def test_huge_sync_batch(self, client):
        """V8-8: 同步推送超过 500 items → 413。"""
        token = _register_and_login(client, "b8@x.com")
        now_ms = int(time.time() * 1000)
        items = [{
            "app": "focus",
            "item_type": "snapshot",
            "client_uuid": f"uuid-{i}",
            "data": {"title": f"批量 {i}"},
            "client_created_at": now_ms,
            "client_updated_at": now_ms,
        } for i in range(501)]
        res = client.post("/api/sync/push", json={"items": items}, headers=auth_headers(token))
        assert res.status_code == 413, f"超 500 items 应 413，实际: {res.status_code}"

    # ---------- Unicode ----------

    def test_unicode_project_name(self, client):
        """V8-9: Unicode 项目名（中文+emoji）→ 201（正常创建）。"""
        token = _register_and_login(client, "b9@x.com")
        unicode_name = "中文项目🎮测试日本語한국어"
        res = client.post(
            "/api/projects",
            json={"name": unicode_name},
            headers=auth_headers(token),
        )
        assert res.status_code == 201, f"Unicode 项目名应 201，实际: {res.status_code} {res.text}"
        data = res.json()
        assert data["name"] == unicode_name
        assert data["id"] > 0  # R2-V8: 创建成功应返回有效 id
        assert data["qr_token"]  # R2-V8: 创建成功应返回 qr_token

    def test_unicode_task_title(self, client):
        """V8-10: Unicode 任务标题 → 201。"""
        token = _register_and_login(client, "b10@x.com")
        pid = _create_project(client, token, "Unicode 任务测试")
        unicode_title = "タスク🏆작업"
        # 任务创建端点使用 require_admin（Basic Auth），JWT 用户无权直接创建任务
        res = client.post(
            f"/api/projects/{pid}/tasks",
            json={"title": unicode_title},
            auth=ADMIN_AUTH,
            headers=CSRF_HEADERS,
        )
        assert res.status_code == 201, f"Unicode 任务应 201，实际: {res.status_code}"
        task_data = res.json()
        assert task_data["title"] == unicode_title
        assert task_data["id"] > 0  # R2-V8: 创建成功应返回有效 id
        assert task_data["status"] == "todo"  # R2-V8: 新任务默认状态

    # ---------- 超长字符串 ----------

    def test_super_long_message_content(self, client):
        """V8-11: 留言内容超长（50000 字符）→ 422（max_length=2000）。"""
        token = _register_and_login(client, "b11@x.com")
        pid = _create_project(client, token, "超长留言测试")
        res = client.post(
            f"/api/projects/{pid}/messages",
            json={"content": "X" * 50000},
            headers=auth_headers(token),
        )
        assert res.status_code == 422, f"超长留言应 422，实际: {res.status_code}"

    # ---------- JSON 类型混淆 ----------

    def test_json_type_confusion_project_name_int(self, client):
        """V8-12: 项目名传整数 → 422（类型错误）。"""
        token = _register_and_login(client, "b12@x.com")
        res = client.post(
            "/api/projects",
            json={"name": 12345},
            headers=auth_headers(token),
        )
        assert res.status_code == 422, f"类型混淆应 422，实际: {res.status_code}"

    def test_json_type_confusion_project_name_array(self, client):
        """V8-13: 项目名传数组 → 422。"""
        token = _register_and_login(client, "b13@x.com")
        res = client.post(
            "/api/projects",
            json={"name": ["array", "value"]},
            headers=auth_headers(token),
        )
        assert res.status_code == 422, f"数组类型应 422，实际: {res.status_code}"

    def test_json_type_confusion_task_id_string(self, client):
        """V8-14: task_id 传字符串 → 422（应为 int）。"""
        token = _register_and_login(client, "b14@x.com")
        pid = _create_project(client, token, "类型混淆 task_id")
        res = client.post(
            f"/api/projects/{pid}/messages",
            json={"content": "测试", "task_id": "not-a-number"},
            headers=auth_headers(token),
        )
        assert res.status_code == 422, f"task_id 字符串应 422，实际: {res.status_code}"

    # ---------- 缺失必填字段 ----------

    def test_missing_required_fields_project(self, client):
        """V8-15: 缺少 name 字段 → 422。"""
        token = _register_and_login(client, "b15@x.com")
        res = client.post(
            "/api/projects",
            json={"description": "有描述但没名字"},
            headers=auth_headers(token),
        )
        assert res.status_code == 422, f"缺少必填字段应 422，实际: {res.status_code}"

    # ---------- 多余字段（extra="forbid"）----------

    def test_extra_fields_rejected(self, client):
        """V8-16: 多余字段被拒绝 → 422（extra="forbid"）。"""
        token = _register_and_login(client, "b16@x.com")
        res = client.post(
            "/api/projects",
            json={"name": "正常名", "extra_field": "不应存在"},
            headers=auth_headers(token),
        )
        assert res.status_code == 422, f"多余字段应 422，实际: {res.status_code}"

    # ---------- 同步时间戳边界 ----------

    def test_sync_item_negative_timestamp(self, client):
        """V8-17: 同步项 client_created_at 为负数 → 422（ge=0）。"""
        token = _register_and_login(client, "b17@x.com")
        res = client.post("/api/sync/push", json={
            "items": [{
                "app": "focus",
                "item_type": "snapshot",
                "client_uuid": "neg-ts-uuid",
                "data": {"title": "负时间戳"},
                "client_created_at": -1,
                "client_updated_at": -1,
            }]
        }, headers=auth_headers(token))
        assert res.status_code == 422, f"负时间戳应 422，实际: {res.status_code}"

    def test_sync_item_huge_timestamp(self, client):
        """V8-18: 同步项时间戳过大（超过 now+60s）→ 422。"""
        token = _register_and_login(client, "b18@x.com")
        huge_ts = int(time.time() * 1000) + 600000  # 远超 60s 容差
        res = client.post("/api/sync/push", json={
            "items": [{
                "app": "focus",
                "item_type": "snapshot",
                "client_uuid": "huge-ts-uuid",
                "data": {"title": "超大时间戳"},
                "client_created_at": huge_ts,
                "client_updated_at": huge_ts,
            }]
        }, headers=auth_headers(token))
        assert res.status_code == 422, f"超大时间戳应 422，实际: {res.status_code}"

    def test_sync_item_empty_uuid(self, client):
        """V8-19: 同步项 client_uuid 为空 → 422（min_length=1）。"""
        token = _register_and_login(client, "b19@x.com")
        now_ms = int(time.time() * 1000)
        res = client.post("/api/sync/push", json={
            "items": [{
                "app": "focus",
                "item_type": "snapshot",
                "client_uuid": "",
                "data": {"title": "空 UUID"},
                "client_created_at": now_ms,
                "client_updated_at": now_ms,
            }]
        }, headers=auth_headers(token))
        assert res.status_code == 422, f"空 UUID 应 422，实际: {res.status_code}"

    def test_sync_item_invalid_app(self, client):
        """V8-20: 同步项 app 值非法 → 422。"""
        token = _register_and_login(client, "b20@x.com")
        now_ms = int(time.time() * 1000)
        res = client.post("/api/sync/push", json={
            "items": [{
                "app": "invalid_app",
                "item_type": "snapshot",
                "client_uuid": "invalid-app-uuid",
                "data": {"title": "非法 app"},
                "client_created_at": now_ms,
                "client_updated_at": now_ms,
            }]
        }, headers=auth_headers(token))
        assert res.status_code == 422, f"非法 app 应 422，实际: {res.status_code}"

    # ---------- 状态值校验 ----------

    def test_invalid_project_status(self, client):
        """V8-21: 非法项目状态 → 422。"""
        token = _register_and_login(client, "b21@x.com")
        res = client.post(
            "/api/projects",
            json={"name": "非法状态测试", "status": "invalid_status"},
            headers=auth_headers(token),
        )
        assert res.status_code == 422, f"非法状态应 422，实际: {res.status_code}"

    def test_invalid_task_status_update(self, client):
        """V8-22: 更新任务时传非法状态 → 422。"""
        token = _register_and_login(client, "b22@x.com")
        pid = _create_project(client, token, "非法任务状态测试")
        # 任务端点使用 require_admin（Basic Auth），JWT 用户无权直接操作任务
        create_res = client.post(
            f"/api/projects/{pid}/tasks",
            json={"title": "测试任务"},
            auth=ADMIN_AUTH,
            headers=CSRF_HEADERS,
        )
        assert create_res.status_code == 201
        task_id = create_res.json()["id"]
        assert task_id > 0  # R2-V8: 创建成功应返回有效 id
        # 尝试更新为非法状态（任务 PATCH 路由前缀为 /api/tasks）
        res = client.patch(
            f"/api/tasks/{task_id}",
            json={"status": "invalid_status"},
            auth=ADMIN_AUTH,
            headers=CSRF_HEADERS,
        )
        assert res.status_code == 422, f"非法任务状态应 422，实际: {res.status_code}"
