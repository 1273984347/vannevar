# auth.py — Bridge MVP 认证模块（Basic Auth + JWT 统一账号体系）
import logging
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Organization,
    OrgMembership,
    Project,
    ProjectShare,
    Team,
    TeamMembership,
    User,
)

# R2-V2/R2-V5: 模块级 logger，用于安全审计日志
logger = logging.getLogger(__name__)

# auto_error=False 使得志愿者侧带 qr_token 的请求无需提供凭据；
# 管理员侧由 require_admin 手动校验并返回 401。
security = HTTPBasic(auto_error=False)

ADMIN_USER = os.getenv("BRIDGE_ADMIN_USER", "admin")

# B-SEC-01: dev 模式下生成随机一次性密码并打印到 stderr，避免弱密码硬编码
_DEFAULT_ADMIN_PASS = "bridge-dev-2024"  # nosec B105

if os.getenv("BRIDGE_DEBUG") == "1":
    # dev/测试环境：生成随机密码，避免源码中硬编码弱密码被误用
    ADMIN_PASS = os.getenv("BRIDGE_ADMIN_PASS") or secrets.token_urlsafe(16)
    if not os.getenv("BRIDGE_ADMIN_PASS"):
        print(f"[BRIDGE] Dev admin password: {ADMIN_PASS}", file=sys.stderr)
else:
    ADMIN_PASS = os.getenv("BRIDGE_ADMIN_PASS", _DEFAULT_ADMIN_PASS)
    # P1-2: 非 dev 模式下拒绝默认弱密码启动，避免生产环境误用
    if ADMIN_PASS == _DEFAULT_ADMIN_PASS:
        raise RuntimeError(
            "BRIDGE_ADMIN_PASS 仍为默认弱密码；生产环境请设置强密码，"
            "或在 dev/测试环境显式设置 BRIDGE_DEBUG=1 跳过此检查"
        )

# B-SEC-02: 启动时生成随机 CSRF token，配合双重提交 Cookie 模式
# Cookie 由中间件写入（非 HttpOnly，前端 JS 需读取回传到 X-CSRF-Token 头）
CSRF_TOKEN = secrets.token_urlsafe(32)
CSRF_COOKIE_NAME = "bridge_csrf"


def require_admin(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(security),
):
    """要求管理员 Basic Auth 认证，失败抛 401。

    既可作为 Depends() 用于纯管理员端点，也可在条件认证端点中手动调用。
    同时校验 X-Requested-With 头以提供简单有效的 CSRF 防护：
    浏览器原生表单无法自定义该头，只有通过 fetch/XHR 主动设置才能通过。
    """
    # M1: CSRF 防护 — 写端点必须携带 X-Requested-With 头
    # P2-10: 校验固定值，而非仅检查存在性，避免任意自定义值绕过
    # DF-2: 先认证后 CSRF — 未认证请求返回 401，避免泄露端点存在 CSRF 防护
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="需要管理员认证",
            headers={"WWW-Authenticate": "Basic"},
        )
    ok_user = secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), ADMIN_PASS.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="需要管理员认证",
            headers={"WWW-Authenticate": "Basic"},
        )
    # 认证通过后再校验 CSRF 头
    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
        # M1/P2-10: X-Requested-With 固定值作为额外防线（保留）
        if request.headers.get("x-requested-with") != "BridgeAdmin":
            raise HTTPException(status_code=403, detail="缺少必要请求头")
        # B-SEC-02: 双重提交 Cookie — 校验 X-CSRF-Token 头与 Cookie 值一致
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
        header_token = request.headers.get("x-csrf-token", "")
        if not cookie_token or not secrets.compare_digest(cookie_token, header_token):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
    return credentials.username


# ========== 阶段1.1: JWT 统一账号体系 ==========

# bcrypt 密码哈希（passlib 1.7.4 + bcrypt 4.0.1）
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 7
# DRL-R1-V23: JWT 增加 aud/iss 声明，防止 token 跨服务误用
JWT_AUDIENCE = "bridge"
JWT_ISSUER = "bridge"

# DRL-R1-V1: 进程内用户活跃状态短 TTL 缓存，避免每请求查 DB。
# R5-BE-11: 缓存值改为 (is_active, token_revoked_at, cached_at)，
#   _is_user_active_cached 与 _check_token_revoked 共享同一缓存，避免重复查询 User 表。
# 结构：{user_id: (is_active_bool, token_revoked_at, cached_at_ts)}；TTL 30 秒。
_USER_ACTIVE_CACHE_TTL = 30.0
_user_active_cache: dict = {}
# R2-V7: threading.Lock 保护 _user_active_cache 并发读写，防止多线程竞态导致数据不一致
_user_active_cache_lock = threading.Lock()


def _get_user_state_cached(user_id: int, db: Session) -> tuple:
    """R5-BE-11: 获取用户状态 (is_active, token_revoked_at)，带 30s 进程内缓存。

    _is_user_active_cached 和 _check_token_revoked 共享此缓存，避免同一请求内
    重复查询 User 表。user_id=0 为 dev 模式虚拟用户，直接返回 (True, None)。
    R2-V7: 使用双重检查锁（double-checked locking）保护并发读写。
    """
    if user_id == 0:
        return (True, None)
    now = time.monotonic()
    # 第一次无锁读（快速路径，缓存命中时无需加锁）
    cached = _user_active_cache.get(user_id)
    if cached is not None:
        is_active, token_revoked_at, cached_at = cached
        if now - cached_at < _USER_ACTIVE_CACHE_TTL:
            return (is_active, token_revoked_at)
    # 缓存未命中或过期，加锁后二次检查（防止多个线程同时查 DB + 写缓存）
    with _user_active_cache_lock:
        cached = _user_active_cache.get(user_id)
        if cached is not None:
            is_active, token_revoked_at, cached_at = cached
            if now - cached_at < _USER_ACTIVE_CACHE_TTL:
                return (is_active, token_revoked_at)
        # 查 DB 并刷新缓存
        user = db.query(User).filter(User.id == user_id).first()
        is_active = bool(user is not None and user.is_active)
        token_revoked_at = getattr(user, "token_revoked_at", None) if user else None
        _user_active_cache[user_id] = (is_active, token_revoked_at, time.monotonic())
        return (is_active, token_revoked_at)


def _is_user_active_cached(user_id: int, db: Session) -> bool:
    """DRL-R1-V1: 校验用户是否活跃（is_active=True），带 30s 进程内缓存。

    R5-BE-11: 委托给 _get_user_state_cached，复用共享缓存。
    """
    return _get_user_state_cached(user_id, db)[0]


def _check_token_revoked(user_id: int, jwt_iat: int, db: Session) -> bool:
    """DRL-R1-V7: 校验 JWT 是否已被撤销。

    比对 JWT 的 iat（签发时间）与 User.token_revoked_at：
    - token_revoked_at 为 None → 未撤销
    - iat < token_revoked_at → 已撤销（早于撤销时间签发）
    - iat >= token_revoked_at → 未撤销（撤销后重新签发）

    R5-BE-3 修复：比较改为严格小于（iat < revoked_at），iat == revoked_at 视为
        撤销后重新签发的合法 token（撤销瞬间签发的边界情况按未撤销处理）。
    R5-BE-4 修复：int() 转换失败时 fail-closed（返回 True 拒绝 token），
        避免异常 jwt_iat 绕过撤销校验。
    R5-BE-11: 复用 _user_active_cache 缓存（通过 _get_user_state_cached），
        避免与 _is_user_active_cached 重复查询 User 表。

    user_id=0 为 dev 模式虚拟用户，直接返回 False（未撤销）。
    """
    if user_id == 0:
        return False
    is_active, token_revoked_at = _get_user_state_cached(user_id, db)
    # 用户不存在（is_active=False 且 token_revoked_at=None）或已停用 → 视为已撤销
    if not is_active:
        return True
    if token_revoked_at is None:
        return False
    try:
        # R5-BE-3: 严格小于；R5-BE-4: int() 失败时 fail-closed 返回 True
        return int(jwt_iat) < int(token_revoked_at)
    except (TypeError, ValueError):
        # R5-BE-4: 转换失败视为已撤销（fail-closed），防止异常 iat 绕过校验
        return True


def invalidate_user_cache(user_id: int) -> None:
    """R7-BE-1/R7-BE-2: 主动失效用户状态缓存（logout / 撤销 token 时调用）。

    内部持锁后 pop，与 _get_user_state_cached 的锁纪律保持一致，
    消除 logout 中 commit 与 pop 之间的 TOCTOU 窗口，避免并发请求
    在缓存失效前读到陈旧的 (is_active, token_revoked_at)。
    """
    with _user_active_cache_lock:
        _user_active_cache.pop(user_id, None)


def _get_jwt_secret() -> str:
    """获取 JWT 签名密钥。

    - 优先使用 BRIDGE_JWT_SECRET 环境变量
    - dev 模式（BRIDGE_DEBUG=1）自动生成一次性密钥并打印到 stderr
    - 非 dev 模式且未设置 → 拒绝启动
    """
    secret = os.getenv("BRIDGE_JWT_SECRET")
    if secret:
        return secret
    if os.getenv("BRIDGE_DEBUG") == "1":
        secret = secrets.token_urlsafe(32)
        print(
            f"[BRIDGE] Dev JWT secret (valid for this session only, "
            f"will change on restart): {secret}",
            file=sys.stderr,
        )
        return secret
    raise RuntimeError(
        "BRIDGE_JWT_SECRET 未设置；生产环境请设置强密钥，"
        "或在 dev/测试环境显式设置 BRIDGE_DEBUG=1 跳过此检查"
    )


JWT_SECRET = _get_jwt_secret()


def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码。

    DRL-R1-V17: 预检密码字节长度 ≤ 72，bcrypt 超过 72 字节会静默截断导致认证异常。
    schemas.py 已限制 max_length=72 字符，但多字节 UTF-8 字符（如中文）可能超 72 字节，
    此处做字节级预检兜底。
    """
    # DRL-R1-V17: bcrypt 限制 72 字节，超长截断会导致认证不匹配
    if len(password.encode("utf-8")) > 72:
        raise ValueError("密码过长（超过 72 字节），请缩短密码")
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码与 bcrypt 哈希是否匹配。"""
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, email: str) -> str:
    """签发 JWT，载荷含 sub(user_id) / email / iat / exp / jti / csrf_jti / aud / iss。

    DRL-R1-V2: 增加 csrf_jti（per-session CSRF token），写入 Cookie 后由 require_auth 校验。
    DRL-R1-V7: 增加 jti（JWT 唯一 ID），用于审计日志与未来黑名单机制。
    DRL-R1-V23: 增加 aud/iss 声明，解码时校验。
    R2-V8: iat/exp 显式转为 int（Unix 时间戳），确保 _check_token_revoked 中
        int(jwt_iat) 不会因 datetime 对象序列化不一致而失败。
    """
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=JWT_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "email": email,
        # R2-V8: 显式转 int 时间戳，避免 jose 序列化 datetime 时类型不一致
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        # DRL-R1-V7: JWT 唯一标识，便于审计与撤销
        "jti": secrets.token_hex(8),
        # DRL-R1-V2: per-session CSRF token，前端写入 XSRF-TOKEN Cookie，
        # 写端点要求 X-CSRF-Token 头 == 此值
        "csrf_jti": secrets.token_urlsafe(24),
        # DRL-R1-V23: 受众与签发方
        "aud": JWT_AUDIENCE,
        "iss": JWT_ISSUER,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """解码并验证 JWT，失败返回 None。

    阶段2.6: 保留此函数用于静默解码（如 get_optional_user_id）。
    需要区分错误码的场景请用 verify_access_token。
    DRL-R1-V23: 增加 aud/iss 校验；旧 token（无 aud/iss）回退到宽松解码以保持兼容。
    R6-BE-1 修复：宽松回退路径同步 R5-BE-1 模式——解码后检查 payload 是否含 aud/iss，
        仅当完全缺失 aud/iss 时才视为旧 token 放行；含 aud/iss 但与预期不匹配则拒绝。
    """
    try:
        return jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
        )
    except JWTError:
        # DRL-R1-V23: 兼容旧 token（无 aud/iss 声明），宽松解码仅校验签名+exp
        # R6-BE-1: 宽松回退同步 R5-BE-1 模式，含 aud/iss 但不匹配的 token 拒绝
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except JWTError:
            return None
        # 仅当 payload 完全不含 aud/iss 时才视为旧 token 放行
        if "aud" in payload or "iss" in payload:
            logger.warning("jwt_token_aud_iss_mismatch rejected decode")
            return None
        logger.warning("jwt_legacy_token_decoded reason=no_aud_iss")
        return payload


def verify_access_token(token: str) -> dict:
    """解码并验证 JWT，失败抛 HTTPException（区分错误码）。

    阶段2.6: JWT 错误码区分，前端根据 error.code 做不同提示。
    - token_expired → "会话已过期，请重新登录"
    - token_invalid → "令牌无效，请重新登录"
    - token_error   → "认证失败"
    DRL-R1-V23: 增加 aud/iss 校验；旧 token（无 aud/iss）回退到宽松解码以保持兼容。
    """
    from jose import ExpiredSignatureError, JWTError as _JWTError
    try:
        return jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
        )
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"code": "token_expired", "message": "会话已过期，请重新登录"},
            headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
        )
    except _JWTError:
        # DRL-R1-V23: 兼容旧 token（无 aud/iss 声明），宽松解码仅校验签名+exp
        # R5-BE-1 修复：原实现 except _JWTError 捕获 JWTClaimsError，导致 aud/iss 不匹配
        #   的 token 也走宽松回退被接受。现改为：宽松解码后检查 payload 是否含 aud/iss，
        #   仅当完全缺失 aud/iss 时才视为旧 token 放行；含 aud/iss 但与预期不匹配则拒绝。
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except _JWTError:
            raise HTTPException(
                status_code=401,
                detail={"code": "token_invalid", "message": "令牌无效"},
                headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
            )
        # 仅当 payload 完全不含 aud/iss 时才视为旧 token 放行
        if "aud" in payload or "iss" in payload:
            logger.warning("jwt_token_aud_iss_mismatch rejected")
            raise HTTPException(
                status_code=401,
                detail={"code": "token_invalid", "message": "令牌受众校验失败"},
                headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
            )
        logger.warning("jwt_legacy_token_verified reason=no_aud_iss")
        return payload
    except Exception:
        raise HTTPException(
            status_code=401,
            detail={"code": "token_error", "message": "认证失败"},
            headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
        )


def require_user(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> int:
    """要求用户认证，返回 user_id（int）。

    认证优先级：
    1. JWT（Authorization: Bearer <token>）→ 返回真实 user_id
    2. dev 模式降级（BRIDGE_DEBUG=1）：Basic Auth → 返回虚拟 user_id=0
    3. 生产模式无 JWT → 401

    阶段1.1 仅 auth_router 的 /me 端点使用；
    阶段1.3 后业务端点将逐步切换到此依赖。

    DRL-R1-V1: JWT 解析后校验 User.is_active=True，停用用户拒绝访问。
    DRL-R1-V2: JWT 路径写端点校验 X-CSRF-Token 头 == payload.csrf_jti（per-session）。
    DRL-R1-V7: 校验 JWT iat > User.token_revoked_at，已撤销 token 拒绝访问。
    """
    # 1. 尝试 JWT
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            # 阶段2.6: verify_access_token 抛出带错误码的 HTTPException
            payload = verify_access_token(token)
            user_id_str = payload.get("sub")
            if user_id_str is not None:
                try:
                    uid = int(user_id_str)
                except ValueError:
                    uid = None
                if uid is not None:
                    # DRL-R1-V1: 校验用户活跃状态（带 30s 缓存）
                    if not _is_user_active_cached(uid, db):
                        raise HTTPException(
                            status_code=401,
                            detail={"code": "token_error", "message": "账号已停用"},
                            headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
                        )
                    # DRL-R1-V7: 校验 token 是否已撤销
                    # R5-BE-4 修复：jwt_iat 为 None 时 fail-closed 拒绝 token
                    jwt_iat = payload.get("iat")
                    if jwt_iat is None:
                        raise HTTPException(
                            status_code=401,
                            detail={"code": "token_invalid", "message": "令牌缺少签发时间"},
                            headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
                        )
                    if _check_token_revoked(uid, jwt_iat, db):
                        raise HTTPException(
                            status_code=401,
                            detail={"code": "token_revoked", "message": "令牌已撤销，请重新登录"},
                            headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
                        )
                    # R2-V2 修复：写端点必须强制有 csrf_jti，旧 token（无 csrf_jti）拒绝写操作
                    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
                        csrf_jti = payload.get("csrf_jti")
                        if not csrf_jti:
                            # 旧 token（无 csrf_jti）不允许写操作，强制重新登录
                            logger.warning(
                                "csrf_jti_missing user_id=%s method=%s path=%s",
                                uid, request.method, request.url.path,
                            )
                            raise HTTPException(
                                status_code=403,
                                detail={"code": "csrf_token_missing", "message": "令牌版本过旧，请重新登录"},
                            )
                        header_csrf = request.headers.get("x-csrf-token", "")
                        if not header_csrf or not secrets.compare_digest(str(csrf_jti), header_csrf):
                            raise HTTPException(status_code=403, detail="CSRF 校验失败")
                    return uid
            # payload 存在但 sub 缺失/无效 → token_invalid
            raise HTTPException(
                status_code=401,
                detail={"code": "token_invalid", "message": "令牌无效，请重新登录"},
                headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
            )

    # 2. dev 模式降级：Basic Auth → 虚拟用户
    if os.getenv("BRIDGE_DEBUG") == "1":
        if credentials is not None:
            ok_user = secrets.compare_digest(
                credentials.username.encode(), ADMIN_USER.encode()
            )
            ok_pass = secrets.compare_digest(
                credentials.password.encode(), ADMIN_PASS.encode()
            )
            if ok_user and ok_pass:
                # R3-V2 修复：dev 模式 Basic Auth 路径也需 CSRF 校验（与 require_auth 一致）
                if request.method in ("POST", "PATCH", "PUT", "DELETE"):
                    if request.headers.get("x-requested-with") != "BridgeAdmin":
                        raise HTTPException(status_code=403, detail="缺少必要请求头")
                    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
                    header_token = request.headers.get("x-csrf-token", "")
                    if not cookie_token or not secrets.compare_digest(cookie_token, header_token):
                        raise HTTPException(status_code=403, detail="CSRF 校验失败")
                return 0

    # 3. 未认证 → 401
    raise HTTPException(
        status_code=401,
        detail="需要认证",
        headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
    )


def get_optional_user_id(request: Request) -> Optional[int]:
    """从 Authorization 头提取 JWT user_id，无 JWT 或无效则返回 None。

    阶段1.2: 用于 require_admin 端点中可选注入 owner_id。
    - JWT 用户创建项目 → owner_id = user.id
    - Basic Auth（dev 模式）创建项目 → owner_id = None（兼容旧数据）

    与 require_user 不同：不抛 401，静默返回 None。
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            payload = decode_access_token(token)
            if payload is not None:
                user_id_str = payload.get("sub")
                if user_id_str is not None:
                    try:
                        return int(user_id_str)
                    except ValueError:
                        pass
    return None


def require_auth(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> int:
    """阶段1.3: 统一认证依赖，JWT 优先 + Basic Auth dev 降级。

    返回 user_id（int）：
    - JWT 用户 → 真实 user_id（>0），per-session CSRF 校验（DRL-R1-V2）
    - Basic Auth（dev 模式）→ 0（管理员，看全部数据），保留全局 CSRF 校验
    - 都无 → 401

    用于项目 CRUD 端点；纯管理端点（backup/stats）仍用 require_admin。

    DRL-R1-V1: JWT 解析后校验 User.is_active=True，停用用户拒绝访问。
    DRL-R1-V2: JWT 路径写端点校验 X-CSRF-Token 头 == payload.csrf_jti（per-session）。
    DRL-R1-V7: 校验 JWT iat > User.token_revoked_at，已撤销 token 拒绝访问。
    """
    # 1. 尝试 JWT
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            # 阶段2.6: verify_access_token 抛出带错误码的 HTTPException
            payload = verify_access_token(token)
            user_id_str = payload.get("sub")
            if user_id_str is not None:
                try:
                    uid = int(user_id_str)
                except ValueError:
                    uid = None
                if uid is not None:
                    # DRL-R1-V1: 校验用户活跃状态（带 30s 缓存）
                    if not _is_user_active_cached(uid, db):
                        raise HTTPException(
                            status_code=401,
                            detail={"code": "token_error", "message": "账号已停用"},
                            headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
                        )
                    # DRL-R1-V7: 校验 token 是否已撤销
                    # R5-BE-4 修复：jwt_iat 为 None 时 fail-closed 拒绝 token
                    jwt_iat = payload.get("iat")
                    if jwt_iat is None:
                        raise HTTPException(
                            status_code=401,
                            detail={"code": "token_invalid", "message": "令牌缺少签发时间"},
                            headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
                        )
                    if _check_token_revoked(uid, jwt_iat, db):
                        raise HTTPException(
                            status_code=401,
                            detail={"code": "token_revoked", "message": "令牌已撤销，请重新登录"},
                            headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
                        )
                    # R2-V2 修复：写端点必须强制有 csrf_jti，旧 token（无 csrf_jti）拒绝写操作
                    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
                        csrf_jti = payload.get("csrf_jti")
                        if not csrf_jti:
                            # 旧 token（无 csrf_jti）不允许写操作，强制重新登录
                            logger.warning(
                                "csrf_jti_missing user_id=%s method=%s path=%s",
                                uid, request.method, request.url.path,
                            )
                            raise HTTPException(
                                status_code=403,
                                detail={"code": "csrf_token_missing", "message": "令牌版本过旧，请重新登录"},
                            )
                        header_csrf = request.headers.get("x-csrf-token", "")
                        if not header_csrf or not secrets.compare_digest(str(csrf_jti), header_csrf):
                            raise HTTPException(status_code=403, detail="CSRF 校验失败")
                    return uid
            raise HTTPException(
                status_code=401,
                detail={"code": "token_invalid", "message": "令牌无效，请重新登录"},
                headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
            )

    # 2. dev 模式降级：Basic Auth → 管理员（user_id=0）
    if os.getenv("BRIDGE_DEBUG") == "1":
        if credentials is not None:
            ok_user = secrets.compare_digest(
                credentials.username.encode(), ADMIN_USER.encode()
            )
            ok_pass = secrets.compare_digest(
                credentials.password.encode(), ADMIN_PASS.encode()
            )
            if ok_user and ok_pass:
                # CSRF 校验（JWT 路径已在上文处理，Basic Auth 路径保留全局 CSRF）
                if request.method in ("POST", "PATCH", "PUT", "DELETE"):
                    if request.headers.get("x-requested-with") != "BridgeAdmin":
                        raise HTTPException(status_code=403, detail="缺少必要请求头")
                    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
                    header_token = request.headers.get("x-csrf-token", "")
                    if not cookie_token or not secrets.compare_digest(cookie_token, header_token):
                        raise HTTPException(status_code=403, detail="CSRF 校验失败")
                return 0

    # 3. 未认证 → 401
    raise HTTPException(
        status_code=401,
        detail="需要认证",
        headers={"WWW-Authenticate": 'Bearer realm="bridge"'},
    )


# ============================================================
# 阶段2.4: RBAC 权限模型（嵌套多租户）
# ============================================================
# 权限等级：admin(3) > edit(2) > view(1) > None(0)
_PERMISSION_RANK = {"admin": 3, "edit": 2, "view": 1}


def _permission_ge(perm: Optional[str], min_perm: str) -> bool:
    """判断 perm 是否 >= min_perm。"""
    return _PERMISSION_RANK.get(perm or "", 0) >= _PERMISSION_RANK.get(min_perm, 0)


def _is_active_org_member(db: Session, org_id: int, user_id: int) -> Optional[str]:
    """校验用户是组织的活跃成员。

    返回角色名（owner/admin/member/viewer），非成员或组织已软删除返回 None。
    补充-P1-1: "活跃成员"定义 = org_memberships 存在记录且组织未软删除。
    DRL-R1-V1: JOIN users 表过滤 is_active=True，停用用户即使有成员记录也返回 None。
    """
    org = db.query(Organization).filter(
        Organization.id == org_id,
        Organization.deleted_at.is_(None),
    ).first()
    if not org:
        return None
    # DRL-R1-V1: JOIN User 表校验 is_active，停用用户无权访问组织
    membership = db.query(OrgMembership).join(
        User, User.id == OrgMembership.user_id
    ).filter(
        OrgMembership.org_id == org_id,
        OrgMembership.user_id == user_id,
        User.is_active.is_(True),
    ).first()
    if not membership:
        return None
    return membership.role


def _is_active_team_member(db: Session, team_id: int, user_id: int) -> Optional[str]:
    """校验用户是团队的活跃成员。

    返回角色名（team_admin/member），非成员或团队已软删除返回 None。
    DRL-R1-V1: JOIN users 表过滤 is_active=True，停用用户即使有成员记录也返回 None。
    """
    team = db.query(Team).filter(
        Team.id == team_id,
        Team.deleted_at.is_(None),
    ).first()
    if not team:
        return None
    # DRL-R1-V1: JOIN User 表校验 is_active，停用用户无权访问团队
    membership = db.query(TeamMembership).join(
        User, User.id == TeamMembership.user_id
    ).filter(
        TeamMembership.team_id == team_id,
        TeamMembership.user_id == user_id,
        User.is_active.is_(True),
    ).first()
    if not membership:
        return None
    return membership.role


def _check_project_shares(
    db: Session, project: Project, user_id: int
) -> Optional[str]:
    """检查 project_shares 表，返回通过共享获得的权限。

    三种共享目标（org/team/user）按优先级检查，返回首个匹配的 share.permission。
    补充-P1-6: 查询时校验共享目标未软删除，防止残留共享记录导致权限泄露。
    """
    # 1. 组织共享：项目归属组织 + 共享给该组织 + 组织未软删除
    if project.org_id is not None:
        org = db.query(Organization).filter(
            Organization.id == project.org_id,
            Organization.deleted_at.is_(None),
        ).first()
        if org:
            share = db.query(ProjectShare).filter(
                ProjectShare.project_id == project.id,
                ProjectShare.shared_with_org_id == project.org_id,
            ).first()
            if share:
                return share.permission

    # 2. 团队共享：项目归属团队 + 共享给该团队 + 团队未软删除 + 用户在该团队
    if project.team_id is not None:
        team = db.query(Team).filter(
            Team.id == project.team_id,
            Team.deleted_at.is_(None),
        ).first()
        if team:
            # 用户需在该团队中才生效
            team_role = _is_active_team_member(db, project.team_id, user_id)
            if team_role:
                share = db.query(ProjectShare).filter(
                    ProjectShare.project_id == project.id,
                    ProjectShare.shared_with_team_id == project.team_id,
                ).first()
                if share:
                    return share.permission

    # 3. 个人共享：共享给该用户 + 用户未停用
    user = db.query(User).filter(
        User.id == user_id,
        User.is_active == True,  # noqa: E712
    ).first()
    if user:
        share = db.query(ProjectShare).filter(
            ProjectShare.project_id == project.id,
            ProjectShare.shared_with_user_id == user_id,
        ).first()
        if share:
            return share.permission

    return None


def _resolve_project_permission(
    db: Session, user_id: int, project: Project
) -> Optional[str]:
    """权限决策树：解析用户对项目的最高权限。

    返回 'admin' / 'edit' / 'view' / None。
    决策树详见 phase2-plan.md A.2，关键规则：
    - dev admin（user_id=0）→ 'admin'
    - owner_id 特权：个人项目直接 admin；组织项目需校验活跃组织成员身份
    - 组织 owner/admin → admin；团队 team_admin → admin
    - project_shares 按 share.permission（校验共享目标未软删除）
    - 组织 member/viewer → view；团队 member → edit
    """
    # 1. dev admin 全权
    if user_id == 0:
        return "admin"

    # 2. owner_id 特权
    if project.owner_id == user_id:
        if project.org_id is None:
            return "admin"  # 个人项目所有者
        # 补充-P1-1: 组织项目需校验活跃组织成员身份
        org_role = _is_active_org_member(db, project.org_id, user_id)
        if org_role:
            return "admin"  # 仍是活跃成员 → admin
        # 被移除/组织已删除 → 降级到 share 路径（不直接给 admin）

    # 3. 组织项目
    if project.org_id is not None:
        # 3a. 组织 owner/admin → admin
        org_role = _is_active_org_member(db, project.org_id, user_id)
        if org_role in ("owner", "admin"):
            return "admin"
        # 3b. 团队 team_admin → admin
        if project.team_id is not None:
            team_role = _is_active_team_member(db, project.team_id, user_id)
            if team_role == "team_admin":
                return "admin"
        # 3c. project_shares
        share_perm = _check_project_shares(db, project, user_id)
        if share_perm:
            return share_perm
        # 3d. 团队 member → edit（先于组织 member，因为 edit > view）
        if project.team_id is not None:
            team_role = _is_active_team_member(db, project.team_id, user_id)
            if team_role:
                return "edit"
        # 3e. 组织 member/viewer → view
        if org_role:
            return "view"
        return None

    # 4. 个人项目：只有 share 路径
    share_perm = _check_project_shares(db, project, user_id)
    if share_perm:
        return share_perm
    return None


# ---------- 依赖函数 ----------


def require_org_member(
    org_id: int,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> tuple[int, str]:
    """校验当前用户是该组织的活跃成员，返回 (user_id, role)。"""
    role = _is_active_org_member(db, org_id, user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="无权访问该组织")
    return (user_id, role)


def require_org_admin(
    org_id: int,
    member: tuple[int, str] = Depends(require_org_member),
) -> tuple[int, str]:
    """校验组织 admin/owner 角色。"""
    _, role = member
    if role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="需要组织管理员权限")
    return member


def require_org_owner(
    org_id: int,
    member: tuple[int, str] = Depends(require_org_member),
) -> tuple[int, str]:
    """校验组织 owner 角色（仅创建者）。"""
    _, role = member
    if role != "owner":
        raise HTTPException(status_code=403, detail="需要组织所有者权限")
    return member


def require_team_member(
    team_id: int,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> tuple[int, str]:
    """校验当前用户是该团队的活跃成员，返回 (user_id, role)。"""
    role = _is_active_team_member(db, team_id, user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="无权访问该团队")
    return (user_id, role)


def require_team_admin(
    team_id: int,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> tuple[int, str]:
    """校验团队 team_admin 角色（或组织 admin/owner）。"""
    # 1. 团队 team_admin
    team_role = _is_active_team_member(db, team_id, user_id)
    if team_role == "team_admin":
        return (user_id, team_role)
    # 2. 组织 admin/owner 也可以管理团队
    team = db.query(Team).filter(
        Team.id == team_id,
        Team.deleted_at.is_(None),
    ).first()
    if not team:
        raise HTTPException(status_code=404, detail="团队不存在")
    org_role = _is_active_org_member(db, team.org_id, user_id)
    if org_role in ("owner", "admin"):
        return (user_id, org_role)
    raise HTTPException(status_code=403, detail="需要团队管理员权限")


def require_project_access(min_permission: str = "view"):
    """校验项目访问权限，返回 (user_id, permission)。

    用法：Depends(require_project_access('edit'))
    权限等级：admin > edit > view
    """
    def dep(
        project_id: int,
        user_id: int = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> tuple[int, str]:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        perm = _resolve_project_permission(db, user_id, project)
        if perm is None or not _permission_ge(perm, min_permission):
            raise HTTPException(status_code=403, detail="无项目访问权限")
        return (user_id, perm)
    return dep
