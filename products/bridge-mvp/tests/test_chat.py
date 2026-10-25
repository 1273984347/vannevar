# tests/test_chat.py — Phase 6.J AI 助手对话化测试
"""
覆盖 Phase 6.J 的 5 个端点 + 三层降级 + 持久化逻辑：

1. POST   /api/projects/{project_id}/conversations         创建会话
2. GET    /api/projects/{project_id}/conversations         列出会话
3. GET    /api/conversations/{conversation_id}/messages    列出消息
4. POST   /api/conversations/{conversation_id}/ask         SSE 流式回答
5. DELETE /api/conversations/{conversation_id}             软删除会话

测试维度：
- 鉴权（未认证 401 / 认证 200）
- 三层降级（demo 模式 / 无 AI 配置 fallback / LLM 路径由 demo 模式覆盖）
- 持久化（user/assistant 消息落库 + is_fallback 标记）
- 软删除（deleted_at 设置 + 列表过滤）
- 标题自动更新（首问后从"新会话"改为问题前 30 字）
- 上限保护（MAX_CONVERSATIONS_PER_PROJECT / MAX_MESSAGES_PER_CONVERSATION）
- Schema 校验（空 question / > 2000 字 question 拒绝）
"""
import pytest
from fastapi.testclient import TestClient

from auth import CSRF_COOKIE_NAME
from conftest import ADMIN_AUTH, CSRF_HEADERS
from main import app

client = TestClient(app)
client.cookies.set(CSRF_COOKIE_NAME, CSRF_HEADERS["X-CSRF-Token"])


# ============================================================
# 共享 fixture
# ============================================================

@pytest.fixture
def demo_mode(monkeypatch):
    """局部启用演示模式，测试结束后自动恢复，避免污染其他测试。

    注意：不使用 importlib.reload —— is_demo_mode() 动态读取 os.environ，
    reload 会导致跨测试污染。
    """
    monkeypatch.setenv("BRIDGE_AI_DEMO_MODE", "1")
    yield


def _create_project(name="AI 助手测试项目"):
    """创建测试项目，返回 project JSON。"""
    res = client.post(
        "/api/projects",
        json={"name": name, "description": "测试 AI 助手对话化"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_conversation(project_id, title="新会话"):
    """创建会话，返回 conversation JSON。"""
    res = client.post(
        f"/api/projects/{project_id}/conversations",
        json={"title": title},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201, res.text
    return res.json()


def _parse_sse_events(body: str):
    """解析 SSE body 为 [(event_type, data_dict), ...] 列表。"""
    events = []
    current_event = ""
    current_data_lines = []
    for line in body.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            current_data_lines.append(line[6:])
        elif line.strip() == "":
            # 事件分隔符
            if current_event:
                import json as _json
                data_str = "\n".join(current_data_lines)
                try:
                    data = _json.loads(data_str) if data_str else {}
                except Exception:
                    data = {"raw": data_str}
                events.append((current_event, data))
                current_event = ""
                current_data_lines = []
    return events


# ============================================================
# 1. 鉴权测试
# ============================================================

def test_create_conversation_requires_auth():
    """未认证创建会话返回 401。"""
    proj = _create_project()
    res = client.post(f"/api/projects/{proj['id']}/conversations", json={"title": "新会话"})
    assert res.status_code == 401, f"未认证应 401，实际 {res.status_code}"


def test_list_conversations_requires_auth():
    """未认证列出会话返回 401。"""
    proj = _create_project()
    res = client.get(f"/api/projects/{proj['id']}/conversations")
    assert res.status_code == 401, f"未认证应 401，实际 {res.status_code}"


def test_list_messages_requires_auth():
    """未认证列出消息返回 401。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    res = client.get(f"/api/conversations/{conv['id']}/messages")
    assert res.status_code == 401, f"未认证应 401，实际 {res.status_code}"


def test_ask_requires_auth():
    """未认证提问返回 401。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "项目状态如何？"},
    )
    assert res.status_code == 401, f"未认证应 401，实际 {res.status_code}"


def test_delete_conversation_requires_auth():
    """未认证删除会话返回 401。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    res = client.delete(f"/api/conversations/{conv['id']}")
    assert res.status_code == 401, f"未认证应 401，实际 {res.status_code}"


# ============================================================
# 2. POST /api/projects/{id}/conversations — 创建会话
# ============================================================

def test_create_conversation_returns_201():
    """创建会话返回 201 + ConversationOut schema。"""
    proj = _create_project()
    res = client.post(
        f"/api/projects/{proj['id']}/conversations",
        json={"title": "我的会话"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["id"] > 0
    assert data["project_id"] == proj["id"]
    assert data["title"] == "我的会话"
    assert "created_at" in data
    assert "updated_at" in data


def test_create_conversation_default_title():
    """不传 title 时使用默认值「新会话」。"""
    proj = _create_project()
    res = client.post(
        f"/api/projects/{proj['id']}/conversations",
        json={},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201
    assert res.json()["title"] == "新会话"


def test_create_conversation_project_not_found():
    """项目不存在返回 404。"""
    res = client.post(
        "/api/projects/99999/conversations",
        json={"title": "新会话"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 404


def test_create_conversation_limit_exceeded():
    """超过 MAX_CONVERSATIONS_PER_PROJECT 时返回 429。"""
    from routers.chat_router import MAX_CONVERSATIONS_PER_PROJECT

    proj = _create_project()
    for i in range(MAX_CONVERSATIONS_PER_PROJECT):
        _create_conversation(proj["id"], title=f"会话 {i + 1}")
    # 第 11 个应被拒绝
    res = client.post(
        f"/api/projects/{proj['id']}/conversations",
        json={"title": "超限会话"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 429
    assert "活跃会话" in res.json()["detail"]


# ============================================================
# 3. GET /api/projects/{id}/conversations — 列出会话
# ============================================================

def test_list_conversations_empty():
    """新项目应返回空列表。"""
    proj = _create_project()
    res = client.get(
        f"/api/projects/{proj['id']}/conversations",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 0
    assert data["items"] == []


def test_list_conversations_returns_items():
    """创建会话后列表应包含。"""
    proj = _create_project()
    _create_conversation(proj["id"], "会话 1")
    _create_conversation(proj["id"], "会话 2")
    res = client.get(
        f"/api/projects/{proj['id']}/conversations",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2
    titles = [item["title"] for item in data["items"]]
    assert "会话 1" in titles
    assert "会话 2" in titles


def test_list_conversations_excludes_deleted():
    """软删除的会话不应出现在列表中。"""
    proj = _create_project()
    conv1 = _create_conversation(proj["id"], "保留的会话")
    conv2 = _create_conversation(proj["id"], "删除的会话")
    # 删除 conv2
    del_res = client.delete(
        f"/api/conversations/{conv2['id']}",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert del_res.status_code == 200
    # 列表应只包含 conv1
    res = client.get(
        f"/api/projects/{proj['id']}/conversations",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["items"][0]["title"] == "保留的会话"


def test_list_conversations_project_not_found():
    """项目不存在返回 404。"""
    res = client.get(
        "/api/projects/99999/conversations",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 404


# ============================================================
# 4. GET /api/conversations/{id}/messages — 列出消息
# ============================================================

def test_list_messages_empty_for_new_conversation():
    """新会话应返回空消息列表。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    res = client.get(
        f"/api/conversations/{conv['id']}/messages",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 0
    assert data["items"] == []


def test_list_messages_not_found():
    """会话不存在返回 404。"""
    res = client.get(
        "/api/conversations/99999/messages",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 404


def test_list_messages_returns_history(demo_mode):
    """demo 模式下提问后，消息历史应包含 user + assistant 两条。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    # 提问
    ask_res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "项目当前状态如何？"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert ask_res.status_code == 200
    # 列出消息
    res = client.get(
        f"/api/conversations/{conv['id']}/messages",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2, f"应有 user + assistant 两条消息，实际 {data['count']}"
    roles = [m["role"] for m in data["items"]]
    assert roles == ["user", "assistant"]
    # user 消息内容
    assert data["items"][0]["content"] == "项目当前状态如何？"
    # assistant 消息内容非空
    assert data["items"][1]["content"], "assistant 消息内容不应为空"
    # demo 模式下 is_fallback=False
    assert data["items"][1]["is_fallback"] is False, "demo 模式不应标记 fallback"


# ============================================================
# 5. POST /api/conversations/{id}/ask — SSE 流式回答
# ============================================================

def test_ask_demo_mode_streams_delta_and_done(demo_mode):
    """demo 模式下：SSE 应产出多个 delta 事件 + 一个 done 事件。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "项目状态如何？"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    body = res.text
    assert "event: delta" in body, "应包含 delta 事件"
    assert "event: done" in body, "应包含 done 事件"

    events = _parse_sse_events(body)
    event_types = [e[0] for e in events]
    assert "delta" in event_types
    assert event_types[-1] == "done", f"最后一个事件应为 done，实际: {event_types[-1]}"
    # done 事件应包含 message_id 和 is_fallback
    done_data = events[-1][1]
    assert "message_id" in done_data
    assert "is_fallback" in done_data
    assert done_data["is_fallback"] is False, "demo 模式不应标记 fallback"


def test_ask_demo_mode_aggregates_delta_content(demo_mode):
    """demo 模式下：所有 delta 拼接后应等于完整回答。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "下一步该做什么？"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    events = _parse_sse_events(res.text)
    delta_content = "".join(e[1].get("delta", "") for e in events if e[0] == "delta")
    assert delta_content, "delta 拼接后内容不应为空"
    # demo 模式回答应包含项目名或预设关键词
    assert "项目" in delta_content or "AI" in delta_content, \
        f"demo 模式回答应包含项目/AI 关键词，实际: {delta_content[:100]}"


def test_ask_fallback_when_ai_not_configured():
    """无 AI 配置且非 demo 模式：应 yield 降级文本 + is_fallback=True。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "项目状态如何？"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    events = _parse_sse_events(res.text)
    event_types = [e[0] for e in events]
    assert "delta" in event_types
    assert event_types[-1] == "done"
    # done 事件的 is_fallback 应为 True
    done_data = events[-1][1]
    assert done_data["is_fallback"] is True, "无 AI 配置应标记 is_fallback=True"
    # delta 内容应包含降级关键词
    delta_content = "".join(e[1].get("delta", "") for e in events if e[0] == "delta")
    assert "AI 未启用" in delta_content or "规则引擎" in delta_content, \
        f"降级文本应包含 AI 未启用/规则引擎关键词，实际: {delta_content[:200]}"


def test_ask_conversation_not_found():
    """会话不存在返回 404。"""
    res = client.post(
        "/api/conversations/99999/ask",
        json={"question": "项目状态如何？"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 404


def test_ask_persists_user_message_before_streaming(demo_mode):
    """user 消息应在流式开始前持久化（即使 SSE 中断也能保留）。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    # 提问
    ask_res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "项目当前状态如何？"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert ask_res.status_code == 200
    # 列出消息应能看到 user 消息
    res = client.get(
        f"/api/conversations/{conv['id']}/messages",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    user_msgs = [m for m in data["items"] if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "项目当前状态如何？"


def test_ask_auto_updates_title_on_first_question(demo_mode):
    """首次提问后会话标题应从「新会话」自动更新为问题前 30 字。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])  # 默认 title="新会话"
    question = "这个项目的下一步应该优先做什么？"
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": question},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    # 列出会话验证标题更新
    list_res = client.get(
        f"/api/projects/{proj['id']}/conversations",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert list_res.status_code == 200
    items = list_res.json()["items"]
    assert len(items) == 1
    updated_title = items[0]["title"]
    assert updated_title != "新会话", f"标题应已更新，仍是默认值: {updated_title}"
    # 标题应是问题前 30 字（截断后可能加 …）
    assert question[:20] in updated_title, \
        f"标题应包含问题前 20 字，实际: {updated_title}"


def test_ask_preserves_custom_title(demo_mode):
    """自定义标题的会话提问后标题不应被覆盖。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"], title="我的专属会话")
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "项目状态如何？"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    list_res = client.get(
        f"/api/projects/{proj['id']}/conversations",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    items = list_res.json()["items"]
    assert items[0]["title"] == "我的专属会话", \
        f"自定义标题不应被覆盖，实际: {items[0]['title']}"


def test_ask_truncates_long_title(demo_mode):
    """超长问题标题应截断到 30 字 + 「…」后缀。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    long_question = "这是一个非常非常长的问题" + "用于测试标题截断逻辑" * 10 + "?"
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": long_question},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    list_res = client.get(
        f"/api/projects/{proj['id']}/conversations",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    title = list_res.json()["items"][0]["title"]
    assert title.endswith("…"), f"长问题标题应以 … 结尾，实际: {title}"
    assert len(title) <= 31, f"标题长度应 <= 31（30 字 + …），实际: {len(title)}"


# ============================================================
# 6. DELETE /api/conversations/{id} — 软删除会话
# ============================================================

def test_delete_conversation_soft_deletes():
    """DELETE 应软删除会话（不真正移除，但列表过滤）。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"], "待删除会话")
    res = client.delete(
        f"/api/conversations/{conv['id']}",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["deleted"] is True
    # 列表应不包含
    list_res = client.get(
        f"/api/projects/{proj['id']}/conversations",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    items = list_res.json()["items"]
    assert all(item["id"] != conv["id"] for item in items), "软删除后不应出现在列表"


def test_delete_conversation_not_found():
    """删除不存在的会话返回 404。"""
    res = client.delete(
        "/api/conversations/99999",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 404


def test_deleted_conversation_messages_inaccessible():
    """软删除后访问消息应返回 404。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    # 删除
    client.delete(
        f"/api/conversations/{conv['id']}",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    # 访问消息应 404
    res = client.get(
        f"/api/conversations/{conv['id']}/messages",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 404


# ============================================================
# 7. Schema 校验
# ============================================================

def test_ask_rejects_empty_question():
    """空 question 应返回 422。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": ""},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422


def test_ask_rejects_whitespace_question():
    """纯空格 question 应返回 422（strip 后为空）。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "   "},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422


def test_ask_rejects_oversized_question():
    """超过 2000 字的 question 应返回 422。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "x" * 2001},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422


def test_create_conversation_rejects_oversized_title():
    """超过 200 字的 title 应返回 422。"""
    proj = _create_project()
    res = client.post(
        f"/api/projects/{proj['id']}/conversations",
        json={"title": "x" * 201},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422


def test_ask_rejects_unknown_field():
    """未知字段应返回 422（extra="forbid"）。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "测试", "unknown_field": "value"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 422


# ============================================================
# 8. 多轮对话上下文（持久化 + 历史）
# ============================================================

def test_multi_turn_conversation_accumulates_messages(demo_mode):
    """多轮对话：连续提问应累积消息（user1, assistant1, user2, assistant2）。"""
    proj = _create_project()
    conv = _create_conversation(proj["id"])
    # 第 1 轮
    r1 = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "项目状态如何？"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert r1.status_code == 200
    # 第 2 轮
    r2 = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "下一步该做什么？"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert r2.status_code == 200
    # 列出消息应有 4 条
    res = client.get(
        f"/api/conversations/{conv['id']}/messages",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 4, f"应有 4 条消息（2 user + 2 assistant），实际 {data['count']}"
    roles = [m["role"] for m in data["items"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    # 验证两条 user 消息内容
    user_msgs = [m for m in data["items"] if m["role"] == "user"]
    assert user_msgs[0]["content"] == "项目状态如何？"
    assert user_msgs[1]["content"] == "下一步该做什么？"


def test_message_count_limit_returns_429(demo_mode):
    """超过 MAX_MESSAGES_PER_CONVERSATION 时返回 429。"""
    from routers.chat_router import MAX_MESSAGES_PER_CONVERSATION

    # 这是软上限测试：我们不真的发 200 次提问（太慢），
    # 而是直接向数据库插入接近上限的消息，再验证下一次 ask 返回 429。
    # 但为了测试稳定性，这里直接用 mock 方式：插入 MAX_MESSAGES_PER_CONVERSATION 条
    # 然后验证 ask 返回 429。
    from database import SessionLocal
    from models import ChatMessage

    proj = _create_project()
    conv = _create_conversation(proj["id"])
    # 直接插库：填充到上限
    db = SessionLocal()
    try:
        for i in range(MAX_MESSAGES_PER_CONVERSATION):
            db.add(ChatMessage(
                conversation_id=conv["id"],
                role="user" if i % 2 == 0 else "assistant",
                content=f"测试消息 {i}",
                is_fallback=False,
            ))
        db.commit()
    finally:
        db.close()
    # 下一次 ask 应返回 429
    res = client.post(
        f"/api/conversations/{conv['id']}/ask",
        json={"question": "再问一次"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 429
    assert "上限" in res.json()["detail"]


# ============================================================
# 9. 路由注册验证（main.py include_router）
# ============================================================

def test_chat_router_is_registered():
    """main.py 应注册 chat_router（路由表包含 /api/conversations 路径）。"""
    from main import app as _app
    paths = [route.path for route in _app.routes]
    conversation_paths = [p for p in paths if "/conversations" in p]
    assert len(conversation_paths) >= 3, \
        f"应至少注册 3 条 conversations 路由（create/list/messages/ask/delete），实际: {conversation_paths}"
    # 关键路径检查
    assert any("/api/projects/{project_id}/conversations" in p for p in paths), \
        "应注册 POST/GET /api/projects/{project_id}/conversations"
    assert any("/api/conversations/{conversation_id}/messages" in p for p in paths), \
        "应注册 GET /api/conversations/{conversation_id}/messages"
    assert any("/api/conversations/{conversation_id}/ask" in p for p in paths), \
        "应注册 POST /api/conversations/{conversation_id}/ask"
    assert any("/api/conversations/{conversation_id}" in p for p in paths), \
        "应注册 DELETE /api/conversations/{conversation_id}"


def test_sse_path_pattern_includes_conversations_ask():
    """SSE 白名单正则应匹配 /api/conversations/{id}/ask 路径。"""
    from main import SSE_PATH_PATTERN
    assert SSE_PATH_PATTERN.match("/api/conversations/123/ask"), \
        "SSE 白名单应匹配 /api/conversations/123/ask"
    assert SSE_PATH_PATTERN.match("/api/conversations/123/ask/"), \
        "SSE 白名单应匹配带尾部斜杠的 /api/conversations/123/ask/"
    # 原 /api/projects/{id}/events 仍应匹配
    assert SSE_PATH_PATTERN.match("/api/projects/123/events"), \
        "SSE 白名单仍应匹配 /api/projects/123/events"
    # 非白名单路径不应匹配
    assert not SSE_PATH_PATTERN.match("/api/conversations/123/messages"), \
        "非 SSE 路径不应被白名单匹配"
