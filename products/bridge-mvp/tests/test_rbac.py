# tests/test_rbac.py — 阶段2.4 RBAC 权限决策树测试
import os

# R3 class-level fix: 用 setdefault 而非 []=，遵循 conftest.py 约定（允许 CI/命令行覆盖）
os.environ.setdefault("BRIDGE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BRIDGE_DEBUG", "1")

import pytest
from datetime import datetime, timezone

from auth import (
    _check_project_shares,
    _is_active_org_member,
    _is_active_team_member,
    _permission_ge,
    _resolve_project_permission,
)
from database import Base, SessionLocal, engine
from models import (
    Organization,
    OrgMembership,
    Project,
    ProjectShare,
    Team,
    TeamMembership,
    User,
)


# ---------- 测试辅助 ----------

def _make_user(email="user@example.com"):
    db = SessionLocal()
    u = User(email=email, password_hash="x", display_name=email)
    db.add(u)
    db.commit()
    db.refresh(u)
    uid = u.id
    db.close()
    return uid


def _make_org(name="Test Org", slug="test-org", owner_id=1):
    db = SessionLocal()
    org = Organization(name=name, slug=slug, owner_id=owner_id)
    db.add(org)
    db.commit()
    db.refresh(org)
    oid = org.id
    db.close()
    return oid


def _make_team(org_id, name="Frontend", slug="frontend"):
    db = SessionLocal()
    team = Team(org_id=org_id, name=name, slug=slug)
    db.add(team)
    db.commit()
    db.refresh(team)
    tid = team.id
    db.close()
    return tid


def _add_org_member(org_id, user_id, role="member"):
    db = SessionLocal()
    m = OrgMembership(org_id=org_id, user_id=user_id, role=role)
    db.add(m)
    db.commit()
    db.close()


def _add_team_member(team_id, user_id, role="member"):
    db = SessionLocal()
    m = TeamMembership(team_id=team_id, user_id=user_id, role=role)
    db.add(m)
    db.commit()
    db.close()


def _make_project(name="P", owner_id=None, org_id=None, team_id=None):
    db = SessionLocal()
    p = Project(name=name, owner_id=owner_id, org_id=org_id, team_id=team_id)
    db.add(p)
    db.commit()
    db.refresh(p)
    pid = p.id
    db.close()
    return pid


def _add_share(project_id, shared_by, perm="view", org_id=None, team_id=None, user_id=None):
    db = SessionLocal()
    s = ProjectShare(
        project_id=project_id,
        shared_with_org_id=org_id,
        shared_with_team_id=team_id,
        shared_with_user_id=user_id,
        permission=perm,
        shared_by_user_id=shared_by,
    )
    db.add(s)
    db.commit()
    db.close()


def _soft_delete_org(org_id):
    db = SessionLocal()
    db.query(Organization).filter(Organization.id == org_id).update(
        {"deleted_at": datetime.now(timezone.utc)}
    )
    db.commit()
    db.close()


def _soft_delete_team(team_id):
    db = SessionLocal()
    db.query(Team).filter(Team.id == team_id).update(
        {"deleted_at": datetime.now(timezone.utc)}
    )
    db.commit()
    db.close()


def _get_project(pid):
    db = SessionLocal()
    p = db.query(Project).filter(Project.id == pid).first()
    db.close()
    return p, db


# ---------- _permission_ge ----------

def test_permission_ge_admin_ge_view():
    assert _permission_ge("admin", "view") is True

def test_permission_ge_view_ge_admin():
    assert _permission_ge("view", "admin") is False

def test_permission_ge_edit_ge_edit():
    assert _permission_ge("edit", "edit") is True

def test_permission_ge_none_ge_view():
    assert _permission_ge(None, "view") is False

def test_permission_ge_admin_ge_admin():
    assert _permission_ge("admin", "admin") is True


# ---------- _is_active_org_member ----------

def test_org_member_active():
    uid = _make_user("a@x.com")
    oid = _make_org(owner_id=uid)
    _add_org_member(oid, uid, "member")
    db = SessionLocal()
    assert _is_active_org_member(db, oid, uid) == "member"
    db.close()

def test_org_member_not_member():
    uid = _make_user("b@x.com")
    oid = _make_org()
    db = SessionLocal()
    assert _is_active_org_member(db, oid, uid) is None
    db.close()

def test_org_member_org_deleted():
    uid = _make_user("c@x.com")
    oid = _make_org(owner_id=uid)
    _add_org_member(oid, uid, "admin")
    _soft_delete_org(oid)
    db = SessionLocal()
    assert _is_active_org_member(db, oid, uid) is None
    db.close()


# ---------- _is_active_team_member ----------

def test_team_member_active():
    uid = _make_user("d@x.com")
    oid = _make_org(owner_id=uid)
    tid = _make_team(oid)
    _add_team_member(tid, uid, "member")
    db = SessionLocal()
    assert _is_active_team_member(db, tid, uid) == "member"
    db.close()

def test_team_member_not_member():
    uid = _make_user("e@x.com")
    oid = _make_org()
    tid = _make_team(oid)
    db = SessionLocal()
    assert _is_active_team_member(db, tid, uid) is None
    db.close()

def test_team_member_team_deleted():
    uid = _make_user("f@x.com")
    oid = _make_org(owner_id=uid)
    tid = _make_team(oid)
    _add_team_member(tid, uid, "team_admin")
    _soft_delete_team(tid)
    db = SessionLocal()
    assert _is_active_team_member(db, tid, uid) is None
    db.close()


# ---------- _resolve_project_permission: dev admin ----------

def test_dev_admin_gets_admin():
    uid = _make_user("dev@x.com")
    pid = _make_project("P1", owner_id=uid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, 0, p) == "admin"  # user_id=0 → dev admin
    db.close()


# ---------- _resolve_project_permission: 个人项目 ----------

def test_personal_project_owner_admin():
    uid = _make_user("owner1@x.com")
    pid = _make_project("P2", owner_id=uid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid, p) == "admin"
    db.close()

def test_personal_project_non_owner_no_share_none():
    uid1 = _make_user("owner2@x.com")
    uid2 = _make_user("stranger@x.com")
    pid = _make_project("P3", owner_id=uid1)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid2, p) is None
    db.close()

def test_personal_project_user_share_view():
    uid1 = _make_user("owner3@x.com")
    uid2 = _make_user("sharee@x.com")
    pid = _make_project("P4", owner_id=uid1)
    _add_share(pid, uid1, perm="view", user_id=uid2)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid2, p) == "view"
    db.close()

def test_personal_project_user_share_edit():
    uid1 = _make_user("owner4@x.com")
    uid2 = _make_user("editor@x.com")
    pid = _make_project("P5", owner_id=uid1)
    _add_share(pid, uid1, perm="edit", user_id=uid2)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid2, p) == "edit"
    db.close()


# ---------- _resolve_project_permission: 组织项目 owner_id 特权 ----------

def test_org_project_owner_active_member_admin():
    uid = _make_user("orgowner@x.com")
    oid = _make_org(owner_id=uid)
    _add_org_member(oid, uid, "owner")
    pid = _make_project("OP1", owner_id=uid, org_id=oid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid, p) == "admin"
    db.close()

def test_org_project_owner_removed_falls_to_share():
    """补充-P1-1: owner 被移除组织后特权降级到 share 路径。"""
    uid = _make_user("removed@x.com")
    oid = _make_org(owner_id=uid)
    # owner_id == uid 但不创建 org_memberships（不是活跃成员）
    pid = _make_project("OP2", owner_id=uid, org_id=oid)
    _add_share(pid, uid, perm="view", user_id=uid)  # 有个人共享
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid, p) == "view"  # 降级到 share
    db.close()

def test_org_project_owner_removed_no_share_none():
    """owner 被移除且无 share → None。"""
    uid = _make_user("removed2@x.com")
    oid = _make_org(owner_id=uid)
    pid = _make_project("OP3", owner_id=uid, org_id=oid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid, p) is None
    db.close()


# ---------- _resolve_project_permission: 组织 owner/admin ----------

def test_org_admin_gets_admin():
    uid_owner = _make_user("o@x.com")
    uid_admin = _make_user("adm@x.com")
    oid = _make_org(owner_id=uid_owner)
    _add_org_member(oid, uid_admin, "admin")
    pid = _make_project("OP4", owner_id=uid_owner, org_id=oid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid_admin, p) == "admin"
    db.close()


# ---------- _resolve_project_permission: 团队 team_admin ----------

def test_team_admin_gets_admin():
    uid_owner = _make_user("o2@x.com")
    uid_ta = _make_user("ta@x.com")
    oid = _make_org(owner_id=uid_owner)
    tid = _make_team(oid)
    _add_org_member(oid, uid_ta, "member")
    _add_team_member(tid, uid_ta, "team_admin")
    pid = _make_project("TP1", owner_id=uid_owner, org_id=oid, team_id=tid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid_ta, p) == "admin"
    db.close()


# ---------- _resolve_project_permission: project_shares ----------

def test_org_share_permission():
    uid_owner = _make_user("o3@x.com")
    uid_member = _make_user("m@x.com")
    oid = _make_org(owner_id=uid_owner)
    _add_org_member(oid, uid_member, "member")
    pid = _make_project("SP1", owner_id=uid_owner, org_id=oid)
    _add_share(pid, uid_owner, perm="edit", org_id=oid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid_member, p) == "edit"
    db.close()

def test_team_share_permission():
    uid_owner = _make_user("o4@x.com")
    uid_tm = _make_user("tm@x.com")
    oid = _make_org(owner_id=uid_owner)
    tid = _make_team(oid)
    _add_org_member(oid, uid_tm, "member")
    _add_team_member(tid, uid_tm, "member")
    pid = _make_project("SP2", owner_id=uid_owner, org_id=oid, team_id=tid)
    _add_share(pid, uid_owner, perm="edit", team_id=tid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid_tm, p) == "edit"
    db.close()

def test_user_share_permission_in_org_project():
    uid_owner = _make_user("o5@x.com")
    uid_ext = _make_user("ext@x.com")  # 外部用户，非组织成员
    oid = _make_org(owner_id=uid_owner)
    pid = _make_project("SP3", owner_id=uid_owner, org_id=oid)
    _add_share(pid, uid_owner, perm="view", user_id=uid_ext)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid_ext, p) == "view"
    db.close()


# ---------- _resolve_project_permission: 默认角色权限 ----------

def test_org_member_default_view():
    uid_owner = _make_user("o6@x.com")
    uid_member = _make_user("m2@x.com")
    oid = _make_org(owner_id=uid_owner)
    _add_org_member(oid, uid_member, "member")
    pid = _make_project("DP1", owner_id=uid_owner, org_id=oid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid_member, p) == "view"
    db.close()

def test_team_member_default_edit():
    uid_owner = _make_user("o7@x.com")
    uid_tm = _make_user("tm2@x.com")
    oid = _make_org(owner_id=uid_owner)
    tid = _make_team(oid)
    _add_org_member(oid, uid_tm, "member")
    _add_team_member(tid, uid_tm, "member")
    pid = _make_project("DP2", owner_id=uid_owner, org_id=oid, team_id=tid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid_tm, p) == "edit"
    db.close()

def test_org_viewer_default_view():
    uid_owner = _make_user("o8@x.com")
    uid_viewer = _make_user("v@x.com")
    oid = _make_org(owner_id=uid_owner)
    _add_org_member(oid, uid_viewer, "viewer")
    pid = _make_project("DP3", owner_id=uid_owner, org_id=oid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid_viewer, p) == "view"
    db.close()


# ---------- _resolve_project_permission: 无权限 ----------

def test_non_member_no_access():
    uid_owner = _make_user("o9@x.com")
    uid_stranger = _make_user("s@x.com")
    oid = _make_org(owner_id=uid_owner)
    pid = _make_project("NP1", owner_id=uid_owner, org_id=oid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid_stranger, p) is None
    db.close()


# ---------- 幽灵权限防御（补充-P1-6）----------

def test_ghost_permission_org_deleted_share_invalidated():
    """组织软删除后，共享给该组织的 share 不再生效。"""
    uid_owner = _make_user("o10@x.com")
    uid_member = _make_user("m3@x.com")
    oid = _make_org(owner_id=uid_owner)
    _add_org_member(oid, uid_member, "member")
    pid = _make_project("GP1", owner_id=uid_owner, org_id=oid)
    _add_share(pid, uid_owner, perm="edit", org_id=oid)
    _soft_delete_org(oid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid_member, p) is None
    db.close()

def test_ghost_permission_team_deleted_share_invalidated():
    """团队软删除后，共享给该团队的 share 不再生效。

    用户仍是组织 member，默认 view 权限仍生效；
    团队 share(edit) 失效，降级到组织 member 默认 view。
    """
    uid_owner = _make_user("o11@x.com")
    uid_tm = _make_user("tm3@x.com")
    oid = _make_org(owner_id=uid_owner)
    tid = _make_team(oid)
    _add_org_member(oid, uid_tm, "member")
    _add_team_member(tid, uid_tm, "member")
    pid = _make_project("GP2", owner_id=uid_owner, org_id=oid, team_id=tid)
    _add_share(pid, uid_owner, perm="edit", team_id=tid)
    _soft_delete_team(tid)
    p, db = _get_project(pid)
    # 团队 share 失效，但组织 member 默认 view 仍生效
    assert _resolve_project_permission(db, uid_tm, p) == "view"
    db.close()

def test_ghost_permission_team_deleted_non_org_member_none():
    """团队软删除后，非组织成员的团队 member 完全无权限。"""
    uid_owner = _make_user("o11b@x.com")
    uid_tm = _make_user("tm3b@x.com")
    oid = _make_org(owner_id=uid_owner)
    tid = _make_team(oid)
    # uid_tm 只是团队成员，不是组织成员
    _add_team_member(tid, uid_tm, "member")
    pid = _make_project("GP2b", owner_id=uid_owner, org_id=oid, team_id=tid)
    _add_share(pid, uid_owner, perm="edit", team_id=tid)
    _soft_delete_team(tid)
    p, db = _get_project(pid)
    assert _resolve_project_permission(db, uid_tm, p) is None
    db.close()


# ---------- _check_project_shares 优先级 ----------

def test_share_priority_org_over_team():
    """组织共享和团队共享同时存在时，先匹配组织共享。"""
    uid_owner = _make_user("o12@x.com")
    uid_tm = _make_user("tm4@x.com")
    oid = _make_org(owner_id=uid_owner)
    tid = _make_team(oid)
    _add_org_member(oid, uid_tm, "member")
    _add_team_member(tid, uid_tm, "member")
    pid = _make_project("PP1", owner_id=uid_owner, org_id=oid, team_id=tid)
    _add_share(pid, uid_owner, perm="admin", org_id=oid)  # org share = admin
    _add_share(pid, uid_owner, perm="view", team_id=tid)  # team share = view
    p, db = _get_project(pid)
    # org share 优先 → admin
    assert _resolve_project_permission(db, uid_tm, p) == "admin"
    db.close()
