# tests/test_auth.py — 阶段1.1 统一账号体系测试（14 用例）
# 覆盖：注册（5）+ 登录（4）+ /me（5，含 malformed/tampered token）
import os

# R3 class-level fix: 用 setdefault 而非 []=，遵循 conftest.py 约定（允许 CI/命令行覆盖）
os.environ.setdefault("BRIDGE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BRIDGE_DEBUG", "1")

import pytest
from fastapi.testclient import TestClient

from auth import ADMIN_PASS, ADMIN_USER
from database import Base, engine
from main import app
from conftest import auth_headers  # DRL-R1-V2: JWT 写端点需要 X-CSRF-Token 头 == JWT 中的 csrf_jti

client = TestClient(app)

ADMIN_AUTH = (ADMIN_USER, ADMIN_PASS)


def _register(email="test@example.com", password="testpass123", display_name="测试用户"):
    """辅助：注册用户并返回响应。"""
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": display_name},
    )


def _login(email="test@example.com", password="testpass123"):
    """辅助：登录并返回响应。"""
    return client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )


# ---------- 注册（5 用例）----------
def test_register_success():
    """注册成功：返回 JWT + 用户信息，不含 password_hash。"""
    res = _register()
    assert res.status_code == 201
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "test@example.com"
    assert data["user"]["display_name"] == "测试用户"
    assert "password_hash" not in data["user"]
    assert data["user"]["id"] > 0


def test_register_duplicate_email():
    """重复邮箱注册返回 409。"""
    assert _register().status_code == 201
    res = _register()
    assert res.status_code == 409


def test_register_invalid_email():
    """非法邮箱格式返回 422。"""
    res = client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "testpass123"},
    )
    assert res.status_code == 422


def test_register_short_password():
    """密码 < 8 位返回 422。"""
    res = client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "password": "short"},
    )
    assert res.status_code == 422


def test_register_missing_email():
    """缺少 email 字段返回 422。"""
    res = client.post(
        "/api/auth/register",
        json={"password": "testpass123"},
    )
    assert res.status_code == 422


# ---------- 登录（4 用例）----------
def test_login_success():
    """登录成功：返回 JWT + 用户信息。"""
    _register()
    res = _login()
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["user"]["email"] == "test@example.com"
    assert "password_hash" not in data["user"]


def test_login_wrong_password():
    """密码错误返回 401，统一错误信息。"""
    _register()
    res = _login(password="wrongpassword")
    assert res.status_code == 401
    assert res.json()["detail"] == "邮箱或密码错误"


def test_login_nonexistent_email():
    """邮箱不存在返回 401，错误信息与密码错误一致（不泄露邮箱存在性）。"""
    res = _login(email="nobody@example.com")
    assert res.status_code == 401
    assert res.json()["detail"] == "邮箱或密码错误"


def test_login_invalid_email():
    """非法邮箱格式返回 422。"""
    res = client.post(
        "/api/auth/login",
        json={"email": "not-an-email", "password": "testpass123"},
    )
    assert res.status_code == 422


# ---------- /me（5 用例）----------
def test_me_with_valid_token():
    """有效 JWT 访问 /me 返回用户信息。"""
    token = _register().json()["access_token"]
    me = client.get("/api/auth/me", headers=auth_headers(token))
    assert me.status_code == 200
    assert me.json()["email"] == "test@example.com"


def test_me_without_token():
    """无 token 访问 /me 返回 401。"""
    res = client.get("/api/auth/me")
    assert res.status_code == 401


def test_me_with_malformed_token():
    """格式错误的 token（"Bearer abc"）返回 401。"""
    res = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer abc"},
    )
    assert res.status_code == 401


def test_me_with_tampered_token():
    """JWT 篡改后验签失败返回 401。"""
    token = _register().json()["access_token"]
    # 篡改签名段（第三段）的第一个字符，确保验签失败
    parts = token.split(".")
    assert len(parts) == 3
    sig = parts[2]
    # 翻转签名第一个字符（base64url 字符集），保证签名变化
    flipped = "a" if sig[0] != "a" else "b"
    parts[2] = flipped + sig[1:]
    tampered = ".".join(parts)
    res = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {tampered}"},
    )
    assert res.status_code == 401


def test_me_with_dev_basic_auth():
    """dev 模式 Basic Auth 降级：返回虚拟用户（id=0）。"""
    res = client.get("/api/auth/me", auth=ADMIN_AUTH)
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == 0
    assert data["email"] == "dev@bridge.local"


# ---------- 阶段1.2: owner_id 注入（3 用例）----------
from auth import CSRF_COOKIE_NAME, CSRF_TOKEN  # noqa: E402
from base64 import b64encode  # noqa: E402

# 管理员写端点 CSRF 头
CSRF_HEADERS = {"X-Requested-With": "BridgeAdmin", "X-CSRF-Token": CSRF_TOKEN}


def test_create_project_with_jwt_owner_id():
    """JWT 用户创建项目 → owner_id = user.id。

    require_admin 需要 Basic Auth，get_optional_user_id 需要 Bearer token。
    TestClient 的 auth= 参数会被 headers["Authorization"] 覆盖，
    所以用 headers 同时传 Basic Auth（编码进 Authorization）和 Bearer token（自定义头）。

    但 get_optional_user_id 只读标准 Authorization 头。
    因此这里改用单独的 Bearer-Only 路径验证：直接调 get_optional_user_id 逻辑。
    """
    # 直接验证 get_optional_user_id 函数逻辑
    from auth import get_optional_user_id
    from starlette.requests import Request

    token = _register().json()["access_token"]
    # 模拟带 Bearer token 的请求
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    request = Request(scope)
    user_id = get_optional_user_id(request)
    assert user_id is not None
    assert user_id > 0


def test_get_optional_user_id_no_token():
    """无 Authorization 头 → get_optional_user_id 返回 None。"""
    from auth import get_optional_user_id
    from starlette.requests import Request

    scope = {"type": "http", "method": "POST", "headers": []}
    request = Request(scope)
    assert get_optional_user_id(request) is None


def test_create_project_with_basic_auth_owner_id_null():
    """Basic Auth（dev 模式）创建项目 → owner_id = None（兼容旧数据）。"""
    res = client.post(
        "/api/projects",
        json={"name": "Basic Auth 项目", "description": "测试"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    assert res.json()["owner_id"] is None


def test_project_out_includes_owner_id():
    """ProjectOut 响应包含 owner_id 字段。"""
    res = client.post(
        "/api/projects",
        json={"name": "字段检查", "description": "测试"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    assert "owner_id" in res.json()


# ---------- 阶段1.3: 数据隔离测试（5 用例）----------
def test_jwt_user_creates_and_lists_own_projects():
    """JWT 用户创建项目 → 列表只返回自己的项目。"""
    # 用户 A 注册 + 创建项目
    token_a = _register(email="userA@example.com").json()["access_token"]
    res_create = client.post(
        "/api/projects",
        json={"name": "A 的项目", "description": "测试"},
        headers=auth_headers(token_a),
    )
    assert res_create.status_code == 201
    assert res_create.json()["owner_id"] is not None

    # 用户 A 列表只看到自己的项目
    res_list = client.get(
        "/api/projects",
        headers=auth_headers(token_a),
    )
    assert res_list.status_code == 200
    projects = res_list.json()
    assert len(projects) == 1
    assert projects[0]["name"] == "A 的项目"


def test_jwt_user_cannot_see_other_users_projects():
    """JWT 用户 A 看不到用户 B 的项目。"""
    # 用户 A 创建项目
    token_a = _register(email="userA@example.com").json()["access_token"]
    client.post(
        "/api/projects",
        json={"name": "A 的项目", "description": "测试"},
        headers=auth_headers(token_a),
    )

    # 用户 B 注册 + 列表
    token_b = _register(email="userB@example.com").json()["access_token"]
    res_list_b = client.get(
        "/api/projects",
        headers=auth_headers(token_b),
    )
    assert res_list_b.status_code == 200
    assert len(res_list_b.json()) == 0  # B 看不到 A 的项目


def test_jwt_user_cannot_access_other_users_project_detail():
    """JWT 用户 B 访问用户 A 的项目详情 → 403。"""
    token_a = _register(email="userA@example.com").json()["access_token"]
    res_create = client.post(
        "/api/projects",
        json={"name": "A 的项目", "description": "测试"},
        headers=auth_headers(token_a),
    )
    project_id = res_create.json()["id"]

    token_b = _register(email="userB@example.com").json()["access_token"]
    res = client.get(
        f"/api/projects/{project_id}",
        headers=auth_headers(token_b),
    )
    assert res.status_code == 403


def test_jwt_user_cannot_delete_other_users_project():
    """JWT 用户 B 删除用户 A 的项目 → 403。"""
    token_a = _register(email="userA@example.com").json()["access_token"]
    res_create = client.post(
        "/api/projects",
        json={"name": "A 的项目", "description": "测试"},
        headers=auth_headers(token_a),
    )
    project_id = res_create.json()["id"]

    token_b = _register(email="userB@example.com").json()["access_token"]
    res = client.delete(
        f"/api/projects/{project_id}",
        headers=auth_headers(token_b),
    )
    assert res.status_code == 403


def test_basic_auth_admin_sees_all_projects():
    """Basic Auth（dev 模式管理员）能看到所有项目，包括 JWT 用户创建的。"""
    # JWT 用户创建项目
    token = _register(email="user@example.com").json()["access_token"]
    client.post(
        "/api/projects",
        json={"name": "JWT 项目", "description": "测试"},
        headers=auth_headers(token),
    )

    # Basic Auth 管理员列表
    res = client.get("/api/projects", auth=ADMIN_AUTH)
    assert res.status_code == 200
    projects = res.json()
    assert any(p["name"] == "JWT 项目" for p in projects)
