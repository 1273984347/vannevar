# main.py — Bridge MVP 服务入口
import ipaddress
import logging
import os
import re
import sys
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

# P0-1 修复：移除"未设密码自动降级到 dev 模式"逻辑——这是安全反模式。
# 用户体验由 BRIDGE_DEBUG=1 显式启用（见 docs/deploy-guide.md）。
# 生产环境必须显式设置 BRIDGE_ADMIN_PASS + BRIDGE_JWT_SECRET，否则 auth.py 会 RuntimeError。
# 开发环境必须显式设置 BRIDGE_DEBUG=1。

# DRL-R1-V14: 配置根日志器，确保所有模块的日志统一格式输出到 stderr。
# 之前仅 getLogger 而未 basicConfig，导致默认级别 WARNING 且无时间戳，调试困难。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

import auth  # noqa: F401 — 注册基础认证模块
from auth import CSRF_COOKIE_NAME, CSRF_TOKEN
from database import init_db
from routers import auth_router, agent_router, chat_router, context_card_router, events, orgs_router, pages, projects, sync_router, tasks
from backup import auto_backup_on_startup

logger = logging.getLogger("bridge.main")


# 阶段2.2: APScheduler 后台任务调度器
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # W087b: 启动时执行完整性检查 + 自动备份（损坏时自动从最近备份恢复）
    auto_backup_on_startup()
    # P0 修复（dogfood 7.0-1）：init_db 失败时优雅降级——注册维护页面路由，
    # 避免进程直接退出导致demo 演示 看到 ERR_CONNECTION_REFUSED。
    _db_init_failed = False
    try:
        init_db()
    except Exception as e:
        logger.exception("[Lifespan] init_db 失败，进入维护模式: %s", e)
        _db_init_failed = True

        @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        async def _maintenance_page(path: str):
            return HTMLResponse(
                status_code=503,
                content=(
                    "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>"
                    "<title>Bridge 维护中</title>"
                    "<style>body{font-family:system-ui,sans-serif;padding:48px;text-align:center;"
                    "color:#064e3b;background:#ecfdf5}"
                    "h1{font-size:32px;margin-bottom:12px}"
                    "p{font-size:16px;color:#059669;line-height:1.6}"
                    "</style></head><body>"
                    "<h1>🔧 Bridge 维护中</h1>"
                    "<p>数据库初始化失败，服务进入维护模式。</p>"
                    "<p>请检查 <code>bridge_mvp.db</code> 文件，或执行 <code>python scripts/seed_demo_data.py --reset</code> 重置。</p>"
                    "<p style='margin-top:24px;font-size:14px;color:#6b7280'>"
                    "demo 演示 应急方案：备份 <code>backups/</code> 目录下有最近备份，"
                    "可手动 <code>copy backups\\bridge_mvp_*.db bridge_mvp.db</code> 恢复。</p>"
                    "</body></html>"
                ),
            )

    # 阶段2.2: 启动 APScheduler 定时扫描 Agent 建议
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(
            agent_router.scan_stale_projects, "interval", hours=6,
            coalesce=True, max_instances=1, id="scan_stale",
        )
        _scheduler.add_job(
            agent_router.scan_overdue_tasks, "interval", hours=3,
            coalesce=True, max_instances=1, id="scan_overdue",
        )
        _scheduler.add_job(
            agent_router.daily_summary_job, "cron", hour=18,
            coalesce=True, max_instances=1, id="daily_summary",
        )
        _scheduler.start()
        logger.info("APScheduler 已启动：Agent 定时扫描就绪")
    except ImportError:
        logger.warning("apscheduler 未安装，Agent 定时扫描禁用（手动调用仍可用）")
    except Exception as e:
        logger.warning("APScheduler 启动失败（不影响主服务）: %s", e)

    yield

    # 关闭 scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass


# B-SEC-01: 生产环境关闭 OpenAPI docs
_debug = os.getenv("BRIDGE_DEBUG") == "1"
app = FastAPI(
    title="Vannevar · Bridge MVP",
    description="公益团队任务上下文共享与交接工具",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _debug else None,
    redoc_url="/redoc" if _debug else None,
    openapi_url="/openapi.json" if _debug else None,
)

# Phase 6.2 性能优化：GZipMiddleware 压缩响应（≥1KB 的文本响应 gzip 压缩，节省 60-80% 带宽）
app.add_middleware(GZipMiddleware, minimum_size=1000)


# Phase 6.2 性能优化：CachedStaticFiles 为 /static/* 添加长期缓存头
# - 生产模式（BRIDGE_DEBUG=0）：max-age=31536000, immutable（Lighthouse Best Practices 加分）
# - 开发模式（BRIDGE_DEBUG=1）：no-cache, must-revalidate（演示时 CSS/JS 改动立即生效）
class CachedStaticFiles(StaticFiles):
    """静态文件响应附加 Cache-Control 头。"""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if _debug:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        else:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", CachedStaticFiles(directory="static"), name="static")

# P1-DOG-01 修复：页面端点的 HTTPException 渲染 error.html 模板，而非空 body
_templates = Jinja2Templates(directory="templates")


# B-SEC-08: 全局异常处理器 — 捕获未处理异常返回 500，避免堆栈泄露
# 注意：HTTPException 已被 FastAPI 默认处理器拦截，不会进入此处理器
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


# P1-DOG-01 修复：自定义 HTTPException 处理器
# 注意：Starlette 中间件 call_next 与 exception_handler 交互时可能丢失 body，
#   因此页面端点（pages.py）的 404 改为直接返回 TemplateResponse，不依赖此 handler。
#   此 handler 仅作为 API 端点（/api/*）和非 HTML 请求的兜底，保持 FastAPI 默认 JSON 行为。
@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.include_router(auth_router.router)
app.include_router(projects.router)
app.include_router(projects.stats_router)
app.include_router(tasks.router)
app.include_router(events.router)
app.include_router(pages.router)
app.include_router(sync_router.router)
app.include_router(context_card_router.router)
app.include_router(agent_router.router)
app.include_router(orgs_router.router)
app.include_router(chat_router.router)  # Phase 6.J: AI 助手对话化


# M8: 速率限制 — 内存字典 + 时间窗口
# P1-15/P1-16: 公开页面端点、管理员/API 写端点均纳入限流；SSE 长连接单独处理
# DF-1/B-SEC-09: 限流存储键改为 (org_id, client_ip, prefix) 元组，避免跨路径配额串扰
# 阶段2.6: 限流维度升级为 (org_id, client_ip, prefix)，组织级配额
# B-SEC-07: 本限流为进程内实现，多 worker 部署时各进程独立计数，
#           实际阈值会被放大 N 倍（N=worker 数）。MVP 建议单 worker 部署
#           （uvicorn --workers 1）；生产环境如需精确限流应改用 Redis 共享存储。
RATE_LIMIT_RULES = [
    # (path prefix, max requests per window, window seconds)
    ("/p/", 30, 60),      # 公开志愿者页面
    ("/qr/", 30, 60),     # 二维码页面
    ("/api/auth/", 30, 60),  # 认证 API（注册/登录限流防暴力破解）
    ("/api/projects/", 120, 60),  # API（含 SSE 心跳，阈值较高）
    ("/api/tasks/", 60, 60),      # 任务 API
    ("/api/sync/", 30, 60),       # 同步 API（防滥用）
    ("/api/context-cards", 30, 60),  # Context Card 只读 API
    ("/admin/", 60, 60),          # 管理后台页面
    ("/api/orgs/", 60, 60),       # 组织 API（阶段2.6 新增）
    ("/api/conversations/", 60, 60),  # Phase 6.J: AI 助手对话 API（ask SSE 走白名单不计入）
]
# P1-16: 限制内存中 key 数量上限，避免长期运行无界增长
RATE_LIMIT_MAX_KEYS = 10000
_rate_limit_store: dict = defaultdict(list)

# B-SEC-06: 请求体大小限制（2MB），防止超大 JSON 导致 OOM
MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024

# B-SEC-03: SSE 端点白名单 — 仅匹配已知 SSE 路径，避免 endswith("/events") 被绕过
# Phase 6.J: 新增 /api/conversations/{id}/ask SSE 流式回答端点
SSE_PATH_PATTERN = re.compile(r"^/api/projects/\d+/events/?$|^/api/conversations/\d+/ask/?$")

# DRL-R1-V4: SSE 并发连接限制（按 IP），防止单 IP 耗尽连接资源
MAX_SSE_CONNECTIONS_PER_IP = 10
_sse_connections_per_ip: dict = defaultdict(int)

# DRL-R1-V5: 可信代理头仅在非 dev 模式下读取（dev 模式直连，避免本地代理干扰）
# R2-V3: 仅当直连 IP 在 BRIDGE_TRUSTED_PROXIES 列表中时才信任 X-Forwarded-For，
#        避免客户端伪造 XFF 头绕过限流或审计。
_TRUSTED_PROXIES = []
_proxy_env = os.getenv("BRIDGE_TRUSTED_PROXIES", "")
if _proxy_env:
    for _cidr in _proxy_env.split(","):
        _cidr = _cidr.strip()
        if _cidr:
            try:
                _TRUSTED_PROXIES.append(ipaddress.ip_network(_cidr, strict=False))
            except ValueError:
                logger.warning("BRIDGE_TRUSTED_PROXIES 中无效的 CIDR/IP 已忽略: %s", _cidr)


def _get_client_ip(request: Request) -> str:
    """R2-V3: 获取真实客户端 IP（防 X-Forwarded-For 伪造）。

    规则：
    1. 取 request.client.host 作为直连 IP
    2. 若 BRIDGE_TRUSTED_PROXIES 为空 → 直接返回直连 IP（生产环境不信任 XFF）
    3. 若直连 IP 在可信代理列表中 → 解析 XFF 取最左端（原始客户端）
    4. 否则 → 返回直连 IP（XFF 不可信）
    """
    direct = request.client.host if request.client else "unknown"
    if not _TRUSTED_PROXIES:
        return direct
    # 检查直连 IP 是否在可信代理列表中
    try:
        direct_ip = ipaddress.ip_address(direct)
    except ValueError:
        return direct
    if not any(direct_ip in net for net in _TRUSTED_PROXIES):
        return direct
    # 直连方是可信代理，才信任 XFF
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        # 取最左端（原始客户端）
        leftmost = xff.split(",")[0].strip()
        # R3-V3 修复：校验 XFF 最左端 IP 格式，无效则回退到 direct
        try:
            ipaddress.ip_address(leftmost)
            return leftmost
        except ValueError:
            pass  # 无效 IP，返回 direct
    return direct


def _close_cached_db(cached_db) -> None:
    """DRL-R1-V22: 安全关闭中间件缓存的 db Session，异常静默（不阻断请求）。"""
    if cached_db is not None:
        try:
            cached_db.close()
        except Exception:
            pass


def _apply_security_headers(response: JSONResponse) -> JSONResponse:
    """R5-BE-6: 为早期返回的 JSONResponse 补充安全响应头。

    中间件末尾统一设置安全头的逻辑不会作用于早期 return 的 413/429 响应，
    此辅助函数确保这些响应也携带基础安全头（nosniff / DENY / Referrer-Policy）。
    """
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if not _debug:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


def _resolve_tenant_context(request: Request) -> tuple:
    """阶段2.6: 从 JWT 解析 user_id + default_org_id，注入 request.state。

    返回 (user_id, org_id)，均可能为 None（未认证请求）。
    中间件层只做"尽力解析"，具体鉴权由 require_user / require_auth 等依赖完成。

    DRL-R1-V9: 异常不再静默吞咽，改为 logger.warning 记录，便于排查。
        中间件层失败仍降级为 org_id=None（不 fail-fast，避免阻断请求）。
    DRL-R1-V22: 解析出的 db Session 缓存到 request.state.db，get_db 依赖优先复用，
        避免每认证请求创建两个 DB Session。
    DRL-R1-V2: 同时提取 csrf_jti 存入 request.state，供中间件写入 per-session CSRF Cookie。
    """
    user_id = None
    org_id = None
    csrf_jti = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            try:
                from auth import decode_access_token
                payload = decode_access_token(token)
                if payload is not None:
                    sub = payload.get("sub")
                    if sub is not None:
                        try:
                            user_id = int(sub)
                        except ValueError:
                            pass
                    # DRL-R1-V2: 提取 per-session CSRF token，供中间件写入 Cookie
                    csrf_jti = payload.get("csrf_jti")
            except Exception as e:
                # DRL-R1-V9: JWT 解码失败记录警告（不阻断请求，降级为未认证）
                logger.warning("tenant_context JWT 解析失败: %s", e, exc_info=True)
    # DRL-R1-V22: 缓存 db Session 到 request.state，供 get_db 依赖复用
    cached_db = None
    if user_id is not None and user_id > 0:
        # 查询用户的 default_org_id（避免循环导入，延迟引入）
        try:
            from database import SessionLocal
            from models import User
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if user is not None:
                    org_id = getattr(user, "default_org_id", None)
                # DRL-R1-V22: 缓存 Session 供后续 get_db 复用，避免二次创建
                cached_db = db
            except Exception as e:
                # DRL-R1-V9: DB 查询失败记录警告，关闭 Session，降级为 org_id=None
                logger.warning("tenant_context DB 查询失败: %s", e, exc_info=True)
                db.close()
        except Exception as e:
            # DRL-R1-V9: 导入失败记录警告
            logger.warning("tenant_context 模块导入失败: %s", e, exc_info=True)
    request.state.user_id = user_id
    request.state.org_id = org_id
    # DRL-R1-V2: 缓存 csrf_jti 供中间件写入 per-session CSRF Cookie
    request.state.csrf_jti = csrf_jti
    # DRL-R1-V22: 若缓存了 db Session，存入 request.state.db 供 get_db 复用
    if cached_db is not None:
        request.state._cached_db = cached_db
    return (user_id, org_id)


@app.middleware("http")
async def security_and_rate_limit_middleware(request: Request, call_next):
    """阶段2.6: 统一中间件 — tenant_context + rate_limit + security_headers + audit_log。

    执行顺序：
    1. 请求体大小校验（DRL-R1-V3: 含 chunked 编码防护）
    2. tenant_context 解析（注入 request.state.user_id / org_id）
    3. 限流（维度：org_id + client_ip + prefix）
    4. 路由处理
    5. 安全响应头
    6. 审计日志（记录 actor / org / method / path / status）

    DRL-R1-V3: 请求体大小限制改为读取 body 后校验字节数，防止 chunked 编码绕过 Content-Length。
    DRL-R1-V4: SSE 端点增加按 IP 并发连接限制。
    DRL-R1-V5: client_ip 改用 _get_client_ip，支持 X-Forwarded-For。
    DRL-R1-V13: 非 debug 模式添加 HSTS 响应头。
    DRL-R1-V21: 限流存储驱逐逻辑改为只清理窗口内全部过期的 key。
    DRL-R1-V22: 缓存的 db Session 在请求结束后由中间件关闭。
    DRL-R1-V27: CSP 保留 unsafe-inline（改为 nonce 需前端配合，超出本次范围）。
    R2-V3: _get_client_ip 仅信任 BRIDGE_TRUSTED_PROXIES 中的代理发来的 XFF。
    R2-V10: body 缓存跳过 multipart/form-data（文件上传流式处理），避免破坏流式读取。
    R2-V11: 限流驱逐扫描限制为前 200 个 key，避免 O(N) 全扫描导致延迟尖峰。
    """
    # B-SEC-06: 请求体大小限制 — 先检查 Content-Length 头（快速路径）
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_BODY_BYTES:
        return _apply_security_headers(JSONResponse(status_code=413, content={"detail": "请求体过大"}))

    # DRL-R1-V3: 对写请求读取 body 流并累计字节数，防止 chunked 编码绕过 Content-Length
    # 注意：Starlette 的 request.body() 会自动缓存 _body，下游可重复读取
    # R5-BE-5 修复：multipart/form-data 跳过 body 缓存，避免破坏文件上传的流式读取
    content_type = request.headers.get("content-type", "").lower()
    # R6-BE-9 修复：multipart 请求跳过 body 缓存后，chunked 传输编码可绕过 2MB 限制。
    #   要求 multipart 请求必须带 Content-Length 头，否则拒绝（411 Length Required）。
    #   这能阻止无 Content-Length 的 chunked multipart 请求绕过大小限制。
    # R7-BE-4 修复：原 `not content_length` 条件无法拦截非数字 Content-Length（如 "abc"），
    #   改为 `not content_length or not content_length.isdigit()` 拦截非数字值；
    #   同时为 multipart 路径补上基于 int(content_length) 的 2MB 上限校验，
    #   防止恶意客户端用超大 Content-Length 的 multipart 请求绕过 body 大小限制。
    if (
        request.method in ("POST", "PATCH", "PUT", "DELETE")
        and content_type.startswith("multipart/")
        and (not content_length or not content_length.isdigit())
    ):
        return _apply_security_headers(JSONResponse(
            status_code=411,
            content={"detail": "multipart 请求需要有效的 Content-Length"},
        ))
    # R8-BE-3: 删除冗余的 multipart 413 检查（第 311 行的快速路径已覆盖所有 Content-Type）
    if request.method in ("POST", "PATCH", "PUT", "DELETE") and not content_type.startswith("multipart/"):
        try:
            body = await request.body()
            if len(body) > MAX_REQUEST_BODY_BYTES:
                return _apply_security_headers(JSONResponse(status_code=413, content={"detail": "请求体过大"}))
        except Exception:
            # 读取失败不阻断（下游可能需要流式处理），降级为仅 Content-Length 校验
            pass

    # 阶段2.6: tenant_context 解析（注入 request.state）
    user_id, org_id = _resolve_tenant_context(request)

    # 限流检查
    path = request.url.path
    # DRL-R1-V5: 统一使用 _get_client_ip 获取客户端 IP
    client_ip = _get_client_ip(request)
    # DRL-R1-V22: 确保 cached_db 在所有路径（含限流 429、SSE）下都被关闭
    cached_db_to_close = getattr(getattr(request, "state", None), "_cached_db", None)
    # B-SEC-03: SSE 端点通过长连接保活，不计入普通限流；使用显式正则白名单匹配
    if SSE_PATH_PATTERN.match(path):
        # DRL-R1-V4: SSE 按 IP 并发连接限制，防止单 IP 耗尽连接资源
        if _sse_connections_per_ip[client_ip] >= MAX_SSE_CONNECTIONS_PER_IP:
            logger.info(
                "AUDIT actor=%s org=%s %s %s -> 429 (sse_concurrent_limit ip=%s)",
                user_id, org_id, request.method, path, client_ip,
            )
            _close_cached_db(cached_db_to_close)
            return _apply_security_headers(JSONResponse(
                status_code=429,
                content={"detail": "SSE 并发连接数超限，请稍后再试"},
            ))
        _sse_connections_per_ip[client_ip] += 1
        try:
            response = await call_next(request)
        finally:
            _sse_connections_per_ip[client_ip] -= 1
            if _sse_connections_per_ip[client_ip] <= 0:
                _sse_connections_per_ip.pop(client_ip, None)
            _close_cached_db(cached_db_to_close)
    else:
        matched_rule = None
        for prefix, max_req, window in RATE_LIMIT_RULES:
            if path.startswith(prefix):
                matched_rule = (prefix, max_req, window)
                break

        if matched_rule:
            prefix, max_req, window = matched_rule
            # R5-BE-12 修复：client_ip == "unknown" 时所有匿名请求共享同一限流桶，
            # 阈值减半以防恶意流量通过伪造/缺失 IP 信息绕过限流。
            if client_ip == "unknown":
                max_req = max(1, max_req // 2)
            # DRL-R1-V5: 使用 _get_client_ip 获取真实客户端 IP
            # 阶段2.6: 限流键升级为 (org_id, client_ip, prefix)，组织级配额
            rl_key = (org_id, client_ip, prefix)
            now = time.time()
            window_start = now - window
            recent = [t for t in _rate_limit_store[rl_key] if t > window_start]
            if not recent:
                _rate_limit_store.pop(rl_key, None)
            # DRL-R1-V21: 驱逐逻辑改为只清理窗口内全部过期的 key，避免重置合法用户计数器
            # R2-V11: 限制驱逐扫描范围为前 200 个 key，避免 O(N) 全扫描导致延迟尖峰。
            #   所有 RATE_LIMIT_RULES 的窗口均为 60s，window_start 对所有 key 通用。
            #   200 个 key 的扫描在微秒级完成，足够逐步清理过期 key。
            if len(_rate_limit_store) > RATE_LIMIT_MAX_KEYS:
                _evicted = 0
                for k, v in list(_rate_limit_store.items())[:200]:
                    if not v or all(t <= window_start for t in v):
                        _rate_limit_store.pop(k, None)
                        _evicted += 1
                # 若前 200 个 key 中无过期项但仍超限，强制驱逐最旧的 key（FIFO）
                if _evicted == 0 and len(_rate_limit_store) > RATE_LIMIT_MAX_KEYS:
                    _oldest_key = next(iter(_rate_limit_store), None)
                    if _oldest_key is not None:
                        _rate_limit_store.pop(_oldest_key, None)
            if len(recent) >= max_req:
                # 阶段2.6: 审计日志（限流命中也记录）
                logger.info(
                    "AUDIT actor=%s org=%s %s %s -> 429 (rate_limited)",
                    user_id, org_id, request.method, path,
                )
                _close_cached_db(cached_db_to_close)
                return _apply_security_headers(JSONResponse(
                    status_code=429,
                    content={"detail": "请求过于频繁，请稍后再试"},
                ))
            recent.append(now)
            _rate_limit_store[rl_key] = recent
        try:
            response = await call_next(request)
        finally:
            _close_cached_db(cached_db_to_close)

    # B-SEC-02: 统一设置安全响应头
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # DRL-R1-V13: 非 debug 模式添加 HSTS 响应头，强制 HTTPS
    if not _debug:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    # B-SEC-14: 增加 CSP，限制外部资源与内联脚本执行来源
    # DRL-R1-V27: CSP 仍允许 unsafe-inline（script-src/style-src）。
    #   改为 nonce-based CSP 需要前端配合改造（每个 inline script/style 注入 nonce），
    #   改动太大超出本次修复范围。生产环境应在前端迁移到 nonce-based 后收紧 CSP。
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "manifest-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    # B-SEC-02: 写入 CSRF Cookie（非 HttpOnly，前端 JS 需读取回传到 X-CSRF-Token 头）
    # P2-11 修复：secure 属性根据 BRIDGE_DEBUG 动态判断，生产环境（HTTPS）应设为 True
    # DRL-R1-V2: JWT 用户的 per-session CSRF token 由 require_auth 校验；
    #   此处全局 Cookie 仍写入全局 CSRF_TOKEN（供 Basic Auth 路径使用）。
    #   JWT 路径的 per-session CSRF 校验不依赖此 Cookie（直接比对 JWT.csrf_jti 与 X-CSRF-Token 头）。
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=CSRF_TOKEN,
        httponly=False,
        samesite="lax",
        secure=not _debug,  # 生产环境（非 dev）启用 Secure，要求 HTTPS
        path="/",
    )
    # 阶段2.6: 成功请求的审计日志（4xx/5xx 也记录，便于安全审计追溯）
    # 排除健康检查与静态资源噪声
    if path not in ("/health",) and not path.startswith("/static/"):
        status_code = response.status_code
        logger.info(
            "AUDIT actor=%s org=%s %s %s -> %d",
            user_id, org_id, request.method, path, status_code,
        )
    return response


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/ai-status")
def ai_status():
    """P0-AI: 返回 AI 增强配置状态（不泄露凭据），供前端显示"AI 已启用/未启用"标识。"""
    from ai import has_ai_config, is_demo_mode
    return {"ai_enabled": has_ai_config(), "demo_mode": is_demo_mode()}


@app.get("/landing", response_class=HTMLResponse)
def landing():
    """P2-4: PWA 公开引导页，无需 Basic Auth。

    manifest.json 的 start_url 指向此页面，避免 PWA 启动时落到需 Basic Auth 的 "/"。
    同时被 sw.js 预缓存，离线时也可正常打开。
    """
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bridge · 公益项目交接平台</title>
<link rel="manifest" href="/static/manifest.json">
<!-- Phase 7.1: 移除 Google Fonts 远程依赖，改用系统字体栈（与 base.html 同步） -->
<link rel="stylesheet" href="/static/style.css">
<style>
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:linear-gradient(to right, var(--v-bg), var(--v-border)); color:var(--v-text);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",Roboto,"Helvetica Neue",Arial,sans-serif; }
  .ld-card { background:var(--v-surface); border-radius:16px; padding:2.5rem 2rem; max-width:480px; width:90%;
             box-shadow:0 10px 30px rgba(15,23,42,0.08); text-align:center; }
  .ld-logo { width:56px; height:56px; margin:0 auto 1rem; background:var(--v-primary); border-radius:14px;
             display:flex; align-items:center; justify-content:center; color:#fff; }
  .ld-logo svg { width:32px; height:32px; }
  .ld-title { font-size:1.5rem; font-weight:700; margin:0 0 0.5rem; }
  .ld-desc { color:var(--v-text-secondary); font-size:0.95rem; line-height:1.6; margin:0 0 1.5rem; }
  .ld-actions { display:flex; gap:0.75rem; justify-content:center; flex-wrap:wrap; }
  .ld-btn { display:inline-block; padding:0.6rem 1.2rem; border-radius:12px; text-decoration:none;
            font-size:0.9rem; font-weight:500; transition:transform 0.1s; }
  .ld-btn:hover { transform:translateY(-1px); }
  .ld-btn--primary { background:var(--v-gradient); color:#fff; }
  .ld-btn--secondary { background:var(--v-success-bg); color:var(--v-text-secondary); }
  .ld-illustration { margin:0 auto 1.25rem; max-width:320px; }
  .ld-illustration svg { width:100%; height:auto; display:block; }
  @media (prefers-color-scheme: dark) {
    body { background:var(--v-bg); }
    .ld-card { background:var(--v-surface); box-shadow:0 10px 30px rgba(0,0,0,0.25); }
    .ld-btn--secondary { background:rgba(16,185,129,0.15); color:var(--v-text-secondary); }
  }
  @media (prefers-reduced-motion: reduce) {
    .ld-illustration .ld-bridge-float { animation:none !important; }
  }
</style>
</head>
<body>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <main id="main-content" class="ld-card">
    <div class="ld-logo" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 6 L12 18 L20 6"/>
      </svg>
    </div>
    <h1 class="ld-title" translate="no">Vannevar Bridge</h1>
    <p class="ld-desc">公益项目交接平台，让志愿团队上下文无缝衔接。<br>扫描项目二维码即可进入志愿者视图。</p>
    <div class="ld-illustration" aria-hidden="true">
      <svg viewBox="0 0 320 120" fill="none" stroke-linecap="round" stroke-linejoin="round">
        <defs>
          <linearGradient id="ld-bridge-grad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="var(--v-primary)"/>
            <stop offset="100%" stop-color="var(--v-accent)"/>
          </linearGradient>
        </defs>
        <!-- 桥梁主弧 -->
        <path d="M20 90 Q160 20 300 90" stroke="url(#ld-bridge-grad)" stroke-width="3" fill="none"/>
        <!-- 桥塔 -->
        <line x1="60" y1="90" x2="60" y2="55" stroke="var(--v-primary)" stroke-width="2.5"/>
        <line x1="160" y1="90" x2="160" y2="35" stroke="var(--v-primary)" stroke-width="2.5"/>
        <line x1="260" y1="90" x2="260" y2="55" stroke="var(--v-primary)" stroke-width="2.5"/>
        <!-- 缆绳 -->
        <line x1="60" y1="55" x2="80" y2="68" stroke="var(--v-accent)" stroke-width="1.2" opacity="0.6"/>
        <line x1="60" y1="55" x2="100" y2="60" stroke="var(--v-accent)" stroke-width="1.2" opacity="0.6"/>
        <line x1="160" y1="35" x2="120" y2="48" stroke="var(--v-accent)" stroke-width="1.2" opacity="0.6"/>
        <line x1="160" y1="35" x2="200" y2="48" stroke="var(--v-accent)" stroke-width="1.2" opacity="0.6"/>
        <line x1="260" y1="55" x2="220" y2="60" stroke="var(--v-accent)" stroke-width="1.2" opacity="0.6"/>
        <line x1="260" y1="55" x2="240" y2="68" stroke="var(--v-accent)" stroke-width="1.2" opacity="0.6"/>
        <!-- 水面 -->
        <path d="M10 100 Q40 108 70 100 Q100 92 130 100 Q160 108 190 100 Q220 92 250 100 Q280 108 310 100" stroke="var(--v-accent)" stroke-width="1.5" opacity="0.4" fill="none"/>
        <!-- 节点 -->
        <circle cx="60" cy="55" r="4" fill="var(--v-primary)"/>
        <circle cx="160" cy="35" r="5" fill="var(--v-accent)"/>
        <circle cx="260" cy="55" r="4" fill="var(--v-primary)"/>
      </svg>
    </div>
    <div class="ld-actions">
      <a class="ld-btn ld-btn--primary" href="/" rel="noopener">进入管理后台</a>
      <a class="ld-btn ld-btn--secondary" href="/health" rel="noopener">健康检查</a>
    </div>
  </main>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn

    # 开源发布改造（2026-10-25）：此前 host/port 被写死为 127.0.0.1:8000，
    # 导致 .env.example 与文档里承诺的 BRIDGE_BIND_ADDR / BRIDGE_PORT 完全不生效。
    bind_addr = os.getenv("BRIDGE_BIND_ADDR", "127.0.0.1")
    try:
        port = int(os.getenv("BRIDGE_PORT", "8000"))
    except ValueError:
        print(f"[BRIDGE] BRIDGE_PORT 不是合法整数: {os.getenv('BRIDGE_PORT')!r}", file=sys.stderr)
        raise SystemExit(2)
    if not 1 <= port <= 65535:
        print(f"[BRIDGE] BRIDGE_PORT 超出合法范围 (1-65535): {port}", file=sys.stderr)
        raise SystemExit(2)

    uvicorn.run(
        "main:app",
        host=bind_addr,
        port=port,
        reload=os.getenv("BRIDGE_DEBUG") == "1",
    )
