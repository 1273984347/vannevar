# schemas.py — Bridge MVP Pydantic 校验
import time
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from templates import ALLOWED_TEMPLATE_IDS, DEFAULT_TEMPLATE_ID

ALLOWED_STATUS = {"todo", "in_progress", "done"}
ALLOWED_PROJECT_STATUS = {"active", "archived", "done"}
# Prompt 模板名白名单（与 ai.PROMPT_TEMPLATES 保持一致；此处单独定义避免 schemas 依赖 ai 模块的 httpx）
ALLOWED_TEMPLATE_NAMES = {"volunteer", "handover", "delivery", "default"}
# 阶段2.2: Agent 建议类型与状态白名单
ALLOWED_SUGGESTION_TYPES = {"stale_warning", "overdue_task", "handoff_gap", "daily_summary"}
ALLOWED_SUGGESTION_STATUS = {"pending", "shown", "dismissed", "acted"}


def strip(value: Optional[str]) -> Optional[str]:
    return value.strip() if isinstance(value, str) else value


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    owner_name: str = Field(default="", max_length=60)

    _strip = field_validator("title", "description", "owner_name", mode="before")(strip)


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=5000)
    status: Optional[str] = None
    owner_name: Optional[str] = Field(None, max_length=60)
    # 乐观锁：客户端传入读取时的 version，服务端校验不匹配则返回 409。
    # 不传时保持向后兼容（不做冲突检测）。
    version: Optional[int] = None

    # L2: status 也参与 strip 处理
    _strip = field_validator("title", "description", "status", "owner_name", mode="before")(strip)

    @field_validator("status")
    @classmethod
    def status_must_be_allowed(cls, v):
        if v is not None and v not in ALLOWED_STATUS:
            raise ValueError(f"status 必须是 {ALLOWED_STATUS} 之一")
        return v


class TaskOut(BaseModel):
    id: int
    project_id: int
    title: str
    description: str
    status: str
    owner_name: str
    created_at: datetime
    updated_at: datetime
    # 乐观锁版本号：客户端读取后回传用于冲突检测
    version: int = 1

    model_config = {"from_attributes": True}


class HandoffCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=1, max_length=2000)
    outgoing_volunteer: str = Field(default="", max_length=60)
    incoming_volunteer: str = Field(default="", max_length=60)
    # P0: auto_generate 仅作为标记位，由 auto-generate 端点使用；普通创建端点忽略此字段
    auto_generate: bool = False

    _strip = field_validator("summary", "outgoing_volunteer", "incoming_volunteer", mode="before")(strip)


class HandoffAutoGenerateRequest(BaseModel):
    """auto-generate 端点可选请求体：仅接受交接人/接棒人姓名，summary 自动生成。"""
    model_config = ConfigDict(extra="forbid")

    outgoing_volunteer: str = Field(default="", max_length=60)
    incoming_volunteer: str = Field(default="", max_length=60)
    # Prompt 模板名：volunteer/handover/delivery/default；None 表示按关键词自动检测
    template_name: Optional[str] = Field(default=None)

    _strip = field_validator("outgoing_volunteer", "incoming_volunteer", "template_name", mode="before")(strip)

    @field_validator("template_name")
    @classmethod
    def template_name_must_be_allowed(cls, v):
        if v is not None and v not in ALLOWED_TEMPLATE_NAMES:
            raise ValueError(f"template_name 必须是 {sorted(ALLOWED_TEMPLATE_NAMES)} 之一")
        return v


class HandoffConfirm(BaseModel):
    """志愿者/管理员确认接收交接。confirmed_by 为请求者姓名。

    DRL-R1-V25: 本 schema 仅校验字段格式，不校验确认者身份。
    实际身份校验（确认者须为项目的活跃成员/管理员）需在路由层 handoffs 端点实现（不在本次修复范围）。
    """
    model_config = ConfigDict(extra="forbid")

    confirmed_by: str = Field(..., min_length=1, max_length=60)

    _strip = field_validator("confirmed_by", mode="before")(strip)


class HandoffOut(BaseModel):
    id: int
    project_id: int
    summary: str
    outgoing_volunteer: str
    incoming_volunteer: str
    status: str
    confirmed_at: Optional[datetime] = None
    confirmed_by: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=2000)
    # DRL-R1-V11: author 缺 max_length 修复，限制 60 字符与 DB 列 String(60) 对齐
    author: str = Field(default="志愿者", max_length=60)
    task_id: Optional[int] = None

    _strip = field_validator("content", "author", mode="before")(strip)


class MessageOut(BaseModel):
    id: int
    project_id: int
    task_id: Optional[int]
    author: str
    content: str
    created_at: datetime
    # P0: 关联任务信息（嵌套 TaskOut），便于前端展示任务标签
    task: Optional[TaskOut] = None
    # 内容审核：敏感词命中标记 + 给用户的提示文本
    moderation_flagged: bool = False
    moderation_warning: Optional[str] = None
    # 管理员软删除标记：True 时管理员视图显示「已删除」，志愿者视图不显示
    is_deleted: bool = False

    model_config = {"from_attributes": True}


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=5000)
    status: str = "active"
    # 交接模板 ID：volunteer / resignation / outsourcing / opensource
    template_id: str = DEFAULT_TEMPLATE_ID

    _strip = field_validator("name", "description", "status", "template_id", mode="before")(strip)

    @field_validator("status")
    @classmethod
    def status_must_be_allowed(cls, v):
        if v is not None and v not in ALLOWED_PROJECT_STATUS:
            raise ValueError(f"status 必须是 {ALLOWED_PROJECT_STATUS} 之一")
        return v

    @field_validator("template_id")
    @classmethod
    def template_id_must_be_allowed(cls, v):
        if v is not None and v not in ALLOWED_TEMPLATE_IDS:
            raise ValueError(f"template_id 必须是 {ALLOWED_TEMPLATE_IDS} 之一")
        return v


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=5000)
    status: Optional[str] = None
    template_id: Optional[str] = None
    # 乐观锁：客户端传入读取时的 version，服务端校验不匹配则返回 409。
    # 不传时保持向后兼容（不做冲突检测）。
    version: Optional[int] = None

    _strip = field_validator("name", "description", "status", "template_id", mode="before")(strip)

    @field_validator("status")
    @classmethod
    def status_must_be_allowed(cls, v):
        if v is not None and v not in ALLOWED_PROJECT_STATUS:
            raise ValueError(f"status 必须是 {ALLOWED_PROJECT_STATUS} 之一")
        return v

    @field_validator("template_id")
    @classmethod
    def template_id_must_be_allowed(cls, v):
        if v is not None and v not in ALLOWED_TEMPLATE_IDS:
            raise ValueError(f"template_id 必须是 {ALLOWED_TEMPLATE_IDS} 之一")
        return v


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str
    qr_token: str
    status: str
    template_id: str
    created_at: datetime
    updated_at: datetime
    # 乐观锁版本号：客户端读取后回传用于冲突检测
    version: int = 1
    # 阶段1.2: 项目所有者 ID（nullable，旧数据/dev 模式为 None）
    owner_id: Optional[int] = None

    model_config = {"from_attributes": True}


class ProjectDetail(ProjectOut):
    tasks: List[TaskOut] = []
    handoffs: List[HandoffOut] = []
    messages: List[MessageOut] = []


# P0-2: 公开端点返回的 schema，不含 qr_token，避免敏感凭据泄露
class ProjectPublicOut(BaseModel):
    id: int
    name: str
    description: str
    status: str = "active"
    template_id: str = DEFAULT_TEMPLATE_ID
    created_at: datetime
    updated_at: Optional[datetime] = None
    # 乐观锁版本号：客户端读取后回传用于冲突检测
    version: int = 1
    # 阶段1.2: 项目所有者 ID（公开端点也返回，便于前端判断归属）
    owner_id: Optional[int] = None

    model_config = {"from_attributes": True}


class ProjectDetailPublic(ProjectPublicOut):
    tasks: List[TaskOut] = []
    handoffs: List[HandoffOut] = []
    messages: List[MessageOut] = []


# ---------- P0: 导入/导出 ----------
class ProjectImportTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    owner_name: str = Field(default="", max_length=60)
    status: str = "todo"

    _strip = field_validator("title", "description", "owner_name", "status", mode="before")(strip)

    @field_validator("status")
    @classmethod
    def status_must_be_allowed(cls, v):
        if v is not None and v not in ALLOWED_STATUS:
            raise ValueError(f"status 必须是 {ALLOWED_STATUS} 之一")
        return v


class ProjectImportHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=1, max_length=2000)
    outgoing_volunteer: str = Field(default="", max_length=60)
    incoming_volunteer: str = Field(default="", max_length=60)
    status: str = "draft"

    _strip = field_validator("summary", "outgoing_volunteer", "incoming_volunteer", "status", mode="before")(strip)

    @field_validator("status")
    @classmethod
    def status_must_be_allowed(cls, v):
        if v not in {"draft", "confirmed"}:
            raise ValueError("status 必须是 draft 或 confirmed")
        return v


class ProjectImportMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author: str = Field(default="志愿者", max_length=60)
    content: str = Field(..., min_length=1, max_length=2000)
    # P0: 导入时用 task_index 关联任务（按 tasks 数组下标），避免原 task_id 冲突
    task_index: Optional[int] = None

    _strip = field_validator("author", "content", mode="before")(strip)


class ProjectImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=5000)
    tasks: List[ProjectImportTask] = Field(default=[], max_length=1000)
    handoffs: List[ProjectImportHandoff] = Field(default=[], max_length=500)
    messages: List[ProjectImportMessage] = Field(default=[], max_length=2000)

    @field_validator("tasks", "handoffs", "messages", mode="before")
    @classmethod
    def _ensure_list(cls, v):
        return v if isinstance(v, list) else []

    _strip = field_validator("name", "description", mode="before")(strip)


# ---------- 阶段1.1: 统一账号体系 ----------
class UserRegister(BaseModel):
    """注册请求：email + password + 可选 display_name。

    DRL-R1-V10: 邮箱枚举防护相关——schema 层加 max_length 与 DB 列 String(255) 对齐，
    避免超长邮箱导致 DB 截断或 500 错误（实际枚举防护在 auth_router.py 路由层，不在本次范围）。
    """
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(..., max_length=255)
    # DRL-R1-V17: bcrypt 限制密码字节长度 ≤ 72，超长会被静默截断导致认证异常
    password: str = Field(..., min_length=8, max_length=72)
    display_name: str = Field(default="", max_length=60)

    _strip = field_validator("email", "password", "display_name", mode="before")(strip)


class UserLogin(BaseModel):
    """登录请求：email + password。"""
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(..., max_length=255)
    # DRL-R1-V17: 登录密码同样限制 72 字节，与注册一致
    password: str = Field(..., min_length=1, max_length=72)

    _strip = field_validator("email", "password", mode="before")(strip)


class UserOut(BaseModel):
    """用户信息输出（不含 password_hash）。"""
    id: int
    email: str
    display_name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    """注册/登录成功返回：JWT + 用户信息。"""
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- Phase 4.4 收尾: 端到端加密档案（跨设备解密） ----------

class CryptoVerificationIn(BaseModel):
    """加密档案中的 verification 密文。

    内容是「固定明文」的密文，用于在另一台设备上反验 passphrase 是否一致。
    服务端无法用它反推 passphrase。
    """

    model_config = ConfigDict(extra="forbid")

    ciphertext: str = Field(..., min_length=1, max_length=512)
    nonce: str = Field(..., min_length=1, max_length=64)


class CryptoProfileIn(BaseModel):
    """客户端上报的加密档案。

    salt 是 PBKDF2 的**公开盐值**（不是密钥），可以安全上云；
    passphrase 永不上行、永不落盘。
    """

    model_config = ConfigDict(extra="forbid")

    salt: str = Field(..., min_length=1, max_length=64)
    iterations: int = Field(..., ge=1000, le=1_000_000)
    kdf: str = Field(..., min_length=1, max_length=40)
    salt_fingerprint: str = Field(default="", max_length=16)
    verification: CryptoVerificationIn


class CryptoProfileOut(BaseModel):
    """加密档案输出（结构与 CryptoProfileIn 对称）。"""

    model_config = ConfigDict(extra="forbid")

    salt: str
    iterations: int
    kdf: str
    salt_fingerprint: str
    verification: CryptoVerificationIn


# ---------- 阶段1.5: 数据同步 ----------
ALLOWED_SYNC_APPS = {"focus", "muse"}
ALLOWED_SYNC_ITEM_TYPES = {"snapshot", "inspiration", "mood"}


class SyncItemPush(BaseModel):
    """单条同步推送项。"""
    model_config = ConfigDict(extra="forbid")

    app: str
    item_type: str
    client_uuid: str = Field(..., min_length=1, max_length=64)
    data: dict
    # DRL-R1-V16: 时间戳加上界，防止恶意推送未来时间戳污染 LWW 合并
    # 修复：模块加载时计算的 le 在长测试会话中会过期，改用 field_validator 动态校验
    client_created_at: int = Field(..., ge=0)
    client_updated_at: int = Field(..., ge=0)

    @field_validator("client_created_at", "client_updated_at")
    @classmethod
    def _check_timestamp_upper_bound(cls, v):
        """动态校验时间戳上界（当前时间 + 60s 容差），避免模块加载时固定值在长会话中过期。"""
        upper = int(time.time() * 1000) + 60000
        if v > upper:
            raise ValueError(f"时间戳不能超过当前时间 +60s（上界 {upper}）")
        return v

    @field_validator("app")
    @classmethod
    def app_must_be_allowed(cls, v):
        if v not in ALLOWED_SYNC_APPS:
            raise ValueError(f"app 必须是 {sorted(ALLOWED_SYNC_APPS)} 之一")
        return v

    @field_validator("item_type")
    @classmethod
    def item_type_must_be_allowed(cls, v):
        if v not in ALLOWED_SYNC_ITEM_TYPES:
            raise ValueError(f"item_type 必须是 {sorted(ALLOWED_SYNC_ITEM_TYPES)} 之一")
        return v


class SyncPushRequest(BaseModel):
    """批量推送请求。"""
    model_config = ConfigDict(extra="forbid")

    items: List[SyncItemPush] = Field(default=[], max_length=5000)


class SyncPushResult(BaseModel):
    """单条推送结果。"""
    client_uuid: str
    action: str  # created / updated / skipped


class SyncPushResponse(BaseModel):
    """推送响应。"""
    pushed: int
    created: int
    updated: int
    skipped: int
    results: List[SyncPushResult]


class SyncItemOut(BaseModel):
    """拉取返回的单条同步项。"""
    client_uuid: str
    app: str
    item_type: str
    data: dict
    client_created_at: int
    client_updated_at: int

    model_config = {"from_attributes": True}


class SyncPullResponse(BaseModel):
    """拉取响应。"""
    app: str
    count: int
    items: List[SyncItemOut]


# ---------- 阶段1.6: Context Card 只读视图 ----------

class ContextCardOut(BaseModel):
    """Context Card v1 格式的同步项（只读视图，从 sync_items 包装）。"""
    # 用 alias 输出 "schema" 字段名（与 vannevar-context-card/v1 规范对齐）
    schema_: str = Field(default="vannevar-context-card/v1", alias="schema")
    type: str  # focus-snapshot / muse-inspiration
    app: str   # focus / muse
    exportedAt: Optional[str]  # ISO 8601，服务端接收时间
    data: dict
    client_uuid: str

    model_config = ConfigDict(populate_by_name=True)


class ContextCardListResponse(BaseModel):
    """Context Card 列表响应。"""
    count: int
    items: List[ContextCardOut]


# ============================================================
# 阶段2.2: Agent 建议 Schema
# ============================================================


class AgentSuggestionOut(BaseModel):
    """Agent 建议输出。"""
    id: int
    project_id: int
    type: str
    title: str
    body: Optional[str] = None
    severity: str
    status: str
    created_at: Optional[datetime] = None
    acted_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AgentSuggestionListResponse(BaseModel):
    """Agent 建议列表响应。"""
    count: int
    items: List[AgentSuggestionOut]


# ============================================================
# Phase 4.1: 跨产品联动 schemas
# ============================================================


class ContextSnapshotCreate(BaseModel):
    """Focus 快照推送到 Bridge 项目。"""
    snapshot_uuid: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=200)
    summary: str = Field("", max_length=4000)
    urls: List[str] = Field(default_factory=list, max_length=100)
    tab_count: int = Field(0, ge=0, le=1000)
    created_at: int = Field(..., ge=0)  # Focus 端创建时间（ms 时间戳）

    model_config = {"extra": "forbid"}


class ContextSnapshotOut(BaseModel):
    """Focus 快照输出。"""
    id: int
    project_id: int
    snapshot_uuid: str
    title: str
    summary: str
    urls: List[str]
    tab_count: int
    created_at: int
    pushed_at: int
    pushed_by: int

    model_config = {"from_attributes": True}


class ContextInspirationCreate(BaseModel):
    """Muse 灵感推送到 Bridge 项目。"""
    inspiration_uuid: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=200)
    url: str = Field(..., min_length=1, max_length=2048)
    note: str = Field("", max_length=4000)
    mood: str = Field("", max_length=60)
    created_at: int = Field(..., ge=0)  # Muse 端创建时间（ms 时间戳）

    model_config = {"extra": "forbid"}


class ContextInspirationOut(BaseModel):
    """Muse 灵感输出。"""
    id: int
    project_id: int
    inspiration_uuid: str
    title: str
    url: str
    note: str
    mood: str
    created_at: int
    pushed_at: int
    pushed_by: int

    model_config = {"from_attributes": True}


class TaskUpdateOut(BaseModel):
    """Phase 4.1: Bridge → Focus 任务状态变更通知通道。

    返回当前用户最近更新的任务，Focus 扩展轮询此端点检测新变更。
    updated_at_iso 为 ISO 8601 字符串（UTC，带 'Z' 后缀），
    便于客户端 new Date() 直接解析。
    """
    task_id: int
    project_id: int
    project_name: str
    title: str
    status: str  # todo / in_progress / done
    owner_name: str
    updated_at: datetime
    updated_at_iso: str

    model_config = {"from_attributes": True}


# ============================================================
# Phase 6.J: AI 助手对话化 schemas
# ============================================================

# 允许的消息角色
ALLOWED_CHAT_ROLES = {"user", "assistant"}


class ConversationCreate(BaseModel):
    """创建会话请求。"""
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="新会话", min_length=1, max_length=200)

    _strip = field_validator("title", mode="before")(strip)


class ConversationOut(BaseModel):
    """会话输出。"""
    id: int
    project_id: int
    title: str
    owner_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationListResponse(BaseModel):
    """会话列表响应。"""
    count: int
    items: List[ConversationOut]


class ChatMessageOut(BaseModel):
    """单条聊天消息输出。"""
    id: int
    conversation_id: int
    role: str
    content: str
    is_fallback: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatMessageListResponse(BaseModel):
    """会话内消息列表响应。"""
    count: int
    items: List[ChatMessageOut]


class ChatAskRequest(BaseModel):
    """向 AI 助手提问请求。

    question: 用户问题文本
    conversation_id: 可选，不传则新建会话（标题用问题前 30 字截断）
    """
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[int] = None

    _strip = field_validator("question", mode="before")(strip)
