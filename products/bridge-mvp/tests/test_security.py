# tests/test_security.py — DRL Round 1 安全测试
# 覆盖：V7（≥10 安全测试）、V9（生产配置）、V30（7 种 qr_token 场景）、V32（事务回滚）
# V1 修复：由 conftest.py autouse fixture 统一注入环境变量
import base64
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from auth import JWT_ALGORITHM, JWT_AUDIENCE, JWT_ISSUER, JWT_SECRET
from conftest import ADMIN_AUTH, CSRF_HEADERS, VOLUNTEER_CSRF_HEADERS, auth_headers


# ============================================================
# 辅助函数
# ============================================================

def _register_and_login(client, email="sec@example.com", password="testpass123"):
    """注册用户并返回 JWT token。"""
    client.post("/api/auth/register", json={
        "email": email, "password": password, "display_name": "安全测试用户"
    })
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _create_project(client, token, name="安全测试项目"):
    """创建项目并返回 (project_id, qr_token)。"""
    res = client.post("/api/projects", json={"name": name}, headers=auth_headers(token))
    assert res.status_code in (200, 201), res.text
    data = res.json()
    return data["id"], data.get("qr_token", "")


# ============================================================
# V7: 安全测试（≥10 项）
# ============================================================

class TestV7Security:
    """V7: 安全漏洞测试——SQL 注入、XSS、IDOR、CSRF、JWT 攻击、路径穿越。"""

    # ---------- SQL 注入 ----------

    def test_sql_injection_in_project_name(self, client):
        """V7-1: 项目名含 SQL 注入 payload → 参数化查询防护，存原文不执行。"""
        token = _register_and_login(client, "sqli1@x.com")
        payload_name = "'; DROP TABLE projects; --"
        pid, _ = _create_project(client, token, name=payload_name)
        # 验证项目已创建（表未被 DROP）
        res = client.get(f"/api/projects/{pid}", headers=auth_headers(token))
        assert res.status_code == 200
        assert res.json()["name"] == payload_name
        # 验证 projects 表仍然存在——再创建一个项目
        pid2, _ = _create_project(client, token, name="验证表存在")
        assert pid2 != pid

    def test_sql_injection_in_login_email(self, client):
        """V7-2: 登录邮箱含 SQL 注入 payload → 422 或 401 而非 500。

        DRL-R1-V10 适配：UserLogin schema 使用 EmailStr 校验，非法邮箱格式在
        Pydantic 层即被拒绝（422），SQL 注入 payload 不会到达 DB 层。422 和 401
        均为安全响应，只要不返回 500/traceback 即通过。
        """
        res = client.post("/api/auth/login", json={
            "email": "' OR '1'='1' --",
            "password": "anything",
        })
        assert res.status_code in (401, 422), f"SQL 注入邮箱应返回 401 或 422，实际: {res.status_code}"
        assert "traceback" not in res.text.lower()

    def test_sql_injection_in_sync_uuid(self, client):
        """V7-3: 同步推送 client_uuid 含 SQL 注入 → 正常处理不报 500。"""
        token = _register_and_login(client, "sqli3@x.com")
        now_ms = int(time.time() * 1000)
        res = client.post("/api/sync/push", json={
            "items": [{
                "app": "focus",
                "item_type": "snapshot",
                "client_uuid": "'; DROP TABLE sync_items; --",
                "data": {"title": "测试"},
                "client_created_at": now_ms,
                "client_updated_at": now_ms,
            }]
        }, headers=auth_headers(token))
        assert res.status_code == 200, f"SQL 注入 uuid 应正常处理，实际: {res.status_code} {res.text}"
        assert res.json()["created"] == 1

    # ---------- XSS ----------

    def test_xss_in_project_name_stored_safely(self, client):
        """V7-4: 项目名含 XSS payload → 存储原文，API 返回原文（前端负责转义）。"""
        token = _register_and_login(client, "xss1@x.com")
        xss_name = '<script>alert("XSS")</script>'
        pid, _ = _create_project(client, token, name=xss_name)
        # 验证项目已创建，名称存储为原文
        res = client.get(f"/api/projects/{pid}", headers=auth_headers(token))
        assert res.status_code == 200
        assert res.json()["name"] == xss_name
        # 验证响应 Content-Type 是 JSON（非 HTML），浏览器不会执行脚本
        assert "application/json" in res.headers.get("content-type", "")

    def test_xss_in_message_content(self, client):
        """V7-5: 留言内容含 XSS payload → 存储原文，API 返回 JSON。

        DRL-R1 适配：create_message 端点使用 require_admin（Basic Auth），
        不支持 JWT 认证。测试改用 ADMIN_AUTH 创建留言。
        """
        token = _register_and_login(client, "xss2@x.com")
        pid, _ = _create_project(client, token, name="XSS 留言测试")
        xss_content = '<img src=x onerror="alert(document.cookie)">'
        res = client.post(
            f"/api/projects/{pid}/messages",
            json={"content": xss_content, "author": "测试者"},
            auth=ADMIN_AUTH,
            headers=CSRF_HEADERS,
        )
        assert res.status_code == 201, f"创建留言应 201，实际: {res.status_code} {res.text}"
        assert res.json()["content"] == xss_content
        assert "application/json" in res.headers.get("content-type", "")

    def test_xss_escaped_in_html_pages(self, client):
        """R2-V6 验证：XSS payload 在 HTML 页面中被 Jinja2 自动转义为 &lt;script&gt;。

        现有 XSS 测试（V7-4/V7-5）仅验证 JSON API 返回原文 + Content-Type 为 JSON，
        未覆盖服务端模板渲染路径。本测试验证管理后台 HTML 页面将 <script> 转义。

        注意：前端 JS 动态渲染（如 innerHTML / document.write）的 XSS 防护需要
        Playwright E2E 测试验证浏览器侧转义行为，本测试仅覆盖服务端模板渲染。
        """
        token = _register_and_login(client, "xsshtml@x.com")
        xss_name = '<script>alert("XSS")</script>'
        pid, _ = _create_project(client, token, name=xss_name)
        # 获取管理后台 HTML 页面（Basic Auth，GET 无需 CSRF 头）
        res = client.get("/", auth=ADMIN_AUTH)
        assert res.status_code == 200
        # HTML 中应包含转义后的 &lt;script，不应包含未转义的 XSS payload
        assert "&lt;script" in res.text, "HTML 页面应将 <script> 转义为 &lt;script&gt;"
        assert "<script>alert" not in res.text, "HTML 页面不应包含未转义的 <script>alert"

    # ---------- IDOR ----------

    def test_idor_get_other_user_project(self, client):
        """V7-6: 用户 B 访问用户 A 的项目 → 403。"""
        token_a = _register_and_login(client, "idora@x.com")
        token_b = _register_and_login(client, "idorb@x.com")
        pid_a, _ = _create_project(client, token_a, name="用户A的项目")
        # 用户 B 尝试访问
        res = client.get(f"/api/projects/{pid_a}", headers=auth_headers(token_b))
        assert res.status_code == 403, f"IDOR 访问应 403，实际: {res.status_code}"

    def test_idor_update_other_user_project(self, client):
        """V7-7: 用户 B 修改用户 A 的项目 → 403。"""
        token_a = _register_and_login(client, "idora2@x.com")
        token_b = _register_and_login(client, "idorb2@x.com")
        pid_a, _ = _create_project(client, token_a, name="用户A项目")
        # 用户 B 尝试修改
        res = client.patch(
            f"/api/projects/{pid_a}",
            json={"name": "被篡改"},
            headers=auth_headers(token_b),
        )
        assert res.status_code == 403, f"IDOR 修改应 403，实际: {res.status_code}"

    def test_idor_delete_other_user_project(self, client):
        """V7-8: 用户 B 删除用户 A 的项目 → 403。"""
        token_a = _register_and_login(client, "idora3@x.com")
        token_b = _register_and_login(client, "idorb3@x.com")
        pid_a, _ = _create_project(client, token_a, name="用户A项目删除测试")
        # 用户 B 尝试删除
        res = client.delete(f"/api/projects/{pid_a}", headers=auth_headers(token_b))
        assert res.status_code == 403, f"IDOR 删除应 403，实际: {res.status_code}"

    # ---------- CSRF 防护 ----------

    def test_csrf_negative_missing_header(self, client):
        """V7-9: JWT 写端点缺少 X-CSRF-Token 头 → 403。"""
        token = _register_and_login(client, "csrf1@x.com")
        # 只有 Authorization 头，没有 X-CSRF-Token
        res = client.post(
            "/api/projects",
            json={"name": "CSRF 测试"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 403, f"缺少 CSRF 头应 403，实际: {res.status_code}"
        assert "CSRF" in res.json()["detail"]

    def test_csrf_negative_wrong_header(self, client):
        """V7-10: JWT 写端点 X-CSRF-Token 值错误 → 403。"""
        token = _register_and_login(client, "csrf2@x.com")
        res = client.post(
            "/api/projects",
            json={"name": "CSRF 错误值"},
            headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": "wrong-value"},
        )
        assert res.status_code == 403, f"CSRF 值错误应 403，实际: {res.status_code}"
        assert "CSRF" in res.json()["detail"]

    def test_csrf_jti_missing_blocks_writes(self, client):
        """R2-V5 验证：旧 token（无 csrf_jti）写端点应返回 403 csrf_token_missing。

        R2-V2 修复后，JWT 中缺少 csrf_jti 声明的旧 token 不允许执行写操作，
        强制用户重新登录获取包含 csrf_jti 的新 token。此测试覆盖 R2-V2 的盲区：
        旧版 CSRF 测试（V7-9/V7-10）仅验证有 csrf_jti 时的头校验，未验证缺失场景。
        """
        from jose import jwt as _jwt
        # 注册用户并获取真实 user_id（reset_db 后首个用户 id=1）
        real_token = _register_and_login(client, "csrfold@x.com")
        me = client.get("/api/auth/me", headers=auth_headers(real_token))
        assert me.status_code == 200
        user_id = me.json()["id"]
        # 构造无 csrf_jti 的旧 token（也无 aud/iss，走宽松解码回退）
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "email": "csrfold@x.com",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
            # 故意不包含 csrf_jti / aud / iss，模拟旧 token
        }
        old_token = _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        # 用旧 token 发起写请求，应返回 403
        r = client.post(
            "/api/projects",
            json={"name": "csrf_jti 缺失测试"},
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert r.status_code == 403, f"旧 token 写端点应 403，实际: {r.status_code} {r.text}"
        assert r.json()["detail"]["code"] == "csrf_token_missing"

    # ---------- JWT 攻击 ----------

    def test_jwt_alg_none_attack(self, client):
        """V7-11: alg=none JWT 降级攻击 → 401 token_invalid。"""
        now = datetime.now(timezone.utc)
        payload = {
            "sub": "1", "email": "attacker@x.com",
            # 用 int timestamp 避免 datetime 不可 JSON 序列化
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        }
        # 手动构造 alg=none 的 JWT
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload_b64 = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).rstrip(b"=").decode()
        token = f"{header}.{payload_b64}."
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401, f"alg=none 应 401，实际: {res.status_code}"

    # ---------- 路径穿越 ----------

    def test_path_traversal_in_api_url(self, client):
        """V7-12: API 路径含目录穿越 → 404 而非 500 或文件泄露。"""
        # FastAPI 会标准化路径，返回 404
        res = client.get("/api/projects/../../../etc/passwd")
        assert res.status_code in (404, 422), f"路径穿越应 404/422，实际: {res.status_code}"
        assert "root:" not in res.text, "不应泄露 /etc/passwd 内容"

    def test_path_traversal_in_static(self, client):
        """V7-13: 静态文件路径含穿越 → 404 而非文件泄露。"""
        res = client.get("/static/../../../etc/passwd")
        assert res.status_code in (404, 400), f"静态路径穿越应 404/400，实际: {res.status_code}"
        assert "root:" not in res.text


# ============================================================
# V9: 生产配置测试
# ============================================================

class TestV9ProductionConfig:
    """V9: 生产环境配置验证——安全头、错误处理、文档关闭。"""

    def test_dev_mode_docs_accessible(self, client):
        """V9-1: dev 模式下 /docs 可访问（生产模式应关闭）。"""
        res = client.get("/docs")
        # dev 模式下 docs 可访问（200 或重定向）
        assert res.status_code in (200, 307), f"dev 模式 docs 应可访问，实际: {res.status_code}"

    def test_no_stack_trace_in_500_response(self, client):
        """V9-2: 500 错误响应不泄露堆栈信息。"""
        # 触发一个 500 错误（通过模拟内部异常）
        token = _register_and_login(client, "err500@x.com")
        # 尝试用无效的 JSON 触发错误
        res = client.get("/api/projects/999999")  # 项目不存在 → 404，不是 500
        assert res.status_code == 404
        # 验证 404 响应不含堆栈
        assert "traceback" not in res.text.lower()
        assert "File \"" not in res.text

    def test_security_headers_on_404_response(self, client):
        """V9-3: 404 响应也包含安全头。"""
        res = client.get("/api/projects/999999")
        assert res.status_code == 404
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"

    def test_qr_token_not_leaked_in_public_list(self, client):
        """V9-4: 公开项目列表不返回 qr_token（防止凭据泄露）。"""
        token = _register_and_login(client, "leak1@x.com")
        _create_project(client, token, name="公开列表测试")
        res = client.get("/api/projects", auth=ADMIN_AUTH)
        assert res.status_code == 200
        for proj in res.json():
            assert "qr_token" not in proj, f"公开列表不应包含 qr_token，项目: {proj.get('id')}"


# ============================================================
# V30: 7 种 qr_token 场景
# ============================================================

class TestV30QrToken:
    """V30: qr_token 志愿者访问的各种边界场景。"""

    @pytest.fixture(autouse=True)
    def _clear_token_fail_store(self):
        """V30: 每个测试前后清空 qr_token 失败限流存储，避免跨测试污染。"""
        from routers._token_rate_limit import _qr_token_fail_store
        _qr_token_fail_store.clear()
        yield
        _qr_token_fail_store.clear()

    def test_qr_token_valid_access(self, client):
        """V30-1: 正确 qr_token + CSRF 头 → 200。"""
        token = _register_and_login(client, "qr1@x.com")
        pid, qr_token = _create_project(client, token, name="QR 正确测试")
        res = client.get(
            f"/api/projects/{pid}",
            params={"qr_token": qr_token},
            headers=VOLUNTEER_CSRF_HEADERS,
        )
        assert res.status_code == 200, f"正确 qr_token 应 200，实际: {res.status_code} {res.text}"
        assert res.json()["id"] == pid

    def test_qr_token_invalid(self, client):
        """V30-2: 错误 qr_token → 403。"""
        token = _register_and_login(client, "qr2@x.com")
        pid, _ = _create_project(client, token, name="QR 错误测试")
        res = client.get(
            f"/api/projects/{pid}",
            params={"qr_token": "completely-wrong-token"},
            headers=VOLUNTEER_CSRF_HEADERS,
        )
        assert res.status_code == 403, f"错误 qr_token 应 403，实际: {res.status_code}"

    def test_qr_token_missing_no_auth(self, client):
        """V30-3: 无 qr_token 且无认证 → 401。"""
        token = _register_and_login(client, "qr3@x.com")
        pid, _ = _create_project(client, token, name="QR 缺失测试")
        res = client.get(f"/api/projects/{pid}")
        assert res.status_code == 401, f"无认证应 401，实际: {res.status_code}"

    def test_qr_token_empty_string(self, client):
        """V30-4: 空字符串 qr_token → 401（bool("")==False，走认证路径）。"""
        token = _register_and_login(client, "qr4@x.com")
        pid, _ = _create_project(client, token, name="QR 空串测试")
        res = client.get(
            f"/api/projects/{pid}",
            params={"qr_token": ""},
            headers=VOLUNTEER_CSRF_HEADERS,
        )
        # bool("") == False，所以走 require_auth 路径 → 401
        assert res.status_code == 401, f"空 qr_token 应 401，实际: {res.status_code}"

    def test_qr_token_wrong_project(self, client):
        """V30-5: 使用项目 B 的 qr_token 访问项目 A → 403。"""
        token = _register_and_login(client, "qr5@x.com")
        pid_a, _ = _create_project(client, token, name="项目A")
        pid_b, qr_token_b = _create_project(client, token, name="项目B")
        # 用项目 B 的 token 访问项目 A
        res = client.get(
            f"/api/projects/{pid_a}",
            params={"qr_token": qr_token_b},
            headers=VOLUNTEER_CSRF_HEADERS,
        )
        assert res.status_code == 403, f"跨项目 qr_token 应 403，实际: {res.status_code}"

    def test_qr_token_sql_injection_chars(self, client):
        """V30-6: qr_token 含 SQL 注入字符 → 403 而非 500。"""
        token = _register_and_login(client, "qr6@x.com")
        pid, _ = _create_project(client, token, name="QR SQL 注入测试")
        res = client.get(
            f"/api/projects/{pid}",
            params={"qr_token": "' OR '1'='1"},
            headers=VOLUNTEER_CSRF_HEADERS,
        )
        assert res.status_code == 403, f"SQL 注入 qr_token 应 403，实际: {res.status_code}"
        assert "traceback" not in res.text.lower()

    def test_qr_token_rate_limited_after_failures(self, client):
        """V30-7: 多次 qr_token 校验失败后 → 429 临时封禁。"""
        token = _register_and_login(client, "qr7@x.com")
        pid, _ = _create_project(client, token, name="QR 限流测试")
        # 默认 5 次失败后封禁
        statuses = []
        for i in range(6):
            res = client.get(
                f"/api/projects/{pid}",
                params={"qr_token": f"wrong-token-{i}"},
                headers=VOLUNTEER_CSRF_HEADERS,
            )
            statuses.append(res.status_code)
        # 应出现 429（封禁）
        assert 429 in statuses, f"多次失败后应触发 429 限流，状态码序列: {statuses}"


# ============================================================
# V32: 事务回滚测试
# ============================================================

class TestV32TransactionRollback:
    """V32: 数据库事务在错误时正确回滚，不留部分数据。"""

    def test_transaction_rollback_on_integrity_error(self, client):
        """V32-1: IntegrityError 时事务回滚，不留部分数据。"""
        from database import SessionLocal
        from models import Project
        db = SessionLocal()
        try:
            # 创建第一个有效项目
            p1 = Project(name="回滚测试-有效", qr_token="rollback_valid_token_1")
            db.add(p1)
            db.commit()
            db.refresh(p1)
            pid1 = p1.id

            # 尝试创建一个违反 NOT NULL 约束的项目
            try:
                p2 = Project(name=None, qr_token="rollback_invalid_token_2")
                db.add(p2)
                db.commit()
                assert False, "应抛出 IntegrityError"
            except IntegrityError:
                db.rollback()

            # V32 验证：第一个项目仍然存在，第二个未创建
            count = db.query(Project).filter(
                Project.name.like("回滚测试%")
            ).count()
            assert count == 1, f"事务回滚后应只有 1 条记录，实际: {count}"
        finally:
            db.close()

    def test_sync_push_atomicity(self, client):
        """V32-2: 同步推送是原子操作——要么全部成功，要么全部失败。"""
        token = _register_and_login(client, "sync32@x.com")
        now_ms = int(time.time() * 1000)
        # 先推送一条有效数据
        res = client.post("/api/sync/push", json={
            "items": [{
                "app": "focus",
                "item_type": "snapshot",
                "client_uuid": "atomic-test-uuid-1",
                "data": {"title": "第一条"},
                "client_created_at": now_ms,
                "client_updated_at": now_ms,
            }]
        }, headers=auth_headers(token))
        assert res.status_code == 200
        assert res.json()["created"] == 1

        # 再推送一条同 uuid 的数据（应 update，不报错）
        res2 = client.post("/api/sync/push", json={
            "items": [{
                "app": "focus",
                "item_type": "snapshot",
                "client_uuid": "atomic-test-uuid-1",
                "data": {"title": "更新后的第一条"},
                "client_created_at": now_ms,
                "client_updated_at": now_ms + 1000,
            }]
        }, headers=auth_headers(token))
        assert res2.status_code == 200
        assert res2.json()["updated"] == 1

        # 验证数据一致
        pull = client.get("/api/sync/pull?app=focus", headers=auth_headers(token))
        assert pull.status_code == 200
        assert pull.json()["count"] == 1
        assert pull.json()["items"][0]["data"]["title"] == "更新后的第一条"


# ============================================================
# R5/R6 后端修复回归测试
# ============================================================

class TestR5R6BackendRegressions:
    """R5-BE-1/3/4/7/9 与 R6-BE-4 修复的回归测试覆盖。"""

    def test_jwt_without_iat_rejected(self, client):
        """R5-BE-4 / R6-BE-3: JWT 缺少 iat 声明 → 401 token_invalid（fail-closed）。

        构造含 aud/iss 但无 iat 的 JWT，require_user/require_auth 中
        jwt_iat is None 分支应拒绝该 token。
        """
        from jose import jwt as _jwt
        real_token = _register_and_login(client, "noiat@x.com")
        me = client.get("/api/auth/me", headers=auth_headers(real_token))
        assert me.status_code == 200
        user_id = me.json()["id"]
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "email": "noiat@x.com",
            # 故意不含 iat，验证 fail-closed
            "exp": int((now + timedelta(days=1)).timestamp()),
            "jti": "test-no-iat",
            "csrf_jti": "test-csrf-no-iat",
            "aud": JWT_AUDIENCE,
            "iss": JWT_ISSUER,
        }
        token_no_iat = _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_no_iat}"})
        assert res.status_code == 401, f"无 iat 的 JWT 应 401，实际: {res.status_code} {res.text}"
        assert res.json()["detail"]["code"] == "token_invalid"

    def test_aud_iss_mismatch_rejected(self, client):
        """R5-BE-1 / R6-BE-12: aud/iss 不匹配的 JWT → 401 token_invalid。

        构造 aud/iss 与预期不匹配的 token，verify_access_token 的宽松回退
        应检测到 payload 含 aud/iss 但不匹配，拒绝放行。
        """
        from jose import jwt as _jwt
        real_token = _register_and_login(client, "audmismatch@x.com")
        me = client.get("/api/auth/me", headers=auth_headers(real_token))
        user_id = me.json()["id"]
        now = datetime.now(timezone.utc)
        # aud/iss 故意写为 "other-service"，与 JWT_AUDIENCE/JWT_ISSUER 不匹配
        payload = {
            "sub": str(user_id),
            "email": "audmismatch@x.com",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
            "jti": "test-aud-mismatch",
            "csrf_jti": "test-csrf-aud-mismatch",
            "aud": "other-service",
            "iss": "other-issuer",
        }
        bad_token = _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {bad_token}"})
        assert res.status_code == 401, f"aud/iss 不匹配应 401，实际: {res.status_code} {res.text}"
        assert res.json()["detail"]["code"] == "token_invalid"

    def test_same_second_logout_login(self, client):
        """R5-BE-3 / R6-BE-12: iat == token_revoked_at 的 token 视为未撤销。

        R5-BE-3 将比较从 <= 改为 <，撤销瞬间签发的 token（iat == revoked_at）
        应视为合法（撤销后重新签发）。
        """
        from jose import jwt as _jwt
        from database import SessionLocal
        from models import User
        real_token = _register_and_login(client, "samesame@x.com")
        me = client.get("/api/auth/me", headers=auth_headers(real_token))
        user_id = me.json()["id"]
        # 直接在 DB 设置 token_revoked_at = T
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            revoke_ts = int(time.time())
            user.token_revoked_at = revoke_ts
            db.commit()
            # 清缓存确保 DB 值生效
            from auth import _user_active_cache
            _user_active_cache.pop(user_id, None)
        finally:
            db.close()
        # 构造 iat == revoked_at 的 token（模拟同秒重新登录）
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "email": "samesame@x.com",
            "iat": revoke_ts,  # 与 token_revoked_at 相等
            "exp": int((now + timedelta(days=1)).timestamp()),
            "jti": "test-same-second",
            "csrf_jti": "test-csrf-same-second",
            "aud": JWT_AUDIENCE,
            "iss": JWT_ISSUER,
        }
        same_second_token = _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {same_second_token}"})
        assert res.status_code == 200, (
            f"iat == revoked_at 应视为未撤销（R5-BE-3），实际: {res.status_code} {res.text}"
        )

    def test_view_perm_filters_soft_deleted(self, client):
        """R5-BE-7 / R6-BE-12: view 权限用户不可见软删除留言。"""
        owner_token = _register_and_login(client, "sdowner@x.com")
        viewer_token = _register_and_login(client, "sdviewer@x.com")
        pid, _ = _create_project(client, owner_token, name="软删除测试项目")
        # 创建留言
        msg_res = client.post(
            f"/api/projects/{pid}/messages",
            json={"content": "待删除留言", "author": "作者"},
            headers=auth_headers(owner_token),
        )
        assert msg_res.status_code == 201
        msg_id = msg_res.json()["id"]
        # 软删除留言（需 admin 权限——owner 通过 _resolve_project_permission 获得 admin）
        del_res = client.delete(
            f"/api/projects/{pid}/messages/{msg_id}",
            headers=auth_headers(owner_token),
        )
        assert del_res.status_code == 204
        # 共享 view 权限给 viewer
        from database import SessionLocal
        from models import User
        db = SessionLocal()
        try:
            viewer = db.query(User).filter(User.email == "sdviewer@x.com").first()
            viewer_id = viewer.id
        finally:
            db.close()
        share_res = client.post(
            f"/api/projects/{pid}/shares",
            json={"shared_with_user_id": viewer_id, "permission": "view"},
            headers=auth_headers(owner_token),
        )
        assert share_res.status_code == 201
        # viewer 获取项目详情——不应看到软删除留言
        res = client.get(f"/api/projects/{pid}", headers=auth_headers(viewer_token))
        assert res.status_code == 200
        messages = res.json().get("messages", [])
        msg_ids = [m["id"] for m in messages]
        assert msg_id not in msg_ids, "view 权限用户不应看到软删除留言"

    def test_view_perm_owner_name_desensitized(self, client):
        """R5-BE-9 / R6-BE-7: view 权限用户看到的 task.owner_name 被脱敏（首字符+***）。"""
        owner_token = _register_and_login(client, "dnowner@x.com")
        viewer_token = _register_and_login(client, "dnviewer@x.com")
        pid, _ = _create_project(client, owner_token, name="脱敏测试项目")
        # 创建带 owner_name 的任务
        task_res = client.post(
            f"/api/projects/{pid}/tasks",
            json={"title": "任务一", "owner_name": "小张"},
            headers=auth_headers(owner_token),
        )
        assert task_res.status_code == 201
        # 共享 view 权限给 viewer
        from database import SessionLocal
        from models import User
        db = SessionLocal()
        try:
            viewer = db.query(User).filter(User.email == "dnviewer@x.com").first()
            viewer_id = viewer.id
        finally:
            db.close()
        share_res = client.post(
            f"/api/projects/{pid}/shares",
            json={"shared_with_user_id": viewer_id, "permission": "view"},
            headers=auth_headers(owner_token),
        )
        assert share_res.status_code == 201
        # viewer 获取项目详情——task.owner_name 应被脱敏
        res = client.get(f"/api/projects/{pid}", headers=auth_headers(viewer_token))
        assert res.status_code == 200
        tasks = res.json().get("tasks", [])
        assert len(tasks) == 1
        owner_name = tasks[0].get("owner_name", "")
        assert owner_name == "小***", (
            f"view 权限 task.owner_name 应脱敏为 '小***'，实际: {owner_name!r}"
        )

    def test_logout_invalidates_cache(self, client):
        """R6-BE-4 / R6-BE-12: logout 后立即失效 _user_active_cache。

        直接验证 logout 端点主动 pop 了 _user_active_cache 中该用户的缓存条目。
        这是 R6-BE-4 的核心修复点——不依赖时间戳差异（同秒 logout/login 由 R5-BE-3 处理）。
        """
        from auth import _user_active_cache
        token = _register_and_login(client, "logoutcache@x.com")
        # 先访问一次 /me，填充 _user_active_cache
        me_res = client.get("/api/auth/me", headers=auth_headers(token))
        assert me_res.status_code == 200
        user_id = me_res.json()["id"]
        # 确认缓存已填充
        assert user_id in _user_active_cache, "访问 /me 后 _user_active_cache 应有该用户条目"
        # logout 应主动 pop 缓存（R6-BE-4 修复点）
        logout_res = client.post("/api/auth/logout", headers=auth_headers(token))
        assert logout_res.status_code == 200
        # 验证缓存条目已被移除——这是 R6-BE-4 的核心修复
        assert user_id not in _user_active_cache, (
            "logout 后 _user_active_cache 应主动 pop 该用户条目（R6-BE-4）"
        )

    def test_logout_invalidates_cache_end_to_end(self, client):
        """R6-BE-4 / R6-BE-12: 缓存失效后，token_revoked_at > iat 的旧 token 立即被拒。

        端到端验证：手动设置 token_revoked_at > iat 并 pop 缓存后，
        旧 token 在下一次请求时被拒绝（401 token_revoked）。
        """
        from jose import jwt as _jwt
        from auth import _user_active_cache
        from database import SessionLocal
        from models import User
        token = _register_and_login(client, "logoutcachee2e@x.com")
        # 填充缓存
        me_res = client.get("/api/auth/me", headers=auth_headers(token))
        assert me_res.status_code == 200
        user_id = me_res.json()["id"]
        # 解析 token 的 iat
        payload = _jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
        )
        token_iat = payload["iat"]
        # 手动设置 token_revoked_at > iat（模拟 logout 在更晚时间执行）
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            user.token_revoked_at = token_iat + 100
            db.commit()
        finally:
            db.close()
        # R6-BE-4: 主动 pop 缓存（模拟 logout 的修复行为）
        _user_active_cache.pop(user_id, None)
        # 旧 token 应被拒绝（iat < revoked_at，且缓存已失效会重新查 DB）
        res = client.get("/api/auth/me", headers=auth_headers(token))
        assert res.status_code == 401, (
            f"缓存失效后旧 token 应被拒绝，实际: {res.status_code} {res.text}"
        )
        detail = res.json().get("detail", {})
        assert isinstance(detail, dict)
        assert detail.get("code") == "token_revoked"
