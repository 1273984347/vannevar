# tests/test_sync.py — 阶段1.5 数据同步 API 测试
import os

# R3 class-level fix: 用 setdefault 而非 []=，遵循 conftest.py 约定（允许 CI/命令行覆盖）
os.environ.setdefault("BRIDGE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BRIDGE_DEBUG", "1")

import pytest
from fastapi.testclient import TestClient

from auth import ADMIN_PASS, ADMIN_USER
from database import Base, engine
from main import app
from conftest import auth_headers as _auth_headers, CSRF_HEADERS  # DRL-R1-V2: JWT 写端点需要 X-CSRF-Token 头

client = TestClient(app)

ADMIN_AUTH = (ADMIN_USER, ADMIN_PASS)


def _register_and_login(email="syncuser@example.com", password="testpass123"):
    """注册并登录，返回 JWT token。"""
    client.post("/api/auth/register", json={
        "email": email, "password": password, "display_name": "同步用户"
    })
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]


def _make_item(client_uuid, app="focus", item_type="snapshot",
               data=None, created_at=1000, updated_at=1000):
    return {
        "app": app,
        "item_type": item_type,
        "client_uuid": client_uuid,
        "data": data or {"title": "测试快照", "tabs": []},
        "client_created_at": created_at,
        "client_updated_at": updated_at,
    }


# ---------- 鉴权 ----------
def test_push_requires_auth():
    """未认证推送 → 401。"""
    res = client.post("/api/sync/push", json={"items": []})
    assert res.status_code == 401


def test_pull_requires_auth():
    """未认证拉取 → 401。"""
    res = client.get("/api/sync/pull?app=focus")
    assert res.status_code == 401


def test_push_with_jwt():
    """JWT 用户可以推送。"""
    token = _register_and_login()
    item = _make_item("uuid-1")
    res = client.post("/api/sync/push", json={"items": [item]}, headers=_auth_headers(token))
    assert res.status_code == 200
    assert res.json()["created"] == 1


def test_push_with_dev_basic_auth():
    """dev 模式 Basic Auth 降级（user_id=0）也可以推送。"""
    item = _make_item("uuid-dev")
    res = client.post("/api/sync/push", json={"items": [item]}, auth=ADMIN_AUTH, headers=CSRF_HEADERS)
    assert res.status_code == 200
    assert res.json()["created"] == 1


# ---------- push：创建/更新/跳过/LWW ----------
def test_push_creates_new_item():
    """首次推送 → created。"""
    token = _register_and_login()
    item = _make_item("uuid-new")
    res = client.post("/api/sync/push", json={"items": [item]}, headers=_auth_headers(token))
    assert res.status_code == 200
    data = res.json()
    assert data["created"] == 1
    assert data["updated"] == 0
    assert data["skipped"] == 0
    assert data["results"][0]["action"] == "created"


def test_push_updates_existing_with_newer_timestamp():
    """相同 client_uuid + 更新的 client_updated_at → updated。"""
    token = _register_and_login()
    # 第一次推送
    item1 = _make_item("uuid-upd", updated_at=1000)
    client.post("/api/sync/push", json={"items": [item1]}, headers=_auth_headers(token))
    # 第二次推送：更新的时间戳
    item2 = _make_item("uuid-upd", updated_at=2000, data={"title": "更新后的快照"})
    res = client.post("/api/sync/push", json={"items": [item2]}, headers=_auth_headers(token))
    assert res.status_code == 200
    data = res.json()
    assert data["created"] == 0
    assert data["updated"] == 1
    assert data["skipped"] == 0
    assert data["results"][0]["action"] == "updated"


def test_push_skips_older_timestamp():
    """相同 client_uuid + 更旧的 client_updated_at → skipped（LWW）。"""
    token = _register_and_login()
    # 第一次推送
    item1 = _make_item("uuid-skip", updated_at=2000)
    client.post("/api/sync/push", json={"items": [item1]}, headers=_auth_headers(token))
    # 第二次推送：更旧的时间戳
    item2 = _make_item("uuid-skip", updated_at=1000, data={"title": "旧数据"})
    res = client.post("/api/sync/push", json={"items": [item2]}, headers=_auth_headers(token))
    assert res.status_code == 200
    data = res.json()
    assert data["created"] == 0
    assert data["updated"] == 0
    assert data["skipped"] == 1
    assert data["results"][0]["action"] == "skipped"


def test_push_skips_same_timestamp():
    """相同 client_uuid + 相同 client_updated_at → skipped。"""
    token = _register_and_login()
    item1 = _make_item("uuid-same", updated_at=1000)
    client.post("/api/sync/push", json={"items": [item1]}, headers=_auth_headers(token))
    res = client.post("/api/sync/push", json={"items": [item1]}, headers=_auth_headers(token))
    assert res.json()["skipped"] == 1


def test_push_batch_mixed_actions():
    """批量推送：新建 + 更新 + 跳过混合。"""
    token = _register_and_login()
    # 预置一条
    client.post("/api/sync/push", json={"items": [
        _make_item("uuid-batch-1", updated_at=1000),
        _make_item("uuid-batch-2", updated_at=2000),
    ]}, headers=_auth_headers(token))
    # 批量推送：uuid-batch-1 更新、uuid-batch-2 跳过、uuid-batch-3 新建
    res = client.post("/api/sync/push", json={"items": [
        _make_item("uuid-batch-1", updated_at=1500),
        _make_item("uuid-batch-2", updated_at=1000),
        _make_item("uuid-batch-3", updated_at=3000),
    ]}, headers=_auth_headers(token))
    data = res.json()
    assert data["created"] == 1
    assert data["updated"] == 1
    assert data["skipped"] == 1


def test_push_empty_items():
    """空 items 列表 → 成功，全 0。"""
    token = _register_and_login()
    res = client.post("/api/sync/push", json={"items": []}, headers=_auth_headers(token))
    assert res.status_code == 200
    data = res.json()
    assert data["pushed"] == 0
    assert data["created"] == 0


# ---------- push：参数校验 ----------
def test_push_rejects_invalid_app():
    """非法 app 值 → 422。"""
    token = _register_and_login()
    item = _make_item("uuid-x", app="invalid")
    res = client.post("/api/sync/push", json={"items": [item]}, headers=_auth_headers(token))
    assert res.status_code == 422


def test_push_rejects_invalid_item_type():
    """非法 item_type 值 → 422。"""
    token = _register_and_login()
    item = _make_item("uuid-x", item_type="invalid")
    res = client.post("/api/sync/push", json={"items": [item]}, headers=_auth_headers(token))
    assert res.status_code == 422


def test_push_rejects_empty_client_uuid():
    """空 client_uuid → 422。"""
    token = _register_and_login()
    item = _make_item("", )
    res = client.post("/api/sync/push", json={"items": [item]}, headers=_auth_headers(token))
    assert res.status_code == 422


# ---------- pull ----------
def test_pull_returns_user_items():
    """拉取返回该用户指定 app 的所有数据。"""
    token = _register_and_login()
    # 推送 2 条 focus + 1 条 muse
    client.post("/api/sync/push", json={"items": [
        _make_item("uuid-f1", app="focus", updated_at=1000),
        _make_item("uuid-f2", app="focus", updated_at=2000),
        _make_item("uuid-m1", app="muse", item_type="inspiration", updated_at=3000),
    ]}, headers=_auth_headers(token))
    # 拉取 focus
    res = client.get("/api/sync/pull?app=focus", headers=_auth_headers(token))
    assert res.status_code == 200
    data = res.json()
    assert data["app"] == "focus"
    assert data["count"] == 2
    uuids = {item["client_uuid"] for item in data["items"]}
    assert uuids == {"uuid-f1", "uuid-f2"}


def test_pull_filters_by_app():
    """拉取 focus 不返回 muse 数据。"""
    token = _register_and_login()
    client.post("/api/sync/push", json={"items": [
        _make_item("uuid-f1", app="focus"),
        _make_item("uuid-m1", app="muse", item_type="inspiration"),
    ]}, headers=_auth_headers(token))
    res = client.get("/api/sync/pull?app=muse", headers=_auth_headers(token))
    assert res.json()["count"] == 1
    assert res.json()["items"][0]["client_uuid"] == "uuid-m1"


def test_pull_rejects_invalid_app():
    """拉取非法 app → 422。"""
    token = _register_and_login()
    res = client.get("/api/sync/pull?app=invalid", headers=_auth_headers(token))
    assert res.status_code == 422


def test_pull_empty_when_no_data():
    """无数据时返回空列表。"""
    token = _register_and_login()
    res = client.get("/api/sync/pull?app=focus", headers=_auth_headers(token))
    assert res.status_code == 200
    assert res.json()["count"] == 0
    assert res.json()["items"] == []


# ---------- 数据隔离 ----------
def test_pull_does_not_leak_other_users_data():
    """用户 A 拉取不到用户 B 的数据。"""
    token_a = _register_and_login("userA@example.com")
    token_b = _register_and_login("userB@example.com")
    # A 推送
    client.post("/api/sync/push", json={"items": [
        _make_item("uuid-a1"),
    ]}, headers=_auth_headers(token_a))
    # B 推送
    client.post("/api/sync/push", json={"items": [
        _make_item("uuid-b1"),
    ]}, headers=_auth_headers(token_b))
    # B 拉取，应只有自己的数据
    res = client.get("/api/sync/pull?app=focus", headers=_auth_headers(token_b))
    assert res.json()["count"] == 1
    assert res.json()["items"][0]["client_uuid"] == "uuid-b1"


# ---------- Phase 4.2: 媒体元数据在 sync push/pull 中存活 ----------

def test_sync_media_metadata_survives_round_trip():
    """Phase 4.2: Muse 灵感推送的媒体元数据应在 pull 时完整返回。

    覆盖 mediaType / mediaArtist / mediaAlbum / mediaArtworkUrl 4 个新字段，
    确保扩展端的 media session 元数据能跨设备同步。
    """
    token = _register_and_login("media1@example.com")
    media_data = {
        "title": "夜空中最亮的星",
        "url": "https://music.example.com/track/123",
        "note": "escape - 逃跑计划",
        "mood": "夜晚",
        "selectedText": "",
        "createdAt": 1700000000000,
        "pinned": False,
        "mediaType": "music",
        "mediaArtist": "逃跑计划",
        "mediaAlbum": "世界",
        "mediaArtworkUrl": "https://music.example.com/art/123.jpg",
    }
    item = _make_item(
        "media-uuid-1",
        app="muse",
        item_type="inspiration",
        data=media_data,
        created_at=1700000000000,
        updated_at=1700000000000,
    )
    push_res = client.post("/api/sync/push", json={"items": [item]}, headers=_auth_headers(token))
    assert push_res.status_code == 200, push_res.text
    assert push_res.json()["created"] == 1

    # 拉取验证
    pull_res = client.get("/api/sync/pull?app=muse", headers=_auth_headers(token))
    assert pull_res.status_code == 200
    pulled_items = pull_res.json()["items"]
    assert len(pulled_items) == 1
    data = pulled_items[0]["data"]
    # 验证 4 个媒体字段完整存活
    assert data["mediaType"] == "music"
    assert data["mediaArtist"] == "逃跑计划"
    assert data["mediaAlbum"] == "世界"
    assert data["mediaArtworkUrl"] == "https://music.example.com/art/123.jpg"
    # 验证原有字段未受影响
    assert data["title"] == "夜空中最亮的星"
    assert data["url"] == "https://music.example.com/track/123"


def test_sync_media_metadata_lww_update():
    """Phase 4.2: 媒体元数据随 LWW 更新正常覆盖。

    同一 client_uuid 推送两次，第二次 updated_at 更新且 mediaType 变化，
    应触发 LWW 更新，pull 返回最新值。
    """
    token = _register_and_login("media2@example.com")
    # 第一次推送：music
    item_v1 = _make_item(
        "media-uuid-lww",
        app="muse",
        item_type="inspiration",
        data={
            "title": "v1 track",
            "url": "https://music.example.com/v1",
            "mediaType": "music",
            "mediaArtist": "Artist A",
            "mediaAlbum": None,
            "mediaArtworkUrl": None,
        },
        created_at=1700000000000,
        updated_at=1700000000000,
    )
    client.post("/api/sync/push", json={"items": [item_v1]}, headers=_auth_headers(token))

    # 第二次推送：updated_at 更新，mediaType 改为 podcast
    item_v2 = _make_item(
        "media-uuid-lww",
        app="muse",
        item_type="inspiration",
        data={
            "title": "v2 episode",
            "url": "https://podcast.example.com/v2",
            "mediaType": "podcast",
            "mediaArtist": "Host B",
            "mediaAlbum": "Season 1",
            "mediaArtworkUrl": "https://podcast.example.com/art/v2.jpg",
        },
        created_at=1700000000000,
        updated_at=1700000001000,  # 更新时间戳
    )
    push_res = client.post("/api/sync/push", json={"items": [item_v2]}, headers=_auth_headers(token))
    assert push_res.json()["updated"] == 1

    # 拉取验证 LWW 生效
    pull_res = client.get("/api/sync/pull?app=muse", headers=_auth_headers(token))
    data = pull_res.json()["items"][0]["data"]
    assert data["title"] == "v2 episode"
    assert data["mediaType"] == "podcast"
    assert data["mediaArtist"] == "Host B"
    assert data["mediaAlbum"] == "Season 1"
    assert data["mediaArtworkUrl"] == "https://podcast.example.com/art/v2.jpg"


def test_push_does_not_update_other_users_item():
    """用户 B 推送相同 client_uuid 不会更新用户 A 的数据（各自隔离）。"""
    token_a = _register_and_login("userA@example.com")
    token_b = _register_and_login("userB@example.com")
    # A 推送 uuid-shared
    client.post("/api/sync/push", json={"items": [
        _make_item("uuid-shared", updated_at=1000, data={"title": "A的数据"}),
    ]}, headers=_auth_headers(token_a))
    # B 推送相同 uuid（不同 owner，应新建而非更新 A 的）
    res = client.post("/api/sync/push", json={"items": [
        _make_item("uuid-shared", updated_at=2000, data={"title": "B的数据"}),
    ]}, headers=_auth_headers(token_b))
    assert res.json()["created"] == 1
    # A 拉取，数据未被 B 覆盖
    res = client.get("/api/sync/pull?app=focus", headers=_auth_headers(token_a))
    assert res.json()["count"] == 1
    assert res.json()["items"][0]["data"]["title"] == "A的数据"


# ---------- data 字段透传 ----------
def test_pull_returns_data_intact():
    """拉取的 data 字段与推送时一致（JSON 透传）。"""
    token = _register_and_login()
    complex_data = {
        "title": "复杂快照",
        "tabs": [
            {"title": "标签1", "url": "https://example.com/1"},
            {"title": "标签2", "url": "https://example.com/2"},
        ],
        "tags": ["工作", "学习"],
        "nested": {"key": "value", "number": 42},
    }
    client.post("/api/sync/push", json={"items": [
        _make_item("uuid-complex", data=complex_data, updated_at=1000),
    ]}, headers=_auth_headers(token))
    res = client.get("/api/sync/pull?app=focus", headers=_auth_headers(token))
    assert res.json()["items"][0]["data"] == complex_data


def test_pull_returns_client_timestamps():
    """拉取返回 client_created_at 和 client_updated_at。"""
    token = _register_and_login()
    client.post("/api/sync/push", json={"items": [
        _make_item("uuid-ts", created_at=1000, updated_at=2000),
    ]}, headers=_auth_headers(token))
    res = client.get("/api/sync/pull?app=focus", headers=_auth_headers(token))
    item = res.json()["items"][0]
    assert item["client_created_at"] == 1000
    assert item["client_updated_at"] == 2000
