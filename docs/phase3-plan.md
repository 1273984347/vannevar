# Vannevar 阶段 3 详细方案：用户验证数据 + 生产就绪

> 基于阶段 1（统一账号 + 数据协同）和阶段 2（Agent + 企业版 + 端侧 AI）的增量演进。
> 早期评审核心反馈：三产品均无用户验证数据，是关键弱点。
> Phase 3 聚焦：端到端用户场景测试 + 性能基准 + 生产部署准备。

## 执行顺序

```
阶段 3
  ├─ 3.1 端到端用户场景测试（三产品协同流程 → 测试报告）
  ├─ 3.2 性能基准测试（Bridge API + SQLite WAL → 性能报告）
  ├─ 3.3 生产部署准备（Dockerfile + docker-compose + 部署文档）
  ├─ 3.4 API 文档导出 + 用户指南精简
  └─ 3.5 DRL 三阶段回归 + 复利经验沉淀
```

---

## 3.1 端到端用户场景测试

### 目标

模拟真实用户旅程，产出可展示的端到端测试数据报告，弥补"无用户验证数据"弱点。

### 测试场景（6 个核心用户旅程）

| 场景 | 产品 | 用户旅程 | 验证点 |
|------|------|---------|--------|
| E2E-1 | Bridge | 管理员创建项目 → 志愿者扫码加入 → 任务协作 → 交接 | 全流程无错误 + 数据持久化 |
| E2E-2 | Bridge | 注册 → 登录 → JWT 鉴权 → 创建个人项目 → 登出 | 认证流程完整 + JWT 过期处理 |
| E2E-3 | Bridge | 创建组织 → 邀请成员 → 加入团队 → 共享项目 → 权限隔离 | 多租户权限正确 |
| E2E-4 | Focus | 捕获标签页 → 保存快照 → 恢复快照 → AI 摘要 | 扩展核心功能可用 |
| E2E-5 | Bridge+Focus | Focus 捕获 → sync push → Bridge pull → 数据一致 | 跨产品同步正确 |
| E2E-6 | Bridge | Agent 建议生成 → SSE 推送 → 客户端 ACK → 状态流转 | Agent 主动服务可用 |

### 新增文件

- `products/bridge-mvp/tests/e2e/test_user_journey.py` — 6 个用户旅程测试
- `products/bridge-mvp/tests/e2e/test_report_generator.py` — 测试报告生成器（输出 Markdown 报告）

### 报告产出

- `worklog/e2e-test-report.md` — 端到端测试报告（含通过率、耗时、覆盖场景）

---

## 3.2 性能基准测试

### 目标

产出 Bridge API 性能数据，证明系统可承载公益团队规模使用。

### 基准测试项

| 测试项 | 方法 | 目标 |
|--------|------|------|
| API 响应时间 | 100 次请求测 P50/P95/P99 | P95 < 100ms |
| 并发写入 | 10 并发 × 50 次项目创建 | 无 429/500 |
| SQLite WAL 性能 | 读写混合负载 | 读不阻塞写 |
| 限流验证 | 突发请求触发 429 | 限流正确生效 |
| AI 演示模式延迟 | 流式首字延迟 | < 500ms |

### 新增文件

- `products/bridge-mvp/tests/test_performance.py` — 性能基准测试
- `worklog/performance-report.md` — 性能测试报告

---

## 3.3 生产部署准备

### 目标

提供 Docker 化部署方案，支持一键启动。

### 新增文件

- `products/bridge-mvp/Dockerfile` — Bridge 容器镜像
- `docker-compose.yml` — 一键编排（Bridge + 持久化卷）
- `products/bridge-mvp/.dockerignore` — 构建排除
- `docs/deploy-guide.md` — 部署指南

### Dockerfile 设计

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV BRIDGE_DEBUG=0
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker-compose 设计

```yaml
version: "3.8"
services:
  bridge:
    build: ./products/bridge-mvp
    ports:
      - "8000:8000"
    volumes:
      - bridge-data:/app/data
    environment:
      - BRIDGE_ADMIN_PASS=${BRIDGE_ADMIN_PASS:-changeme}
      - BRIDGE_JWT_SECRET=${BRIDGE_JWT_SECRET:-}
    restart: unless-stopped
volumes:
  bridge-data:
```

---

## 3.4 API 文档导出 + 用户指南

### 目标

导出 OpenAPI 静态文档，提供精简用户指南。

### 新增文件

- `docs/api-reference.md` — 从 OpenAPI schema 导出的 API 参考文档
- `docs/user-guide.md` — 三产品快速上手指南

---

## 3.5 DRL 三阶段回归

### Phase 1：回归测试
- Bridge 全量单元测试 218+
- 新增端到端测试 6 场景
- 新增性能基准测试
- JS 语法校验

### Phase 2：落地审计
- Dockerfile 构建验证
- docker-compose 编排验证
- 文档完整性检查

### Phase 3：复利经验沉淀
- 新增复利经验 #237+
- CHANGELOG W111 记录

---

## 工作量预估

| 子阶段 | 新增代码/文档行 | 复杂度 |
|--------|---------------|--------|
| 3.1 端到端测试 | ~400 | 中 |
| 3.2 性能基准 | ~300 | 中 |
| 3.3 Docker 部署 | ~100 | 低 |
| 3.4 文档 | ~200 | 低 |
| 3.5 DRL | — | — |
| **合计** | **~1000** | — |
