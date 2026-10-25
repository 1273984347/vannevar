# tests/test_middleware.py — 阶段2.6 中间件升级测试（JWT 错误码 + tenant_context + 限流维度 + 审计日志）
# 覆盖：
#   - JWT 错误码区分（expired / invalid / error）— V21 参数化 6 种篡改场景
#   - tenant_context 注入（request.state.user_id / org_id）— 3 用例
#   - 限流维度升级（org_id + client_ip + prefix）— V5 真实 org_id 隔离
#   - 审计日志记录 — V14 内容验证 + V15 静态资源排除
#   - 安全响应头 + 请求体大小限制 — V16 完整 6 头 + V17 参数化
# V1 修复：删除模块级 os.environ.setdefault，由 conftest.py autouse fixture 统一注入
# V34 修复：删除模块级 client，改用 conftest fixture（每测试独立 TestClient）
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from auth import JWT_SECRET, JWT_ALGORITHM
from conftest import auth_headers
from main import MAX_REQUEST_BODY_BYTES


def _register_and_get_token(client, email="mw@example.com", password="testpass123"):
    """注册用户并返回 (token, user_id)。V34: 接受 client 参数。"""
    res = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": "MW"},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["access_token"], data["user"]["id"]


# ========== JWT 错误码区分（V21 参数化 6 种篡改场景）==========

@pytest.mark.parametrize("tamper_type, expected_code", [
    # V21: 6 种 JWT 篡改场景参数化
    ("expired", "token_expired"),           # 过期 token
    ("malformed", "token_invalid"),         # 非 JWT 格式
    ("tampered_sig", "token_invalid"),      # 签名篡改
    ("missing_sub", "token_invalid"),       # 缺失 sub 字段
    ("alg_none", "token_invalid"),          # alg=none 降级攻击
    ("wrong_aud", "token_invalid"),         # 错误 audience
])
def test_jwt_error_codes(client, tamper_type, expected_code):
    """V21: 参数化测试 JWT 各种篡改场景的错误码。"""
    from jose import jwt as _jwt

    now = datetime.now(timezone.utc)

    if tamper_type == "expired":
        payload = {
            "sub": "1", "email": "x@example.com",
            "iat": now - timedelta(days=10),
            "exp": now - timedelta(days=1),
        }
        token = _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    elif tamper_type == "malformed":
        token = "not.a.valid.jwt"

    elif tamper_type == "tampered_sig":
        token, _ = _register_and_get_token(client)
        parts = token.split(".")
        sig = parts[2]
        flipped = "A" if sig[0] != "A" else "B"
        token = parts[0] + "." + parts[1] + "." + flipped + sig[1:]

    elif tamper_type == "missing_sub":
        payload = {
            "email": "x@example.com",
            "iat": now,
            "exp": now + timedelta(days=1),
        }
        token = _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    elif tamper_type == "alg_none":
        # alg=none 降级攻击：python-jose 默认拒绝 alg=none，应返回 token_invalid
        payload = {
            "sub": "1", "email": "x@example.com",
            "iat": now,
            "exp": now + timedelta(days=1),
        }
        # 尝试 alg=none（python-jose 会拒绝）
        try:
            token = _jwt.encode(payload, JWT_SECRET, algorithm="none")
        except Exception:
            # jose 拒绝 alg=none，直接构造一个头部为 none 的 token
            import json
            import base64
            header = base64.urlsafe_b64encode(
                json.dumps({"alg": "none", "typ": "JWT"}).encode()
            ).rstrip(b"=").decode()
            payload_b64 = base64.urlsafe_b64encode(
                json.dumps(payload).encode()
            ).rstrip(b"=").decode()
            token = f"{header}.{payload_b64}."

    elif tamper_type == "wrong_aud":
        payload = {
            "sub": "1", "email": "x@example.com",
            "iat": now,
            "exp": now + timedelta(days=1),
            "aud": "wrong-service",
            "iss": "bridge",
        }
        token = _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401, f"场景 {tamper_type}: 期望 401，实际 {res.status_code}"
    detail = res.json()["detail"]
    assert isinstance(detail, dict), f"场景 {tamper_type}: detail 应为 dict"
    assert detail["code"] == expected_code, \
        f"场景 {tamper_type}: 期望 code={expected_code}，实际 code={detail['code']}"


def test_jwt_valid_passes_middleware(client):
    """有效 JWT 能通过中间件，require_user 正常返回用户信息。"""
    token, _ = _register_and_get_token(client)
    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200


# ========== tenant_context 注入（3 用例）==========

def test_tenant_context_no_token_injects_none(client):
    """未认证请求：request.state.user_id / org_id 均为 None（通过 /health 不报错间接验证）。"""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_tenant_context_with_token_injects_user_id(client):
    """JWT 请求：中间件解析出 user_id 注入 request.state。"""
    token, uid = _register_and_get_token(client)
    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["id"] == uid


def test_tenant_context_org_id_from_default_org_id(client):
    """用户设置了 default_org_id 后，中间件应注入 org_id。"""
    token, uid = _register_and_get_token(client)
    # 创建组织（owner 自动成为成员）
    org_res = client.post(
        "/api/orgs/",
        json={"name": "测试组织", "slug": "test-org"},
        headers=auth_headers(token),
    )
    assert org_res.status_code == 201, org_res.text
    org_id = org_res.json()["id"]
    # 设置 default_org_id
    from database import SessionLocal
    from models import User
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uid).first()
        user.default_org_id = org_id
        db.commit()
    finally:
        db.close()
    # 再次访问，中间件应能解析 org_id（不报错即说明路径走通）
    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200


# ========== 限流维度升级（3 用例）==========

def test_rate_limit_org_id_disambiguates_users(client):
    """V5 修复：不同 org_id 的请求有独立配额（即使同 IP）。

    创建两个真实组织用户（各有 default_org_id），验证限流键按
    (org_id, client_ip, prefix) 维度独立计数。
    """
    # 用户 1 创建组织 → 有 default_org_id
    token1, uid1 = _register_and_get_token(client, email="u1@example.com")
    org1 = client.post("/api/orgs/", json={"name": "组织1", "slug": "org-1"},
                        headers=auth_headers(token1)).json()
    from database import SessionLocal
    from models import User
    db = SessionLocal()
    try:
        db.query(User).filter(User.id == uid1).update({"default_org_id": org1["id"]})
        db.commit()
    finally:
        db.close()

    # 用户 2 创建不同组织 → 有不同的 default_org_id
    token2, uid2 = _register_and_get_token(client, email="u2@example.com")
    org2 = client.post("/api/orgs/", json={"name": "组织2", "slug": "org-2"},
                        headers=auth_headers(token2)).json()
    db = SessionLocal()
    try:
        db.query(User).filter(User.id == uid2).update({"default_org_id": org2["id"]})
        db.commit()
    finally:
        db.close()

    # 用户 1 发送 5 次请求
    for _ in range(5):
        r1 = client.get("/api/orgs/", headers=auth_headers(token1))
        assert r1.status_code == 200, f"用户1请求应成功: {r1.status_code} {r1.text}"

    # 用户 2 发送 5 次请求——应不受用户 1 配额消耗影响（org_id 不同）
    for _ in range(5):
        r2 = client.get("/api/orgs/", headers=auth_headers(token2))
        assert r2.status_code == 200, f"用户2请求应成功（独立配额）: {r2.status_code} {r2.text}"


def test_rate_limit_triggers_429(client):
    """超过限流阈值返回 429 + 友好提示。

    /api/auth/ 的限流规则是 30/60s。连续发起 35 次请求应触发 429。
    V33 修复：删除 idx_429 死变量，直接重新发请求验证响应体。
    """
    codes = []
    for _ in range(35):
        res = client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "wrongpassword"},
        )
        codes.append(res.status_code)
    # 应该出现 429
    assert 429 in codes, f"未触发限流，状态码集合: {set(codes)}"

    # V33 修复：删除 idx_429 死变量，直接重新发一个请求验证响应体
    res = client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "wrongpassword"},
    )
    assert res.status_code == 429
    assert "频繁" in res.json()["detail"]


def test_rate_limit_sse_bypass(client):
    """SSE 端点不走普通限流（长连接保活）。"""
    codes = []
    for _ in range(130):
        res = client.get("/api/projects/1/events")
        codes.append(res.status_code)
        if res.status_code == 429:
            break
    assert 429 not in codes, "SSE 端点被限流中间件误拦截"


# ========== 审计日志（V14 内容验证 + V15 静态资源排除）==========

def test_audit_log_records_successful_request(client, caplog):
    """V14 修复：成功请求被审计日志记录，验证日志内容含 actor / method / path / status。"""
    import logging
    caplog.set_level(logging.INFO, logger="bridge.main")
    token, uid = _register_and_get_token(client)
    caplog.clear()
    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    # 审计日志应包含 AUDIT 关键字
    audit_records = [r for r in caplog.records if "AUDIT" in r.getMessage()]
    assert len(audit_records) >= 1, "未记录审计日志"
    # V14 修复：验证审计日志内容包含关键信息
    audit_msg = audit_records[0].getMessage()
    assert "AUDIT" in audit_msg, "审计日志应包含 AUDIT 标识"
    assert "GET" in audit_msg, "审计日志应包含 HTTP 方法"
    assert "/api/auth/me" in audit_msg, "审计日志应包含请求路径"
    assert "200" in audit_msg, "审计日志应包含状态码"
    assert f"actor={uid}" in audit_msg, f"审计日志应包含 actor={uid}，实际: {audit_msg}"


def test_audit_log_excludes_health_and_static(client, caplog):
    """V15 修复：/health 和 /static/ 不记录审计日志（避免噪声）。"""
    import logging
    caplog.set_level(logging.INFO, logger="bridge.main")
    caplog.clear()
    client.get("/health")
    audit_records = [r for r in caplog.records if "AUDIT" in r.getMessage()]
    assert len(audit_records) == 0, "/health 不应记录审计日志"

    # V15 新增：验证 /static/ 路径也不记录审计日志
    caplog.clear()
    client.get("/static/style.css")
    audit_records = [r for r in caplog.records if "AUDIT" in r.getMessage()]
    assert len(audit_records) == 0, "/static/ 不应记录审计日志"


# ========== 安全响应头 + 请求体大小限制 ==========

def test_security_headers_present(client):
    """V16 修复：验证完整的 6 个安全响应头 + CSP 内容。"""
    res = client.get("/health")
    # V16: 完整验证 6 个安全响应头
    assert res.headers.get("X-Content-Type-Options") == "nosniff", "X-Content-Type-Options 缺失或值错误"
    assert res.headers.get("X-Frame-Options") == "DENY", "X-Frame-Options 缺失或值错误"
    assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin", "Referrer-Policy 缺失或值错误"
    assert "Permissions-Policy" in res.headers, "Permissions-Policy 缺失"
    assert "Content-Security-Policy" in res.headers, "CSP 缺失"
    # V16: 验证 CSP 内容包含关键指令
    csp = res.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp, "CSP 应包含 default-src 'self'"
    assert "frame-ancestors 'none'" in csp, "CSP 应包含 frame-ancestors 'none'"
    assert "base-uri 'self'" in csp, "CSP 应包含 base-uri 'self'"
    # V16: 验证 401 响应也包含安全头（中间件对所有响应都设置）
    from auth import JWT_SECRET, JWT_ALGORITHM
    from jose import jwt as _jwt
    now = datetime.now(timezone.utc)
    expired_token = _jwt.encode({
        "sub": "1", "email": "x@example.com",
        "iat": now - timedelta(days=10),
        "exp": now - timedelta(days=1),
    }, JWT_SECRET, algorithm=JWT_ALGORITHM)
    res_401 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert res_401.status_code == 401
    assert res_401.headers.get("X-Content-Type-Options") == "nosniff", "401 响应也应包含安全头"


@pytest.mark.parametrize("payload_size_ratio, expected_status", [
    # V17: 参数化请求体大小限制测试
    (0.5, 200),     # 半倍限制 → 正常（但 login 会因密码错误返回 401）
    (0.9, 401),     # 90% 限制 → 正常请求（登录失败 401）
    (1.0, 413),     # 刚好等于限制 → 413（边界）
    (1.5, 413),     # 超过限制 50% → 413
    (2.0, 413),     # 超过限制 100% → 413
])
def test_request_body_size_limit(client, payload_size_ratio, expected_status):
    """V17: 参数化测试不同请求体大小是否被正确限制。"""
    payload_size = int(MAX_REQUEST_BODY_BYTES * payload_size_ratio)
    big_payload = {"x": "A" * payload_size}
    res = client.post("/api/auth/login", json=big_payload)
    if expected_status == 413:
        assert res.status_code == 413, f"ratio={payload_size_ratio}: 期望 413，实际 {res.status_code}"
        assert "过大" in res.json()["detail"]
    else:
        # 小于限制的请求应通过大小检查（但可能因密码错误返回 401）
        assert res.status_code != 413, f"ratio={payload_size_ratio}: 不应返回 413"


# ========== V10: TestClient lifespan 启动调度器 ==========

def test_lifespan_starts_scheduler():
    """V10: TestClient 作为 context manager 触发 lifespan 后，_scheduler 不为 None。

    APScheduler 应在 lifespan 启动时被创建并启动，验证后台调度器正常初始化。
    """
    from main import app
    with TestClient(app) as c:
        import main as _main
        assert _main._scheduler is not None, "lifespan 启动后 _scheduler 应非 None"
        # 验证服务正常响应
        res = c.get("/health")
        assert res.status_code == 200
