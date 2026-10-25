# tests/test_crypto_profile.py — Phase 4.4 收尾：端到端加密档案端点测试
#
# 覆盖 /api/auth/me/crypto-profile 的：
#   - 鉴权（未认证 401；写端点缺 CSRF 头 403）
#   - CRUD（未设置 404 → PUT 200 → GET 200 → 幂等 PUT → 覆盖 PUT）
#   - 用户隔离（A 的档案不会被 B 读到）
#   - 入参校验（缺 salt / iterations 越界 → 422）
#   - 安全边界（档案里不含 passphrase；库中只存公开参数与密文）
#
# 设计依据：salt 是 PBKDF2 公开盐值（不是密钥），可以安全上云；
# passphrase 永不上行、永不落库。

from fastapi.testclient import TestClient

from conftest import auth_headers
from database import SessionLocal
from models import UserCryptoProfile


PROFILE_A = {
    "salt": "c2FsdHNhbHRzYWx0c2FsdA==",
    "iterations": 100000,
    "kdf": "PBKDF2-SHA256-100000",
    "salt_fingerprint": "a1b2c3d4",
    "verification": {
        "ciphertext": "dmVyaWZpY2F0aW9uY2lwaGVydGV4dA==",
        "nonce": "bm9uY2UxMjM0NTY3OA==",
    },
}


def _register(client, email, password="testpass12345"):
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": email.split("@")[0]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


# ---------- 鉴权 ----------

def test_get_crypto_profile_requires_auth(client):
    resp = client.get("/api/auth/me/crypto-profile")
    assert resp.status_code == 401


def test_put_crypto_profile_requires_auth(client):
    resp = client.put("/api/auth/me/crypto-profile", json=PROFILE_A)
    assert resp.status_code == 401


def test_put_crypto_profile_requires_csrf_header(client):
    """写端点必须带 X-CSRF-Token（等于 JWT.csrf_jti），否则 403。"""
    token = _register(client, "csrf@example.com")
    resp = client.put(
        "/api/auth/me/crypto-profile",
        json=PROFILE_A,
        headers={"Authorization": f"Bearer {token}"},  # 故意不带 X-CSRF-Token
    )
    assert resp.status_code == 403
    assert "CSRF" in resp.text


# ---------- CRUD ----------

def test_get_profile_returns_404_when_not_set(client):
    token = _register(client, "noset@example.com")
    resp = client.get("/api/auth/me/crypto-profile", headers=auth_headers(token))
    assert resp.status_code == 404
    assert "加密档案" in resp.text


def test_put_then_get_profile_roundtrip(client):
    token = _register(client, "roundtrip@example.com")
    headers = auth_headers(token)

    put_resp = client.put("/api/auth/me/crypto-profile", json=PROFILE_A, headers=headers)
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["salt"] == PROFILE_A["salt"]
    assert put_resp.json()["verification"]["ciphertext"] == PROFILE_A["verification"]["ciphertext"]

    get_resp = client.get("/api/auth/me/crypto-profile", headers=headers)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["salt"] == PROFILE_A["salt"]
    assert body["iterations"] == PROFILE_A["iterations"]
    assert body["kdf"] == PROFILE_A["kdf"]
    assert body["salt_fingerprint"] == PROFILE_A["salt_fingerprint"]
    assert body["verification"]["nonce"] == PROFILE_A["verification"]["nonce"]


def test_put_profile_is_idempotent_single_row(client):
    """同一 salt 重复 PUT 不应产生第二行档案。"""
    token = _register(client, "idem@example.com")
    headers = auth_headers(token)

    client.put("/api/auth/me/crypto-profile", json=PROFILE_A, headers=headers)
    client.put("/api/auth/me/crypto-profile", json=PROFILE_A, headers=headers)
    client.put("/api/auth/me/crypto-profile", json=PROFILE_A, headers=headers)

    db = SessionLocal()
    try:
        rows = db.query(UserCryptoProfile).all()
        assert len(rows) == 1, f"应只有 1 行档案，实际 {len(rows)}"
    finally:
        db.close()


def test_put_profile_conflicts_on_different_salt(client):
    """盐值不同 ⇒ 视为另一台设备的档案，服务端拒绝覆盖（否则毁掉它的历史密文）。"""
    token = _register(client, "conflict@example.com")
    headers = auth_headers(token)

    client.put("/api/auth/me/crypto-profile", json=PROFILE_A, headers=headers)
    new_profile = dict(PROFILE_A)
    new_profile["salt"] = "bmV3c2FsdGJhc2U2NGJ5dGVz"
    new_profile["salt_fingerprint"] = "ffff0000"
    resp = client.put("/api/auth/me/crypto-profile", json=new_profile, headers=headers)
    assert resp.status_code == 409, f"应返回 409 冲突，实际 {resp.status} {resp.text}"
    assert "crypto_profile_conflict" in resp.text

    # 远端仍是原档案，未被覆盖
    get_resp = client.get("/api/auth/me/crypto-profile", headers=headers)
    assert get_resp.json()["salt"] == PROFILE_A["salt"]
    assert get_resp.json()["salt_fingerprint"] == PROFILE_A["salt_fingerprint"]


# ---------- 用户隔离 ----------

def test_profile_is_scoped_to_own_user(client):
    """A 设的档案不应被 B 读到。"""
    token_a = _register(client, "user-a@example.com")
    token_b = _register(client, "user-b@example.com")

    client.put("/api/auth/me/crypto-profile", json=PROFILE_A, headers=auth_headers(token_a))

    resp_b = client.get("/api/auth/me/crypto-profile", headers=auth_headers(token_b))
    assert resp_b.status_code == 404, "B 不应看到 A 的档案"

    # B 写自己的档案后，各自独立
    profile_b = dict(PROFILE_A)
    profile_b["salt"] = "c2FsdGJ5dXNlcjEyMzQ1Njc4OTA="
    client.put("/api/auth/me/crypto-profile", json=profile_b, headers=auth_headers(token_b))

    resp_a = client.get("/api/auth/me/crypto-profile", headers=auth_headers(token_a))
    resp_b2 = client.get("/api/auth/me/crypto-profile", headers=auth_headers(token_b))
    assert resp_a.json()["salt"] == PROFILE_A["salt"]
    assert resp_b2.json()["salt"] == profile_b["salt"]


# ---------- 入参校验 ----------

def test_put_profile_rejects_missing_salt(client):
    token = _register(client, "nosalt@example.com")
    payload = {k: v for k, v in PROFILE_A.items() if k != "salt"}
    resp = client.put("/api/auth/me/crypto-profile", json=payload, headers=auth_headers(token))
    assert resp.status_code == 422


def test_put_profile_rejects_out_of_range_iterations(client):
    token = _register(client, "baditer@example.com")
    payload = dict(PROFILE_A)
    payload["iterations"] = 10  # 低于下限 1000
    resp = client.put("/api/auth/me/crypto-profile", json=payload, headers=auth_headers(token))
    assert resp.status_code == 422


def test_put_profile_rejects_missing_verification(client):
    token = _register(client, "noverify@example.com")
    payload = {k: v for k, v in PROFILE_A.items() if k != "verification"}
    resp = client.put("/api/auth/me/crypto-profile", json=payload, headers=auth_headers(token))
    assert resp.status_code == 422


# ---------- 安全边界 ----------

def test_crypto_profile_schema_rejects_extra_fields(client):
    """schema 禁止额外字段 —— 客户端无法把口令等敏感内容塞进档案。"""
    token = _register(client, "extra@example.com")
    payload = dict(PROFILE_A)
    payload["passphrase"] = "must-be-rejected"
    resp = client.put("/api/auth/me/crypto-profile", json=payload, headers=auth_headers(token))
    assert resp.status_code == 422, "传入额外字段（passphrase）应被 schema 拒绝"


def test_profile_table_has_no_secret_columns():
    """档案表不应存在口令/密钥类列 —— 结构层面的硬约束。

    这条断言不依赖任何运行时值：列名集合由模型定义直接决定，
    因此实现若新增口令列，本用例会立刻变红。
    """
    cols = {c.name for c in UserCryptoProfile.__table__.columns}
    for forbidden in ("passphrase", "password", "secret", "key", "private_key", "passphrase_hash"):
        assert forbidden not in cols, f"档案表不应包含 {forbidden} 列"
    # 只允许公开参数与密文
    assert {"salt", "iterations", "kdf", "verification_ciphertext", "verification_nonce"} <= cols


def test_stored_profile_holds_only_public_params_and_ciphertext(client):
    """落库的档案只应是公开参数与密文，且不得出现口令明文。"""
    token = _register(client, "nosecret@example.com")
    headers = auth_headers(token)

    secret_profile = dict(PROFILE_A)
    secret_profile["salt"] = "c2FsdHdpdGhvdXRlc2VjcmV0"
    secret_profile["verification"] = {
        "ciphertext": "Y2lwaGVydGV4dHdvbm90bGVhc2U=",
        "nonce": "bm9uY2UwMTIzNDU2Nzg=",
    }
    client.put("/api/auth/me/crypto-profile", json=secret_profile, headers=headers)

    db = SessionLocal()
    try:
        row = db.query(UserCryptoProfile).filter(UserCryptoProfile.user_id == 1).first()
        assert row is not None
        # 口令既没有对应的列，也不会以明文形式出现在任何存储字段里
        assert not hasattr(row, "passphrase")
        assert "passphrase" not in row.verification_ciphertext.lower()
        # 存的是客户端上报的公开参数（salt 是 PBKDF2 公开盐值）
        assert row.salt == secret_profile["salt"]
        assert row.verification_ciphertext == secret_profile["verification"]["ciphertext"]
    finally:
        db.close()
