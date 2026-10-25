# routers/projects.py — 项目档案 API
import logging
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBasicCredentials
from sqlalchemy import case, func
from sqlalchemy.orm import Session, selectinload

from auth import (
    _permission_ge,
    _resolve_project_permission,
    require_admin,
    require_auth,
    require_project_access,
    security,
)
from ai import generate_handoff_summary_llm, generate_handoff_summary_stream, has_ai_config
from context_card import ContextCardError, card_to_background_text, parse_context_card
from database import get_db
from models import (
    AgentSuggestion,
    Handoff,
    Message,
    Project,
    ProjectContextInspiration,
    ProjectContextSnapshot,
    SyncItem,
    Task,
    User,
    _now,
)
from moderation import moderate_text
from routers.events import publish
from schemas import (
    ALLOWED_PROJECT_STATUS,
    ContextInspirationCreate,
    ContextInspirationOut,
    ContextSnapshotCreate,
    ContextSnapshotOut,
    HandoffAutoGenerateRequest,
    HandoffConfirm,
    HandoffCreate,
    HandoffOut,
    MessageCreate,
    MessageOut,
    ProjectCreate,
    ProjectDetail,
    ProjectDetailPublic,
    ProjectImportPayload,
    ProjectOut,
    ProjectPublicOut,
    ProjectUpdate,
    TaskCreate,
    TaskOut,
)

# P1-3/P1-13: 志愿者写端点 CSRF 固定值
VOLUNTEER_CSRF_VALUE = "BridgeVolunteer"

# DRL-R1-V17: qr_token 失败限流逻辑已提取到 routers._token_rate_limit 共享模块，
# 供 tasks.py / projects.py / events.py 复用，并增加最大 key 数限制防止无界增长。
from routers._token_rate_limit import _check_token_fail_limit, _record_token_fail


def _verify_volunteer(qr_token: str, project: Project, request: Request) -> None:
    """统一校验志愿者身份与 CSRF 头。"""
    # R2-V9: 统一使用 _get_client_ip 获取客户端 IP（延迟导入避免循环依赖）
    from main import _get_client_ip
    client_ip = _get_client_ip(request)

    # W088c: 先检查是否已被临时封禁（防枚举扫描）
    if not _check_token_fail_limit(client_ip):
        logger.warning(
            "auth_blocked client_ip=%s path=%s reason=token_fail_limit_exceeded",
            client_ip,
            request.url.path,
        )
        raise HTTPException(status_code=429, detail="扫码验证失败次数过多，请稍后再试")

    if request.headers.get("x-requested-with") != VOLUNTEER_CSRF_VALUE:
        # B-LOG-01: 记录鉴权失败日志，便于追踪异常访问
        logger.warning(
            "auth_failed client_ip=%s path=%s reason=missing_volunteer_csrf_header project_id=%s",
            client_ip,
            request.url.path,
            getattr(project, "id", "?"),
        )
        raise HTTPException(status_code=403, detail="缺少必要请求头")
    if not secrets.compare_digest(project.qr_token or "", qr_token):
        # W088c: 记录 token 校验失败，用于限流
        _record_token_fail(client_ip)
        logger.warning(
            "auth_failed client_ip=%s path=%s reason=invalid_qr_token project_id=%s",
            client_ip,
            request.url.path,
            getattr(project, "id", "?"),
        )
        raise HTTPException(status_code=403, detail="无权操作此项目")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

# 第三批升级：统计端点单独路由（不带 /projects 前缀）
stats_router = APIRouter(prefix="/api", tags=["stats"])


def _actor(admin: Optional[str], qr_token: Optional[str]) -> str:
    """P2-9: 构造审计日志中的操作者字段。

    管理员记录用户名；志愿者仅记录为 "volunteer"，不携带任何 token 信息
    （B-SEC-12: 即使前 4 位也可能助长枚举攻击，故完全不返回 token）。
    阶段1.3: admin 可能为 "user:{user_id}"（JWT 用户）或 "admin"（Basic Auth）。
    """
    if admin:
        return f"admin={admin}"
    if qr_token:
        return "volunteer"
    return "anonymous"


def _actor_from_user_id(user_id: int) -> str:
    """阶段1.3: 从 user_id 构造操作者标识。0=admin，>0=user:{id}。"""
    return "admin" if user_id == 0 else f"user:{user_id}"


def _check_project_owner(project: Project, user_id: int) -> None:
    """阶段1.3: 校验项目归属。user_id=0（管理员）跳过；JWT 用户校验 owner_id。"""
    if user_id == 0:
        return  # 管理员（dev 模式 Basic Auth）可访问全部
    if project.owner_id is None or project.owner_id != user_id:
        raise HTTPException(status_code=403, detail="无权访问该项目")


def _escape_like(q: str) -> str:
    """M5: 转义 LIKE 通配符 % 和 _，避免用户输入影响匹配语义。"""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=List[ProjectPublicOut])
def list_projects(
    q: str = "",
    status: str = Query("", description="按状态筛选：active / archived / done；留空返回全部"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    user_id: int = Depends(require_auth),
):
    query = db.query(Project)
    # 阶段1.3: JWT 用户只看自己的项目；user_id=0（管理员）看全部
    if user_id > 0:
        query = query.filter(Project.owner_id == user_id)
    if q:
        like = f"%{_escape_like(q)}%"
        query = query.filter(
            Project.name.ilike(like, escape="\\")
            | Project.description.ilike(like, escape="\\")
        )
    if status:
        if status not in ALLOWED_PROJECT_STATUS:
            raise HTTPException(status_code=422, detail=f"status 必须是 {ALLOWED_PROJECT_STATUS} 之一")
        query = query.filter(Project.status == status)
    return query.order_by(Project.updated_at.desc()).offset(skip).limit(limit).all()


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_auth),
):
    # 阶段1.3: owner_id 直接从 require_auth 获取（>0=JWT 用户，0=admin→None）
    owner_id = user_id if user_id > 0 else None
    project = Project(
        name=payload.name,
        description=payload.description,
        status=payload.status or "active",
        template_id=payload.template_id or "volunteer",
        owner_id=owner_id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    logger.info(f"actor={_actor_from_user_id(user_id)} action=create_project project_id={project.id} owner_id={owner_id}")
    return project


@router.post("/import", response_model=ProjectOut, status_code=201)
def import_project(
    payload: ProjectImportPayload,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_auth),
):
    """P0: 导入全量 JSON 重建项目（含 tasks/handoffs/messages）。

    消息通过 task_index 关联到新创建的任务（按 tasks 数组下标），避免原 task_id 冲突。
    """
    # DRL-R1-V6: 限制导入数据规模，防止超大 payload 导致 DoS
    if len(payload.tasks) > 1000 or len(payload.handoffs) > 100 or len(payload.messages) > 1000:
        raise HTTPException(
            status_code=413,
            detail="导入数据量过大（上限：tasks=1000, handoffs=100, messages=1000）",
        )
    # 阶段1.3: owner_id 直接从 require_auth 获取
    owner_id = user_id if user_id > 0 else None
    project = Project(name=payload.name, description=payload.description, owner_id=owner_id)
    db.add(project)
    db.flush()  # 拿到 project.id

    # 先建任务，记录 index -> new task_id 映射
    task_id_by_index: dict = {}
    for idx, t in enumerate(payload.tasks):
        task = Task(
            project_id=project.id,
            title=t.title,
            description=t.description,
            owner_name=t.owner_name,
            status=t.status,
        )
        db.add(task)
        db.flush()
        task_id_by_index[idx] = task.id

    for h in payload.handoffs:
        handoff = Handoff(
            project_id=project.id,
            summary=h.summary,
            outgoing_volunteer=h.outgoing_volunteer,
            incoming_volunteer=h.incoming_volunteer,
            status=h.status,
        )
        db.add(handoff)

    for m in payload.messages:
        task_id = None
        if m.task_index is not None:
            # P2-3: 校验 task_index 越界，避免静默丢失任务关联
            if m.task_index not in task_id_by_index:
                db.rollback()
                raise HTTPException(
                    status_code=422,
                    detail=f"task_index {m.task_index} 越界（有效范围 0-{len(task_id_by_index) - 1}）",
                )
            task_id = task_id_by_index[m.task_index]
        message = Message(
            project_id=project.id,
            author=m.author,
            content=m.content,
            task_id=task_id,
        )
        db.add(message)

    project.updated_at = _now()
    db.commit()
    db.refresh(project)
    logger.info(f"actor={_actor_from_user_id(user_id)} action=import_project project_id={project.id} tasks={len(payload.tasks)} handoffs={len(payload.handoffs)} messages={len(payload.messages)}")
    return project


@router.get("/{project_id}", response_model=ProjectDetailPublic)
def get_project(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    qr_token: str = Query(None, description="志愿者侧凭 qr_token 授权；未提供则要求管理员认证"),
    credentials: HTTPBasicCredentials = Depends(security),
):
    # M3: 使用 selectinload 预加载关系，消除 N+1 查询
    # P0: 同时预加载 message.task，让留言气泡展示关联任务标签
    # P0-SEC: 校验管理员或志愿者身份
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    is_volunteer = bool(qr_token)
    # R2-V6: 记录权限级别，view 权限时对敏感字段脱敏
    _perm_level = None
    if is_volunteer:
        _verify_volunteer(qr_token, project, request)
    else:
        # 阶段1.3: 用 require_auth 替换 require_admin（JWT 优先 + Basic Auth dev 降级）
        user_id = require_auth(request, credentials, db)  # DRL-R1-V7: 传入 db 供 token_revoked_at 校验
        # DRL-R1 修复：get_project 是读操作，应支持 RBAC 权限（owner / share / org / team）
        # 原 _check_project_owner 只校验 owner_id，导致有共享权限的用户无法访问
        if user_id != 0:
            _perm_level = _resolve_project_permission(db, user_id, project)
            if _perm_level is None:
                raise HTTPException(status_code=403, detail="无权访问该项目")
    detail = (
        db.query(Project)
        .options(
            selectinload(Project.tasks),
            selectinload(Project.handoffs),
            selectinload(Project.messages).selectinload(Message.task),
        )
        .filter(Project.id == project_id)
        .first()
    )
    # R5-BE-8 修复：TOCTOU 二次查询后增加 None 检查，防止项目在首次查询后被删除
    if detail is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    # 软删除过滤：志愿者视图不显示已删除留言；管理员视图保留全部（带「已删除」标记）
    # R5-BE-7 修复：view 权限也过滤软删除留言（与志愿者视图一致），防止 view 用户看到已删除内容
    # R6-BE-6 修复：edit 权限同样过滤软删除留言（与 view 一致），仅 admin 可见已删除标记
    if (is_volunteer or _perm_level in ("view", "edit")) and detail is not None:
        detail.messages = [m for m in detail.messages if not m.is_deleted]
    # R2-V6: view 权限脱敏 — 隐藏交接人真实姓名（仅保留首字符），防止信息泄露
    if _perm_level == "view" and detail is not None:
        # R3-V4 修复：脱敏前将对象从 session 分离，避免意外持久化
        db.expunge(detail)
        # 然后执行脱敏
        for h in detail.handoffs:
            if h.outgoing_volunteer:
                h.outgoing_volunteer = h.outgoing_volunteer[0] + "***" if len(h.outgoing_volunteer) > 1 else "***"
            if h.incoming_volunteer:
                h.incoming_volunteer = h.incoming_volunteer[0] + "***" if len(h.incoming_volunteer) > 1 else "***"
        # R3-V5 修复：messages.author 也需脱敏（view 权限）
        for m in detail.messages:
            if m.author:
                m.author = m.author[0] + "***" if len(m.author) > 1 else "***"
        # R5-BE-9 修复：tasks.owner_name 也需脱敏（view 权限），防止任务负责人真实姓名泄露
        for t in detail.tasks:
            if t.owner_name:
                t.owner_name = t.owner_name[0] + "***" if len(t.owner_name) > 1 else "***"
    return detail


@router.get("/{project_id}/export")
def export_project(
    project_id: int,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("view")),
):
    """P0: 导出项目全量 JSON（含 tasks/handoffs/messages）。

    消息通过 task_index 标记关联任务的下标，便于 import 端点重建关联。
    R5-BE-2: require_admin → require_project_access("view")，支持 JWT 用户访问。
    """
    project = (
        db.query(Project)
        .options(
            selectinload(Project.tasks),
            selectinload(Project.handoffs),
            selectinload(Project.messages),
        )
        .filter(Project.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    task_index_by_id = {t.id: idx for idx, t in enumerate(project.tasks)}

    return {
        "name": project.name,
        "description": project.description,
        "exported_at": _now().isoformat(),
        "tasks": [
            {
                "title": t.title,
                "description": t.description,
                "owner_name": t.owner_name,
                "status": t.status,
            }
            for t in project.tasks
        ],
        "handoffs": [
            {
                "summary": h.summary,
                "outgoing_volunteer": h.outgoing_volunteer,
                "incoming_volunteer": h.incoming_volunteer,
                "status": h.status,
            }
            for h in project.handoffs
        ],
        "messages": [
            {
                "author": m.author,
                "content": m.content,
                "task_index": task_index_by_id.get(m.task_id) if m.task_id else None,
            }
            for m in project.messages
        ],
    }


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("edit")),
):
    # L9: 项目 PATCH 端点，可修改 name 和 description
    # R6-BE-5: 改用 require_project_access("edit")，与其它端点对齐。
    #   owner 通过 _resolve_project_permission 的 owner_id 特权分支自动获得 admin（>=edit），
    #   保留 owner 编辑自己项目的权限；share 权限为 edit/admin 的用户也能编辑。
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    data = payload.model_dump(exclude_unset=True)
    # 乐观锁：客户端传入 version 时做冲突检测，不匹配返回 409 + 当前 version。
    # version 字段不参与 setattr 赋值（由服务端在更新成功后自增）。
    incoming_version = data.pop("version", None)

    if incoming_version is not None:
        # DRL-R1-V18: 原子乐观锁更新，避免 read-modify-write 竞态
        new_version = (project.version or 1) + 1
        update_fields = dict(data)
        update_fields["version"] = new_version
        update_fields["updated_at"] = _now()
        result = db.query(Project).filter(
            Project.id == project_id,
            Project.version == incoming_version,
        ).update(update_fields, synchronize_session=False)
        if result == 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "版本冲突：项目已被其他客户端更新，请刷新后重试",
                    "current_version": project.version,
                    "your_version": incoming_version,
                },
            )
    else:
        # 不带版本号：保留原 setattr 行为
        for key, value in data.items():
            setattr(project, key, value)
        # 乐观锁：更新成功后版本号 +1
        project.version = (project.version or 1) + 1
        project.updated_at = _now()

    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/tasks", response_model=TaskOut, status_code=201)
def create_task(
    project_id: int,
    payload: TaskCreate,
    request: Request,
    qr_token: str = Query(None, description="志愿者侧凭 qr_token 授权；未提供则要求管理员认证"),
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    # 双鉴权：志愿者凭 qr_token（可记录待办），JWT 用户凭 require_auth + edit 权限
    # R5-BE-2: require_admin → require_auth + require_project_access("edit")
    if qr_token:
        _verify_volunteer(qr_token, project, request)
    else:
        user_id = require_auth(request, credentials, db)
        if user_id != 0:
            _perm = _resolve_project_permission(db, user_id, project)
            if not _permission_ge(_perm, "edit"):
                raise HTTPException(status_code=403, detail="无项目编辑权限")
    task = Task(project_id=project_id, **payload.model_dump())
    db.add(task)
    # H-5: 子实体变更更新父项目 updated_at
    project.updated_at = _now()
    db.commit()
    db.refresh(task)
    publish(project_id, "task_created", {"id": task.id, "status": task.status})
    admin_name = credentials.username if (credentials and not qr_token) else None
    logger.info(f"actor={_actor(admin_name, qr_token)} action=create_task project_id={project_id} task_id={task.id}")
    return task


@router.post("/{project_id}/handoffs", response_model=HandoffOut, status_code=201)
def create_handoff(
    project_id: int,
    payload: HandoffCreate,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("edit")),
):
    """R5-BE-2: require_admin → require_project_access("edit")，支持 JWT 用户访问。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    # P0: 排除 auto_generate 标记位，它不是 Handoff 模型字段
    handoff = Handoff(project_id=project_id, **payload.model_dump(exclude={"auto_generate"}))
    db.add(handoff)
    # H-5: 子实体变更更新父项目 updated_at
    project.updated_at = _now()
    db.commit()
    db.refresh(handoff)
    publish(project_id, "handoff_created", {"id": handoff.id})
    return handoff


def _build_handoff_summary(project: Project, now) -> str:
    """P0: 基于未完成任务 + 近 7 天留言 + 关键时间节点生成交接摘要。

    根据项目的 template_id 调整摘要结构：
    - volunteer（默认）：未完成任务 + 近 7 天留言 + 进度概览
    - resignation：进行中任务 + 近 7 天留言 + 文档清单提示
    - outsourcing：待验收任务 + 近 7 天留言 + 交付物概览
    - opensource：未完成任务 + 近 7 天留言 + 版本状态
    """
    from templates import DEFAULT_TEMPLATE_ID

    seven_days_ago = now - timedelta(days=7)
    # B-BUG-01: SQLite DateTime 列不存储时区信息，读回为 naive datetime；
    # 统一去掉 now 的时区信息再做比较，避免 TypeError
    recent_msgs = [
        m for m in project.messages
        if (m.created_at.replace(tzinfo=None) if m.created_at.tzinfo else m.created_at) >= seven_days_ago.replace(tzinfo=None)
    ]

    template_id = getattr(project, "template_id", None) or DEFAULT_TEMPLATE_ID
    # 摘要开头标识：不同模板用不同前缀，方便用户一眼区分交接场景
    prefix_map = {
        "volunteer": "自动生成于",
        "resignation": "离职交接 · 自动生成于",
        "outsourcing": "外包交付 · 自动生成于",
        "opensource": "开源接力 · 自动生成于",
    }
    prefix = prefix_map.get(template_id, prefix_map[DEFAULT_TEMPLATE_ID])
    lines = [f"【{prefix} {now.strftime('%Y-%m-%d %H:%M')}】", ""]

    # 第一段：按模板选择"未完成 / 进行中 / 待验收"任务视角
    if template_id == "resignation":
        # 离职交接：聚焦"进行中"任务（继承者最需要接手的是正在做的事）
        in_progress = [t for t in project.tasks if t.status == "in_progress"]
        lines.append(f"进行中任务（{len(in_progress)}）：")
        if in_progress:
            for t in in_progress:
                owner = f"（{t.owner_name}）" if t.owner_name else ""
                lines.append(f"- {t.title}{owner}")
        else:
            lines.append("- 无进行中任务")
    elif template_id == "outsourcing":
        # 外包交付：聚焦"待验收"任务（未完成的都视作待验收）
        pending = [t for t in project.tasks if t.status != "done"]
        lines.append(f"待验收任务（{len(pending)}）：")
        if pending:
            for t in pending:
                owner = f"（{t.owner_name}）" if t.owner_name else ""
                tag = "进行中" if t.status == "in_progress" else "待办"
                lines.append(f"- [{tag}] {t.title}{owner}")
        else:
            lines.append("- 无待验收任务")
    else:
        # volunteer / opensource：未完成任务
        pending = [t for t in project.tasks if t.status != "done"]
        lines.append(f"未完成任务（{len(pending)}）：")
        if pending:
            for t in pending:
                owner = f"（{t.owner_name}）" if t.owner_name else ""
                tag = "进行中" if t.status == "in_progress" else "待办"
                lines.append(f"- [{tag}] {t.title}{owner}")
        else:
            lines.append("- 无未完成任务")

    lines.append("")
    lines.append(f"近 7 天留言（{len(recent_msgs)}）：")
    if recent_msgs:
        for m in recent_msgs:
            snippet = m.content[:60] + ("…" if len(m.content) > 60 else "")
            lines.append(f"- {m.author}: {snippet}")
    else:
        lines.append("- 近 7 天无留言")

    total = len(project.tasks)
    done = sum(1 for t in project.tasks if t.status == "done")

    # 第三段：按模板选择收尾概览
    if template_id == "resignation":
        lines.append("")
        lines.append("文档清单提示：")
        lines.append("- 请补充：项目文档目录、API 文档、运维手册路径")
        if total:
            lines.append(f"- 任务进度：{done}/{total} 已完成")
    elif template_id == "outsourcing":
        lines.append("")
        lines.append("交付物概览：")
        if total:
            lines.append(f"- 任务完成度：{done}/{total}")
        lines.append("- 请核对：交付物清单、验收标准、技术文档是否齐全")
    elif template_id == "opensource":
        lines.append("")
        lines.append("版本状态：")
        if total:
            lines.append(f"- 任务进度：{done}/{total} 已完成")
        lines.append("- 请补充：最新发布版本、待发布版本、已知阻塞问题")
    else:
        # volunteer 保持原有"任务进度"输出
        if total:
            lines.append("")
            lines.append(f"任务进度：{done}/{total} 已完成")

    summary = "\n".join(lines)
    if len(summary) > 2000:
        summary = summary[:1997] + "…"
    return summary


@router.post("/{project_id}/handoffs/auto-generate", response_model=HandoffOut, status_code=201)
def auto_generate_handoff(
    project_id: int,
    payload: Optional[HandoffAutoGenerateRequest] = None,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("edit")),
):
    """P0: 基于项目未完成任务 + 近 7 天留言 + 关键时间节点生成交接草稿。

    R5-BE-2: require_admin → require_project_access("edit")，支持 JWT 用户访问。
    """
    project = (
        db.query(Project)
        .options(
            selectinload(Project.tasks),
            selectinload(Project.messages),
        )
        .filter(Project.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    now = _now()

    # P0-AI: 优先尝试 LLM 生成交接摘要；未配置或失败时降级到规则引擎
    project_data = {
        "name": project.name,
        "description": project.description or "",
        "tasks": [
            {"title": t.title, "owner_name": t.owner_name, "status": t.status}
            for t in project.tasks
        ],
        "messages": [
            {"author": m.author, "content": m.content}
            for m in project.messages
        ],
        "handoffs": [
            {
                "summary": h.summary,
                "outgoing_volunteer": h.outgoing_volunteer,
                "incoming_volunteer": h.incoming_volunteer,
            }
            for h in project.handoffs
        ],
    }
    summary = generate_handoff_summary_llm(
        project_data,
        payload.template_name if payload else None,
    )
    if not summary:
        summary = _build_handoff_summary(project, now)

    outgoing = payload.outgoing_volunteer if payload else ""
    incoming = payload.incoming_volunteer if payload else ""
    handoff = Handoff(
        project_id=project_id,
        summary=summary,
        outgoing_volunteer=outgoing,
        incoming_volunteer=incoming,
        status="draft",
    )
    db.add(handoff)
    project.updated_at = now
    db.commit()
    db.refresh(handoff)
    publish(project_id, "handoff_created", {"id": handoff.id})
    return handoff


@router.post("/{project_id}/handoffs/stream-generate")
async def stream_generate_handoff(
    project_id: int,
    payload: Optional[HandoffAutoGenerateRequest] = None,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("edit")),
):
    """阶段2.7: 流式生成交接摘要（SSE）。

    与 auto_generate_handoff 不同：本端点不落库，仅流式返回文本增量，
    前端订阅 SSE 实时显示生成进度，用户确认后再调用 auto_generate_handoff 落库。

    降级策略：
    - AI 未配置/失败 → 流式返回规则引擎生成的完整摘要（一次性 yield）

    R5-BE-2: require_admin → require_project_access("edit")，支持 JWT 用户访问。
    """
    import json as _json

    project = (
        db.query(Project)
        .options(
            selectinload(Project.tasks),
            selectinload(Project.messages),
        )
        .filter(Project.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    project_data = {
        "name": project.name,
        "description": project.description or "",
        "tasks": [
            {"title": t.title, "owner_name": t.owner_name, "status": t.status}
            for t in project.tasks
        ],
        "messages": [
            {"author": m.author, "content": m.content}
            for m in project.messages
        ],
        "handoffs": [
            {
                "summary": h.summary,
                "outgoing_volunteer": h.outgoing_volunteer,
                "incoming_volunteer": h.incoming_volunteer,
            }
            for h in project.handoffs
        ],
    }

    template_name = payload.template_name if payload else None

    async def event_stream():
        collected = []
        try:
            async for delta in generate_handoff_summary_stream(project_data, template_name):
                collected.append(delta)
                data_str = _json.dumps({"delta": delta}, ensure_ascii=False)
                yield f"event: delta\ndata: {data_str}\n\n"
            # 流结束：若未收集到任何内容，降级到规则引擎一次性返回
            if not collected:
                fallback = _build_handoff_summary(project, _now())
                data_str = _json.dumps({"delta": fallback, "fallback": True}, ensure_ascii=False)
                yield f"event: delta\ndata: {data_str}\n\n"
            yield "event: done\ndata: {}\n\n"
        except Exception as e:
            logger.warning("流式生成交接摘要异常: %s", e)
            err_str = _json.dumps({"message": "生成失败，请重试"}, ensure_ascii=False)
            yield f"event: error\ndata: {err_str}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{project_id}/handoffs/import-card")
def import_handoff_card(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("edit")),
):
    """接收 Context Card JSON，返回解析后的交接背景信息文本。

    支持 focus-snapshot / muse-inspiration / bridge-handoff 三类卡片，
    把卡片内容转换为可填入交接摘要 textarea 的多行文本。本端点只解析不写库，
    便于前端在用户确认前预览背景信息。

    JSON 解析或卡片格式校验失败返回 422，避免触发全局 500。
    R5-BE-2: require_admin → require_project_access("edit")，支持 JWT 用户访问。
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # DRL-R1-V9: 限制请求体大小（1 MiB），防止超大 payload 导致 DoS
    try:
        content_length = int(request.headers.get("content-length", 0))
    except (TypeError, ValueError):
        content_length = 0
    if content_length > 1024 * 1024:
        raise HTTPException(status_code=413, detail="请求体过大（上限 1 MiB）")

    raw = request.body()
    if not raw:
        raise HTTPException(status_code=422, detail="请求体为空")
    try:
        json_str = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="请求体必须为 UTF-8 编码")
    try:
        card = parse_context_card(json_str)
    except ContextCardError as err:
        raise HTTPException(status_code=422, detail=str(err))
    background_text = card_to_background_text(card)
    logger.info(
        f"actor={_actor_from_user_id(access[0])} action=import_handoff_card project_id={project_id} type={card.get('type')}"
    )
    return {
        "ok": True,
        "type": card.get("type"),
        "app": card.get("app"),
        "background": background_text,
    }


@router.post("/{project_id}/handoffs/{handoff_id}/confirm", response_model=HandoffOut)
def confirm_handoff(
    project_id: int,
    handoff_id: int,
    payload: HandoffConfirm,
    request: Request,
    qr_token: str = Query(None, description="志愿者侧凭 qr_token 授权；未提供则要求管理员认证"),
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    """P0: 标记交接 status=confirmed, confirmed_at=now, confirmed_by=请求者。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    # H-3: IDOR 防护 — 志愿者凭 qr_token，JWT 用户凭 require_auth + edit 权限
    # R5-BE-2: require_admin → require_auth + require_project_access("edit")
    if qr_token:
        _verify_volunteer(qr_token, project, request)
    else:
        user_id = require_auth(request, credentials, db)
        if user_id != 0:
            _perm = _resolve_project_permission(db, user_id, project)
            if not _permission_ge(_perm, "edit"):
                raise HTTPException(status_code=403, detail="无项目编辑权限")

    handoff = (
        db.query(Handoff)
        .filter(Handoff.id == handoff_id, Handoff.project_id == project_id)
        .first()
    )
    if not handoff:
        raise HTTPException(status_code=404, detail="交接记录不存在")

    # P1-8: 已确认的交接不允许重复确认
    if handoff.status == "confirmed":
        raise HTTPException(status_code=409, detail="此交接已被确认")

    handoff.status = "confirmed"
    handoff.confirmed_at = _now()
    handoff.confirmed_by = payload.confirmed_by
    project.updated_at = _now()
    db.commit()
    db.refresh(handoff)
    publish(project_id, "handoff_updated", {"id": handoff.id, "status": handoff.status})
    admin_name = credentials.username if (credentials and not qr_token) else None
    logger.info(f"actor={_actor(admin_name, qr_token)} action=confirm_handoff project_id={project_id} handoff_id={handoff_id} confirmed_by={payload.confirmed_by}")
    return handoff


@router.post("/{project_id}/messages", response_model=MessageOut, status_code=201)
def create_message(
    project_id: int,
    payload: MessageCreate,
    request: Request,
    qr_token: str = Query(None, description="志愿者侧凭 qr_token 授权；未提供则要求管理员认证"),
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    # H-3: IDOR 防护 — 志愿者凭 qr_token，JWT 用户凭 require_auth + edit 权限
    # R5-BE-2: require_admin → require_auth + require_project_access("edit")
    if qr_token:
        _verify_volunteer(qr_token, project, request)
    else:
        user_id = require_auth(request, credentials, db)
        if user_id != 0:
            _perm = _resolve_project_permission(db, user_id, project)
            if not _permission_ge(_perm, "edit"):
                raise HTTPException(status_code=403, detail="无项目编辑权限")
    if payload.task_id is not None:
        task = db.query(Task).filter(Task.id == payload.task_id, Task.project_id == project_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="关联任务不存在")
    # 内容审核：检测敏感词，不阻止提交，只标记 + 提示
    moderation = moderate_text(payload.content)
    message = Message(
        project_id=project_id,
        **payload.model_dump(),
        moderation_flagged=moderation.flagged,
        moderation_warning=moderation.warning or None,
    )
    db.add(message)
    # H-5: 子实体变更更新父项目 updated_at
    project.updated_at = _now()
    db.commit()
    db.refresh(message)
    publish(project_id, "message_created", {"id": message.id, "author": message.author})
    return message


@router.delete("/{project_id}/messages/{message_id}", status_code=204)
def soft_delete_message(
    project_id: int,
    message_id: int,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("admin")),
):
    """管理员软删除留言：仅标记 is_deleted=True，不真正删除数据，保留审计痕迹。

    软删除后：管理员视图显示「已删除」灰色样式，志愿者视图不显示。
    R5-BE-2: require_admin → require_project_access("admin")，支持 JWT 用户访问。
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    message = (
        db.query(Message)
        .filter(Message.id == message_id, Message.project_id == project_id)
        .first()
    )
    if not message:
        raise HTTPException(status_code=404, detail="留言不存在")
    message.is_deleted = True
    project.updated_at = _now()
    db.commit()
    publish(project_id, "message_created", {"id": message.id, "author": message.author})
    logger.info(
        f"actor={_actor_from_user_id(access[0])} action=soft_delete_message project_id={project_id} message_id={message_id}"
    )
    return None


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("admin")),
):
    # R6-BE-5: 改用 require_project_access("admin")，与其它端点对齐。
    #   owner 通过 _resolve_project_permission 的 owner_id 特权分支自动获得 admin，
    #   保留 owner 删除自己项目的权限；share 权限为 admin 的用户也能删除。
    # R7-BE-3 修复：收紧删除权限——仅 owner 可删除项目本体，share=admin 可管理共享
    #   但不能删除项目（硬删除不可逆，风险过高）。组织项目的 owner_id 是创建者的
    #   user_id，组织 admin 不是 owner_id，因此组织 admin 也不能删除项目。
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    user_id = access[0]
    # 仅 owner 可删除项目本体，share=admin 可管理共享但不能删除项目
    # R8-BE-1/R8-BE-2/R9-1: dev admin (user_id=0) 可删除任意项目（含 owner_id=None 孤儿项目）；
    #   JWT 用户仅能删除 owner_id == 自己的项目，share=admin 不可删除
    if user_id != 0 and project.owner_id != user_id:
        raise HTTPException(status_code=403, detail="仅项目所有者可删除项目")
    # DRL-R1-V20: 硬删除前记录审计日志（软删除需 models.py 加 deleted_at 字段，由其他 subagent 处理）
    logger.warning(
        "DELETE project %s by user %s actor=%s",
        project_id, user_id, _actor_from_user_id(user_id),
    )
    db.delete(project)
    db.commit()
    # P2-1: 推送 project_deleted 事件，让订阅客户端跳转回列表页
    publish(project_id, "project_deleted", {"id": project_id})
    logger.info(f"actor={_actor_from_user_id(user_id)} action=delete_project project_id={project_id}")
    return None


# ---------- 第三批升级：统计仪表盘 ----------
def _build_sankey_coords(volunteers: list, projects: list, flows: list) -> dict:
    """Phase 6.K1: 桑基图坐标预算（viewBox 800x360）。

    节点高度按 task_count 加权分配，流向在节点内部按顺序堆叠。
    返回结构包含 volunteers/projects 的 y_start/y_end/y_center 和 flows 的 y1/y2，
    模板直接遍历渲染即可。
    """
    Y_START = 40
    Y_END = 320
    GAP = 8
    total_tasks = sum(v["task_count"] for v in volunteers) or 1
    # 防御：节点数过多时缩小 gap 避免负高度
    n_v = len(volunteers)
    n_p = len(projects)
    left_gap = min(GAP, max(2, (Y_END - Y_START) // max(n_v * 4, 1)))
    right_gap = min(GAP, max(2, (Y_END - Y_START) // max(n_p * 4, 1)))
    left_avail = max(40, Y_END - Y_START - left_gap * max(n_v - 1, 0))
    right_avail = max(40, Y_END - Y_START - right_gap * max(n_p - 1, 0))

    # 志愿者节点 y 坐标
    v_cursor = Y_START
    v_y_map: dict = {}
    for v in volunteers:
        h = max(3, v["task_count"] / total_tasks * left_avail)
        v["y_start"] = round(v_cursor, 2)
        v["y_end"] = round(v_cursor + h, 2)
        v["y_center"] = round(v_cursor + h / 2, 2)
        v_y_map[v["name"]] = v_cursor
        v_cursor += h + left_gap

    # 项目节点 y 坐标
    p_cursor = Y_START
    p_y_map: dict = {}
    for p in projects:
        h = max(3, p["task_count"] / total_tasks * right_avail)
        p["y_start"] = round(p_cursor, 2)
        p["y_end"] = round(p_cursor + h, 2)
        p["y_center"] = round(p_cursor + h / 2, 2)
        p_y_map[p["name"]] = p_cursor
        p_cursor += h + right_gap

    # 流向 y1/y2（在节点内部按顺序堆叠）
    for f in flows:
        v_h = max(1, f["value"] / total_tasks * left_avail)
        p_h = max(1, f["value"] / total_tasks * right_avail)
        f["y1"] = round(v_y_map[f["volunteer"]] + v_h / 2, 2)
        f["y2"] = round(p_y_map[f["project"]] + p_h / 2, 2)
        f["stroke_w"] = round(max(1, f["value"] / max(total_tasks, 1) * 24), 2)
        v_y_map[f["volunteer"]] += v_h
        p_y_map[f["project"]] += p_h

    return {
        "volunteers": volunteers,
        "projects": projects,
        "flows": flows,
        "total_tasks": int(total_tasks),
        "cross_project_volunteers": sum(1 for v in volunteers if len(v.get("projects", [])) > 1),
        "viewBox_w": 800,
        "viewBox_h": 360,
    }


def _build_project_network(projects: list, volunteers: list) -> dict:
    """Phase 6.K3: 项目间共享志愿者网络图。

    节点 = 项目（圆形，半径按 task_count 缩放）
    边 = 两个项目共享的志愿者（粗细 = 共享人数）
    布局 = 固定圆形布局（≤6 个项目时三角形/四边形/五边形/六边形）
    """
    import math
    n = len(projects)
    if n == 0:
        return {"nodes": [], "edges": [], "shared_volunteers": [], "viewBox_w": 400, "viewBox_h": 360}

    CX, CY, R_LAYOUT = 200, 180, 120
    # 节点圆形布局
    nodes = []
    for i, p in enumerate(projects):
        ang = -90 + i * (360 / n)  # 顶部起顺时针
        rad = math.radians(ang)
        x = CX + R_LAYOUT * math.cos(rad)
        y = CY + R_LAYOUT * math.sin(rad)
        nodes.append({
            "name": p["name"],
            "task_count": p["task_count"],
            "volunteer_count": len(p.get("volunteers", [])),
            "x": round(x, 2),
            "y": round(y, 2),
            "radius": round(20 + min(p["task_count"], 20) * 0.8, 2),
        })

    # 项目名 → index 映射
    name_to_idx = {p["name"]: i for i, p in enumerate(projects)}

    # 遍历跨项目志愿者，构建 edges
    edge_map: dict = {}  # (i, j) -> [volunteer names]
    shared_volunteers = []
    for v in volunteers:
        ps = v.get("projects", [])
        if len(ps) < 2:
            continue
        shared_volunteers.append({"name": v["name"], "projects": ps, "task_count": v["task_count"]})
        # 为每对项目创建 edge
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                a, b = name_to_idx.get(ps[i]), name_to_idx.get(ps[j])
                if a is None or b is None:
                    continue
                key = (min(a, b), max(a, b))
                edge_map.setdefault(key, []).append(v["name"])

    edges = []
    for (a, b), vol_names in edge_map.items():
        edges.append({
            "source": a,
            "target": b,
            "volunteers": vol_names,
            "weight": len(vol_names),
            "source_xy": (nodes[a]["x"], nodes[a]["y"]),
            "target_xy": (nodes[b]["x"], nodes[b]["y"]),
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "shared_volunteers": shared_volunteers,
        "viewBox_w": 400,
        "viewBox_h": 360,
        "cx": CX,
        "cy": CY,
    }


def _build_radar_coords(project_health: list) -> list:
    """Phase 6.K2: 雷达图坐标预算（5 维，每维 0-100）。

    5 个轴：活跃度 / 完成率 / 交接质量 / 留言密度 / Agent 干预
    顶点角度从 -90°（顶部）顺时针，每轴 72°。
    为每个项目的 scores 添加 points 字段（SVG polygon 顶点坐标列表）。
    同时返回公共的轴标签坐标（供模板渲染网格线）。
    """
    import math
    CX = 200  # 中心 x
    CY = 200  # 中心 y
    R = 150   # 最大半径
    N = 5     # 维度数
    angles = [-90 + i * (360 / N) for i in range(N)]  # 顶部起顺时针

    # 公共轴端点（满分 100 处的网格点）
    axis_endpoints = []
    for i, ang in enumerate(angles):
        rad = math.radians(ang)
        x = CX + R * math.cos(rad)
        y = CY + R * math.sin(rad)
        axis_endpoints.append({"x": round(x, 2), "y": round(y, 2)})

    # 5 级网格圆（20/40/60/80/100）
    grid_rings = []
    for level in (20, 40, 60, 80, 100):
        pts = []
        for ang in angles:
            rad = math.radians(ang)
            r = R * level / 100
            pts.append((round(CX + r * math.cos(rad), 2), round(CY + r * math.sin(rad), 2)))
        grid_rings.append({"level": level, "points": pts})

    # 轴标签位置（端点外侧）
    axis_labels = [
        {"label": "活跃度", "x": round(CX + (R + 25) * math.cos(math.radians(angles[0])), 2),
         "y": round(CY + (R + 25) * math.sin(math.radians(angles[0])), 2) - 5},
        {"label": "完成率", "x": round(CX + (R + 25) * math.cos(math.radians(angles[1])), 2) + 5,
         "y": round(CY + (R + 25) * math.sin(math.radians(angles[1])), 2) + 5},
        {"label": "交接质量", "x": round(CX + (R + 25) * math.cos(math.radians(angles[2])), 2) + 5,
         "y": round(CY + (R + 25) * math.sin(math.radians(angles[2])), 2) + 5},
        {"label": "留言密度", "x": round(CX + (R + 25) * math.cos(math.radians(angles[3])), 2),
         "y": round(CY + (R + 25) * math.sin(math.radians(angles[3])), 2) + 12},
        {"label": "Agent 干预", "x": round(CX + (R + 25) * math.cos(math.radians(angles[4])), 2) - 5,
         "y": round(CY + (R + 25) * math.sin(math.radians(angles[4])), 2) + 5},
    ]

    # 每个项目的 polygon 顶点
    score_keys = ["activity", "completion", "handoff_quality", "message_density", "agent_action"]
    for ph in project_health:
        pts = []
        for i, ang in enumerate(angles):
            rad = math.radians(ang)
            score = ph["scores"][score_keys[i]]
            r = R * score / 100
            pts.append((round(CX + r * math.cos(rad), 2), round(CY + r * math.sin(rad), 2)))
        ph["polygon_points"] = " ".join(f"{x},{y}" for x, y in pts)

    return {
        "projects": project_health,
        "axis_endpoints": axis_endpoints,
        "grid_rings": grid_rings,
        "axis_labels": axis_labels,
        "cx": CX,
        "cy": CY,
        "r": R,
        "viewBox_w": 400,
        "viewBox_h": 400,
        "score_keys": score_keys,
    }


def compute_stats(db: Session) -> dict:
    """聚合项目/任务/留言/交接统计 + 志愿者贡献榜。

    返回结构稳定：by_status 字典始终包含全部状态键（即使计数为 0），
    便于模板和前端直接取用，无需处理缺键。

    P2-8: 改用 SQL GROUP BY 聚合查询，避免全表扫描。
    Phase 4.3: 扩展指标——用户总数、SyncItem 跨产品使用、Agent 建议统计、
    Context Snapshots/Inspirations 聚合、近 7 天多维度趋势。
    """
    # 项目按状态分组
    project_status = {"active": 0, "archived": 0, "done": 0}
    for status, count in db.query(Project.status, func.count(Project.id)).group_by(Project.status).all():
        if status in project_status:
            project_status[status] = count

    # 任务按状态分组
    task_status = {"todo": 0, "in_progress": 0, "done": 0}
    for status, count in db.query(Task.status, func.count(Task.id)).group_by(Task.status).all():
        if status in task_status:
            task_status[status] = count

    # 总数：用聚合查询替代 len(list)
    project_total = db.query(func.count(Project.id)).scalar() or 0
    task_total = db.query(func.count(Task.id)).scalar() or 0
    message_total = db.query(func.count(Message.id)).scalar() or 0
    handoff_total = db.query(func.count(Handoff.id)).scalar() or 0

    # 志愿者贡献榜：按 owner_name 统计任务数，取前 10
    # 过滤 NULL 和空字符串，保持与原实现一致（schema 已 strip，无需再处理空白）
    top_owners_rows = (
        db.query(Task.owner_name, func.count(Task.id))
        .filter(Task.owner_name.isnot(None), Task.owner_name != "")
        .group_by(Task.owner_name)
        .order_by(func.count(Task.id).desc())
        .limit(10)
        .all()
    )
    top_owners = [{"owner_name": name, "task_count": count} for name, count in top_owners_rows]

    # Phase 6.K1 桑基图：志愿者 × 项目流向（task 数为权重）
    # 复用 Phase 6.1 叙事数据：小李 P3→P1 跨项目成长线，小张 P1+P2 物资协调线
    flow_rows = (
        db.query(Task.owner_name, Task.project_id, Project.name, func.count(Task.id))
        .join(Project, Task.project_id == Project.id)
        .filter(Task.owner_name.isnot(None), Task.owner_name != "")
        .group_by(Task.owner_name, Task.project_id, Project.name)
        .order_by(Task.owner_name, func.count(Task.id).desc())
        .all()
    )
    v_counter: dict = {}
    p_counter: dict = {}
    v_projects: dict = {}
    p_volunteers: dict = {}
    flows = []
    for owner_name, pid, pname, cnt in flow_rows:
        v_counter[owner_name] = v_counter.get(owner_name, 0) + cnt
        p_counter[pname] = p_counter.get(pname, 0) + cnt
        v_projects.setdefault(owner_name, []).append(pname)
        p_volunteers.setdefault(pname, []).append(owner_name)
        flows.append({"volunteer": owner_name, "project": pname, "value": int(cnt)})
    # 按任务数降序排序，桑基图节点定位依赖有序输入
    volunteers = [
        {"name": n, "task_count": c, "projects": v_projects[n]}
        for n, c in sorted(v_counter.items(), key=lambda x: -x[1])
    ]
    projects_flow = [
        {"name": n, "task_count": c, "volunteers": p_volunteers[n]}
        for n, c in sorted(p_counter.items(), key=lambda x: -x[1])
    ]
    volunteer_flows = _build_sankey_coords(volunteers, projects_flow, flows)

    # Phase 6.K3 网络图：项目间共享志愿者关联（项目-项目网络）
    # 复用 volunteer_flows 数据：遍历跨项目志愿者，为每对项目创建 edge
    project_network = _build_project_network(projects_flow, volunteers)

    # Phase 6.K2 雷达图：项目健康度 5 维评分
    # 5 维：活跃度（近 7 天活动）/ 完成率 / 交接质量 / 留言密度 / Agent 干预
    # 4 次 GROUP BY 查询避免 N+1，外加 2 次 recent 活动查询
    today = datetime.now(timezone.utc).date()
    seven_days_ago = today - timedelta(days=7)
    heat_start_dt_recent = datetime.combine(seven_days_ago, datetime.min.time(), tzinfo=timezone.utc)

    task_by_proj = {
        pid: {"total": t_cnt, "done": d_cnt or 0}
        for pid, t_cnt, d_cnt in db.query(
            Task.project_id,
            func.count(Task.id),
            func.sum(case((Task.status == "done", 1), else_=0)),
        ).group_by(Task.project_id).all()
    }
    handoff_by_proj = {
        pid: {"total": h_cnt, "confirmed": c_cnt or 0}
        for pid, h_cnt, c_cnt in db.query(
            Handoff.project_id,
            func.count(Handoff.id),
            func.sum(case((Handoff.status == "confirmed", 1), else_=0)),
        ).group_by(Handoff.project_id).all()
    }
    msg_by_proj = {
        pid: cnt for pid, cnt in db.query(Message.project_id, func.count(Message.id)).group_by(Message.project_id).all()
    }
    sug_by_proj = {
        pid: {"total": s_cnt, "acted": a_cnt or 0}
        for pid, s_cnt, a_cnt in db.query(
            AgentSuggestion.project_id,
            func.count(AgentSuggestion.id),
            func.sum(case((AgentSuggestion.status == "acted", 1), else_=0)),
        ).group_by(AgentSuggestion.project_id).all()
    }
    # 近 7 天活动数（tasks + messages）
    recent_task_by_proj = {
        pid: cnt
        for pid, cnt in db.query(Task.project_id, func.count(Task.id))
        .filter(Task.created_at >= heat_start_dt_recent)
        .group_by(Task.project_id).all()
    }
    recent_msg_by_proj = {
        pid: cnt
        for pid, cnt in db.query(Message.project_id, func.count(Message.id))
        .filter(Message.created_at >= heat_start_dt_recent)
        .group_by(Message.project_id).all()
    }
    # Phase 6.K2 修复：空数据库时 max() 抛 ValueError，用 default=1 兜底；
    # 同时复用 projects 列表避免重复查询（与 Phase 6.2 N+1 优化一致）
    all_projects = db.query(Project).order_by(Project.created_at.desc()).all()
    max_recent = max(
        (recent_task_by_proj.get(p.id, 0) + recent_msg_by_proj.get(p.id, 0) for p in all_projects),
        default=1,
    )

    project_health = []
    for p in all_projects:
        t = task_by_proj.get(p.id, {"total": 0, "done": 0})
        h = handoff_by_proj.get(p.id, {"total": 0, "confirmed": 0})
        m = msg_by_proj.get(p.id, 0)
        s = sug_by_proj.get(p.id, {"total": 0, "acted": 0})
        recent = recent_task_by_proj.get(p.id, 0) + recent_msg_by_proj.get(p.id, 0)
        # 5 维评分（0-100）
        activity = round(recent / max_recent * 100) if max_recent else 0
        completion = round(t["done"] / t["total"] * 100) if t["total"] else 0
        handoff_q = round(h["confirmed"] / h["total"] * 100) if h["total"] else 0
        msg_density = round(min(m / t["total"], 1.0) * 100) if t["total"] else 0
        agent_act = round(s["acted"] / s["total"] * 100) if s["total"] else 0
        project_health.append({
            "id": p.id,
            "name": p.name,
            "status": p.status,
            "scores": {
                "activity": activity,
                "completion": completion,
                "handoff_quality": handoff_q,
                "message_density": msg_density,
                "agent_action": agent_act,
            },
            "raw": {
                "recent_activity": recent,
                "tasks_total": t["total"],
                "tasks_done": t["done"],
                "handoffs_total": h["total"],
                "handoffs_confirmed": h["confirmed"],
                "messages": m,
                "suggestions_total": s["total"],
                "suggestions_acted": s["acted"],
            },
        })

    # 雷达图 SVG 坐标预算
    project_health = _build_radar_coords(project_health)

    # 近 7 天趋势：today 锚点（Phase 6.2 优化：复用热力图聚合数据，避免 35 次 N+1 查询）
    # today 已在 Phase 6.K2 雷达图段定义

    # Phase 4.3: 用户总数 + 活跃用户数（is_active=1）
    user_total = db.query(func.count(User.id)).scalar() or 0
    user_active = (
        db.query(func.count(User.id))
        .filter(User.is_active.is_(True))
        .scalar()
        or 0
    )

    # Phase 4.3: SyncItem 跨产品使用情况——按 app 和 item_type 双维度聚合
    sync_by_app = {"focus": 0, "muse": 0}
    for app, count in db.query(SyncItem.app, func.count(SyncItem.id)).group_by(SyncItem.app).all():
        if app in sync_by_app:
            sync_by_app[app] = count
    sync_by_item_type = {"snapshot": 0, "inspiration": 0, "mood": 0}
    for item_type, count in db.query(SyncItem.item_type, func.count(SyncItem.id)).group_by(SyncItem.item_type).all():
        if item_type in sync_by_item_type:
            sync_by_item_type[item_type] = count
    sync_total = db.query(func.count(SyncItem.id)).scalar() or 0
    # SyncItem 活跃用户数（distinct owner_id，dev 模式 owner_id=0 也计入）
    sync_active_users = (
        db.query(func.count(func.distinct(SyncItem.owner_id)))
        .scalar()
        or 0
    )

    # Phase 4.3: Context Snapshots/Inspirations 总数
    context_snapshots_total = db.query(func.count(ProjectContextSnapshot.id)).scalar() or 0
    context_inspirations_total = db.query(func.count(ProjectContextInspiration.id)).scalar() or 0

    # Phase 4.3: Agent 建议——按 type 和 status 双维度聚合
    agent_by_type = {"stale_warning": 0, "overdue_task": 0, "handoff_gap": 0, "daily_summary": 0}
    for s_type, count in db.query(AgentSuggestion.type, func.count(AgentSuggestion.id)).group_by(AgentSuggestion.type).all():
        if s_type in agent_by_type:
            agent_by_type[s_type] = count
    agent_by_status = {"pending": 0, "shown": 0, "dismissed": 0, "acted": 0}
    for status, count in db.query(AgentSuggestion.status, func.count(AgentSuggestion.id)).group_by(AgentSuggestion.status).all():
        if status in agent_by_status:
            agent_by_status[status] = count
    agent_total = db.query(func.count(AgentSuggestion.id)).scalar() or 0

    # Phase 4.3: 志愿者活跃度热力图（近 30 天多源活动按天聚合）
    # 5 次大查询拉取近 30 天所有记录的 created_at，Python 端按天聚合
    # 避免 30 × 5 = 150 次 N+1 查询；SQLite 对 DateTime/Integer ms 异构字段无法统一 SQL 聚合
    heat_start_dt = datetime.combine(today - timedelta(days=29), datetime.min.time(), tzinfo=timezone.utc)
    heat_start_ms = int(heat_start_dt.timestamp() * 1000)

    proj_dates = [r[0] for r in db.query(Project.created_at).filter(Project.created_at >= heat_start_dt).all()]
    task_dates = [r[0] for r in db.query(Task.created_at).filter(Task.created_at >= heat_start_dt).all()]
    msg_dates = [r[0] for r in db.query(Message.created_at).filter(Message.created_at >= heat_start_dt).all()]
    snap_dates_ms = [r[0] for r in db.query(ProjectContextSnapshot.created_at).filter(ProjectContextSnapshot.created_at >= heat_start_ms).all()]
    insp_dates_ms = [r[0] for r in db.query(ProjectContextInspiration.created_at).filter(ProjectContextInspiration.created_at >= heat_start_ms).all()]

    def _to_utc_day(value):
        """将 DateTime（可能 naive）或 Integer ms 时间戳统一转为 UTC date。

        _now() 返回 timezone-aware UTC datetime，但 SQLite 读取 DateTime 列时
        通常返回 naive datetime（驱动未保留 tzinfo），故显式补 UTC。
        """
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).date()
        return value.astimezone(timezone.utc).date()

    proj_by_day: defaultdict = defaultdict(int)
    for v in proj_dates:
        proj_by_day[_to_utc_day(v)] += 1
    task_by_day: defaultdict = defaultdict(int)
    for v in task_dates:
        task_by_day[_to_utc_day(v)] += 1
    msg_by_day: defaultdict = defaultdict(int)
    for v in msg_dates:
        msg_by_day[_to_utc_day(v)] += 1
    snap_by_day: defaultdict = defaultdict(int)
    for v in snap_dates_ms:
        snap_by_day[_to_utc_day(v)] += 1
    insp_by_day: defaultdict = defaultdict(int)
    for v in insp_dates_ms:
        insp_by_day[_to_utc_day(v)] += 1

    activity_heatmap = []
    for i in range(29, -1, -1):
        day = today - timedelta(days=i)
        p = proj_by_day.get(day, 0)
        t = task_by_day.get(day, 0)
        m = msg_by_day.get(day, 0)
        s = snap_by_day.get(day, 0)
        ins = insp_by_day.get(day, 0)
        activity_heatmap.append({
            "date": day.strftime("%Y-%m-%d"),
            "total": p + t + m + s + ins,
            "projects": p,
            "tasks": t,
            "messages": m,
            "snapshots": s,
            "inspirations": ins,
        })

    # Phase 6.2 性能优化：复用热力图 30 天聚合数据提取近 7 天趋势
    # 原实现 7 天 × 5 表 = 35 次 N+1 查询，现 0 次额外查询（热力图已聚合）
    # date 格式：热力图 "YYYY-MM-DD" → 近 7 天趋势 "MM-DD"（与原 daily_* 格式一致）
    last_7_days = activity_heatmap[-7:]
    daily_projects = [{"date": d["date"][5:], "count": d["projects"]} for d in last_7_days]
    daily_tasks = [{"date": d["date"][5:], "count": d["tasks"]} for d in last_7_days]
    daily_messages = [{"date": d["date"][5:], "count": d["messages"]} for d in last_7_days]
    daily_snapshots = [{"date": d["date"][5:], "count": d["snapshots"]} for d in last_7_days]
    daily_inspirations = [{"date": d["date"][5:], "count": d["inspirations"]} for d in last_7_days]

    return {
        "projects": {"total": project_total, "by_status": project_status},
        "tasks": {"total": task_total, "by_status": task_status},
        "messages_total": message_total,
        "handoffs_total": handoff_total,
        "top_owners": top_owners,
        "daily_tasks": daily_tasks,
        # Phase 4.3 扩展指标
        "users": {"total": user_total, "active": user_active},
        "sync": {
            "total": sync_total,
            "by_app": sync_by_app,
            "by_item_type": sync_by_item_type,
            "active_users": sync_active_users,
        },
        "context": {
            "snapshots_total": context_snapshots_total,
            "inspirations_total": context_inspirations_total,
        },
        "agent": {
            "total": agent_total,
            "by_type": agent_by_type,
            "by_status": agent_by_status,
        },
        "daily_projects": daily_projects,
        "daily_messages": daily_messages,
        "daily_snapshots": daily_snapshots,
        "daily_inspirations": daily_inspirations,
        # Phase 4.3: 志愿者活跃度热力图（近 30 天，按天聚合）
        "activity_heatmap": activity_heatmap,
        # Phase 6.K1: 桑基图数据（志愿者 × 项目流向）
        "volunteer_flows": volunteer_flows,
        # Phase 6.K2: 雷达图数据（项目健康度 5 维评分）
        "project_health": project_health,
        # Phase 6.K3: 网络图数据（项目间共享志愿者关联）
        "project_network": project_network,
    }


@stats_router.get("/stats")
def get_stats(db: Session = Depends(get_db), admin: str = Depends(require_admin)):
    """第三批升级：返回统计数据 JSON。

    P0-1: 统计数据含项目聚合信息，限制仅管理员可访问。
    """
    return compute_stats(db)


# W087b: 数据库备份管理端点（仅管理员）
@stats_router.get("/backups")
def list_db_backups(admin: str = Depends(require_admin)):
    """列出所有可用数据库备份。"""
    from backup import list_backups
    return {"backups": list_backups()}


@stats_router.post("/backups")
def create_db_backup(admin: str = Depends(require_admin)):
    """手动触发数据库备份。"""
    from backup import backup_db
    path = backup_db()
    if path is None:
        raise HTTPException(status_code=500, detail="备份失败，请检查日志")
    return {"message": "备份成功", "backup": path.name, "size": path.stat().st_size}


@stats_router.post("/backups/{backup_name}/restore")
def restore_db_backup(backup_name: str, admin: str = Depends(require_admin)):
    """从指定备份恢复数据库。

    恢复后需要重启服务才能生效（当前连接仍指向旧数据库）。
    """
    from backup import restore_db, list_backups
    # 安全校验：backup_name 必须在已有备份列表中
    valid_names = {b["name"] for b in list_backups()}
    if backup_name not in valid_names:
        raise HTTPException(status_code=400, detail="无效的备份文件名")
    success = restore_db(backup_name)
    if not success:
        raise HTTPException(status_code=500, detail="恢复失败，请检查日志")
    return {"message": "恢复成功，请重启服务以生效", "restored_from": backup_name}


# ============================================================
# Phase 4.1: 跨产品联动端点（Focus 快照 / Muse 灵感 → Bridge 项目）
# ============================================================

def _snapshot_to_out(snap: ProjectContextSnapshot) -> dict:
    """将 ProjectContextSnapshot ORM 对象转为 ContextSnapshotOut 兼容 dict。"""
    import json as _json
    try:
        urls = _json.loads(snap.urls_json) if snap.urls_json else []
        if not isinstance(urls, list):
            urls = []
    except (ValueError, TypeError):
        urls = []
    return {
        "id": snap.id,
        "project_id": snap.project_id,
        "snapshot_uuid": snap.snapshot_uuid,
        "title": snap.title,
        "summary": snap.summary or "",
        "urls": urls,
        "tab_count": snap.tab_count,
        "created_at": snap.created_at,
        "pushed_at": snap.pushed_at,
        "pushed_by": snap.pushed_by,
    }


def _inspiration_to_out(insp: ProjectContextInspiration) -> dict:
    """将 ProjectContextInspiration ORM 对象转为 ContextInspirationOut 兼容 dict。"""
    return {
        "id": insp.id,
        "project_id": insp.project_id,
        "inspiration_uuid": insp.inspiration_uuid,
        "title": insp.title,
        "url": insp.url,
        "note": insp.note or "",
        "mood": insp.mood or "",
        "created_at": insp.created_at,
        "pushed_at": insp.pushed_at,
        "pushed_by": insp.pushed_by,
    }


@router.post(
    "/{project_id}/context-snapshots",
    response_model=ContextSnapshotOut,
    status_code=201,
)
def push_context_snapshot(
    project_id: int,
    payload: ContextSnapshotCreate,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("edit")),
):
    """Phase 4.1: Focus 快照推送到 Bridge 项目档案。

    仅推送元数据 + AI 摘要，不含实际标签页内容（隐私优先）。
    幂等：同一 (project_id, snapshot_uuid) 重复推送返回 409。
    权限：require_project_access('edit')。
    """
    import json as _json
    user_id, _perm = access
    existing = (
        db.query(ProjectContextSnapshot)
        .filter(
            ProjectContextSnapshot.project_id == project_id,
            ProjectContextSnapshot.snapshot_uuid == payload.snapshot_uuid,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="该快照已推送到此项目")
    # URL 列表截断 + 类型校验
    urls = [str(u)[:2048] for u in payload.urls if isinstance(u, str)][:100]
    now_ms = int(time.time() * 1000)
    snap = ProjectContextSnapshot(
        project_id=project_id,
        snapshot_uuid=payload.snapshot_uuid,
        title=payload.title[:200],
        summary=payload.summary[:4000],
        urls_json=_json.dumps(urls, ensure_ascii=False),
        tab_count=payload.tab_count,
        created_at=payload.created_at,
        pushed_at=now_ms,
        pushed_by=user_id,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    logger.info(
        f"actor={_actor_from_user_id(user_id)} action=push_context_snapshot "
        f"project_id={project_id} snapshot_uuid={payload.snapshot_uuid} tabs={payload.tab_count}"
    )
    # Phase 7.4 (建议 3): 实时 SSE 推送 — 前端监听后自动刷新快照列表 + toast 提示
    publish(project_id, "context_snapshot_created", {
        "id": snap.id,
        "title": snap.title,
        "tab_count": snap.tab_count,
    })
    return _snapshot_to_out(snap)


@router.get(
    "/{project_id}/context-snapshots",
    response_model=List[ContextSnapshotOut],
)
def list_context_snapshots(
    project_id: int,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("view")),
):
    """Phase 4.1: 列出项目关联的 Focus 快照（按创建时间倒序）。"""
    snaps = (
        db.query(ProjectContextSnapshot)
        .filter(ProjectContextSnapshot.project_id == project_id)
        .order_by(ProjectContextSnapshot.created_at.desc())
        .all()
    )
    return [_snapshot_to_out(s) for s in snaps]


@router.delete(
    "/{project_id}/context-snapshots/{snapshot_id}",
    status_code=204,
)
def delete_context_snapshot(
    project_id: int,
    snapshot_id: int,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("edit")),
):
    """Phase 4.1: 删除项目关联的 Focus 快照。"""
    user_id, _perm = access
    snap = (
        db.query(ProjectContextSnapshot)
        .filter(
            ProjectContextSnapshot.id == snapshot_id,
            ProjectContextSnapshot.project_id == project_id,
        )
        .first()
    )
    if snap is None:
        raise HTTPException(status_code=404, detail="快照不存在")
    db.delete(snap)
    db.commit()
    logger.info(
        f"actor={_actor_from_user_id(user_id)} action=delete_context_snapshot "
        f"project_id={project_id} snapshot_id={snapshot_id}"
    )
    # Phase 7.4 (建议 3): 实时 SSE 推送 — 前端监听后自动刷新快照列表
    publish(project_id, "context_snapshot_deleted", {"id": snapshot_id})
    return None


@router.post(
    "/{project_id}/context-inspirations",
    response_model=ContextInspirationOut,
    status_code=201,
)
def push_context_inspiration(
    project_id: int,
    payload: ContextInspirationCreate,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("edit")),
):
    """Phase 4.1: Muse 灵感推送到 Bridge 项目档案。

    幂等：同一 (project_id, inspiration_uuid) 重复推送返回 409。
    权限：require_project_access('edit')。
    """
    user_id, _perm = access
    existing = (
        db.query(ProjectContextInspiration)
        .filter(
            ProjectContextInspiration.project_id == project_id,
            ProjectContextInspiration.inspiration_uuid == payload.inspiration_uuid,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="该灵感已推送到此项目")
    now_ms = int(time.time() * 1000)
    insp = ProjectContextInspiration(
        project_id=project_id,
        inspiration_uuid=payload.inspiration_uuid,
        title=payload.title[:200],
        url=payload.url[:2048],
        note=payload.note[:4000],
        mood=payload.mood[:60],
        created_at=payload.created_at,
        pushed_at=now_ms,
        pushed_by=user_id,
    )
    db.add(insp)
    db.commit()
    db.refresh(insp)
    logger.info(
        f"actor={_actor_from_user_id(user_id)} action=push_context_inspiration "
        f"project_id={project_id} inspiration_uuid={payload.inspiration_uuid}"
    )
    # Phase 7.4 (建议 3): 实时 SSE 推送 — 前端监听后自动刷新灵感列表 + toast 提示
    publish(project_id, "context_inspiration_created", {
        "id": insp.id,
        "title": insp.title,
        "mood": insp.mood,
    })
    return _inspiration_to_out(insp)


@router.get(
    "/{project_id}/context-inspirations",
    response_model=List[ContextInspirationOut],
)
def list_context_inspirations(
    project_id: int,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("view")),
):
    """Phase 4.1: 列出项目关联的 Muse 灵感（按创建时间倒序）。"""
    insps = (
        db.query(ProjectContextInspiration)
        .filter(ProjectContextInspiration.project_id == project_id)
        .order_by(ProjectContextInspiration.created_at.desc())
        .all()
    )
    return [_inspiration_to_out(i) for i in insps]


@router.delete(
    "/{project_id}/context-inspirations/{inspiration_id}",
    status_code=204,
)
def delete_context_inspiration(
    project_id: int,
    inspiration_id: int,
    db: Session = Depends(get_db),
    access: tuple = Depends(require_project_access("edit")),
):
    """Phase 4.1: 删除项目关联的 Muse 灵感。"""
    user_id, _perm = access
    insp = (
        db.query(ProjectContextInspiration)
        .filter(
            ProjectContextInspiration.id == inspiration_id,
            ProjectContextInspiration.project_id == project_id,
        )
        .first()
    )
    if insp is None:
        raise HTTPException(status_code=404, detail="灵感不存在")
    db.delete(insp)
    db.commit()
    logger.info(
        f"actor={_actor_from_user_id(user_id)} action=delete_context_inspiration "
        f"project_id={project_id} inspiration_id={inspiration_id}"
    )
    # Phase 7.4 (建议 3): 实时 SSE 推送 — 前端监听后自动刷新灵感列表
    publish(project_id, "context_inspiration_deleted", {"id": inspiration_id})
    return None
