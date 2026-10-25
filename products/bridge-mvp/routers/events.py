# routers/events.py — SSE 实时同步（第三批升级）
"""项目级 SSE 推送：任务/留言/交接变更时实时通知订阅客户端。

设计要点：
- 内存 pub/sub，每个 project_id 维护一个订阅者列表
- 订阅者为 (asyncio.Queue, event_loop) 元组，跨线程发布用 call_soon_threadsafe
- 同步端点（task PATCH / message create / handoff create）可直接调用 publish
- 简单可靠：无持久化、无集群广播，进程重启后订阅自动重连
- P0-3: SSE 端点要求鉴权（qr_token query 参数或 admin Basic Auth）
"""
import asyncio
import base64
import json
import logging
import secrets
import threading
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from auth import ADMIN_PASS, ADMIN_USER
from database import get_db
from models import Project
# DRL-R1-V10: 复用共享 token 限流模块，防止 SSE 端点 qr_token 暴力枚举
from routers._token_rate_limit import _check_token_fail_limit, _record_token_fail

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

# project_id -> [(asyncio.Queue, event_loop), ...]
_subscribers: Dict[int, List[Tuple[asyncio.Queue, asyncio.AbstractEventLoop]]] = defaultdict(list)
# P1-6: 跨线程读写 _subscribers 需要并发锁保护（subscribe/unsubscribe 在事件循环线程，
# publish 可能在同步端点线程调用）
_subscribers_lock = threading.Lock()
# P1-7: 每项目最大订阅者数，防止连接耗尽
MAX_SUBSCRIBERS_PER_PROJECT = 10
# DRL-R1-V23: 每用户（按 subscriber_id 即 client_ip）最大订阅数，防 DoS
MAX_SUBSCRIPTIONS_PER_USER = 2
_user_sub_counts: Dict[str, int] = defaultdict(int)
# queue -> subscriber_id 映射，用于 unsubscribe 时递减计数
_queue_subscriber: Dict[asyncio.Queue, str] = {}


def _safe_put(q: asyncio.Queue, item: dict) -> None:
    """事件循环线程内执行：非阻塞入队，队列满则丢弃。"""
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass


def subscribe(project_id: int, subscriber_id: str) -> asyncio.Queue:
    """注册订阅者，返回其事件队列。仅在事件循环线程内调用。"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    with _subscribers_lock:
        # P1-7: 限制每项目最大订阅者数，防止连接耗尽
        if len(_subscribers[project_id]) >= MAX_SUBSCRIBERS_PER_PROJECT:
            raise HTTPException(status_code=429, detail="订阅者数过多，请稍后再试")
        # DRL-R1-V23: 限制每用户最大订阅数，防止同一来源打开过多连接
        if _user_sub_counts[subscriber_id] >= MAX_SUBSCRIPTIONS_PER_USER:
            raise HTTPException(status_code=429, detail="该用户订阅数已达上限，请关闭旧连接后重试")
        _subscribers[project_id].append((q, loop))
        _queue_subscriber[q] = subscriber_id
        _user_sub_counts[subscriber_id] += 1
    return q


def unsubscribe(project_id: int, q: asyncio.Queue) -> None:
    """移除订阅者。"""
    with _subscribers_lock:
        subs = _subscribers.get(project_id, [])
        new_subs = [(sq, l) for (sq, l) in subs if sq is not q]
        # B-SEC-11: 订阅者列表变空时从字典中删除 key，避免缓慢内存增长
        if new_subs:
            _subscribers[project_id] = new_subs
        else:
            _subscribers.pop(project_id, None)
        # DRL-R1-V23: 递减用户订阅计数
        subscriber_id = _queue_subscriber.pop(q, None)
        if subscriber_id is not None:
            _user_sub_counts[subscriber_id] = max(0, _user_sub_counts[subscriber_id] - 1)
            if _user_sub_counts[subscriber_id] == 0:
                _user_sub_counts.pop(subscriber_id, None)


def publish(project_id: int, event_type: str, data: dict) -> None:
    """向指定项目的所有订阅者推送事件。线程安全，可从同步端点调用。"""
    # P1-6: 加锁拷贝订阅者列表快照，避免遍历时其他线程修改
    with _subscribers_lock:
        subs = list(_subscribers.get(project_id, []))
    for q, loop in subs:
        try:
            loop.call_soon_threadsafe(_safe_put, q, {"event": event_type, "data": data})
        except Exception:  # nosec B110
            # 订阅者 loop 可能已关闭，静默跳过
            pass


def _authorize_sse(request: Request, project: Project, qr_token: Optional[str]) -> None:
    """P0-3: SSE 订阅鉴权。

    优先校验 qr_token（志愿者侧）；未提供则要求管理员 Basic Auth。
    EventSource 无法自定义请求头，因此志愿者侧通过 query 参数传递 qr_token；
    管理员侧浏览器可在 URL 中嵌入 http://user:pass@host 或通过 fetch 预鉴权。
    B-SEC-05: 校验 Origin 头，拒绝跨站 EventSource 订阅（防 CSRF DoS 订阅槽）。
    R2-V9: 统一使用 _get_client_ip 获取客户端 IP（延迟导入避免循环依赖）。
    """
    # R2-V9: 延迟导入 main._get_client_ip，避免 main.py ↔ events.py 循环导入
    from main import _get_client_ip
    client_ip = _get_client_ip(request)
    # DRL-R1-V8: Origin 缺失时直接拒绝（原逻辑 if origin: 为假会跳过校验，可被空 Origin 绕过）
    origin = request.headers.get("origin")
    if not origin:
        logger.warning(
            "auth_failed client_ip=%s path=%s reason=missing_origin project_id=%s",
            client_ip,
            request.url.path,
            project.id,
        )
        raise HTTPException(status_code=403, detail="缺少 Origin 头")
    # B-SEC-05: 拒绝跨站 EventSource 订阅
    host = request.headers.get("host", "")
    try:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        if parsed.netloc != host:
            logger.warning(
                "auth_failed client_ip=%s path=%s reason=cross_origin_sse origin=%s",
                client_ip,
                request.url.path,
                origin,
            )
            raise HTTPException(status_code=403, detail="跨站订阅被拒绝")
    except HTTPException:
        raise
    except Exception:
        logger.warning(
            "auth_failed client_ip=%s path=%s reason=invalid_origin origin=%s",
            client_ip,
            request.url.path,
            origin,
        )
        raise HTTPException(status_code=403, detail="跨站订阅被拒绝")

    # DRL-R1-V10: qr_token 校验复用共享限流模块
    if qr_token:
        if not _check_token_fail_limit(client_ip):
            logger.warning(
                "auth_blocked client_ip=%s path=%s reason=token_fail_limit_exceeded project_id=%s",
                client_ip,
                request.url.path,
                project.id,
            )
            raise HTTPException(status_code=429, detail="扫码验证失败次数过多，请稍后再试")
        if not secrets.compare_digest(project.qr_token or "", qr_token):
            _record_token_fail(client_ip)
            logger.warning(
                "auth_failed client_ip=%s path=%s reason=invalid_qr_token project_id=%s",
                client_ip,
                request.url.path,
                project.id,
            )
            raise HTTPException(status_code=403, detail="无权操作此项目")
        return
    # 尝试 admin Basic Auth
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Basic "):
        logger.warning(
            "auth_failed client_ip=%s path=%s reason=missing_basic_auth project_id=%s",
            client_ip,
            request.url.path,
            project.id,
        )
        raise HTTPException(
            status_code=401,
            detail="需要认证",
            headers={"WWW-Authenticate": "Basic"},
        )
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        user, pwd = decoded.split(":", 1)
    except Exception:
        logger.warning(
            "auth_failed client_ip=%s path=%s reason=malformed_basic_auth project_id=%s",
            client_ip,
            request.url.path,
            project.id,
        )
        raise HTTPException(
            status_code=401,
            detail="认证失败",
            headers={"WWW-Authenticate": "Basic"},
        )
    ok_user = secrets.compare_digest(user.encode(), ADMIN_USER.encode())
    ok_pass = secrets.compare_digest(pwd.encode(), ADMIN_PASS.encode())
    if not (ok_user and ok_pass):
        logger.warning(
            "auth_failed client_ip=%s path=%s reason=invalid_basic_credentials project_id=%s",
            client_ip,
            request.url.path,
            project.id,
        )
        raise HTTPException(
            status_code=401,
            detail="认证失败",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.get("/api/projects/{project_id}/events")
async def project_events(
    project_id: int,
    request: Request,
    qr_token: str = Query(None, description="志愿者侧凭 qr_token 授权；未提供则要求管理员认证"),
    db: Session = Depends(get_db),
):
    """SSE 端点：推送项目内任务/留言/交接变更事件。

    P0-3: 必须凭 qr_token（志愿者）或 Basic Auth（管理员）订阅，避免无鉴权监听。
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    _authorize_sse(request, project, qr_token)

    # DRL-R1-V23: 按 client_ip 限制每用户订阅数
    # R2-V9: 统一使用 _get_client_ip 获取真实客户端 IP（与限流/审计一致）
    from main import _get_client_ip
    subscriber_id = _get_client_ip(request)
    q = subscribe(project_id, subscriber_id)

    async def event_stream():
        try:
            # hello 事件让客户端确认连接已建立
            hello_data = json.dumps({"project_id": project_id}, ensure_ascii=False)
            yield f"event: hello\ndata: {hello_data}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    event = msg["event"]
                    data_str = json.dumps(msg["data"], ensure_ascii=False, default=str)
                    yield f"event: {event}\ndata: {data_str}\n\n"
                except asyncio.TimeoutError:
                    # P2-2: 心跳改用 event 格式，部分代理更可靠地透传
                    # 客户端未监听 ping 事件也不会报错，仅作为保活信号
                    yield "event: ping\ndata: {}\n\n"
        finally:
            unsubscribe(project_id, q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
