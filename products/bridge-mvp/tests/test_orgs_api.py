# tests/test_orgs_api.py — 阶段2.5 组织/团队/成员/邀请/共享端点测试
import os

# R3-V3 修复：删除强写 BRIDGE_DATABASE_URL（conftest 已 setdefault，强写会绕过命令行覆盖）。
# 保留 BRIDGE_DEBUG setdefault 作为本模块的防御性默认（conftest 同样 setdefault，幂等）。
os.environ.setdefault("BRIDGE_DEBUG", "1")

import pytest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from auth import ADMIN_PASS, ADMIN_USER
from database import Base, engine, init_db
from main import app
from conftest import auth_headers as _auth  # DRL-R1-V2: JWT 写端点需要 X-CSRF-Token 头
from models import (
    OrgInvitation,
    OrgMembership,
    Organization,
    ProjectShare,
    Team,
    TeamMembership,
    User,
)

# R3-V2 说明：此处保留模块级 client 而非改用 conftest 的 per-test client fixture。
# 原因：本文件含 30+ 测试函数与 3 个 helper（_register_and_login/_create_org/_create_team）
# 均直接引用模块级 client，全面改造需同步修改所有 helper 签名与调用点，工作量与回归
# 风险均较大。conftest.reset_db autouse fixture 已在每个测试前后重建表 + 清空限流存储，
# 可保证数据隔离；CSRF 通过 _auth(token) 从 JWT 提取 csrf_jti 注入 X-CSRF-Token 头，
# 不依赖 conftest fixture 设置的 CSRF cookie。后续如需 per-test TestClient 隔离，
# 应统一改造 helper 接受 client 参数并逐个测试添加 client 形参。
client = TestClient(app)

ADMIN_AUTH = (ADMIN_USER, ADMIN_PASS)


# ---------- 辅助 ----------

def _register_and_login(email="org@example.com", password="testpass123"):
    client.post("/api/auth/register", json={
        "email": email, "password": password, "display_name": "用户"
    })
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]


def _create_org(token, name="测试组织", slug=None):
    payload = {"name": name}
    if slug:
        payload["slug"] = slug
    res = client.post("/api/orgs", json=payload, headers=_auth(token))
    assert res.status_code == 201, res.text
    return res.json()


def _create_team(token, org_id, name="测试团队", slug=None):
    payload = {"name": name}
    if slug:
        payload["slug"] = slug
    res = client.post(f"/api/orgs/{org_id}/teams", json=payload, headers=_auth(token))
    assert res.status_code == 201, res.text
    return res.json()


# ============================================================
# 组织管理
# ============================================================


def test_create_org_auto_owner():
    token = _register_and_login("owner1@example.com")
    org = _create_org(token, "公益组织A", slug="org-a")
    assert org["name"] == "公益组织A"
    assert org["slug"] == "org-a"
    assert org["owner_id"] > 0
    # 创建者应是 owner
    members = client.get(f"/api/orgs/{org['id']}/members", headers=_auth(token)).json()
    assert len(members) == 1
    assert members[0]["role"] == "owner"


def test_create_org_slug_conflict():
    token = _register_and_login("owner2@example.com")
    _create_org(token, "组织1", slug="dup")
    res = client.post("/api/orgs", json={"name": "组织2", "slug": "dup"}, headers=_auth(token))
    assert res.status_code == 409


def test_list_my_orgs():
    token = _register_and_login("owner3@example.com")
    _create_org(token, "组织A", slug="a")
    _create_org(token, "组织B", slug="b")
    res = client.get("/api/orgs", headers=_auth(token))
    assert res.status_code == 200
    assert len(res.json()) == 2


def test_get_org_detail_requires_member():
    owner_token = _register_and_login("owner4@example.com")
    other_token = _register_and_login("other4@example.com")
    org = _create_org(owner_token, "组织", slug="org4")
    # 非成员 403
    res = client.get(f"/api/orgs/{org['id']}", headers=_auth(other_token))
    assert res.status_code == 403
    # 成员 200
    res = client.get(f"/api/orgs/{org['id']}", headers=_auth(owner_token))
    assert res.status_code == 200
    assert res.json()["id"] == org["id"]  # R2-V8: 验证返回体一致性
    assert res.json()["name"] == "组织"


def test_update_org_requires_admin():
    owner_token = _register_and_login("owner5@example.com")
    member_token = _register_and_login("member5@example.com")
    org = _create_org(owner_token, "组织", slug="org5")
    # 邀请 member 加入
    from database import SessionLocal
    db = SessionLocal()
    member_user = db.query(User).filter(User.email == "member5@example.com").first()
    db.add(OrgMembership(org_id=org["id"], user_id=member_user.id, role="member"))
    db.commit()
    db.close()
    # member 尝试 PATCH → 403
    res = client.patch(
        f"/api/orgs/{org['id']}", json={"name": "改名"},
        headers=_auth(member_token),
    )
    assert res.status_code == 403
    # owner PATCH → 200
    res = client.patch(
        f"/api/orgs/{org['id']}", json={"name": "改名"},
        headers=_auth(owner_token),
    )
    assert res.status_code == 200
    assert res.json()["name"] == "改名"


def test_delete_org_only_owner():
    owner_token = _register_and_login("owner6@example.com")
    admin_token = _register_and_login("admin6@example.com")
    org = _create_org(owner_token, "组织", slug="org6")
    from database import SessionLocal
    db = SessionLocal()
    admin_user = db.query(User).filter(User.email == "admin6@example.com").first()
    db.add(OrgMembership(org_id=org["id"], user_id=admin_user.id, role="admin"))
    db.commit()
    db.close()
    # admin 尝试删除 → 403
    res = client.delete(f"/api/orgs/{org['id']}", headers=_auth(admin_token))
    assert res.status_code == 403
    # owner 删除 → 204
    res = client.delete(f"/api/orgs/{org['id']}", headers=_auth(owner_token))
    assert res.status_code == 204
    # 二次访问 → 403（已软删除 → 非活跃成员 → 403）
    res = client.get(f"/api/orgs/{org['id']}", headers=_auth(owner_token))
    assert res.status_code == 403  # 已软删除 → 非活跃成员 → 403


def test_delete_org_cascades_team_and_shares():
    owner_token = _register_and_login("owner7@example.com")
    org = _create_org(owner_token, "组织", slug="org7")
    team = _create_team(owner_token, org["id"], "团队", slug="t7")
    # 创建一个项目
    p_res = client.post("/api/projects", json={"name": "P"}, headers=_auth(owner_token))
    pid = p_res.json()["id"]
    # 共享给组织
    client.post(f"/api/projects/{pid}/shares", json={
        "shared_with_org_id": org["id"], "permission": "view"
    }, headers=_auth(owner_token))
    # 删除组织
    client.delete(f"/api/orgs/{org['id']}", headers=_auth(owner_token))
    # 验证 team 已软删除
    from database import SessionLocal
    db = SessionLocal()
    t = db.query(Team).filter(Team.id == team["id"]).first()
    assert t.deleted_at is not None
    # 验证 project_shares 已硬删除
    shares = db.query(ProjectShare).filter(ProjectShare.shared_with_org_id == org["id"]).all()
    assert len(shares) == 0
    db.close()


# ============================================================
# 团队管理
# ============================================================


def test_create_team_requires_org_admin():
    owner_token = _register_and_login("owner8@example.com")
    member_token = _register_and_login("member8@example.com")
    org = _create_org(owner_token, "组织", slug="org8")
    from database import SessionLocal
    db = SessionLocal()
    member_user = db.query(User).filter(User.email == "member8@example.com").first()
    db.add(OrgMembership(org_id=org["id"], user_id=member_user.id, role="member"))
    db.commit()
    db.close()
    # member 创建 → 403
    res = client.post(
        f"/api/orgs/{org['id']}/teams", json={"name": "T"},
        headers=_auth(member_token),
    )
    assert res.status_code == 403
    # owner 创建 → 201
    res = client.post(
        f"/api/orgs/{org['id']}/teams", json={"name": "T"},
        headers=_auth(owner_token),
    )
    assert res.status_code == 201


def test_list_teams():
    owner_token = _register_and_login("owner9@example.com")
    org = _create_org(owner_token, "组织", slug="org9")
    _create_team(owner_token, org["id"], "团队A", slug="a")
    _create_team(owner_token, org["id"], "团队B", slug="b")
    res = client.get(f"/api/orgs/{org['id']}/teams", headers=_auth(owner_token))
    assert res.status_code == 200
    assert len(res.json()) == 2


def test_delete_team_cascades_memberships():
    owner_token = _register_and_login("owner10@example.com")
    org = _create_org(owner_token, "组织", slug="org10")
    team = _create_team(owner_token, org["id"], "团队", slug="t10")
    from database import SessionLocal
    db = SessionLocal()
    # 验证 owner 自动加入为 team_admin
    tms = db.query(TeamMembership).filter(TeamMembership.team_id == team["id"]).all()
    assert len(tms) == 1
    assert tms[0].role == "team_admin"
    # 删除团队
    client.delete(f"/api/orgs/{org['id']}/teams/{team['id']}", headers=_auth(owner_token))
    # 成员关系已清理
    tms_after = db.query(TeamMembership).filter(TeamMembership.team_id == team["id"]).all()
    assert len(tms_after) == 0
    db.close()


# ============================================================
# 组织成员管理
# ============================================================


def test_list_org_members():
    owner_token = _register_and_login("owner11@example.com")
    org = _create_org(owner_token, "组织", slug="org11")
    res = client.get(f"/api/orgs/{org['id']}/members", headers=_auth(owner_token))
    assert res.status_code == 200
    assert len(res.json()) == 1


def test_update_org_member_role_cannot_modify_owner():
    owner_token = _register_and_login("owner12@example.com")
    org = _create_org(owner_token, "组织", slug="org12")
    from database import SessionLocal
    db = SessionLocal()
    owner_user = db.query(User).filter(User.email == "owner12@example.com").first()
    db.close()
    res = client.patch(
        f"/api/orgs/{org['id']}/members/{owner_user.id}",
        json={"role": "admin"},
        headers=_auth(owner_token),
    )
    assert res.status_code == 400


def test_remove_org_member_cascades_team_memberships():
    owner_token = _register_and_login("owner13@example.com")
    member_token = _register_and_login("member13@example.com")
    org = _create_org(owner_token, "组织", slug="org13")
    team = _create_team(owner_token, org["id"], "团队", slug="t13")
    from database import SessionLocal
    db = SessionLocal()
    member_user = db.query(User).filter(User.email == "member13@example.com").first()
    # 加入组织
    db.add(OrgMembership(org_id=org["id"], user_id=member_user.id, role="member"))
    db.commit()
    # 加入团队
    db.add(TeamMembership(team_id=team["id"], user_id=member_user.id, role="member"))
    db.commit()
    # 移除组织成员
    res = client.delete(
        f"/api/orgs/{org['id']}/members/{member_user.id}",
        headers=_auth(owner_token),
    )
    assert res.status_code == 204
    # 验证团队成员关系也清理了
    tms = db.query(TeamMembership).filter(
        TeamMembership.team_id == team["id"],
        TeamMembership.user_id == member_user.id,
    ).all()
    assert len(tms) == 0
    db.close()


def test_cannot_remove_owner():
    owner_token = _register_and_login("owner14@example.com")
    org = _create_org(owner_token, "组织", slug="org14")
    from database import SessionLocal
    db = SessionLocal()
    owner_user = db.query(User).filter(User.email == "owner14@example.com").first()
    db.close()
    res = client.delete(
        f"/api/orgs/{org['id']}/members/{owner_user.id}",
        headers=_auth(owner_token),
    )
    assert res.status_code == 400


# ============================================================
# 团队成员管理
# ============================================================


def test_add_team_member_must_be_org_member():
    owner_token = _register_and_login("owner15@example.com")
    outsider_token = _register_and_login("outsider15@example.com")
    org = _create_org(owner_token, "组织", slug="org15")
    team = _create_team(owner_token, org["id"], "团队", slug="t15")
    from database import SessionLocal
    db = SessionLocal()
    outsider = db.query(User).filter(User.email == "outsider15@example.com").first()
    db.close()
    # outsider 不在组织中 → 添加失败
    res = client.post(
        f"/api/orgs/{org['id']}/teams/{team['id']}/members",
        json={"user_id": outsider.id, "role": "member"},
        headers=_auth(owner_token),
    )
    assert res.status_code == 400


def test_add_team_member_success():
    owner_token = _register_and_login("owner16@example.com")
    member_token = _register_and_login("member16@example.com")
    org = _create_org(owner_token, "组织", slug="org16")
    team = _create_team(owner_token, org["id"], "团队", slug="t16")
    from database import SessionLocal
    db = SessionLocal()
    member_user = db.query(User).filter(User.email == "member16@example.com").first()
    member_uid = member_user.id
    # 先加入组织
    db.add(OrgMembership(org_id=org["id"], user_id=member_uid, role="member"))
    db.commit()
    db.close()
    # 加入团队
    res = client.post(
        f"/api/orgs/{org['id']}/teams/{team['id']}/members",
        json={"user_id": member_uid, "role": "member"},
        headers=_auth(owner_token),
    )
    assert res.status_code == 201
    assert res.json()["role"] == "member"


def test_add_team_member_duplicate():
    owner_token = _register_and_login("owner17@example.com")
    member_token = _register_and_login("member17@example.com")
    org = _create_org(owner_token, "组织", slug="org17")
    team = _create_team(owner_token, org["id"], "团队", slug="t17")
    from database import SessionLocal
    db = SessionLocal()
    member_user = db.query(User).filter(User.email == "member17@example.com").first()
    member_uid = member_user.id
    db.add(OrgMembership(org_id=org["id"], user_id=member_uid, role="member"))
    db.commit()
    db.close()
    # 第一次加入
    client.post(
        f"/api/orgs/{org['id']}/teams/{team['id']}/members",
        json={"user_id": member_uid, "role": "member"},
        headers=_auth(owner_token),
    )
    # 第二次 → 409
    res = client.post(
        f"/api/orgs/{org['id']}/teams/{team['id']}/members",
        json={"user_id": member_uid, "role": "member"},
        headers=_auth(owner_token),
    )
    assert res.status_code == 409


def test_remove_team_member():
    owner_token = _register_and_login("owner18@example.com")
    member_token = _register_and_login("member18@example.com")
    org = _create_org(owner_token, "组织", slug="org18")
    team = _create_team(owner_token, org["id"], "团队", slug="t18")
    from database import SessionLocal
    db = SessionLocal()
    member_user = db.query(User).filter(User.email == "member18@example.com").first()
    member_uid = member_user.id
    db.add(OrgMembership(org_id=org["id"], user_id=member_uid, role="member"))
    db.add(TeamMembership(team_id=team["id"], user_id=member_uid, role="member"))
    db.commit()
    db.close()
    res = client.delete(
        f"/api/orgs/{org['id']}/teams/{team['id']}/members/{member_uid}",
        headers=_auth(owner_token),
    )
    assert res.status_code == 204


# ============================================================
# 邀请
# ============================================================


def test_invitation_flow():
    owner_token = _register_and_login("owner19@example.com")
    invitee_token = _register_and_login("invitee19@example.com")
    org = _create_org(owner_token, "组织", slug="org19")
    # 创建邀请
    res = client.post(
        f"/api/orgs/{org['id']}/invitations",
        json={"email": "invitee19@example.com", "role": "member"},
        headers=_auth(owner_token),
    )
    assert res.status_code == 201
    raw_token = res.json()["token"]
    assert raw_token  # 明文 token 仅返回一次
    # 接受邀请
    res = client.post(
        "/api/orgs/join",
        json={"token": raw_token},
        headers=_auth(invitee_token),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "accepted"
    # 验证已是组织成员
    members = client.get(f"/api/orgs/{org['id']}/members", headers=_auth(owner_token)).json()
    assert any(m["email"] == "invitee19@example.com" for m in members)


def test_invitation_email_mismatch():
    owner_token = _register_and_login("owner20@example.com")
    other_token = _register_and_login("other20@example.com")
    org = _create_org(owner_token, "组织", slug="org20")
    res = client.post(
        f"/api/orgs/{org['id']}/invitations",
        json={"email": "nonexistent@example.com", "role": "member"},
        headers=_auth(owner_token),
    )
    raw_token = res.json()["token"]
    # 用不匹配的账号接受 → 403
    res = client.post(
        "/api/orgs/join",
        json={"token": raw_token},
        headers=_auth(other_token),
    )
    assert res.status_code == 403


def test_invitation_revoke():
    owner_token = _register_and_login("owner21@example.com")
    org = _create_org(owner_token, "组织", slug="org21")
    res = client.post(
        f"/api/orgs/{org['id']}/invitations",
        json={"email": "revoked@example.com", "role": "member"},
        headers=_auth(owner_token),
    )
    inv_id = res.json()["id"]
    # 撤销
    res = client.delete(
        f"/api/orgs/{org['id']}/invitations/{inv_id}",
        headers=_auth(owner_token),
    )
    assert res.status_code == 204
    # 验证状态
    from database import SessionLocal
    db = SessionLocal()
    inv = db.query(OrgInvitation).filter(OrgInvitation.id == inv_id).first()
    assert inv.status == "revoked"
    db.close()


def test_invitation_token_hashed():
    owner_token = _register_and_login("owner22@example.com")
    org = _create_org(owner_token, "组织", slug="org22")
    res = client.post(
        f"/api/orgs/{org['id']}/invitations",
        json={"email": "hashed@example.com", "role": "member"},
        headers=_auth(owner_token),
    )
    raw_token = res.json()["token"]
    from database import SessionLocal
    db = SessionLocal()
    invs = db.query(OrgInvitation).all()
    assert len(invs) == 1
    # 数据库中只存哈希，不存明文
    assert invs[0].token_hash != raw_token
    assert len(invs[0].token_hash) == 64  # SHA-256 hex
    db.close()


def test_invitation_list_excludes_token():
    owner_token = _register_and_login("owner23@example.com")
    org = _create_org(owner_token, "组织", slug="org23")
    client.post(
        f"/api/orgs/{org['id']}/invitations",
        json={"email": "list@example.com", "role": "member"},
        headers=_auth(owner_token),
    )
    res = client.get(f"/api/orgs/{org['id']}/invitations", headers=_auth(owner_token))
    assert res.status_code == 200
    for inv in res.json():
        assert inv["token"] is None


# ============================================================
# 项目共享
# ============================================================


def test_create_project_share_user():
    owner_token = _register_and_login("owner24@example.com")
    other_token = _register_and_login("other24@example.com")
    # 创建项目
    p_res = client.post("/api/projects", json={"name": "P"}, headers=_auth(owner_token))
    pid = p_res.json()["id"]
    from database import SessionLocal
    db = SessionLocal()
    other_user = db.query(User).filter(User.email == "other24@example.com").first()
    db.close()
    # 共享给 other_user
    res = client.post(f"/api/projects/{pid}/shares", json={
        "shared_with_user_id": other_user.id, "permission": "view"
    }, headers=_auth(owner_token))
    assert res.status_code == 201
    # 列表
    res = client.get(f"/api/projects/{pid}/shares", headers=_auth(owner_token))
    assert res.status_code == 200
    assert len(res.json()) == 1


def test_create_project_share_duplicate():
    owner_token = _register_and_login("owner25@example.com")
    other_token = _register_and_login("other25@example.com")
    p_res = client.post("/api/projects", json={"name": "P"}, headers=_auth(owner_token))
    pid = p_res.json()["id"]
    from database import SessionLocal
    db = SessionLocal()
    other_user = db.query(User).filter(User.email == "other25@example.com").first()
    db.close()
    payload = {"shared_with_user_id": other_user.id, "permission": "view"}
    client.post(f"/api/projects/{pid}/shares", json=payload, headers=_auth(owner_token))
    res = client.post(f"/api/projects/{pid}/shares", json=payload, headers=_auth(owner_token))
    assert res.status_code == 409


def test_share_requires_three_exclusive():
    owner_token = _register_and_login("owner26@example.com")
    p_res = client.post("/api/projects", json={"name": "P"}, headers=_auth(owner_token))
    pid = p_res.json()["id"]
    # 三选一全空 → 400
    res = client.post(f"/api/projects/{pid}/shares", json={
        "permission": "view"
    }, headers=_auth(owner_token))
    assert res.status_code == 400


def test_share_delete():
    owner_token = _register_and_login("owner27@example.com")
    other_token = _register_and_login("other27@example.com")
    p_res = client.post("/api/projects", json={"name": "P"}, headers=_auth(owner_token))
    pid = p_res.json()["id"]
    from database import SessionLocal
    db = SessionLocal()
    other_user = db.query(User).filter(User.email == "other27@example.com").first()
    db.close()
    res = client.post(f"/api/projects/{pid}/shares", json={
        "shared_with_user_id": other_user.id, "permission": "view"
    }, headers=_auth(owner_token))
    share_id = res.json()["id"]
    # 撤销
    res = client.delete(f"/api/projects/{pid}/shares/{share_id}", headers=_auth(owner_token))
    assert res.status_code == 204
    # 列表为空
    res = client.get(f"/api/projects/{pid}/shares", headers=_auth(owner_token))
    assert len(res.json()) == 0


def test_share_list_requires_edit():
    owner_token = _register_and_login("owner28@example.com")
    viewer_token = _register_and_login("viewer28@example.com")
    p_res = client.post("/api/projects", json={"name": "P"}, headers=_auth(owner_token))
    pid = p_res.json()["id"]
    from database import SessionLocal
    db = SessionLocal()
    viewer_user = db.query(User).filter(User.email == "viewer28@example.com").first()
    db.close()
    # 共享 view 权限
    client.post(f"/api/projects/{pid}/shares", json={
        "shared_with_user_id": viewer_user.id, "permission": "view"
    }, headers=_auth(owner_token))
    # viewer 尝试列表 → 403（需要 edit）
    res = client.get(f"/api/projects/{pid}/shares", headers=_auth(viewer_token))
    assert res.status_code == 403


def test_share_create_requires_admin():
    owner_token = _register_and_login("owner29@example.com")
    editor_token = _register_and_login("editor29@example.com")
    p_res = client.post("/api/projects", json={"name": "P"}, headers=_auth(owner_token))
    pid = p_res.json()["id"]
    from database import SessionLocal
    db = SessionLocal()
    editor_user = db.query(User).filter(User.email == "editor29@example.com").first()
    # 共享 edit 给 editor
    client.post(f"/api/projects/{pid}/shares", json={
        "shared_with_user_id": editor_user.id, "permission": "edit"
    }, headers=_auth(owner_token))
    # R3-V4 修复：删除死代码 another_user_id = editor_user.id（未使用）；
    # 将可疑表达式 owner_token and 999（依赖 truthy 字符串恒为 999）改为直接字面量 999。
    res = client.post(f"/api/projects/{pid}/shares", json={
        "shared_with_user_id": 999, "permission": "view"
    }, headers=_auth(editor_token))
    assert res.status_code == 403
    db.close()


# ============================================================
# 鉴权边界
# ============================================================


def test_org_endpoints_require_auth():
    res = client.get("/api/orgs")
    assert res.status_code == 401
    res = client.post("/api/orgs", json={"name": "X"})
    assert res.status_code == 401


def test_team_not_in_org_404():
    owner_token = _register_and_login("owner30@example.com")
    org = _create_org(owner_token, "组织", slug="org30")
    # 不存在的 team_id
    res = client.get(f"/api/orgs/{org['id']}/teams/9999", headers=_auth(owner_token))
    assert res.status_code == 403  # 非团队成员 → 403
