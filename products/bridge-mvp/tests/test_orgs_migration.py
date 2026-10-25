# tests/test_orgs_migration.py — 阶段2.3 v8 迁移测试
# 测试目标：6 张新表创建 + 列追加 + UNIQUE/FK 约束 + 迁移幂等性 + 旧数据无回归
import os

# R3 class-level fix: 用 setdefault 而非 []=，遵循 conftest.py 约定（允许 CI/命令行覆盖）
os.environ.setdefault("BRIDGE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BRIDGE_DEBUG", "1")

import pytest
from sqlalchemy import inspect, text

from database import Base, TARGET_SCHEMA_VERSION, engine, _table_exists, _column_exists
from main import app
from fastapi.testclient import TestClient

client = TestClient(app)


# ---------- 迁移版本 ----------

def test_target_schema_version_is_12():
    """TARGET_SCHEMA_VERSION 应为 12（Phase 4.4 收尾新增 v12：加密档案表）。"""
    assert TARGET_SCHEMA_VERSION == 12


def test_migration_sets_user_version_to_12():
    """init_db 后 PRAGMA user_version 应为 12。"""
    with engine.begin() as conn:
        dbapi_conn = conn.connection
        from database import _ensure_schema_version
        _ensure_schema_version(dbapi_conn)
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA user_version")
        row = cur.fetchone()
        cur.close()
        assert row[0] == 12


# ---------- 6 张新表存在性 ----------

def test_six_new_tables_exist():
    """v8 迁移后 6 张新表应全部存在。"""
    insp = inspect(engine)
    expected = {
        "organizations", "teams", "org_memberships",
        "team_memberships", "project_shares", "org_invitations"
    }
    actual = set(insp.get_table_names())
    missing = expected - actual
    assert not missing, f"缺少表: {missing}"


# ---------- 现有表新增列 ----------

def test_projects_has_org_id_and_team_id():
    """projects 表应有 org_id 和 team_id 列（nullable）。"""
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("projects")}
    assert "org_id" in cols
    assert "team_id" in cols


def test_users_has_default_org_id():
    """users 表应有 default_org_id 列（nullable）。"""
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("users")}
    assert "default_org_id" in cols


# ---------- 软删除字段 ----------

def test_organizations_has_deleted_at():
    """organizations 表应有 deleted_at 列（软删除标记）。"""
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("organizations")}
    assert "deleted_at" in cols


def test_teams_has_deleted_at():
    """teams 表应有 deleted_at 列（软删除标记）。"""
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("teams")}
    assert "deleted_at" in cols


# ---------- token_hash 字段（补充-P1-4） ----------

def test_org_invitations_has_token_hash_not_token():
    """org_invitations 应有 token_hash 列，不应有 token 列（哈希存储）。"""
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("org_invitations")}
    assert "token_hash" in cols
    assert "token" not in cols


# ---------- UNIQUE 约束 ----------

def test_org_memberships_unique_org_user():
    """同一用户在同一组织只能有一条成员记录。"""
    from models import OrgMembership, Organization, User
    from database import SessionLocal
    from auth import hash_password

    db = SessionLocal()
    try:
        # 先创建组织和用户
        org = Organization(name="测试组织", slug="test-org", owner_id=1)
        db.add(org)
        db.flush()
        user = User(email="test@example.com", password_hash=hash_password("pass123"), display_name="测试")
        db.add(user)
        db.flush()

        # 第一条成员记录
        db.add(OrgMembership(org_id=org.id, user_id=user.id, role="member"))
        db.commit()

        # 重复插入应失败
        db.add(OrgMembership(org_id=org.id, user_id=user.id, role="admin"))
        with pytest.raises(Exception):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_team_memberships_unique_team_user():
    """同一用户在同一团队只能有一条成员记录。"""
    from models import TeamMembership, Organization, Team, User
    from database import SessionLocal
    from auth import hash_password

    db = SessionLocal()
    try:
        org = Organization(name="测试组织2", slug="test-org-2", owner_id=1)
        db.add(org)
        db.flush()
        team = Team(org_id=org.id, name="前端组", slug="frontend")
        db.add(team)
        db.flush()
        user = User(email="test2@example.com", password_hash=hash_password("pass123"), display_name="测试2")
        db.add(user)
        db.flush()

        db.add(TeamMembership(team_id=team.id, user_id=user.id, role="member"))
        db.commit()

        db.add(TeamMembership(team_id=team.id, user_id=user.id, role="team_admin"))
        with pytest.raises(Exception):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_organizations_slug_unique():
    """组织 slug 应全局唯一。"""
    from models import Organization
    from database import SessionLocal

    db = SessionLocal()
    try:
        db.add(Organization(name="组织A", slug="unique-slug", owner_id=1))
        db.commit()

        db.add(Organization(name="组织B", slug="unique-slug", owner_id=2))
        with pytest.raises(Exception):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_teams_org_slug_unique():
    """同组织内团队 slug 应唯一，不同组织可相同。"""
    from models import Organization, Team
    from database import SessionLocal

    db = SessionLocal()
    try:
        org1 = Organization(name="组织1", slug="org-1", owner_id=1)
        org2 = Organization(name="组织2", slug="org-2", owner_id=2)
        db.add_all([org1, org2])
        db.flush()

        # 同组织同 slug → 失败
        db.add(Team(org_id=org1.id, name="团队A", slug="dev"))
        db.commit()
        db.add(Team(org_id=org1.id, name="团队B", slug="dev"))
        with pytest.raises(Exception):
            db.commit()
        db.rollback()

        # 不同组织同 slug → 成功
        db.add(Team(org_id=org2.id, name="团队C", slug="dev"))
        db.commit()
    finally:
        db.close()


# ---------- project_shares 三选一 CHECK 约束 ----------

def test_project_shares_check_all_null_rejected():
    """project_shares 三个 shared_with 字段全为 NULL 应被 CHECK 约束拒绝。"""
    from database import SessionLocal

    db = SessionLocal()
    try:
        # CHECK 约束在 execute 时即触发 IntegrityError
        with pytest.raises(Exception):
            db.execute(text("""
                INSERT INTO project_shares (project_id, shared_by_user_id, permission)
                VALUES (999, 1, 'view')
            """))
        db.rollback()
    finally:
        db.close()


def test_project_shares_check_two_non_null_rejected():
    """project_shares 同时设置 org 和 team 应被 CHECK 约束拒绝。"""
    from database import SessionLocal

    db = SessionLocal()
    try:
        with pytest.raises(Exception):
            db.execute(text("""
                INSERT INTO project_shares (project_id, shared_with_org_id, shared_with_team_id, shared_by_user_id, permission)
                VALUES (999, 1, 1, 1, 'view')
            """))
        db.rollback()
    finally:
        db.close()


# ---------- FK 约束（补充-P1-5） ----------

def test_fk_org_memberships_user_invalid_rejected():
    """org_memberships.user_id 引用不存在的用户应被 FK 约束拒绝。"""
    from models import Organization, OrgMembership
    from database import SessionLocal

    db = SessionLocal()
    try:
        org = Organization(name="FK测试组织", slug="fk-org", owner_id=1)
        db.add(org)
        db.flush()

        # user_id=99999 不存在
        db.add(OrgMembership(org_id=org.id, user_id=99999, role="member"))
        with pytest.raises(Exception):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_fk_teams_org_invalid_rejected():
    """teams.org_id 引用不存在的组织应被 FK 约束拒绝。"""
    from models import Team
    from database import SessionLocal

    db = SessionLocal()
    try:
        db.add(Team(org_id=99999, name="孤儿团队", slug="orphan"))
        with pytest.raises(Exception):
            db.commit()
        db.rollback()
    finally:
        db.close()


# ---------- 迁移幂等性 ----------

def test_migration_v8_idempotent():
    """v8 迁移应幂等：重复执行不报错。"""
    with engine.begin() as conn:
        dbapi_conn = conn.connection
        from database import _run_migration_v8
        # 第一次执行
        _run_migration_v8(dbapi_conn)
        # 第二次执行（幂等）
        _run_migration_v8(dbapi_conn)


# ---------- 旧数据无回归 ----------

def test_existing_projects_unaffected_by_v8():
    """v8 迁移后旧项目的 org_id/team_id 应为 NULL（兼容旧数据）。"""
    from models import Project
    from database import SessionLocal

    db = SessionLocal()
    try:
        # 创建一个旧项目（不设置 org_id/team_id）
        proj = Project(name="旧项目", description="v7 时代的项目")
        db.add(proj)
        db.commit()

        # 刷新后 org_id/team_id 应为 None
        db.refresh(proj)
        assert proj.org_id is None
        assert proj.team_id is None
    finally:
        db.close()


def test_existing_users_default_org_id_nullable():
    """v8 迁移后旧用户的 default_org_id 应为 NULL。"""
    from models import User
    from database import SessionLocal
    from auth import hash_password

    db = SessionLocal()
    try:
        user = User(email="olduser@example.com", password_hash=hash_password("pass"), display_name="老用户")
        db.add(user)
        db.commit()
        db.refresh(user)
        assert user.default_org_id is None
    finally:
        db.close()


# ---------- ORM 模型可用性 ----------

def test_orm_models_crud():
    """6 个新模型的 ORM CRUD 应正常工作。"""
    from models import (
        Organization, Team, OrgMembership, TeamMembership,
        ProjectShare, OrgInvitation, User, Project
    )
    from database import SessionLocal
    from auth import hash_password
    import hashlib

    db = SessionLocal()
    try:
        # 创建用户
        user = User(email="crud@example.com", password_hash=hash_password("pass"), display_name="CRUD")
        db.add(user)
        db.flush()

        # 创建组织
        org = Organization(name="CRUD组织", slug="crud-org", owner_id=user.id)
        db.add(org)
        db.flush()

        # 创建团队
        team = Team(org_id=org.id, name="CRUD团队", slug="crud-team")
        db.add(team)
        db.flush()

        # 组织成员
        om = OrgMembership(org_id=org.id, user_id=user.id, role="owner")
        db.add(om)

        # 团队成员
        tm = TeamMembership(team_id=team.id, user_id=user.id, role="team_admin")
        db.add(tm)

        # 项目
        proj = Project(name="CRUD项目", owner_id=user.id, org_id=org.id, team_id=team.id)
        db.add(proj)
        db.flush()

        # 项目共享（共享给用户）
        ps = ProjectShare(
            project_id=proj.id, shared_with_user_id=user.id,
            permission="edit", shared_by_user_id=user.id
        )
        db.add(ps)

        # 邀请
        token = "random-token-32-chars-1234567890"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        inv = OrgInvitation(
            org_id=org.id, email="invited@example.com",
            token_hash=token_hash, invited_by=user.id
        )
        db.add(inv)
        db.commit()

        # 验证查询
        assert db.query(Organization).count() == 1
        assert db.query(Team).count() == 1
        assert db.query(OrgMembership).count() == 1
        assert db.query(TeamMembership).count() == 1
        assert db.query(ProjectShare).count() == 1
        assert db.query(OrgInvitation).count() == 1

        # 验证关系
        loaded_org = db.query(Organization).first()
        assert len(loaded_org.teams) == 1
        assert len(loaded_org.memberships) == 1
    finally:
        db.close()
