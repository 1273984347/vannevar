# models.py — Bridge MVP 数据模型
import secrets
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index, text
from sqlalchemy.orm import relationship

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_token() -> str:
    return secrets.token_urlsafe(16)


class Project(Base):
    __tablename__ = "projects"

    # L4: DB 级 CHECK 约束，确保 status 只能是允许的值
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived', 'done')", name="ck_project_status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, default="")
    qr_token = Column(String(32), unique=True, index=True, default=generate_token)
    # 项目状态：active 进行中 / archived 已归档 / done 已完成
    # B-PERF-02: status 字段加索引，加速按状态筛选查询
    status = Column(String(20), default="active", nullable=False, index=True)
    # 交接模板 ID：volunteer / resignation / outsourcing / opensource
    # server_default 保证旧库 ALTER TABLE 时现有行获得默认值，向后兼容
    template_id = Column(String(20), default="volunteer", server_default="volunteer", nullable=False)
    created_at = Column(DateTime, default=_now)
    # B-P2-7: 此列不开 timezone=True，pages.py 传 naive UTC 与此对齐，
    # 改 timezone=True 会破坏停滞天数计算（now - updated_at 会 TypeError）。勿改。
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    # 乐观锁版本号：每次成功更新 +1，客户端更新时传入当前 version 做冲突检测，
    # 不匹配返回 409。server_default="1" 保证旧库 ALTER TABLE 时现有行获得默认值。
    version = Column(Integer, default=1, server_default="1", nullable=False)
    # 阶段1.2: 项目所有者 ID（关联 users 表）。
    # 不用 ForeignKey：dev 模式 Basic Auth 降级时 user_id=0 不存在于 users 表，FK 约束会失败。
    # nullable=True：旧数据无 owner，dev 模式创建的 owner_id=NULL。
    owner_id = Column(Integer, nullable=True, index=True)
    # 阶段2.3: 嵌套多租户归属。null=个人项目（兼容旧数据），非空=组织/团队项目。
    # 不用 ForeignKey：与 owner_id 同策略，避免 dev 模式与迁移边界问题。
    org_id = Column(Integer, nullable=True, index=True)
    team_id = Column(Integer, nullable=True, index=True)

    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")
    handoffs = relationship("Handoff", back_populates="project", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="project", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"

    # L4: DB 级 CHECK 约束，确保 status 只能是允许的值
    __table_args__ = (
        CheckConstraint("status IN ('todo', 'in_progress', 'done')", name="ck_task_status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    # B-PERF-02: status 字段加索引，加速按状态筛选查询
    status = Column(String(20), default="todo", index=True)  # todo / in_progress / done
    owner_name = Column(String(60), default="")
    created_at = Column(DateTime, default=_now)
    # B-P2-7: 此列不开 timezone=True，pages.py 传 naive UTC 与此对齐，
    # 改 timezone=True 会破坏停滞天数计算（now - updated_at 会 TypeError）。勿改。
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    # 乐观锁版本号：每次成功更新 +1，客户端更新时传入当前 version 做冲突检测，
    # 不匹配返回 409。server_default="1" 保证旧库 ALTER TABLE 时现有行获得默认值。
    version = Column(Integer, default=1, server_default="1", nullable=False)

    project = relationship("Project", back_populates="tasks")
    messages = relationship("Message", back_populates="task")  # 不级联删除，task 删除时 task_id 置空


class Handoff(Base):
    __tablename__ = "handoffs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    summary = Column(Text, nullable=False)
    outgoing_volunteer = Column(String(60), default="")
    incoming_volunteer = Column(String(60), default="")
    # P0: 交接状态 draft / confirmed；自动生成默认为 draft，志愿者确认后置为 confirmed
    status = Column(String(20), default="draft")
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by = Column(String(60), nullable=True)
    created_at = Column(DateTime, default=_now)

    project = relationship("Project", back_populates="handoffs")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    # L14: task 被删除时，消息的 task_id 级联置空（保留消息记录）
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    author = Column(String(60), default="志愿者")
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_now)
    # 内容审核：检测到敏感词时 flagged=True，warning 存提示文本，不阻止提交
    moderation_flagged = Column(Boolean, default=False, server_default="0", nullable=False)
    moderation_warning = Column(String(500), nullable=True)
    # 管理员软删除：True 时管理员视图标记「已删除」，志愿者视图不显示
    is_deleted = Column(Boolean, default=False, server_default="0", nullable=False)

    project = relationship("Project", back_populates="messages")
    task = relationship("Task", back_populates="messages")


class User(Base):
    """阶段1.1: 统一账号体系用户表。

    用 email 作登录标识（跨产品统一身份），password_hash 存 bcrypt 哈希。
    is_active 预留软删除/封禁能力，1.1 不实现停用逻辑。
    不加 role 字段——阶段2 多租户时再引入权限模型。
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)  # bcrypt
    display_name = Column(String(60), default="", nullable=False)
    created_at = Column(DateTime, default=_now)
    # 预留软删除/停用，1.1 不实现逻辑
    is_active = Column(Boolean, default=True, server_default="1", nullable=False)
    # 阶段2.3: 登录后默认组织（nullable，用户未加入任何组织时为 null）
    # 不用 ForeignKey：与 owner_id 同策略，避免迁移边界问题。
    default_org_id = Column(Integer, nullable=True)
    # DRL-R1-V7: JWT 撤销时间戳（unix epoch 秒）。
    # 用户登出/封禁时设置为当前时间；require_user/require_auth 校验 JWT iat < token_revoked_at，
    # 早于此时间签发的 JWT 一律视为已撤销。nullable=True 表示从未撤销。
    token_revoked_at = Column(Integer, nullable=True)


class VannevarMeta(Base):
    """B-P2-1: key-value 元数据表，用于标记种子数据是否已播种。

    避免用户删空所有项目后重启被重新播种（覆盖用户意愿）。
    init_db() 的 create_all 会自动创建此表。
    """
    __tablename__ = "vannevar_meta"

    key = Column(String(64), primary_key=True)
    value = Column(String(256), nullable=False)


class SyncItem(Base):
    """阶段1.5: 跨设备数据同步表（通用）。

    存储扩展端（Focus/Muse）推送的本地数据，用 client_uuid 去重。
    data 字段存 JSON 文本，保持对 Focus 快照 / Muse 灵感 / 氛围标签的通用性。
    owner_id 关联 users 表（不用 FK，与 projects.owner_id 同策略）。
    唯一约束：(owner_id, app, client_uuid) 确保同一用户同一 app 下不重复。
    """
    __tablename__ = "sync_items"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, nullable=False, index=True)
    app = Column(String(20), nullable=False)  # focus / muse
    item_type = Column(String(30), nullable=False)  # snapshot / inspiration / mood
    client_uuid = Column(String(64), nullable=False)
    data = Column(Text, nullable=False)  # JSON 文本
    client_created_at = Column(Integer, nullable=False)  # 客户端创建时间（ms 时间戳）
    client_updated_at = Column(Integer, nullable=False)  # 客户端最后修改时间（ms 时间戳）
    server_updated_at = Column(DateTime, default=_now, onupdate=_now)


# ============================================================
# 阶段2.3: 嵌套多租户模型（org → team → members）
# ============================================================


class Organization(Base):
    """组织（多租户顶层实体）。

    软删除策略：deleted_at 非空表示已删除，权限决策树校验 deleted_at IS NULL。
    owner_id 不用 FK：与 projects.owner_id 同策略，dev 模式兼容。
    """
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    slug = Column(String(60), unique=True, nullable=False, index=True)
    # 创建者（不用 FK，同 owner_id 策略）
    owner_id = Column(Integer, nullable=False)
    plan = Column(String(20), default="free", server_default="free", nullable=False)
    # DRL-R1-V26: max_members 仅作为元数据存储，DB 层不强制。
    # 实际强制（邀请/加入成员时校验当前成员数 < max_members）需在路由层 orgs_router.py 实现（不在本次修复范围）。
    max_members = Column(Integer, default=50, server_default="50", nullable=False)
    # 补充-P1-3: 软删除标记（null=活跃，非空=已删除）
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    teams = relationship("Team", back_populates="org", cascade="all, delete-orphan")
    memberships = relationship("OrgMembership", back_populates="org", cascade="all, delete-orphan")


class Team(Base):
    """团队（组织内的子分组）。

    软删除策略：deleted_at 非空表示已删除。
    org_id 用 FK 引 organizations.id：团队必须属于已存在的组织。
    """
    __tablename__ = "teams"
    __table_args__ = (
        # 同组织内 slug 唯一
        UniqueConstraint("org_id", "slug", name="uq_teams_org_slug"),
    )

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    slug = Column(String(60), nullable=False)
    description = Column(Text, default="", server_default="")
    # 补充-P1-3: 软删除标记
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    org = relationship("Organization", back_populates="teams")
    memberships = relationship("TeamMembership", back_populates="team", cascade="all, delete-orphan")


class OrgMembership(Base):
    """组织成员关系（直属组织的成员，含组织级角色）。

    role: owner / admin / member / viewer
    用 FK 引 organizations.id 和 users.id：成员关系是真实用户加入真实组织，dev 模式不涉及。
    UNIQUE(org_id, user_id) 确保同一用户在同一组织只有一条成员记录。
    """
    __tablename__ = "org_memberships"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_org_memberships_org_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String(20), default="member", server_default="member", nullable=False)
    joined_at = Column(DateTime, default=_now)

    org = relationship("Organization", back_populates="memberships")


class TeamMembership(Base):
    """团队成员关系（含团队级角色）。

    role: team_admin / member
    用 FK 引 teams.id 和 users.id。
    UNIQUE(team_id, user_id) 确保同一用户在同一团队只有一条成员记录。
    补充-P1-4: 添加团队成员时 API 层必须校验该用户已是组织成员，否则拒绝。
    """
    __tablename__ = "team_memberships"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String(20), default="member", server_default="member", nullable=False)
    joined_at = Column(DateTime, default=_now)

    team = relationship("Team", back_populates="memberships")


class ProjectShare(Base):
    """项目共享（项目归 owner，可共享给组织/团队/个人）。

    三选一：shared_with_org_id / shared_with_team_id / shared_with_user_id 有且仅有一个非空。
    permission: view / edit / admin
    shared_by_user_id 不用 FK：与 organizations.owner_id 同策略。
    补充-P1-6: 共享目标被删除时需级联清理此表（软删除组织/团队时硬删除对应共享记录）。
    """
    __tablename__ = "project_shares"
    __table_args__ = (
        # 三选一约束：org / team / user 有且仅有一个非空
        CheckConstraint(
            "(shared_with_org_id IS NOT NULL AND shared_with_team_id IS NULL AND shared_with_user_id IS NULL) "
            "OR (shared_with_org_id IS NULL AND shared_with_team_id IS NOT NULL AND shared_with_user_id IS NULL) "
            "OR (shared_with_org_id IS NULL AND shared_with_team_id IS NULL AND shared_with_user_id IS NOT NULL)",
            name="ck_project_share_target_exclusive"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    # 三选一（CHECK 约束保证）
    shared_with_org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    shared_with_team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    shared_with_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    permission = Column(String(20), default="view", server_default="view", nullable=False)
    # 共享操作者（不用 FK，与 owner_id 同策略）
    shared_by_user_id = Column(Integer, nullable=False)
    shared_at = Column(DateTime, default=_now)


class OrgInvitation(Base):
    """邀请链接（组织级邀请）。

    补充-P1-4: token_hash 存 SHA-256(token)，不存明文，防止数据库泄露后 token 被利用。
    生成邀请时生成 32 字节随机 token 返回给用户明文（仅一次），数据库只存哈希。
    接受邀请端点接收明文 token，计算 SHA-256 后与 token_hash 比对。
    team_id 可选：直接邀请到某团队（需校验 team 属于同一 org）。
    """
    __tablename__ = "org_invitations"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    email = Column(String(255), nullable=False)
    # 补充-P1-4: SHA-256(token) 哈希存储
    token_hash = Column(String(64), unique=True, nullable=False)
    role = Column(String(20), default="member", server_default="member", nullable=False)
    invited_by = Column(Integer, nullable=False)  # 不用 FK，同 owner_id 策略
    status = Column(String(20), default="pending", server_default="pending", nullable=False)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)


# ============================================================
# 阶段2.2: 服务端 Agent 建议
# ============================================================


class AgentSuggestion(Base):
    """服务端 Agent 主动建议（APScheduler 定时扫描生成）。

    补充-P2-1: UNIQUE(project_id, type, date(created_at)) 防止并发/多 worker 重复生成同类型建议。
    SQLite 的 date() 函数返回 'YYYY-MM-DD'，实现按天去重。
    状态流转：pending → shown（客户端 ACK）→ dismissed/acted
    """
    __tablename__ = "agent_suggestions"
    __table_args__ = (
        # 按天去重：同项目同类型同日只生成一条
        # SQLite date() 函数返回 'YYYY-MM-DD'，使用 text() 表达式避免 SQLAlchemy 列名解析失败
        Index(
            "ux_agent_suggestions_project_type_date",
            "project_id",
            "type",
            text("date(created_at)"),
            unique=True,
        ),
        Index("idx_agent_suggestions_project", "project_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    # 类型：stale_warning / overdue_task / handoff_gap / daily_summary
    type = Column(String(30), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=True)
    # 严重级别：info / warning / critical
    severity = Column(String(10), default="info", server_default="info", nullable=False)
    # 状态：pending / shown / dismissed / acted
    status = Column(String(20), default="pending", server_default="pending", nullable=False)
    created_at = Column(DateTime, default=_now)
    acted_at = Column(DateTime, nullable=True)


# ============================================================
# Phase 4.1: 跨产品联动（Focus 快照 / Muse 灵感 → Bridge 项目）
# ============================================================


class ProjectContextSnapshot(Base):
    """Phase 4.1: Focus 快照推送到 Bridge 项目档案。

    存储 Focus 扩展推送的快照元数据（不含实际标签页内容，仅元数据 + AI 摘要）。
    project_id 级联删除：项目删除时关联快照自动清理。
    snapshot_uuid 是 Focus 端的客户端 uuid，用于去重（UNIQUE(project_id, snapshot_uuid)）。
    """
    __tablename__ = "project_context_snapshots"
    __table_args__ = (
        UniqueConstraint("project_id", "snapshot_uuid", name="uq_pcs_project_snapshot"),
        Index("idx_pcs_project_created", "project_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_uuid = Column(String(64), nullable=False)
    title = Column(String(200), nullable=False)
    summary = Column(Text, default="", server_default="")
    urls_json = Column(Text, default="[]", server_default="[]")  # URL 列表 JSON
    tab_count = Column(Integer, default=0, server_default="0", nullable=False)
    # Focus 端创建时间（ms 时间戳）
    created_at = Column(Integer, nullable=False)
    # 推送到 Bridge 的时间（ms 时间戳）
    pushed_at = Column(Integer, nullable=False)
    # 推送者（不用 FK，同 owner_id 策略）
    pushed_by = Column(Integer, nullable=False)


class ProjectContextInspiration(Base):
    """Phase 4.1: Muse 灵感推送到 Bridge 项目档案。

    存储 Muse 扩展推送的灵感元数据。project_id 级联删除。
    inspiration_uuid 是 Muse 端的客户端 uuid，用于去重。
    """
    __tablename__ = "project_context_inspirations"
    __table_args__ = (
        UniqueConstraint("project_id", "inspiration_uuid", name="uq_pci_project_inspiration"),
        Index("idx_pci_project_created", "project_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    inspiration_uuid = Column(String(64), nullable=False)
    title = Column(String(200), nullable=False)
    url = Column(String(2048), nullable=False)
    note = Column(Text, default="", server_default="")
    mood = Column(String(60), default="", server_default="")
    # Muse 端创建时间（ms 时间戳）
    created_at = Column(Integer, nullable=False)
    # 推送到 Bridge 的时间（ms 时间戳）
    pushed_at = Column(Integer, nullable=False)
    # 推送者（不用 FK，同 owner_id 策略）
    pushed_by = Column(Integer, nullable=False)


# ============================================================
# Phase 6.J: AI 助手对话化（多轮对话 + 持久化历史）
# ============================================================


class Conversation(Base):
    """Phase 6.J: AI 助手会话。

    一个项目可有多个会话（demo 演示 时建议每项目 1 个主会话）。
    owner_id 关联 users 表（不用 FK，与 projects.owner_id 同策略）。
    软删除：deleted_at 非空表示已删除，列表不返回。
    """
    __tablename__ = "conversations"
    __table_args__ = (
        Index("idx_conversations_project", "project_id", "deleted_at"),
        Index("idx_conversations_owner", "owner_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), default="新会话", server_default="新会话", nullable=False)
    # 会话所有者（不用 FK，同 owner_id 策略，dev 模式 owner_id=0 兼容）
    owner_id = Column(Integer, nullable=False, index=True)
    # 软删除标记（null=活跃，非空=已删除）
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    messages = relationship("ChatMessage", back_populates="conversation", cascade="all, delete-orphan",
                            order_by="ChatMessage.id")


class ChatMessage(Base):
    """Phase 6.J: 会话内的单条消息（user 提问 / assistant 回答）。

    role: user / assistant
    content: 消息文本（user 输入或 assistant 流式聚合后的完整文本）
    """
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("idx_chat_messages_conv", "conversation_id", "id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user / assistant
    content = Column(Text, nullable=False)
    # 标记是否为降级规则引擎生成（AI 未配置/失败时为 True）
    is_fallback = Column(Boolean, default=False, server_default="0", nullable=False)
    created_at = Column(DateTime, default=_now)

    conversation = relationship("Conversation", back_populates="messages")
