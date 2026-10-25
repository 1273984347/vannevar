# routers/orgs_router.py — 阶段2.5 组织/团队/成员/邀请/共享端点
"""嵌套多租户 RBAC 端点。

路由分组：
- 组织管理   /api/orgs
- 团队管理   /api/orgs/{org_id}/teams
- 组织成员   /api/orgs/{org_id}/members
- 团队成员   /api/orgs/{org_id}/teams/{team_id}/members
- 邀请       /api/orgs/{org_id}/invitations + /api/orgs/join
- 项目共享   /api/projects/{project_id}/shares
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import (
    require_org_admin,
    require_org_member,
    require_org_owner,
    require_project_access,
    require_team_admin,
    require_team_member,
    require_user,
)
from database import get_db
from models import (
    Organization,
    OrgInvitation,
    OrgMembership,
    Project,
    ProjectShare,
    Team,
    TeamMembership,
    User,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Schemas
# ============================================================

ORG_ROLES = {"owner", "admin", "member", "viewer"}
TEAM_ROLES = {"team_admin", "member"}
SHARE_PERMISSIONS = {"view", "edit", "admin"}
INVITATION_EXPIRE_DAYS = 7
# DRL-R1-V21: 允许的组织套餐类型
ALLOWED_PLANS = {"free", "pro", "enterprise"}


def _strip(v: Optional[str]) -> Optional[str]:
    return v.strip() if isinstance(v, str) else v


def _utcnow() -> datetime:
    """返回 naive UTC datetime（与 SQLite 存储一致）。

    R2-V12: 使用 datetime.now(timezone.utc).replace(tzinfo=None) 替代已弃用的 utcnow()，
    保持返回 naive UTC datetime 与现有 DB 时间戳格式兼容。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _generate_slug(name: str) -> str:
    """从名称生成 slug（简单实现：小写 + 非字母数字转 -）。"""
    import re
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    return slug or f"org-{secrets.token_hex(3)}"


# ---------- 组织 ----------
class OrgCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=120)
    slug: Optional[str] = Field(default=None, max_length=60)
    plan: str = Field(default="free", max_length=20)

    _strip = field_validator("name", "slug", "plan", mode="before")(_strip)

    # DRL-R1-V21: 校验 plan 必须在允许的套餐类型中
    @field_validator("plan")
    @classmethod
    def plan_must_be_allowed(cls, v):
        if v not in ALLOWED_PLANS:
            raise ValueError(f"plan 必须是 {sorted(ALLOWED_PLANS)} 之一")
        return v


class OrgUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    plan: Optional[str] = Field(default=None, max_length=20)

    _strip = field_validator("name", "plan", mode="before")(_strip)

    # DRL-R1-V21: 校验 plan 必须在允许的套餐类型中（与 OrgCreate 一致）
    @field_validator("plan")
    @classmethod
    def plan_must_be_allowed(cls, v):
        if v is None:
            return v
        if v not in ALLOWED_PLANS:
            raise ValueError(f"plan 必须是 {sorted(ALLOWED_PLANS)} 之一")
        return v


class OrgOut(BaseModel):
    id: int
    name: str
    slug: str
    owner_id: int
    plan: str
    max_members: int
    deleted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


# ---------- 团队 ----------
class TeamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=120)
    slug: Optional[str] = Field(default=None, max_length=60)
    description: str = Field(default="", max_length=2000)

    _strip = field_validator("name", "slug", "description", mode="before")(_strip)


class TeamUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)

    _strip = field_validator("name", "description", mode="before")(_strip)


class TeamOut(BaseModel):
    id: int
    org_id: int
    name: str
    slug: str
    description: str
    deleted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


# ---------- 成员 ----------
class OrgMemberOut(BaseModel):
    user_id: int
    role: str
    joined_at: Optional[datetime] = None
    email: Optional[str] = None
    display_name: Optional[str] = None
    model_config = {"from_attributes": True}


class OrgMemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str

    _strip = field_validator("role", mode="before")(_strip)

    @field_validator("role")
    @classmethod
    def role_must_be_allowed(cls, v):
        if v not in ORG_ROLES:
            raise ValueError(f"role 必须是 {sorted(ORG_ROLES)} 之一")
        return v


class TeamMemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int
    role: str = "member"

    _strip = field_validator("role", mode="before")(_strip)

    @field_validator("role")
    @classmethod
    def role_must_be_allowed(cls, v):
        if v not in TEAM_ROLES:
            raise ValueError(f"role 必须是 {sorted(TEAM_ROLES)} 之一")
        return v


class TeamMemberOut(BaseModel):
    user_id: int
    role: str
    joined_at: Optional[datetime] = None
    email: Optional[str] = None
    display_name: Optional[str] = None
    model_config = {"from_attributes": True}


class TeamMemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str

    _strip = field_validator("role", mode="before")(_strip)

    @field_validator("role")
    @classmethod
    def role_must_be_allowed(cls, v):
        if v not in TEAM_ROLES:
            raise ValueError(f"role 必须是 {sorted(TEAM_ROLES)} 之一")
        return v


# ---------- 邀请 ----------
class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(..., min_length=1, max_length=255)
    role: str = "member"
    team_id: Optional[int] = None

    _strip = field_validator("email", "role", mode="before")(_strip)

    @field_validator("role")
    @classmethod
    def role_must_be_allowed(cls, v):
        if v not in ORG_ROLES:
            raise ValueError(f"role 必须是 {sorted(ORG_ROLES)} 之一")
        return v


class InvitationOut(BaseModel):
    id: int
    org_id: int
    team_id: Optional[int] = None
    email: str
    role: str
    status: str
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    # token 仅在创建时返回一次（明文），不在此 schema 持久化
    token: Optional[str] = None
    model_config = {"from_attributes": True}


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(..., min_length=1, max_length=128)

    _strip = field_validator("token", mode="before")(_strip)


# ---------- 项目共享 ----------
class ProjectShareCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shared_with_org_id: Optional[int] = None
    shared_with_team_id: Optional[int] = None
    shared_with_user_id: Optional[int] = None
    permission: str = "view"

    _strip = field_validator("permission", mode="before")(_strip)

    @field_validator("permission")
    @classmethod
    def permission_must_be_allowed(cls, v):
        if v not in SHARE_PERMISSIONS:
            raise ValueError(f"permission 必须是 {sorted(SHARE_PERMISSIONS)} 之一")
        return v


class ProjectShareOut(BaseModel):
    id: int
    project_id: int
    shared_with_org_id: Optional[int] = None
    shared_with_team_id: Optional[int] = None
    shared_with_user_id: Optional[int] = None
    permission: str
    shared_by_user_id: int
    shared_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


# ============================================================
# 辅助函数
# ============================================================


def _hash_token(token: str) -> str:
    """SHA-256(token) → 64 字符十六进制。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ensure_team_belongs_to_org(db: Session, team_id: int, org_id: int) -> Team:
    """校验团队属于该组织且未软删除。"""
    team = db.query(Team).filter(
        Team.id == team_id,
        Team.org_id == org_id,
        Team.deleted_at.is_(None),
    ).first()
    if not team:
        raise HTTPException(status_code=404, detail="团队不存在或不属于该组织")
    return team


def _ensure_user_is_org_member(db: Session, user_id: int, org_id: int) -> User:
    """校验用户是该组织的活跃成员（添加团队成员前置条件）。"""
    membership = db.query(OrgMembership).filter(
        OrgMembership.org_id == org_id,
        OrgMembership.user_id == user_id,
    ).first()
    if not membership:
        raise HTTPException(status_code=400, detail="用户必须是组织成员才能加入团队")
    return db.query(User).filter(User.id == user_id).first()


def _cascade_cleanup_org_shares(db: Session, org_id: int) -> int:
    """软删除组织时硬删除共享给该组织的 project_shares 记录。"""
    deleted = db.query(ProjectShare).filter(
        ProjectShare.shared_with_org_id == org_id
    ).delete(synchronize_session=False)
    return deleted


def _cascade_cleanup_team_shares(db: Session, team_id: int) -> int:
    """软删除团队时硬删除共享给该团队的 project_shares 记录。"""
    deleted = db.query(ProjectShare).filter(
        ProjectShare.shared_with_team_id == team_id
    ).delete(synchronize_session=False)
    return deleted


def _cascade_cleanup_team_memberships(db: Session, team_id: int) -> int:
    """软删除团队时清理团队成员关系。"""
    deleted = db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id
    ).delete(synchronize_session=False)
    return deleted


# ============================================================
# 组织管理 /api/orgs
# ============================================================


@router.post("/api/orgs", response_model=OrgOut, status_code=201, tags=["orgs"])
def create_org(
    payload: OrgCreate,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
):
    """创建组织（创建者自动成为 owner）。"""
    slug = payload.slug or _generate_slug(payload.name)
    # slug 唯一性校验
    existing = db.query(Organization).filter(
        Organization.slug == slug,
        Organization.deleted_at.is_(None),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="slug 已被占用")

    org = Organization(
        name=payload.name,
        slug=slug,
        owner_id=user_id,
        plan=payload.plan,
    )
    db.add(org)
    db.flush()  # 获取 org.id
    # 创建者自动成为 owner
    membership = OrgMembership(
        org_id=org.id,
        user_id=user_id,
        role="owner",
    )
    db.add(membership)
    db.commit()
    db.refresh(org)
    logger.info("组织创建：org_id=%d, owner=%d, slug=%s", org.id, user_id, slug)
    return org


@router.get("/api/orgs", response_model=List[OrgOut], tags=["orgs"])
def list_my_orgs(
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
):
    """列出当前用户所在的全部活跃组织。"""
    memberships = db.query(OrgMembership).filter(
        OrgMembership.user_id == user_id
    ).all()
    org_ids = [m.org_id for m in memberships]
    if not org_ids:
        return []
    orgs = db.query(Organization).filter(
        Organization.id.in_(org_ids),
        Organization.deleted_at.is_(None),
    ).all()
    return orgs


@router.get("/api/orgs/{org_id}", response_model=OrgOut, tags=["orgs"])
def get_org(
    org_id: int,
    member: tuple = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """组织详情。"""
    org = db.query(Organization).filter(
        Organization.id == org_id,
        Organization.deleted_at.is_(None),
    ).first()
    if not org:
        raise HTTPException(status_code=404, detail="组织不存在")
    return org


@router.patch("/api/orgs/{org_id}", response_model=OrgOut, tags=["orgs"])
def update_org(
    org_id: int,
    payload: OrgUpdate,
    member: tuple = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """更新组织信息（需 admin/owner）。"""
    org = db.query(Organization).filter(
        Organization.id == org_id,
        Organization.deleted_at.is_(None),
    ).first()
    if not org:
        raise HTTPException(status_code=404, detail="组织不存在")
    if payload.name is not None:
        org.name = payload.name
    if payload.plan is not None:
        org.plan = payload.plan
    db.commit()
    db.refresh(org)
    return org


@router.delete("/api/orgs/{org_id}", status_code=204, tags=["orgs"])
def delete_org(
    org_id: int,
    member: tuple = Depends(require_org_owner),
    db: Session = Depends(get_db),
):
    """删除组织（仅 owner）。软删除 + 级联清理。"""
    org = db.query(Organization).filter(
        Organization.id == org_id,
        Organization.deleted_at.is_(None),
    ).first()
    if not org:
        raise HTTPException(status_code=404, detail="组织不存在")

    # 级联清理：组织下的团队 + 团队成员关系 + 共享记录
    teams = db.query(Team).filter(
        Team.org_id == org_id,
        Team.deleted_at.is_(None),
    ).all()
    for team in teams:
        team.deleted_at = _utcnow()
        _cascade_cleanup_team_memberships(db, team.id)
        _cascade_cleanup_team_shares(db, team.id)
    # 硬删除组织级共享
    _cascade_cleanup_org_shares(db, org_id)
    # DRL-R1-V7: 级联清理组织成员关系 + 撤销 pending 邀请（原实现遗漏）
    db.query(OrgMembership).filter(OrgMembership.org_id == org_id).delete(synchronize_session=False)
    db.query(OrgInvitation).filter(
        OrgInvitation.org_id == org_id,
        OrgInvitation.status == "pending",
    ).update({"status": "revoked"}, synchronize_session=False)
    # 软删除组织
    org.deleted_at = _utcnow()
    db.commit()
    logger.info("组织删除：org_id=%d, by user=%d", org_id, member[0])
    return None


# ============================================================
# 团队管理 /api/orgs/{org_id}/teams
# ============================================================


@router.post(
    "/api/orgs/{org_id}/teams",
    response_model=TeamOut,
    status_code=201,
    tags=["teams"],
)
def create_team(
    org_id: int,
    payload: TeamCreate,
    member: tuple = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """创建团队（需组织 admin/owner）。"""
    # slug 同组织内唯一
    slug = payload.slug or _generate_slug(payload.name)
    existing = db.query(Team).filter(
        Team.org_id == org_id,
        Team.slug == slug,
        Team.deleted_at.is_(None),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="团队 slug 已被占用")

    team = Team(
        org_id=org_id,
        name=payload.name,
        slug=slug,
        description=payload.description,
    )
    db.add(team)
    db.flush()
    # 创建者自动成为 team_admin
    creator_id = member[0]
    tm = TeamMembership(
        team_id=team.id,
        user_id=creator_id,
        role="team_admin",
    )
    db.add(tm)
    db.commit()
    db.refresh(team)
    return team


@router.get(
    "/api/orgs/{org_id}/teams",
    response_model=List[TeamOut],
    tags=["teams"],
)
def list_teams(
    org_id: int,
    member: tuple = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """列出组织下全部活跃团队。"""
    return db.query(Team).filter(
        Team.org_id == org_id,
        Team.deleted_at.is_(None),
    ).all()


@router.get(
    "/api/orgs/{org_id}/teams/{team_id}",
    response_model=TeamOut,
    tags=["teams"],
)
def get_team(
    org_id: int,
    team_id: int,
    member: tuple = Depends(require_team_member),
    db: Session = Depends(get_db),
):
    """团队详情。"""
    team = _ensure_team_belongs_to_org(db, team_id, org_id)
    return team


@router.patch(
    "/api/orgs/{org_id}/teams/{team_id}",
    response_model=TeamOut,
    tags=["teams"],
)
def update_team(
    org_id: int,
    team_id: int,
    payload: TeamUpdate,
    member: tuple = Depends(require_team_admin),
    db: Session = Depends(get_db),
):
    """更新团队信息（需 team_admin 或组织 admin/owner）。"""
    team = _ensure_team_belongs_to_org(db, team_id, org_id)
    if payload.name is not None:
        team.name = payload.name
    if payload.description is not None:
        team.description = payload.description
    db.commit()
    db.refresh(team)
    return team


@router.delete(
    "/api/orgs/{org_id}/teams/{team_id}",
    status_code=204,
    tags=["teams"],
)
def delete_team(
    org_id: int,
    team_id: int,
    member: tuple = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """删除团队（需组织 admin/owner）。软删除 + 级联清理。"""
    team = _ensure_team_belongs_to_org(db, team_id, org_id)
    team.deleted_at = _utcnow()
    _cascade_cleanup_team_memberships(db, team_id)
    _cascade_cleanup_team_shares(db, team_id)
    db.commit()
    logger.info("团队删除：team_id=%d, org_id=%d, by=%d", team_id, org_id, member[0])
    return None


# ============================================================
# 组织成员 /api/orgs/{org_id}/members
# ============================================================


@router.get(
    "/api/orgs/{org_id}/members",
    response_model=List[OrgMemberOut],
    tags=["members"],
)
def list_org_members(
    org_id: int,
    member: tuple = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """组织成员列表。"""
    # R5-BE-10 修复：N+1 查询改为 JOIN，一次性获取成员关系与用户信息
    # R6-BE-10 修复：JOIN 改为 outerjoin，保留孤儿 membership 记录（User 被硬删除时仍可见）
    rows = (
        db.query(OrgMembership, User)
        .outerjoin(User, User.id == OrgMembership.user_id)
        .filter(OrgMembership.org_id == org_id)
        .all()
    )
    # DRL-R1-V12: 非 admin/owner 不返回成员邮箱（防普通成员批量爬取邮箱）
    caller_role = member[1] if len(member) > 1 else "member"
    can_see_email = caller_role in ("admin", "owner")
    result = []
    for m, user in rows:
        result.append(OrgMemberOut(
            user_id=m.user_id,
            role=m.role,
            joined_at=m.joined_at,
            email=(user.email if user else None) if can_see_email else None,
            display_name=user.display_name if user else None,
        ))
    return result


@router.patch(
    "/api/orgs/{org_id}/members/{user_id}",
    response_model=OrgMemberOut,
    tags=["members"],
)
def update_org_member_role(
    org_id: int,
    user_id: int,
    payload: OrgMemberRoleUpdate,
    member: tuple = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """修改组织成员角色（需 admin/owner）。

    补充-P1-2: 不能降级 owner（仅 owner 自己可转让，本期不实现转让）。
    """
    target = db.query(OrgMembership).filter(
        OrgMembership.org_id == org_id,
        OrgMembership.user_id == user_id,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="成员不存在")
    if target.role == "owner":
        raise HTTPException(status_code=400, detail="不能修改 owner 角色")
    # DRL-R1-V16: 禁止 admin 将自己降级为 member/viewer（需先转让管理权，避免组织失去管理员）
    if user_id == member[0] and payload.role not in ("admin", "owner"):
        raise HTTPException(status_code=400, detail="不能将自己降级为普通成员，请先转让管理权")
    target.role = payload.role
    db.commit()
    db.refresh(target)
    user = db.query(User).filter(User.id == user_id).first()
    return OrgMemberOut(
        user_id=target.user_id,
        role=target.role,
        joined_at=target.joined_at,
        email=user.email if user else None,
        display_name=user.display_name if user else None,
    )


@router.delete(
    "/api/orgs/{org_id}/members/{user_id}",
    status_code=204,
    tags=["members"],
)
def remove_org_member(
    org_id: int,
    user_id: int,
    member: tuple = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """移除组织成员（需 admin/owner）。同时移除其全部团队成员关系。"""
    target = db.query(OrgMembership).filter(
        OrgMembership.org_id == org_id,
        OrgMembership.user_id == user_id,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="成员不存在")
    if target.role == "owner":
        raise HTTPException(status_code=400, detail="不能移除 owner")
    # 级联移除团队成员关系
    team_ids = [t.id for t in db.query(Team).filter(
        Team.org_id == org_id, Team.deleted_at.is_(None)
    ).all()]
    if team_ids:
        db.query(TeamMembership).filter(
            TeamMembership.team_id.in_(team_ids),
            TeamMembership.user_id == user_id,
        ).delete(synchronize_session=False)
    db.delete(target)
    db.commit()
    logger.info("移除组织成员：org=%d, user=%d, by=%d", org_id, user_id, member[0])
    return None


# ============================================================
# 团队成员 /api/orgs/{org_id}/teams/{team_id}/members
# ============================================================


@router.get(
    "/api/orgs/{org_id}/teams/{team_id}/members",
    response_model=List[TeamMemberOut],
    tags=["members"],
)
def list_team_members(
    org_id: int,
    team_id: int,
    member: tuple = Depends(require_team_member),
    db: Session = Depends(get_db),
):
    """团队成员列表。"""
    _ensure_team_belongs_to_org(db, team_id, org_id)
    # R5-BE-10 修复：N+1 查询改为 JOIN，一次性获取成员关系与用户信息
    # R6-BE-10 修复：JOIN 改为 outerjoin，保留孤儿 membership 记录（User 被硬删除时仍可见）
    rows = (
        db.query(TeamMembership, User)
        .outerjoin(User, User.id == TeamMembership.user_id)
        .filter(TeamMembership.team_id == team_id)
        .all()
    )
    # R6-BE-11 修复：email 可见性控制——仅 team_admin / 组织 admin / owner 可见邮箱，
    #   普通成员不可见，防止批量爬取邮箱。与 list_org_members 对齐。
    caller_role = member[1] if len(member) > 1 else "member"
    can_see_email = caller_role in ("team_admin", "admin", "owner")
    result = []
    for m, user in rows:
        result.append(TeamMemberOut(
            user_id=m.user_id,
            role=m.role,
            joined_at=m.joined_at,
            email=(user.email if user else None) if can_see_email else None,
            display_name=user.display_name if user else None,
        ))
    return result


@router.post(
    "/api/orgs/{org_id}/teams/{team_id}/members",
    response_model=TeamMemberOut,
    status_code=201,
    tags=["members"],
)
def add_team_member(
    org_id: int,
    team_id: int,
    payload: TeamMemberAdd,
    member: tuple = Depends(require_team_admin),
    db: Session = Depends(get_db),
):
    """添加团队成员（需 team_admin 或组织 admin/owner）。

    补充-P1-4: 必须先校验该用户已是组织成员，否则拒绝。
    """
    team = _ensure_team_belongs_to_org(db, team_id, org_id)
    _ensure_user_is_org_member(db, payload.user_id, org_id)

    existing = db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id,
        TeamMembership.user_id == payload.user_id,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="用户已是团队成员")

    tm = TeamMembership(
        team_id=team_id,
        user_id=payload.user_id,
        role=payload.role,
    )
    db.add(tm)
    db.commit()
    db.refresh(tm)
    user = db.query(User).filter(User.id == tm.user_id).first()
    return TeamMemberOut(
        user_id=tm.user_id,
        role=tm.role,
        joined_at=tm.joined_at,
        email=user.email if user else None,
        display_name=user.display_name if user else None,
    )


@router.patch(
    "/api/orgs/{org_id}/teams/{team_id}/members/{user_id}",
    response_model=TeamMemberOut,
    tags=["members"],
)
def update_team_member_role(
    org_id: int,
    team_id: int,
    user_id: int,
    payload: TeamMemberRoleUpdate,
    member: tuple = Depends(require_team_admin),
    db: Session = Depends(get_db),
):
    """修改团队成员角色。"""
    _ensure_team_belongs_to_org(db, team_id, org_id)
    target = db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id,
        TeamMembership.user_id == user_id,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="团队成员不存在")
    target.role = payload.role
    db.commit()
    db.refresh(target)
    user = db.query(User).filter(User.id == user_id).first()
    return TeamMemberOut(
        user_id=target.user_id,
        role=target.role,
        joined_at=target.joined_at,
        email=user.email if user else None,
        display_name=user.display_name if user else None,
    )


@router.delete(
    "/api/orgs/{org_id}/teams/{team_id}/members/{user_id}",
    status_code=204,
    tags=["members"],
)
def remove_team_member(
    org_id: int,
    team_id: int,
    user_id: int,
    member: tuple = Depends(require_team_admin),
    db: Session = Depends(get_db),
):
    """移除团队成员。"""
    _ensure_team_belongs_to_org(db, team_id, org_id)
    target = db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id,
        TeamMembership.user_id == user_id,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="团队成员不存在")
    db.delete(target)
    db.commit()
    logger.info("移除团队成员：team=%d, user=%d, by=%d", team_id, user_id, member[0])
    return None


# ============================================================
# 邀请 /api/orgs/{org_id}/invitations + /api/orgs/join
# ============================================================


@router.post(
    "/api/orgs/{org_id}/invitations",
    response_model=InvitationOut,
    status_code=201,
    tags=["invitations"],
)
def create_invitation(
    org_id: int,
    payload: InvitationCreate,
    member: tuple = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """发送邀请（可指定 team_id 直接邀请到团队）。

    补充-P1-4: token 存 SHA-256 哈希，明文仅返回一次。
    """
    # DRL-R1-V11: 禁止通过邀请创建 owner 角色（防 admin 邀请新 owner 造成权限提升）
    if payload.role == "owner":
        raise HTTPException(status_code=400, detail="不能通过邀请创建 owner")
    # 校验 team 属于该组织（若指定）
    if payload.team_id is not None:
        _ensure_team_belongs_to_org(db, payload.team_id, org_id)

    # 检查 email 是否已是组织成员
    # DRL-R1-V19: 邮箱大小写不敏感查询，避免同一邮箱因大小写差异绕过去重
    existing_user = db.query(User).filter(func.lower(User.email) == func.lower(payload.email)).first()
    if existing_user:
        existing_membership = db.query(OrgMembership).filter(
            OrgMembership.org_id == org_id,
            OrgMembership.user_id == existing_user.id,
        ).first()
        if existing_membership:
            raise HTTPException(status_code=400, detail="用户已是组织成员")

    # 生成 token（明文 32 字节）+ 哈希存储
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)

    invitation = OrgInvitation(
        org_id=org_id,
        team_id=payload.team_id,
        email=payload.email.strip().lower(),
        token_hash=token_hash,
        role=payload.role,
        invited_by=member[0],
        status="pending",
        expires_at=_utcnow() + timedelta(days=INVITATION_EXPIRE_DAYS),
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    logger.info("邀请创建：org=%d, email=%s, by=%d", org_id, payload.email, member[0])
    # 明文 token 仅返回一次
    return InvitationOut(
        id=invitation.id,
        org_id=invitation.org_id,
        team_id=invitation.team_id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        token=raw_token,
    )


@router.get(
    "/api/orgs/{org_id}/invitations",
    response_model=List[InvitationOut],
    tags=["invitations"],
)
def list_invitations(
    org_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    member: tuple = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """邀请列表（不含 token）。"""
    # DRL-R1-V24: 添加分页参数，避免大量邀请一次性返回
    invitations = (
        db.query(OrgInvitation)
        .filter(OrgInvitation.org_id == org_id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [InvitationOut(
        id=i.id,
        org_id=i.org_id,
        team_id=i.team_id,
        email=i.email,
        role=i.role,
        status=i.status,
        expires_at=i.expires_at,
        created_at=i.created_at,
        token=None,
    ) for i in invitations]


@router.delete(
    "/api/orgs/{org_id}/invitations/{inv_id}",
    status_code=204,
    tags=["invitations"],
)
def revoke_invitation(
    org_id: int,
    inv_id: int,
    member: tuple = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """撤销邀请。"""
    invitation = db.query(OrgInvitation).filter(
        OrgInvitation.id == inv_id,
        OrgInvitation.org_id == org_id,
    ).first()
    if not invitation:
        raise HTTPException(status_code=404, detail="邀请不存在")
    if invitation.status != "pending":
        raise HTTPException(status_code=400, detail="仅可撤销 pending 状态的邀请")
    invitation.status = "revoked"
    db.commit()
    return None


@router.post("/api/orgs/join", tags=["invitations"])
def accept_invitation(
    payload: InvitationAccept,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
):
    """接受邀请（token）。

    流程：
    1. SHA-256(token) 查找 invitation
    2. 校验 status=pending + 未过期
    3. 校验当前登录用户邮箱与邀请邮箱一致
    4. 创建 OrgMembership（若指定 team_id 还创建 TeamMembership）
    5. 标记 invitation 为 accepted
    """
    token_hash = _hash_token(payload.token)
    invitation = db.query(OrgInvitation).filter(
        OrgInvitation.token_hash == token_hash,
    ).first()
    if not invitation:
        raise HTTPException(status_code=404, detail="邀请不存在或 token 无效")
    if invitation.status != "pending":
        raise HTTPException(status_code=400, detail=f"邀请已 {invitation.status}")
    if invitation.expires_at and invitation.expires_at < _utcnow():
        invitation.status = "expired"
        db.commit()
        raise HTTPException(status_code=400, detail="邀请已过期")

    # 校验当前用户邮箱与邀请邮箱一致
    current_user = db.query(User).filter(User.id == user_id).first()
    if not current_user:
        raise HTTPException(status_code=401, detail="用户不存在")
    # DRL-R1-V19: 邮箱大小写不敏感比较，避免大小写差异导致匹配失败
    # 修复：func.lower() 是 SQLAlchemy SQL 函数，用于 Python 字符串比较会生成
    # BinaryExpression 对象（始终 truthy），导致所有邀请接受请求都返回 403。
    # 改用 Python str.lower() 方法做大小写不敏感比较。
    if current_user.email.lower() != invitation.email.lower():
        raise HTTPException(status_code=403, detail="邀请邮箱与当前账号不匹配")

    # 校验组织未软删除
    org = db.query(Organization).filter(
        Organization.id == invitation.org_id,
        Organization.deleted_at.is_(None),
    ).first()
    if not org:
        raise HTTPException(status_code=400, detail="组织已不存在")

    # 校验用户尚未是组织成员
    existing = db.query(OrgMembership).filter(
        OrgMembership.org_id == invitation.org_id,
        OrgMembership.user_id == user_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="已是组织成员")

    # 创建组织成员关系
    membership = OrgMembership(
        org_id=invitation.org_id,
        user_id=user_id,
        role=invitation.role,
    )
    db.add(membership)

    # 若指定 team_id，同时加入团队
    if invitation.team_id is not None:
        team = db.query(Team).filter(
            Team.id == invitation.team_id,
            Team.deleted_at.is_(None),
        ).first()
        if team:
            tm = TeamMembership(
                team_id=invitation.team_id,
                user_id=user_id,
                role="member",
            )
            db.add(tm)

    # DRL-R1-V2: 原子更新 invitation.status，防止 TOCTOU 竞态创建重复 OrgMembership
    # 并发请求中只有一个能成功将 pending → accepted，其余回滚
    result = db.query(OrgInvitation).filter(
        OrgInvitation.id == invitation.id,
        OrgInvitation.status == "pending",
    ).update({"status": "accepted"}, synchronize_session=False)
    if result == 0:
        db.rollback()
        raise HTTPException(status_code=409, detail="邀请已被处理或状态已变更，请刷新后重试")

    db.commit()
    logger.info(
        "邀请接受：org=%d, user=%d, role=%s, team=%s",
        invitation.org_id, user_id, invitation.role, invitation.team_id,
    )
    return {
        "org_id": invitation.org_id,
        "role": invitation.role,
        "team_id": invitation.team_id,
        "status": "accepted",
    }


# ============================================================
# 项目共享 /api/projects/{project_id}/shares
# ============================================================


def _validate_share_target(
    db: Session,
    payload: ProjectShareCreate,
) -> None:
    """校验共享目标三选一 + 目标实体存在且未软删除。"""
    targets = [
        payload.shared_with_org_id,
        payload.shared_with_team_id,
        payload.shared_with_user_id,
    ]
    non_null = [t for t in targets if t is not None]
    if len(non_null) != 1:
        raise HTTPException(
            status_code=400,
            detail="共享目标必须三选一（org/team/user）",
        )
    if payload.shared_with_org_id is not None:
        org = db.query(Organization).filter(
            Organization.id == payload.shared_with_org_id,
            Organization.deleted_at.is_(None),
        ).first()
        if not org:
            raise HTTPException(status_code=404, detail="组织不存在")
    if payload.shared_with_team_id is not None:
        team = db.query(Team).filter(
            Team.id == payload.shared_with_team_id,
            Team.deleted_at.is_(None),
        ).first()
        if not team:
            raise HTTPException(status_code=404, detail="团队不存在")
    if payload.shared_with_user_id is not None:
        user = db.query(User).filter(
            User.id == payload.shared_with_user_id,
            User.is_active == True,  # noqa: E712
        ).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")


@router.get(
    "/api/projects/{project_id}/shares",
    response_model=List[ProjectShareOut],
    tags=["shares"],
)
def list_project_shares(
    project_id: int,
    access: tuple = Depends(require_project_access("edit")),
    db: Session = Depends(get_db),
):
    """项目共享列表（需 edit 权限）。"""
    return db.query(ProjectShare).filter(
        ProjectShare.project_id == project_id
    ).all()


@router.post(
    "/api/projects/{project_id}/shares",
    response_model=ProjectShareOut,
    status_code=201,
    tags=["shares"],
)
def create_project_share(
    project_id: int,
    payload: ProjectShareCreate,
    access: tuple = Depends(require_project_access("admin")),
    db: Session = Depends(get_db),
):
    """新增项目共享（需 admin 权限）。"""
    user_id, _ = access
    _validate_share_target(db, payload)

    # 去重：同一 project + 同一目标不重复
    existing = db.query(ProjectShare).filter(
        ProjectShare.project_id == project_id,
    )
    if payload.shared_with_org_id is not None:
        existing = existing.filter(ProjectShare.shared_with_org_id == payload.shared_with_org_id)
    elif payload.shared_with_team_id is not None:
        existing = existing.filter(ProjectShare.shared_with_team_id == payload.shared_with_team_id)
    else:
        existing = existing.filter(ProjectShare.shared_with_user_id == payload.shared_with_user_id)
    if existing.first():
        raise HTTPException(status_code=409, detail="共享已存在")

    share = ProjectShare(
        project_id=project_id,
        shared_with_org_id=payload.shared_with_org_id,
        shared_with_team_id=payload.shared_with_team_id,
        shared_with_user_id=payload.shared_with_user_id,
        permission=payload.permission,
        shared_by_user_id=user_id,
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


@router.delete(
    "/api/projects/{project_id}/shares/{share_id}",
    status_code=204,
    tags=["shares"],
)
def delete_project_share(
    project_id: int,
    share_id: int,
    access: tuple = Depends(require_project_access("admin")),
    db: Session = Depends(get_db),
):
    """撤销项目共享（需 admin 权限）。"""
    share = db.query(ProjectShare).filter(
        ProjectShare.id == share_id,
        ProjectShare.project_id == project_id,
    ).first()
    if not share:
        raise HTTPException(status_code=404, detail="共享记录不存在")
    db.delete(share)
    db.commit()
    return None
