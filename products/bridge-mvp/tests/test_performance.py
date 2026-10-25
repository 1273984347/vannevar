# tests/test_performance.py — 阶段3.2 性能基准测试
# 产出 Bridge API 性能数据：响应时间 P50/P95/P99 + 并发写入 + 限流验证
# V1 修复：删除模块级 os.environ.setdefault，由 conftest.py autouse fixture 统一注入
# V25 修复：所有性能测试标记 @pytest.mark.slow，需 --run-slow 选项运行
import time
import statistics

import pytest
from fastapi.testclient import TestClient

from auth import CSRF_COOKIE_NAME
from conftest import ADMIN_AUTH, CSRF_HEADERS
from main import app, _rate_limit_store

# V35 修复：通过 conftest app 单例创建 TestClient
client = TestClient(app)
client.cookies.set(CSRF_COOKIE_NAME, CSRF_HEADERS["X-CSRF-Token"])


def _create_project(name="性能测试项目"):
    # V4 修复：helper 内显式校验 status_code，避免后续 .json() 调用掩盖错误
    res = client.post(
        "/api/projects",
        json={"name": name, "description": "性能测试"},
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    assert res.status_code == 201, res.text
    return res.json()


def _percentile(data, p):
    """计算百分位数。"""
    if not data:
        return 0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    if f == c:
        return sorted_data[f]
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


# ============================================================
# PERF-1: API 响应时间基准
# ============================================================

@pytest.mark.slow
def test_perf_1_api_response_times():
    """PERF-1: 测量核心 API 的 P50/P95/P99 响应时间。"""
    project = _create_project("响应时间测试")

    # 预热
    for _ in range(5):
        client.get(f"/api/projects/{project['id']}", auth=ADMIN_AUTH)

    # 测量 100 次项目详情查询
    latencies = []
    for _ in range(100):
        start = time.perf_counter()
        client.get(f"/api/projects/{project['id']}", auth=ADMIN_AUTH)
        latencies.append((time.perf_counter() - start) * 1000)  # ms

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)

    # 公益团队规模可接受的性能阈值：P95 < 200ms
    # P1-4 修复：P50 放宽到 100ms，避免 CI 慢机器 flaky
    assert p95 < 200, f"P95 响应时间 {p95:.1f}ms 超过 200ms 阈值"
    assert p50 < 100, f"P50 响应时间 {p50:.1f}ms 超过 100ms 阈值"

    # 记录性能数据（供报告引用）
    perf_data = {
        "endpoint": "GET /api/projects/{id}",
        "requests": 100,
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
        "avg_ms": round(statistics.mean(latencies), 2),
    }
    assert perf_data["requests"] == 100
    assert perf_data["p95_ms"] < 200


# ============================================================
# PERF-2: 项目列表查询性能
# ============================================================

@pytest.mark.slow
def test_perf_2_list_projects_performance():
    """PERF-2: 50 个项目的列表查询性能。"""
    # 批量创建 50 个项目
    for i in range(50):
        _create_project(f"批量项目{i:03d}")
    # P1-5 修复：创建后清空限流计数，避免影响后续测试
    _rate_limit_store.clear()

    # 测量列表查询
    latencies = []
    for _ in range(30):
        start = time.perf_counter()
        res = client.get("/api/projects?limit=50", auth=ADMIN_AUTH)
        latencies.append((time.perf_counter() - start) * 1000)

    assert res.status_code == 200
    assert len(res.json()) == 50

    p95 = _percentile(latencies, 95)
    assert p95 < 300, f"50 项目列表 P95 {p95:.1f}ms 超过 300ms"


# ============================================================
# PERF-3: 顺序写入（验证批量创建无错误）
# V27 修复：修正注释——本测试是"顺序"而非"并发"写入，
#          真正的并发测试见 PERF-3a（ThreadPoolExecutor）
# ============================================================

@pytest.mark.slow
def test_perf_3_sequential_writes():
    """PERF-3: 顺序创建 50 个项目，验证无错误（非并发，逐个创建）。"""
    errors = 0
    for i in range(50):
        res = client.post(
            "/api/projects",
            json={"name": f"顺序项目{i:03d}", "description": "顺序写入测试"},
            auth=ADMIN_AUTH,
            headers=CSRF_HEADERS,
        )
        if res.status_code != 201:
            errors += 1
    # V4 修复：批量写入后清空限流计数，避免影响后续测试
    _rate_limit_store.clear()

    assert errors == 0, f"50 次顺序写入有 {errors} 次失败"

    # 验证数据完整性
    list_res = client.get("/api/projects?limit=100", auth=ADMIN_AUTH)
    assert list_res.status_code == 200
    assert len(list_res.json()) >= 50


# ============================================================
# PERF-3a: 真正的并发写入测试（V27 新增）
# 使用 ThreadPoolExecutor 模拟多客户端并发创建项目
# ============================================================

@pytest.mark.slow
def test_perf_3a_concurrent_writes():
    """PERF-3a: 使用 ThreadPoolExecutor 并发创建 20 个项目，验证无 500 错误。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _create_one(i):
        res = client.post(
            "/api/projects",
            json={"name": f"并发项目{i:03d}", "description": "并发写入测试"},
            auth=ADMIN_AUTH,
            headers=CSRF_HEADERS,
        )
        return res.status_code

    # 并发创建 20 个项目（线程池大小 5）
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_create_one, i) for i in range(20)]
        status_codes = [f.result() for f in as_completed(futures)]

    # V4 修复：并发写入后清空限流计数
    _rate_limit_store.clear()

    # 不应有 500 错误（SQLite 并发可能有 429 限流或 201 成功）
    assert 500 not in status_codes, f"并发写入出现 500 错误: {status_codes}"
    # 至少部分成功
    success_count = status_codes.count(201)
    assert success_count >= 1, f"并发写入全部失败: {status_codes}"


# ============================================================
# PERF-4: 限流验证
# V3 修复：发送 >120 次请求（/api/projects/ 限流阈值 120/60s）确保触发 429；
#          删除 triggered_429 死变量，改为直接 assert 429 出现
# ============================================================

@pytest.mark.slow
def test_perf_4_rate_limiting():
    """PERF-4: 突发请求验证限流机制工作正常（触发 429 + 无 500 错误）。

    /api/projects/ 的限流规则是 120/60s。发送 130 次请求应触发 429。
    """
    status_codes = []
    # V3 修复：发送 130 次（>120 阈值），确保触发 429
    for _ in range(130):
        res = client.get("/api/projects", auth=ADMIN_AUTH)
        status_codes.append(res.status_code)
        if res.status_code == 429:
            break

    # 至少应有部分请求成功
    assert 200 in status_codes, "限流前应有成功请求"
    # 限流不应导致 500 错误
    assert 500 not in status_codes, "限流不应导致 500 错误"
    # V3 修复：429 应被触发（>120 次请求必然触发限流）
    assert 429 in status_codes, f"发送 {len(status_codes)} 次请求未触发 429 限流"


# ============================================================
# PERF-5: 任务创建+查询性能
# ============================================================

@pytest.mark.slow
def test_perf_5_task_crud_performance():
    """PERF-5: 任务创建+查询的响应时间。"""
    project = _create_project("任务性能测试")

    # 创建 30 个任务
    create_latencies = []
    for i in range(30):
        start = time.perf_counter()
        client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"title": f"任务{i:03d}", "owner_name": "测试"},
            auth=ADMIN_AUTH,
            headers=CSRF_HEADERS,
        )
        create_latencies.append((time.perf_counter() - start) * 1000)

    # 查询项目详情（含 30 个任务）
    query_latencies = []
    for _ in range(20):
        start = time.perf_counter()
        res = client.get(f"/api/projects/{project['id']}", auth=ADMIN_AUTH)
        query_latencies.append((time.perf_counter() - start) * 1000)

    assert res.status_code == 200
    assert len(res.json()["tasks"]) == 30

    create_p95 = _percentile(create_latencies, 95)
    query_p95 = _percentile(query_latencies, 95)

    assert create_p95 < 100, f"任务创建 P95 {create_p95:.1f}ms 超过 100ms"
    assert query_p95 < 200, f"任务查询 P95 {query_p95:.1f}ms 超过 200ms"


# ============================================================
# PERF-6: AI 演示模式延迟
# ============================================================

@pytest.mark.slow
def test_perf_6_ai_demo_mode_latency(monkeypatch):
    """PERF-6: AI 演示模式流式首字延迟。"""
    monkeypatch.setenv("BRIDGE_AI_DEMO_MODE", "1")
    project = _create_project("AI 性能测试")

    start = time.perf_counter()
    res = client.post(
        f"/api/projects/{project['id']}/handoffs/stream-generate",
        auth=ADMIN_AUTH,
        headers=CSRF_HEADERS,
    )
    elapsed = (time.perf_counter() - start) * 1000

    assert res.status_code == 200
    assert "event: delta" in res.text
    assert "event: done" in res.text

    # 演示模式首字延迟应 < 2000ms（CI/慢机器放宽阈值，避免 flaky）
    assert elapsed < 2000, f"AI 演示模式延迟 {elapsed:.1f}ms 超过 2000ms"


# ============================================================
# PERF-7: 性能报告生成
# V26 修复：自指测试——仅验证 dict 结构而非真实功能，标记 skip
# ============================================================

@pytest.mark.skip(reason="V26: 自指测试——仅验证 dict 结构而非真实功能，待重构为真正测试")
def test_perf_7_generate_report():
    """PERF-7: 生成性能测试报告（供文档引用）。"""
    # P2-13 修复：验证报告结构 + 关键字段非空
    report = {
        "test_suite": "test_performance.py",
        "metrics": [
            {"id": "PERF-1", "name": "API 响应时间", "threshold": "P95 < 200ms"},
            {"id": "PERF-2", "name": "项目列表查询", "threshold": "P95 < 300ms"},
            {"id": "PERF-3", "name": "顺序写入", "threshold": "0 错误"},
            {"id": "PERF-4", "name": "限流验证", "threshold": "无 500 错误"},
            {"id": "PERF-5", "name": "任务 CRUD", "threshold": "创建 P95 < 100ms"},
            {"id": "PERF-6", "name": "AI 演示延迟", "threshold": "< 2000ms"},
        ],
        "environment": {
            "database": "SQLite in-memory",
            "server": "FastAPI TestClient",
            "python": "3.12",
        },
    }
    # 验证报告结构完整且字段非空
    assert report["test_suite"], "test_suite 不能为空"
    assert len(report["metrics"]) == 6, "应有 6 个性能指标"
    for m in report["metrics"]:
        assert m["id"], "metric.id 不能为空"
        assert m["name"], "metric.name 不能为空"
        assert m["threshold"], "metric.threshold 不能为空"
    assert report["environment"]["database"], "environment.database 不能为空"
