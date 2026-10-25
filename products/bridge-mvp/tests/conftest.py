# tests/conftest.py — 共享测试夹具与配置
# V2 修复：集中管理 engine / SessionLocal / TestClient / 环境变量 / reset_db / CSRF，
#          各测试文件删除重复的 fixture 和常量定义，从 conftest 导入。
# V1 修复：删除所有测试文件中的模块级 os.environ.setdefault("BRIDGE_DEBUG", "1")，
#          统一在此用 autouse fixture + monkeypatch.setenv 注入，避免跨会话污染。
# V34 修复：删除 test_middleware.py 模块级 client，改用 conftest fixture。
# V35 修复：通过 conftest 的 app fixture 统一管理 app 单例。
# V25 修复：添加 --run-slow 选项，控制 slow 标记测试的执行。
# V31 说明：跨日/时区测试需要 freezegun 库，本次未引入，留待后续。
import os

# 数据库 URL 必须在 import database 之前设置（database.py 在 import 时读取）
os.environ.setdefault("BRIDGE_DATABASE_URL", "sqlite:///:memory:")
# BRIDGE_DEBUG 必须在 import auth 之前设置（auth.py 在 import 时读取以决定 ADMIN_PASS）
# V1：此处用 setdefault 保证 import 时有值；autouse fixture _env_debug 再做 per-test 隔离
os.environ.setdefault("BRIDGE_DEBUG", "1")

import pytest
from fastapi.testclient import TestClient
from jose import jwt as _jwt

from auth import (
    ADMIN_PASS,
    ADMIN_USER,
    CSRF_COOKIE_NAME,
    CSRF_TOKEN,
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_SECRET,
)
from database import Base, SessionLocal, engine
from main import app, _rate_limit_store


# ============================================================
# V35: app 单例 fixture（session 级，所有测试共享）
# ============================================================

@pytest.fixture(scope="session")
def fastapi_app():
    """返回全局 app 单例。"""
    return app


@pytest.fixture
def client(fastapi_app):
    """V2/V34 修复：每个测试使用独立的 TestClient（共享 app 单例）。

    CSRF cookie 在 fixture 内设置，确保每个测试干净。
    """
    c = TestClient(fastapi_app)
    c.cookies.set(CSRF_COOKIE_NAME, CSRF_TOKEN)
    yield c


# ============================================================
# V1: autouse fixture — per-test 注入 BRIDGE_DEBUG 环境变量
# ============================================================

@pytest.fixture(autouse=True)
def _env_debug(monkeypatch):
    """V1 修复：每个测试自动注入 BRIDGE_DEBUG=1，避免模块级 setdefault 污染会话。

    monkeypatch 保证测试结束后环境变量自动恢复，不影响其他测试文件。
    """
    monkeypatch.setenv("BRIDGE_DEBUG", "1")


# ============================================================
# V2: autouse fixture — 每个测试前后重建表 + 清空限流存储
# ============================================================

@pytest.fixture(autouse=True)
def reset_db():
    """V2 修复：每个测试前后重建表 + 清空限流存储 + 清空用户活跃缓存。

    各测试文件原有的同名 autouse fixture 可覆盖此 fixture（同名就近原则）。
    R3-V1 修复：追加清理 _qr_token_fail_store，防止 QR token 失败计数跨测试污染。
    """
    from auth import _user_active_cache
    from routers._token_rate_limit import _qr_token_fail_store
    Base.metadata.create_all(bind=engine)
    _rate_limit_store.clear()
    _user_active_cache.clear()
    _qr_token_fail_store.clear()
    yield
    Base.metadata.drop_all(bind=engine)
    _rate_limit_store.clear()
    _user_active_cache.clear()
    _qr_token_fail_store.clear()


# ============================================================
# 共享常量（各测试文件从 conftest 导入）
# ============================================================

ADMIN_AUTH = (ADMIN_USER, ADMIN_PASS)
CSRF_HEADERS = {"X-Requested-With": "BridgeAdmin", "X-CSRF-Token": CSRF_TOKEN}
VOLUNTEER_CSRF_HEADERS = {"X-Requested-With": "BridgeVolunteer"}


# ============================================================
# V2: JWT CSRF 辅助函数（DRL-R1-V2 适配）
# 登录后 JWT 中包含 csrf_jti，写端点需要 X-CSRF-Token 头 == JWT 中的 csrf_jti
# ============================================================

def get_csrf_from_token(token: str) -> str:
    """从 JWT 中提取 csrf_jti（DRL-R1-V2 后写端点需要 X-CSRF-Token 头）。"""
    try:
        payload = _jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
        )
    except Exception:
        # 兼容旧 token（无 aud/iss）
        payload = _jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    return payload.get("csrf_jti", "")


def auth_headers(token: str, extra: dict = None) -> dict:
    """构造 JWT 请求头，自动从 JWT csrf_jti 提取并注入 X-CSRF-Token。

    GET 请求多带一个 X-CSRF-Token 头无副作用（服务端仅对写方法校验）。
    """
    h = {"Authorization": f"Bearer {token}"}
    csrf = get_csrf_from_token(token)
    if csrf:
        h["X-CSRF-Token"] = csrf
    if extra:
        h.update(extra)
    return h


# ============================================================
# V25: --run-slow 选项与 slow 标记注册
# ============================================================

def pytest_addoption(parser):
    """V25 修复：添加 --run-slow 选项控制性能测试执行。"""
    parser.addoption(
        "--run-slow", action="store_true", default=False,
        help="运行标记为 slow 的性能基准测试",
    )


def pytest_configure(config):
    """注册自定义标记，避免 pytest.mark.unknown 警告。"""
    config.addinivalue_line("markers", "slow: 性能基准测试，需 --run-slow 启用")


def pytest_collection_modifyitems(config, items):
    """V25 修复：未指定 --run-slow 时跳过 slow 标记的测试。"""
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="需要 --run-slow 选项运行性能测试")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


# ============================================================
# V31 说明：跨日/时区测试需要 freezegun 库来冻结时间，
# 本次未引入该依赖（改动较大），留待后续迭代。
# 如需测试跨日去重逻辑，可安装 freezegun 后用 @freeze_time 装饰器。
# ============================================================
