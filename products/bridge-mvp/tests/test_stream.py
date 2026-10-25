# tests/test_stream.py — 阶段2.7 流式响应测试
# 覆盖：
#   - 演示模式流式输出（按切片 yield）
#   - AI 未配置降级到规则引擎
#   - 项目不存在返回 404
#   - SSE 事件格式（delta / done / fallback）
# V1 修复：删除模块级 os.environ.setdefault，由 conftest.py autouse fixture 统一注入
import pytest
from fastapi.testclient import TestClient

from auth import CSRF_COOKIE_NAME
from conftest import ADMIN_AUTH, CSRF_HEADERS
from main import app

client = TestClient(app)
client.cookies.set(CSRF_COOKIE_NAME, CSRF_HEADERS["X-CSRF-Token"])


@pytest.fixture
def demo_mode(monkeypatch):
    """局部启用演示模式，测试结束后自动恢复，避免污染其他测试。

    注意：不使用 importlib.reload —— is_demo_mode() 动态读取 os.environ，
    reload 会导致 projects.py 持有的函数引用与 ai 模块不一致，引发跨测试污染。
    """
    monkeypatch.setenv("BRIDGE_AI_DEMO_MODE", "1")
    yield
    # monkeypatch 自动清理 env，无需手动 delenv


def _create_project(name="流式测试项目"):
    res = client.post(
        "/api/projects",
        json={"name": name, "description": "测试流式生成"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_stream_generate_demo_mode_yields_delta_and_done(demo_mode):
    """演示模式下流式生成：应产出多个 delta 事件 + 一个 done 事件。"""
    proj = _create_project()
    res = client.post(
        f"/api/projects/{proj['id']}/handoffs/stream-generate",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    body = res.text
    # 应包含 delta 事件和 done 事件
    assert "event: delta" in body
    assert "event: done" in body
    # 演示模式摘要内容应包含 AI 演示标记
    assert "AI 演示" in body or "AI" in body

    # V13 修复：按 \n\n 分割 body 解析 SSE 事件，验证事件列表长度/顺序/内容
    events = [e.strip() for e in body.split("\n\n") if e.strip()]
    assert len(events) >= 2, f"SSE 事件数应 >= 2（delta + done），实际: {len(events)}"
    # 验证事件顺序：delta 事件在前，done 事件在后
    event_types = []
    for ev in events:
        for line in ev.split("\n"):
            if line.startswith("event:"):
                event_types.append(line.split(":", 1)[1].strip())
                break
    assert "delta" in event_types, f"事件列表应包含 delta，实际: {event_types}"
    assert event_types[-1] == "done", f"最后一个事件应为 done，实际: {event_types[-1]}"


def test_stream_generate_project_not_found():
    """项目不存在返回 404。"""
    res = client.post(
        "/api/projects/99999/handoffs/stream-generate",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 404


def test_stream_generate_requires_auth():
    """未认证返回 401。"""
    proj = _create_project()
    res = client.post(f"/api/projects/{proj['id']}/handoffs/stream-generate")
    assert res.status_code == 401


def test_stream_generate_fallback_when_ai_not_configured():
    """AI 未配置且非演示模式时，降级到规则引擎一次性返回。

    默认无 BRIDGE_AI_DEMO_MODE → generate_handoff_summary_stream 不 yield 任何内容 →
    端点检测到 collected 为空，降级到 _build_handoff_summary 一次性 yield。
    """
    proj = _create_project()
    res = client.post(
        f"/api/projects/{proj['id']}/handoffs/stream-generate",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 200
    body = res.text
    # 应有降级标记
    assert "fallback" in body
    assert "event: delta" in body
    assert "event: done" in body

    # V13 修复：按 \n\n 分割解析 SSE 事件，验证 fallback 事件内容
    events = [e.strip() for e in body.split("\n\n") if e.strip()]
    assert len(events) >= 2, f"fallback 模式事件数应 >= 2，实际: {len(events)}"
    # 验证至少有一个 delta 事件包含数据
    has_delta_data = any(
        "data:" in ev and "event: delta" in ev for ev in events
    )
    assert has_delta_data, "fallback 模式应包含带 data 的 delta 事件"
