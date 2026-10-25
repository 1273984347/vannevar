# routers/auth_router.py — 阶段1.1 统一账号体系 API（注册/登录/me）
# DRL-R1-V15: register/login 端点限流依赖 main.py 中间件统一处理（/api/auth/ 限流 30/60s），
# 本文件不重复实现应用层限流。
import time
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import (
    create_access_token,
    hash_password,
    invalidate_user_cache,
    require_user,
    verify_password,
)
from database import get_db
from models import Project, Task, User, UserCryptoProfile
from schemas import (
    CryptoProfileIn,
    CryptoProfileOut,
    CryptoVerificationIn,
    TaskUpdateOut,
    TokenOut,
    UserLogin,
    UserOut,
    UserRegister,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# DRL-R1-V14: 防 login 时序攻击——用户不存在时也执行 dummy verify_password
# 使用固定 dummy hash，避免攻击者通过响应时间差异判断邮箱是否存在
DUMMY_HASH = hash_password("dummy-password-for-timing-attack-prevention")


@router.post("/register", response_model=TokenOut, status_code=201)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    """注册新用户，成功即返回 JWT（注册即登录）。

    R2-V4: 邮箱统一存储为小写，查询时用 func.lower() 大小写不敏感匹配。
    """
    # R2-V4: 统一转小写存储，避免大小写差异导致重复注册
    email_normalized = payload.email.strip().lower()
    existing = db.query(User).filter(func.lower(User.email) == email_normalized).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="该邮箱已注册")
    user = User(
        email=email_normalized,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, user.email)
    return TokenOut(
        access_token=token,
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenOut)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    """邮箱 + 密码登录，成功返回 JWT。

    统一返回"邮箱或密码错误"，不泄露邮箱是否存在。
    R2-V4: 邮箱查询用 func.lower() 大小写不敏感匹配，与 orgs_router 一致。
    """
    # R2-V4: 大小写不敏感查询，避免大小写差异导致登录失败
    email_normalized = payload.email.strip().lower()
    user = db.query(User).filter(func.lower(User.email) == email_normalized).first()
    # DRL-R1-V14: 无论 user 是否存在都执行 verify_password，消除时序差异
    # 用户不存在时校验 DUMMY_HASH，使响应时间与真实用户场景一致
    if user is None or not verify_password(payload.password, user.password_hash if user else DUMMY_HASH):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    token = create_access_token(user.id, user.email)
    return TokenOut(
        access_token=token,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def get_me(user_id: int = Depends(require_user), db: Session = Depends(get_db)):
    """获取当前登录用户信息。

    dev 模式 Basic Auth 降级时返回虚拟用户（id=0）。
    """
    if user_id == 0:
        # dev 模式 Basic Auth 降级：返回虚拟用户
        return UserOut(
            id=0,
            email="dev@bridge.local",
            display_name="Dev User",
            created_at=datetime.now(timezone.utc),
        )
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.post("/logout")
def logout(user_id: int = Depends(require_user), db: Session = Depends(get_db)):
    """登出当前用户，撤销其所有已签发 JWT。

    DRL-R2-V1: 写入 User.token_revoked_at = 当前时间戳，
    使该用户此前签发的 JWT 在 _check_token_revoked 校验中失效（iat < revoked_at）。
    require_user 已校验 JWT 合法性，此处 user_id 可信。
    dev 模式虚拟用户（user_id=0）无 DB 记录，直接返回成功。
    R6-BE-4 修复：db.commit() 后主动失效 _user_active_cache 中该用户的缓存条目，
        避免撤销时间戳在 30s 缓存 TTL 内未生效导致旧 token 仍可用。
    R7-BE-1/R7-BE-2 修复：改用 invalidate_user_cache(user_id) 持锁 pop，
        与 _get_user_state_cached 的锁纪律一致，消除 commit 与 pop 之间的 TOCTOU 窗口。
    """
    if user_id > 0:
        user = db.query(User).filter(User.id == user_id).first()
        if user is not None:
            user.token_revoked_at = int(time.time())
            db.commit()
            # R7-BE-1/R7-BE-2: 持锁 pop，消除 TOCTOU 窗口
            invalidate_user_cache(user_id)
    return {"message": "已登出"}


@router.get("/me/crypto-profile", response_model=CryptoProfileOut)
def get_crypto_profile(
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
):
    """取回当前用户的端到端加密档案（跨设备解密用）。

    档案内容 = PBKDF2 salt（**公开参数**，不是密钥）+ 派生参数 + verification 密文。
    服务端既无法反推 passphrase，也无法解密任何同步内容。
    尚未设置档案时返回 404；dev 模式虚拟用户（user_id=0）同样返回 404。
    """
    if user_id == 0:
        raise HTTPException(status_code=404, detail="dev 模式无加密档案")
    profile = db.query(UserCryptoProfile).filter(UserCryptoProfile.user_id == user_id).first()
    if profile is None:
        raise HTTPException(status_code=404, detail="尚未设置加密档案")
    return CryptoProfileOut(
        salt=profile.salt,
        iterations=profile.iterations,
        kdf=profile.kdf,
        salt_fingerprint=profile.salt_fingerprint,
        verification=CryptoVerificationIn(
            ciphertext=profile.verification_ciphertext,
            nonce=profile.verification_nonce,
        ),
    )


@router.put("/me/crypto-profile", response_model=CryptoProfileOut)
def put_crypto_profile(
    payload: CryptoProfileIn,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
):
    """写入 / 更新当前用户的端到端加密档案。

    幂等：同一 salt 重复提交等价于覆盖同一份数据，不产生重复行。
    salt 变化时覆盖旧档案（用户主动更换 passphrase 或在新设备上复用远端 salt）。

    写端点受 require_user 的 per-session CSRF 校验保护（X-CSRF-Token 须等于 JWT.csrf_jti）。
    """
    if user_id == 0:
        raise HTTPException(status_code=404, detail="dev 模式无加密档案")
    profile = db.query(UserCryptoProfile).filter(UserCryptoProfile.user_id == user_id).first()
    if profile is None:
        profile = UserCryptoProfile(user_id=user_id)
        db.add(profile)
    elif profile.salt != payload.salt:
        # 冲突检测：已有档案的 salt 与本次不同 ⇒ 来自另一台设备（或另一套密钥）。
        # 覆盖会毁掉那台设备的历史密文（它是用旧 salt 加密的），因此拒绝。
        # 客户端侧亦有同样的守卫（不覆盖他人档案），这里是服务端侧的纵深防御。
        raise HTTPException(
            status_code=409,
            detail={"code": "crypto_profile_conflict", "message": "已有其它加密档案，不能覆盖"},
        )
    profile.salt = payload.salt
    profile.iterations = payload.iterations
    profile.kdf = payload.kdf
    profile.salt_fingerprint = payload.salt_fingerprint
    profile.verification_ciphertext = payload.verification.ciphertext
    profile.verification_nonce = payload.verification.nonce
    db.commit()
    db.refresh(profile)
    return CryptoProfileOut(
        salt=profile.salt,
        iterations=profile.iterations,
        kdf=profile.kdf,
        salt_fingerprint=profile.salt_fingerprint,
        verification=CryptoVerificationIn(
            ciphertext=profile.verification_ciphertext,
            nonce=profile.verification_nonce,
        ),
    )


@router.get("/me/recent-task-updates", response_model=List[TaskUpdateOut])
def get_recent_task_updates(
    limit: int = Query(20, ge=1, le=100, description="返回的最近任务条数"),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Phase 4.1: 获取当前用户最近更新的任务列表（Bridge → Focus 通知通道）。

    返回该用户拥有的所有项目中按 updated_at 倒序的最近 N 条任务。
    Focus 扩展定期轮询此端点，对比本地缓存 last_seen_updated_at，
    对新变更的任务通过 chrome.notifications + badge 通知用户。

    dev 模式（user_id=0）返回空列表，不报错。
    """
    if user_id <= 0:
        # dev 模式 Basic Auth 降级：返回空列表，不暴露任何项目数据
        return []
    # 一次性 join 查询：用户拥有的项目下的任务，按 updated_at 倒序
    rows = (
        db.query(Task, Project)
        .join(Project, Task.project_id == Project.id)
        .filter(Project.owner_id == user_id)
        .order_by(Task.updated_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for task, project in rows:
        result.append(
            TaskUpdateOut(
                task_id=task.id,
                project_id=task.project_id,
                project_name=project.name,
                title=task.title,
                status=task.status,
                owner_name=task.owner_name or "",
                updated_at=task.updated_at,
                # P4.1: 用 datetime.isoformat() 输出 ISO 字符串，客户端 new Date() 直接解析
                # 后端 updated_at 为 naive UTC，加 'Z' 后缀明示 UTC 时区
                updated_at_iso=(task.updated_at.strftime("%Y-%m-%dT%H:%M:%S") + "Z") if task.updated_at else "",
            )
        )
    return result
