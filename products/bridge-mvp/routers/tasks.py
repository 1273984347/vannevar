# routers/tasks.py — 任务状态更新
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPBasicCredentials
from sqlalchemy.orm import Session

from auth import (
    _permission_ge,
    _resolve_project_permission,
    require_admin,
    require_auth,
    security,
)
from database import get_db
from models import Task, _now
from routers._token_rate_limit import _check_token_fail_limit, _record_token_fail
from routers.events import publish
from schemas import TaskOut, TaskUpdate

# P1-3/P1-13: 志愿者写端点 CSRF 固定值，与管理员端 BridgeAdmin 区分
VOLUNTEER_CSRF_VALUE = "BridgeVolunteer"

logger = logging.getLogger(__name__)


def _verify_volunteer(qr_token: str, project, request: Request) -> None:
    """统一校验志愿者身份与 CSRF 头。"""
    # R2-V9: 统一使用 _get_client_ip 获取客户端 IP（延迟导入避免循环依赖）
    from main import _get_client_ip
    client_ip = _get_client_ip(request)

    # DRL-R1-V1: 复用共享限流模块，防止 qr_token 暴力枚举（与 projects.py 对齐）
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
        # DRL-R1-V1: 记录 token 校验失败，用于限流统计
        _record_token_fail(client_ip)
        logger.warning(
            "auth_failed client_ip=%s path=%s reason=invalid_qr_token project_id=%s",
            client_ip,
            request.url.path,
            getattr(project, "id", "?"),
        )
        raise HTTPException(status_code=403, detail="无权操作此任务")

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _actor(admin: Optional[str], qr_token: Optional[str]) -> str:
    """P2-9: 构造审计日志中的操作者字段。

    管理员记录用户名；志愿者仅记录为 "volunteer"，不携带任何 token 信息
    （B-SEC-12: 即使前 4 位也可能助长枚举攻击，故完全不返回 token）。
    """
    if admin:
        return f"admin={admin}"
    if qr_token:
        return "volunteer"
    return "anonymous"


def _actor_from_user_id(user_id: int) -> str:
    """R5-BE-2: 从 user_id 构造操作者标识。0=admin，>0=user:{id}。"""
    return "admin" if user_id == 0 else f"user:{user_id}"


@router.get("/{task_id}", response_model=TaskOut)
def get_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    qr_token: str = Query(None, description="志愿者侧凭 qr_token 授权；未提供则要求管理员认证"),
    credentials: HTTPBasicCredentials = Depends(security),
):
    # P0-SEC: 校验管理员或志愿者身份，防止未授权读取任务详情
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if qr_token:
        _verify_volunteer(qr_token, task.project, request)
    else:
        # R6-BE-8: require_admin → require_auth + _resolve_project_permission + _permission_ge("view")
        # JWT 用户可凭 view 及以上权限获取单个任务；保留志愿者 qr_token 路径不变
        user_id = require_auth(request, credentials, db)
        if user_id != 0:
            _perm = _resolve_project_permission(db, user_id, task.project)
            if not _permission_ge(_perm, "view"):
                raise HTTPException(status_code=403, detail="无项目访问权限")
    return task


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    payload: TaskUpdate,
    request: Request,
    qr_token: str = Query(None, description="志愿者侧凭 qr_token 授权；未提供则要求管理员认证"),
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # H-2: IDOR 防护 — 志愿者凭 qr_token，JWT 用户凭 require_auth + edit 权限
    # R5-BE-2: require_admin → require_auth + require_project_access("edit")
    _auth_user_id = 0
    if qr_token:
        _verify_volunteer(qr_token, task.project, request)
    else:
        _auth_user_id = require_auth(request, credentials, db)
        if _auth_user_id != 0:
            _perm = _resolve_project_permission(db, _auth_user_id, task.project)
            if not _permission_ge(_perm, "edit"):
                raise HTTPException(status_code=403, detail="无项目编辑权限")

    data = payload.model_dump(exclude_unset=True)
    # 乐观锁：客户端传入 version 时做冲突检测，不匹配返回 409 + 当前 version。
    # version 字段不参与 setattr 赋值（由服务端在更新成功后自增）。
    incoming_version = data.pop("version", None)

    # Phase 4.1: 记录旧 status，用于状态变更时推送 task_status_changed SSE 事件
    # 事件消费方：Bridge Web UI（project_detail.html 通过 EventSource 订阅）
    # 注意：Focus 扩展不直接消费此 SSE 事件，而是通过 task-watcher.js 每 5 分钟 HTTP 轮询
    # /api/auth/me/recent-task-updates 端点获取状态变更（MV3 SW 长连接 SSE 不稳定）
    _old_status = task.status

    if incoming_version is not None:
        # DRL-R1-V18: 原子乐观锁更新，避免 read-modify-write 竞态
        new_version = (task.version or 1) + 1
        update_fields = dict(data)
        update_fields["version"] = new_version
        result = db.query(Task).filter(
            Task.id == task_id,
            Task.version == incoming_version,
        ).update(update_fields, synchronize_session=False)
        if result == 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "版本冲突：任务已被其他客户端更新，请刷新后重试",
                    "current_version": task.version,
                    "your_version": incoming_version,
                },
            )
    else:
        # 不带版本号：保留原 setattr 行为
        for key, value in data.items():
            setattr(task, key, value)
        # 乐观锁：更新成功后版本号 +1
        task.version = (task.version or 1) + 1

    # H-5: 子实体变更更新父项目 updated_at
    task.project.updated_at = _now()
    db.commit()
    db.refresh(task)
    # 第三批升级：推送 SSE 事件
    publish(task.project_id, "task_updated", {"id": task.id, "status": task.status})
    # Phase 4.1: 任务状态变更时额外推送 task_status_changed 事件
    # 消费方：Bridge Web UI（project_detail.html EventSource 订阅）
    # Focus 扩展通过 task-watcher.js HTTP 轮询 recent-task-updates 端点获取变更，不订阅 SSE
    if _old_status != task.status:
        publish(
            task.project_id,
            "task_status_changed",
            {
                "id": task.id,
                "title": task.title,
                "old_status": _old_status,
                "new_status": task.status,
                "project_id": task.project_id,
            },
        )
    # R5-BE-2: JWT 用户路径用 _actor_from_user_id 记录操作者
    if not qr_token:
        logger.info(f"actor={_actor_from_user_id(_auth_user_id)} action=update_task task_id={task.id} project_id={task.project_id} status={task.status} version={task.version}")
    else:
        logger.info(f"actor={_actor(None, qr_token)} action=update_task task_id={task.id} project_id={task.project_id} status={task.status} version={task.version}")
    return task


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task_id: int,
    request: Request,
    qr_token: str = Query(None, description="志愿者侧凭 qr_token 授权；未提供则要求管理员认证"),
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # H-2: IDOR 防护 — 志愿者凭 qr_token，JWT 用户凭 require_auth + edit 权限
    # R5-BE-2: require_admin → require_auth + require_project_access("edit")
    _auth_user_id = 0
    if qr_token:
        _verify_volunteer(qr_token, task.project, request)
    else:
        _auth_user_id = require_auth(request, credentials, db)
        if _auth_user_id != 0:
            _perm = _resolve_project_permission(db, _auth_user_id, task.project)
            if not _permission_ge(_perm, "edit"):
                raise HTTPException(status_code=403, detail="无项目编辑权限")

    project_id = task.project_id
    # R5-BE-2: JWT 用户路径用 _actor_from_user_id 记录操作者
    _log_actor = _actor_from_user_id(_auth_user_id) if not qr_token else _actor(None, qr_token)
    # DRL-R1-V20: 硬删除前记录审计日志（软删除需 models.py 加 deleted_at 字段，由其他 subagent 处理）
    logger.warning(
        "DELETE task %s by user %s project_id=%s actor=%s",
        task_id, _log_actor, project_id, _log_actor,
    )
    db.delete(task)
    db.commit()
    # 第三批升级：推送 SSE 事件
    publish(project_id, "task_deleted", {"id": task_id})
    logger.info(f"actor={_log_actor} action=delete_task task_id={task_id} project_id={project_id}")
    return None
