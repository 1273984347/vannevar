# tests/test_context_cards.py — 阶段1.6 Context Card 只读 API 测试
import os

# R3 class-level fix: 用 setdefault 而非 []=，遵循 conftest.py 约定（允许 CI/命令行覆盖）
os.environ.setdefault("BRIDGE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BRIDGE_DEBUG", "1")

import pytest
from fastapi.testclient import TestClient

from auth import ADMIN_PASS, ADMIN_USER
from database import Base, engine
from main import app
from conftest import auth_headers as _auth_headers  # DRL-R1-V2: JWT 写端点需要 X-CSRF-Token 头

client = TestClient(app)

ADMIN_AUTH = (ADMIN_USER, ADMIN_PASS)


def _register_and_login(email="ccuser@example.com", password="testpass123"):
    """注册并登录，返回 JWT token。"""
    client.post("/api/auth/register", json={
        "email": email, "password": password, "display_name": "卡片用户"
    })
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]


def _push_item(token, app, item_type, client_uuid, data, created_at=1000, updated_at=1000):
    """推送一条同步项。"""
    res = client.post("/api/sync/push", json={
        "items": [{
            "app": app,
            "item_type": item_type,
            "client_uuid": client_uuid,
            "data": data,
            "client_created_at": created_at,
            "client_updated_at": updated_at,
        }]
    }, headers=_auth_headers(token))
    assert res.status_code == 200


# ---------- 鉴权 ----------
def test_list_requires_auth():
    """未认证 → 401。"""
    res = client.get("/api/context-cards")
    assert res.status_code == 401


def test_list_dev_basic_auth():
    """dev 模式 Basic Auth 可访问。"""
    res = client.get("/api/context-cards", auth=ADMIN_AUTH)
    assert res.status_code == 200


# ---------- 基本功能 ----------
def test_empty_list():
    """无数据时返回空列表。"""
    token = _register_and_login()
    res = client.get("/api/context-cards", headers=_auth_headers(token))
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 0
    assert body["items"] == []


def test_snapshot_wrapped_as_context_card():
    """focus snapshot 被包装为 focus-snapshot Context Card。"""
    token = _register_and_login()
    _push_item(token, "focus", "snapshot", "uuid-snap-1", {
        "title": "工作上下文",
        "tabs": [{"title": "Google", "url": "https://google.com"}],
        "createdAt": 1700000000000,
    })
    res = client.get("/api/context-cards", headers=_auth_headers(token))
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    card = body["items"][0]
    assert card["schema"] == "vannevar-context-card/v1"
    assert card["type"] == "focus-snapshot"
    assert card["app"] == "focus"
    assert card["client_uuid"] == "uuid-snap-1"
    assert card["data"]["title"] == "工作上下文"
    assert card["exportedAt"] is not None  # server_updated_at


def test_inspiration_wrapped_as_context_card():
    """muse inspiration 被包装为 muse-inspiration Context Card。"""
    token = _register_and_login()
    _push_item(token, "muse", "inspiration", "uuid-insp-1", {
        "title": "设计灵感",
        "url": "https://example.com",
        "note": "配色参考",
        "mood": "温暖",
        "createdAt": 1700000000000,
    })
    res = client.get("/api/context-cards", headers=_auth_headers(token))
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    card = body["items"][0]
    assert card["schema"] == "vannevar-context-card/v1"
    assert card["type"] == "muse-inspiration"
    assert card["app"] == "muse"
    assert card["data"]["title"] == "设计灵感"


def test_mood_excluded_from_context_cards():
    """mood 类型不出现在 Context Card 列表中。"""
    token = _register_and_login()
    _push_item(token, "muse", "mood", "uuid-mood-1", {
        "name": "温暖", "color": "#ff6b35", "sortOrder": 0,
    })
    _push_item(token, "focus", "snapshot", "uuid-snap-1", {
        "title": "快照", "tabs": [], "createdAt": 1000,
    })
    res = client.get("/api/context-cards", headers=_auth_headers(token))
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["items"][0]["type"] == "focus-snapshot"


# ---------- 过滤 ----------
def test_filter_by_app():
    """app 参数过滤。"""
    token = _register_and_login()
    _push_item(token, "focus", "snapshot", "uuid-snap-1", {
        "title": "快照1", "tabs": [], "createdAt": 1000,
    })
    _push_item(token, "muse", "inspiration", "uuid-insp-1", {
        "title": "灵感1", "createdAt": 2000,
    })

    # 只看 focus
    res = client.get("/api/context-cards?app=focus", headers=_auth_headers(token))
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["items"][0]["app"] == "focus"

    # 只看 muse
    res = client.get("/api/context-cards?app=muse", headers=_auth_headers(token))
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["items"][0]["app"] == "muse"


# ---------- 数据隔离 ----------
def test_data_isolation():
    """不同用户的 Context Card 互不可见。"""
    token1 = _register_and_login("user1@example.com")
    token2 = _register_and_login("user2@example.com")

    _push_item(token1, "focus", "snapshot", "uuid-snap-user1", {
        "title": "用户1的快照", "tabs": [], "createdAt": 1000,
    })
    _push_item(token2, "focus", "snapshot", "uuid-snap-user2", {
        "title": "用户2的快照", "tabs": [], "createdAt": 2000,
    })

    # user1 只看到自己的
    res = client.get("/api/context-cards", headers=_auth_headers(token1))
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["items"][0]["data"]["title"] == "用户1的快照"

    # user2 只看到自己的
    res = client.get("/api/context-cards", headers=_auth_headers(token2))
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["items"][0]["data"]["title"] == "用户2的快照"


# ---------- 排序 ----------
def test_ordering_by_server_updated_at_desc():
    """按 server_updated_at 降序排列。"""
    token = _register_and_login()
    _push_item(token, "focus", "snapshot", "uuid-snap-1", {
        "title": "第一份", "tabs": [], "createdAt": 1000,
    })
    _push_item(token, "focus", "snapshot", "uuid-snap-2", {
        "title": "第二份", "tabs": [], "createdAt": 2000,
    })

    res = client.get("/api/context-cards", headers=_auth_headers(token))
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 2
    # 后推送的排在前面（server_updated_at 更大）
    assert body["items"][0]["client_uuid"] == "uuid-snap-2"
    assert body["items"][1]["client_uuid"] == "uuid-snap-1"
