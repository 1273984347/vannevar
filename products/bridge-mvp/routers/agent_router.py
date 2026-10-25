# routers/agent_router.py — 阶段2.2 服务端 Agent 建议端点 + 扫描函数
"""提供 Agent 建议的 CRUD 端点 + APScheduler 定时扫描函数。

端点：
- GET  /api/projects/{project_id}/suggestions           列出建议
- POST /api/projects/{project_id}/suggestions/{sid}/ack  ACK（pending→shown）
- POST /api/projects/{project_id}/suggestions/{sid}/dismiss  忽略
- POST /api/projects/{project_id}/suggestions/{sid}/act  标记已采纳

扫描函数（由 APScheduler 调用）：
- scan_stale_projects()   每 6h 扫描停滞项目
- scan_overdue_tasks()    每 3h 扫描超期任务
- daily_summary_job()     每日 18:00 日报
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import require_project_access
from ai import generate_proactive_suggestion, is_demo_mode
from database import get_db
from models import AgentSuggestion, Project, Task, Message, Handoff
from routers.events import publish
from schemas import AgentSuggestionOut, AgentSuggestionListResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# DRL-R1-V22: 每日日报使用本地时区计算"今日"，避免 UTC 与部署地时区错配
_TZ = ZoneInfo(os.getenv("BRIDGE_TIMEZONE", "Asia/Shanghai"))


def _utcnow() -> datetime:
    """返回 naive UTC datetime（与 SQLite 存储的 naive datetime 一致，避免时区减法失败）。

    R2-V12: 使用 datetime.now(timezone.utc).replace(tzinfo=None) 替代已弃用的 utcnow()，
    保持返回 naive UTC datetime 与现有 DB 时间戳格式兼容。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ============================================================
# 端点
# ============================================================


@router.get(
    "/api/projects/{project_id}/suggestions",
    response_model=AgentSuggestionListResponse,
    tags=["agent"],
)
def list_suggestions(
    project_id: int,
    access: tuple = Depends(require_project_access("view")),
    db: Session = Depends(get_db),
):
    """列出项目的 Agent 建议（按创建时间倒序）。"""
    suggestions = (
        db.query(AgentSuggestion)
        .filter(AgentSuggestion.project_id == project_id)
        .order_by(AgentSuggestion.created_at.desc())
        .all()
    )
    return AgentSuggestionListResponse(
        count=len(suggestions),
        items=[AgentSuggestionOut.model_validate(s) for s in suggestions],
    )


@router.post(
    "/api/projects/{project_id}/suggestions/{sid}/ack",
    response_model=AgentSuggestionOut,
    tags=["agent"],
)
def ack_suggestion(
    project_id: int,
    sid: int,
    # DRL-R1-V13: 提升权限至 edit，与 act_suggestion 保持一致（状态变更属写操作）
    access: tuple = Depends(require_project_access("edit")),
    db: Session = Depends(get_db),
):
    """ACK 建议：pending → shown（客户端确认已看到）。"""
    s = db.query(AgentSuggestion).filter(
        AgentSuggestion.id == sid,
        AgentSuggestion.project_id == project_id,
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="建议不存在")
    if s.status == "pending":
        s.status = "shown"
        db.commit()
        db.refresh(s)
    return AgentSuggestionOut.model_validate(s)


@router.post(
    "/api/projects/{project_id}/suggestions/{sid}/dismiss",
    response_model=AgentSuggestionOut,
    tags=["agent"],
)
def dismiss_suggestion(
    project_id: int,
    sid: int,
    # DRL-R1-V13: 提升权限至 edit，与 act_suggestion 保持一致（状态变更属写操作）
    access: tuple = Depends(require_project_access("edit")),
    db: Session = Depends(get_db),
):
    """忽略建议：→ dismissed。"""
    s = db.query(AgentSuggestion).filter(
        AgentSuggestion.id == sid,
        AgentSuggestion.project_id == project_id,
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="建议不存在")
    s.status = "dismissed"
    db.commit()
    db.refresh(s)
    return AgentSuggestionOut.model_validate(s)


@router.post(
    "/api/projects/{project_id}/suggestions/{sid}/act",
    response_model=AgentSuggestionOut,
    tags=["agent"],
)
def act_suggestion(
    project_id: int,
    sid: int,
    access: tuple = Depends(require_project_access("edit")),
    db: Session = Depends(get_db),
):
    """标记建议已采纳：→ acted（记录 acted_at）。"""
    s = db.query(AgentSuggestion).filter(
        AgentSuggestion.id == sid,
        AgentSuggestion.project_id == project_id,
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="建议不存在")
    s.status = "acted"
    s.acted_at = _utcnow()
    db.commit()
    db.refresh(s)
    return AgentSuggestionOut.model_validate(s)


# ============================================================
# 扫描函数（由 APScheduler 或手动调用）
# ============================================================


def _create_suggestion(
    db: Session,
    project_id: int,
    suggestion_type: str,
    title: str,
    body: str,
    severity: str = "info",
) -> bool:
    """创建建议（INSERT OR IGNORE 语义，重复跳过）。

    补充-P2-1: 三层并发去重：
    1. DB 层 UNIQUE(project_id, type, date(created_at))
    2. 应用层 INSERT OR IGNORE（通过 try/except IntegrityError 实现）
    3. 调度层 APScheduler coalesce + max_instances（在 main.py 配置）

    Returns:
        True 表示新建，False 表示重复跳过。
    """
    from sqlalchemy.exc import IntegrityError

    s = AgentSuggestion(
        project_id=project_id,
        type=suggestion_type,
        title=title,
        body=body,
        severity=severity,
        status="pending",
    )
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    db.refresh(s)

    # SSE 推送（仅推给在线订阅者）
    try:
        publish(project_id, "agent_suggestion", {
            "id": s.id,
            "type": s.type,
            "title": s.title,
            "severity": s.severity,
        })
    except Exception as e:
        logger.warning("SSE 推送 agent_suggestion 失败: %s", e)

    return True


def _build_project_context(db: Session, project: Project, days_idle: int = 0) -> dict:
    """构造精简项目上下文供 AI 生成。"""
    pending_tasks = (
        db.query(Task)
        .filter(Task.project_id == project.id, Task.status != "done")
        .all()
    )
    recent_messages = (
        db.query(Message)
        .filter(Message.project_id == project.id, Message.is_deleted == False)  # noqa: E712
        .order_by(Message.created_at.desc())
        .limit(10)
        .all()
    )
    recent_handoffs = (
        db.query(Handoff)
        .filter(Handoff.project_id == project.id)
        .order_by(Handoff.created_at.desc())
        .limit(3)
        .all()
    )
    return {
        "name": project.name,
        "status": project.status,
        "days_idle": days_idle,
        "pending_tasks": [{"title": t.title, "status": t.status} for t in pending_tasks],
        "recent_messages": [
            {"author": m.author, "content": m.content} for m in recent_messages
        ],
        "recent_handoffs": [{"summary": h.summary} for h in recent_handoffs],
    }


def scan_stale_projects(db: Session = None) -> int:
    """扫描停滞项目（updated_at > 7 天前 + status=active）。

    Returns: 新生成的建议数。
    """
    from database import SessionLocal
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        threshold = _utcnow() - timedelta(days=7)
        projects = (
            db.query(Project)
            .filter(Project.status == "active", Project.updated_at < threshold)
            .all()
        )
        count = 0
        for project in projects:
            days_idle = (_utcnow() - project.updated_at).days
            context = _build_project_context(db, project, days_idle)
            # AI 增强或规则引擎降级
            body = generate_proactive_suggestion("stale_warning", context)
            if not body:
                body = (
                    f"项目「{project.name}」已 {days_idle} 天无活动更新，建议跟进确认进度。"
                )
            created = _create_suggestion(
                db, project.id, "stale_warning",
                title=f"项目停滞 {days_idle} 天",
                body=body,
                severity="warning",
            )
            if created:
                count += 1
        logger.info("scan_stale_projects: 扫描 %d 个项目，新建 %d 条建议", len(projects), count)
        return count
    finally:
        if should_close:
            db.close()


def scan_overdue_tasks(db: Session = None) -> int:
    """扫描超期任务（status≠done 且 created_at > 3 天前）。

    Returns: 新生成的建议数。
    """
    from database import SessionLocal
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        threshold = _utcnow() - timedelta(days=3)
        # 找有超期任务的项目
        overdue_tasks = (
            db.query(Task)
            .filter(Task.status != "done", Task.created_at < threshold)
            .all()
        )
        # 按项目分组
        project_ids = set(t.project_id for t in overdue_tasks)
        count = 0
        for pid in project_ids:
            project = db.query(Project).filter(Project.id == pid).first()
            if not project:
                continue
            project_overdue = [t for t in overdue_tasks if t.project_id == pid]
            max_age = max(
                (_utcnow() - t.created_at).days for t in project_overdue
            )
            severity = "critical" if max_age > 7 else "warning"
            context = _build_project_context(db, project, 0)
            context["pending_tasks"] = [
                {"title": t.title, "status": t.status} for t in project_overdue
            ]
            body = generate_proactive_suggestion("overdue_task", context)
            if not body:
                body = (
                    f"项目「{project.name}」有 {len(project_overdue)} 个任务超期"
                    f"（最长 {max_age} 天），建议按紧急程度处理。"
                )
            created = _create_suggestion(
                db, pid, "overdue_task",
                title=f"{len(project_overdue)} 个任务超期",
                body=body,
                severity=severity,
            )
            if created:
                count += 1
        logger.info("scan_overdue_tasks: %d 个项目有超期任务，新建 %d 条建议", len(project_ids), count)
        return count
    finally:
        if should_close:
            db.close()


def daily_summary_job(db: Session = None) -> int:
    """每日 18:00 为当日有活动的项目生成日报。

    Returns: 新生成的建议数。
    """
    from database import SessionLocal
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        # DRL-R1-V22: 使用本地时区计算"今日 0:00"，再转 UTC 用于与 DB naive datetime 比较
        # 原实现用 _utcnow() 计算"今日"，在非 UTC 时区会导致日报覆盖范围错位
        now_local = datetime.now(_TZ)
        today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_local.astimezone(timezone.utc).replace(tzinfo=None)
        # 找今日有更新的活跃项目
        projects = (
            db.query(Project)
            .filter(Project.status == "active", Project.updated_at >= today_start_utc)
            .all()
        )
        count = 0
        for project in projects:
            context = _build_project_context(db, project, 0)
            body = generate_proactive_suggestion("daily_summary", context)
            if not body:
                body = f"项目「{project.name}」今日活动日报：任务推进正常。"
            created = _create_suggestion(
                db, project.id, "daily_summary",
                title="今日活动日报",
                body=body,
                severity="info",
            )
            if created:
                count += 1
        logger.info("daily_summary_job: 扫描 %d 个项目，新建 %d 条日报", len(projects), count)
        return count
    finally:
        if should_close:
            db.close()


def run_all_scans() -> dict:
    """手动触发所有扫描（测试用）。返回各扫描结果。"""
    from database import SessionLocal
    db = SessionLocal()
    try:
        return {
            "stale": scan_stale_projects(db),
            "overdue": scan_overdue_tasks(db),
            "daily": daily_summary_job(db),
        }
    finally:
        db.close()
