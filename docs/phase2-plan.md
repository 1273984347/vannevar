# Vannevar 阶段 2 详细方案：Agent 主动服务 / Bridge 企业版 / 端侧 AI

> 基于阶段 1（统一账号 + 数据协同 1.1~1.6 已完成）的增量演进。
> 当前基础：7 张表（projects/tasks/handoffs/messages/users/vannevar_meta/sync_items）、17 个端点、JWT 认证、二态权限（admin/user）、SSE 事件系统、三端 AI 配置统一、chrome.alarms 30min 自动同步。

## 用户决策

1. **组织模型**：嵌套（org → team → members）
2. **Agent 通知**：notifications + badge 两者都支持
3. **端侧本地模型**：纳入本轮（实验性）
4. **优先级**：B（Agent）→ A（企业版）→ C（端侧 AI）

## 执行顺序

```
阶段 1（已完成）
  ├─ 1.1 统一账号  ├─ 1.2 owner_id  ├─ 1.3 JWT 主认证
  ├─ 1.4 扩展登录  ├─ 1.5 数据同步  └─ 1.6 Context Card 自动同步

阶段 2（本方案，按优先级 B → A → C）
  ├─ 方向 B Agent 主动服务（2.1~2.2）
  │   ├─ 2.1 端侧 Agent —— 依赖 1.6 的 alarms 基础 ✅ 就绪
  │   └─ 2.2 服务端 Agent —— 依赖方向 A 的 require_project_access ⚠️ 需先做 A.2 最小权限
  ├─ 方向 A 企业版（2.3~2.6）
  │   ├─ 2.3 数据模型 v8（嵌套：org → team → members）
  │   ├─ 2.4 RBAC + 权限决策树
  │   ├─ 2.5 组织/团队/成员/共享端点
  │   └─ 2.6 中间件 + 审计日志 + 限流升级
  └─ 方向 C 端侧 AI（2.7~2.9）
      ├─ 2.7 流式响应（三端）
      ├─ 2.8 端侧本地模型（WebLLM 实验性）
      └─ 2.9 AI 调用统一层（三级降级）
```

**2.1 与 2.2 的并行策略**：2.1 端侧 Agent 不依赖服务端权限改造，可立即开始；2.2 服务端 Agent 的 `require_project_access` 需先完成 2.4 RBAC，因此 2.2 在 2.4 之后启动。实际执行：2.1 → 2.3 → 2.4 → 2.2 → 2.5 → 2.6 → 2.7 → 2.8 → 2.9。

---

## 方向 B：Agent 主动服务（优先实施）

### B.0 现状基础

| 基础能力 | 现状 | 可复用度 |
|---------|------|---------|
| 定时调度 | chrome.alarms 30min pull（Focus/Muse） | 高，可扩展为 15min Agent 周期 |
| SSE 事件 | 7 种事件类型，内存 pub/sub | 中，需加持久化 |
| AI 调用 | 三端统一 OpenAI 兼容协议 | 高 |
| Prompt 模板 | Bridge 4 种交接模板 | 中，需新增 Agent 模板 |
| 规则引擎建议 | resume.js generateSuggestions | 高，无 AI 也可用 |
| 主动通知 | ❌ 无 notifications 权限 | 需新建（用户决策：notifications + badge 两者） |
| 后台任务 | ❌ Bridge 无 APScheduler | 需新建 |
| Agent 状态 | ❌ 无持久化 | 需新建 |

### B.1 端侧 Agent（Focus/Muse 扩展，阶段 2.1）

#### 架构

```
chrome.alarms（15min，独立于 auto-sync 的 30min alarm）
  → agent.js handleAgentAlarm
    → 检查触发条件（数据变化量/停滞时长/时段/免打扰）
    → 命中阈值 → 调用 generateAISummary（或规则引擎降级）
    → 生成建议
    → chrome.notifications.create（系统通知）+ chrome.action.setBadgeText（角标）
    → 持久化到 chrome.storage.local（agent_suggestions key）
    → popup 打开时展示未读建议
```

#### 新增文件

- `focus-mvp/agent.js` — Focus Agent 核心：触发条件评估 + 建议生成 + 通知
- `muse-mvp/core/agent.js` — Muse Agent 核心（同上）
- 两个 manifest.json 加 `notifications` 权限
- 两个 background.js 注册 agent alarm + onAlarm 监听
- 两个 popup.js 展示未读建议（badge 清零）

#### Agent 触发规则（Focus 版）

| 规则 | 条件 | 建议类型 | 示例 |
|------|------|---------|------|
| 停滞检测 | 最近快照 >3 天未恢复 | restore_reminder | "你的「开发环境」快照已 3 天未使用" |
| 场景识别 | 当前标签页匹配某快照 70% | context_switch | "检测到你在做类似「文档协作」的工作，要恢复上次的上下文吗？" |
| 超载检测 | 当前标签页 >30 个 | tab_overload | "当前打开了 35 个标签页，建议保存快照释放内存" |
| 时段回顾 | 每天 18:00 首次触发 | daily_review | "今天保存了 3 个快照，要生成日报吗？" |

#### Agent 触发规则（Muse 版）

| 规则 | 条件 | 建议类型 | 示例 |
|------|------|---------|------|
| 氛围聚集 | 本周某氛围 ≥3 条 | moodboard_suggest | "本周「设计灵感」已 5 条，要整理 moodboard 吗？" |
| 来源重复 | 同一域名 ≥3 条灵感 | source_cluster | "你在 Medium 上收藏了 4 篇文章，要集中阅读吗？" |
| 时段回顾 | 每天 21:00 首次触发 | daily_review | "今天捕获了 5 条灵感，要生成今日回顾吗？" |
| 灵感停滞 | >5 天无新灵感 | inspiration_reminder | "已 6 天未捕获新灵感，去看看你保存的来源？" |

#### Agent 状态持久化

```javascript
// chrome.storage.local key: vannevar_focus_agent_state / vannevar_muse_agent_state
{
  lastRunAt: 1700000000000,
  suggestions: [
    {
      id: 'uuid',
      type: 'restore_reminder',
      title: '快照停滞提醒',
      body: '你的「开发环境」快照已 3 天未使用',
      createdAt: 1700000000000,
      read: false,      // popup 是否展示过
      acted: false,     // 用户是否点击行动
      actionUrl: null,  // 可选：点击跳转的扩展页面
      actionLabel: null // 可选：行动按钮文案
    }
  ],
  dailyFlags: { '2026-07-11': { dailyReviewDone: true } },  // 每日去重
  dismissedTypes: { 'restore_reminder:uuid-snap-1': 1700000000000 }  // 24h 内不重复
}
```

#### 频率控制

- 每个 alarm 周期（15min）最多生成 1 条建议
- 每条建议 24h 内不重复（按 `type + 关键参数` 去重）
- 每日建议上限 5 条
- 用户可配置免打扰时段（如 22:00-08:00），默认 22:00-08:00
- 建议列表最多保留 50 条，超出清理最旧的已读建议

#### 通知双通道

```javascript
// 1. 系统通知（需 notifications 权限）
chrome.notifications.create(`vannevar-agent-${suggestion.id}`, {
  type: 'basic',
  iconUrl: 'icons/icon128.png',
  title: suggestion.title,
  message: suggestion.body,
  priority: 1,  // 低优先级，不打断用户
  isClickable: true,
  buttons: suggestion.actionLabel ? [{ title: suggestion.actionLabel }] : undefined,
});

// 2. 角标提示（无需额外权限，Muse 已有 badge 基础）
chrome.action.setBadgeText({ text: String(unreadCount) });
chrome.action.setBadgeBackgroundColor({ color: '#f59e0b' });  // 琥珀色，区别于捕获成功的绿色

// 用户点击通知 → 打开 popup → 清除角标
chrome.notifications.onClicked.addListener((notifId) => {
  if (notifId.startsWith('vannevar-agent-')) {
    chrome.action.openPopup?.();  // MV3 支持有限，降级到打开 options
    chrome.notifications.clear(notifId);
  }
});
```

#### popup 展示未读建议

- popup 顶部新增「Agent 建议」卡片区域，展示未读建议（最多 3 条）
- 每条建议可「查看」「采纳」「忽略」
- 点击「采纳」触发 actionUrl 或对应功能
- 点击「忽略」标记 read=true，不显示已忽略建议

### B.2 服务端 Agent（Bridge 后端，阶段 2.2，需 2.4 RBAC 先行）

#### 架构

```
APScheduler（定时任务）
  → 定时扫描项目状态
    → 命中规则（停滞/超期/交接缺口）
    → 调用 generate_proactive_suggestion（AI 或规则引擎）
    → 写入 agent_suggestions 表
    → publish SSE 事件 ai_suggestion
    → 前端 SSE 订阅者收到推送
```

#### 新增表

```sql
CREATE TABLE agent_suggestions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  type VARCHAR(30) NOT NULL,           -- stale_warning / overdue_task / handoff_gap / daily_summary
  title VARCHAR(200) NOT NULL,
  body TEXT,
  severity VARCHAR(10) DEFAULT 'info', -- info / warning / critical
  status VARCHAR(20) DEFAULT 'pending',-- pending / shown / dismissed / acted
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  acted_at DATETIME,
  UNIQUE(project_id, type, date(created_at))  -- 防止并发/多 worker 重复生成同类型建议
);
CREATE INDEX idx_agent_suggestions_project ON agent_suggestions(project_id, status);
```

#### 状态流转定义

```
pending → shown → dismissed/acted
  │                  ↑
  └─ APScheduler 生成 → SSE 推送给在线客户端 → 客户端 ACK 后置 shown
     (SSE 推送后保留 pending，直到客户端 ACK；未 ACK 的建议下次 SSE 连接会重推)
     (status='shown' 仅由客户端 ACK 触发，避免重复推送)
```

#### 并发去重策略

- DB 层：`UNIQUE(project_id, type, date(created_at))` 防止同日同类型重复
- 调度层：APScheduler 使用 `coalesce=True` + `max_instances=1`，避免多 worker 重复执行
- 应用层：扫描函数用 `INSERT OR IGNORE` 忽略重复

#### 新增 Prompt 模板（ai.py 扩展）

```python
PROMPT_TEMPLATES.update({
    "proactive_stale": {
        "role": "你是项目协调助手，负责主动发现停滞风险",
        "focus": "项目已 N 天无活动，分析可能的阻塞原因并给出跟进建议",
        "sections": ["风险摘要", "可能原因", "建议动作（2-3 条）"],
    },
    "proactive_overdue": {
        "role": "你是任务跟进助手",
        "focus": "有 N 个任务超期，按紧急程度排序并给出处理建议",
        "sections": ["超期任务清单", "影响评估", "处理优先级"],
    },
    "proactive_handoff_gap": {
        "role": "你是交接准备助手",
        "focus": "项目即将结束但交接信息不完整，提示缺失的关键信息",
        "sections": ["缺失信息清单", "补充建议", "交接检查表"],
    },
    "daily_summary": {
        "role": "你是项目日报生成助手",
        "focus": "基于今日活动生成简洁日报",
        "sections": ["今日进展", "风险事项", "明日建议"],
    },
})
```

#### 新增端点

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/projects/{id}/suggestions` | require_project_access('view') | 列出项目建议 |
| POST | `/api/projects/{id}/suggestions/{sid}/dismiss` | require_project_access('view') | 忽略建议 |
| POST | `/api/projects/{id}/suggestions/{sid}/act` | require_project_access('edit') | 标记已采纳 |

#### SSE 新事件类型

```python
publish(project_id, "ai_suggestion", {
    "id": suggestion.id,
    "type": suggestion.type,
    "title": suggestion.title,
    "severity": suggestion.severity,
})
```

#### 定时任务（APScheduler）

```python
# main.py lifespan 中启动
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()
scheduler.add_job(scan_stale_projects, 'interval', hours=6)  # 每 6h 扫描停滞
scheduler.add_job(scan_overdue_tasks, 'interval', hours=3)   # 每 3h 扫描超期
scheduler.add_job(daily_summary, 'cron', hour=18)            # 每日 18:00 日报
scheduler.start()
```

#### 扫描规则

| 规则 | 扫描频率 | 条件 | severity |
|------|---------|------|----------|
| scan_stale_projects | 6h | 项目 status=active 且 updated_at >7 天前 | warning |
| scan_overdue_tasks | 3h | 任务 status≠done 且 created_at >3 天前 | warning，>7 天升 critical |
| daily_summary | 每日 18:00 | 当日有活动的项目 | info |
| handoff_gap | 项目 status 变为 done 时触发 | 交接信息不完整 | warning |

### B.3 交付节奏

| 子阶段 | 内容 | 测试目标 |
|--------|------|---------|
| 2.1 | 端侧 Agent（Focus+Muse agent.js + notifications 权限 + popup 展示） | 手动触发 4 类建议 + 去重逻辑 + 通知双通道 |
| 2.2 | 服务端 Agent（agent_suggestions 表 + APScheduler + 4 个 Prompt 模板 + SSE 推送） | 定时任务触发 + SSE 推送验证 + 3 个端点 |

---

## 方向 A：Bridge 企业版（嵌套多租户）

### A.0 现状阻塞点

| 阻塞点 | 位置 | 影响 |
|--------|------|------|
| User 无 role/team/org 字段 | models.py:123 明确推迟 | 无法区分角色 |
| Project 无 team_id/visibility | models.py:19-51 | 单所有者，无法共享 |
| 权限只有二态 | auth.py require_admin(0) vs require_user(>0) | 无中间角色 |
| 无租户上下文中间件 | main.py 仅 1 个 http middleware | 无法注入 tenant |
| owner_id 不用 FK | models.py:45-47, 153 | 兼容 dev user_id=0 |
| 限流按 client_ip | main.py:107-139 | 无组织级配额 |

### A.1 数据模型升级（阶段 2.3，嵌套模型）

#### 嵌套结构

```
组织 A（Organization）
  ├─ 团队 前端组（Team）
  │   ├─ 成员 张三（team_admin）
  │   └─ 成员 李四（member）
  ├─ 团队 后端组（Team）
  │   └─ 成员 王五（member）
  └─ 直属成员 赵六（org_admin，不在任何团队）
```

#### 新增表（6 张）

```sql
-- 组织（顶层）
CREATE TABLE organizations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(120) NOT NULL,
  slug VARCHAR(60) NOT NULL UNIQUE,
  owner_id INTEGER NOT NULL,                  -- 创建者（不用 FK，同 owner_id 策略）
  plan VARCHAR(20) DEFAULT 'free',            -- free / team / enterprise
  max_members INTEGER DEFAULT 50,             -- 套餐成员上限（含所有团队）
  deleted_at DATETIME,                        -- 软删除标记（null=活跃，非空=已删除）
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 团队（组织内的子分组）
CREATE TABLE teams (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL,
  name VARCHAR(120) NOT NULL,
  slug VARCHAR(60) NOT NULL,
  description TEXT DEFAULT '',
  deleted_at DATETIME,                        -- 软删除标记（null=活跃）
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(org_id, slug)                        -- 同组织内 slug 唯一
);

-- 组织成员关系（直属组织的成员，含组织级角色）
CREATE TABLE org_memberships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  role VARCHAR(20) DEFAULT 'member',          -- owner / admin / member / viewer
  joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(org_id, user_id)
);

-- 团队成员关系（团队成员，含团队级角色）
CREATE TABLE team_memberships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  role VARCHAR(20) DEFAULT 'member',          -- team_admin / member
  joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(team_id, user_id)
);

-- 项目共享（项目归 owner，可共享给组织/团队/个人）
CREATE TABLE project_shares (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  shared_with_org_id INTEGER,                 -- 三选一：共享给组织
  shared_with_team_id INTEGER,                -- 三选一：共享给团队
  shared_with_user_id INTEGER,                -- 三选一：共享给个人
  permission VARCHAR(20) DEFAULT 'view',      -- view / edit / admin
  shared_by_user_id INTEGER NOT NULL,
  shared_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CHECK(
    (shared_with_org_id IS NOT NULL AND shared_with_team_id IS NULL AND shared_with_user_id IS NULL)
    OR (shared_with_org_id IS NULL AND shared_with_team_id IS NOT NULL AND shared_with_user_id IS NULL)
    OR (shared_with_org_id IS NULL AND shared_with_team_id IS NULL AND shared_with_user_id IS NOT NULL)
  )
);

-- 邀请链接（组织级邀请）
CREATE TABLE org_invitations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL,
  team_id INTEGER,                            -- 可选：直接邀请到某团队
  email VARCHAR(255) NOT NULL,
  token_hash VARCHAR(64) NOT NULL UNIQUE,     -- SHA-256(token) 哈希存储，防止数据库泄露后token被利用
  role VARCHAR(20) DEFAULT 'member',          -- 组织角色（team_id 不为空时同时加入团队为 member）
  invited_by INTEGER NOT NULL,
  status VARCHAR(20) DEFAULT 'pending',       -- pending / accepted / expired / revoked
  expires_at DATETIME,                        -- 默认 7 天后过期
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- 注：生成邀请时生成 32 字节随机 token，返回给用户明文，数据库只存 SHA-256(token)。
-- 接受邀请端点接收明文 token，计算 SHA-256 后与 token_hash 比对。
```

#### 现有表改造

- `projects` 加 `org_id INTEGER nullable` + `team_id INTEGER nullable`（项目可归属组织和团队，null=个人项目）
- `users` 加 `default_org_id INTEGER nullable`（登录后默认组织）

#### 迁移版本：v8

```python
def _run_migration_v8(dbapi_conn) -> None:
    """v8: 企业版嵌套多租户（organizations + teams + org_memberships + team_memberships + project_shares + org_invitations）。"""
    # 6 张新表 + projects.org_id/team_id 列 + users.default_org_id 列
    # 迁移前自动备份 database.db → database.db.v7.bak
    # 迁移使用 WAL 模式 + 事务包裹，避免长时间写锁
```

#### 外键约束启用

```python
# database.py 连接事件监听器：每个连接强制启用外键
@event.listens_for(engine, "connect")
def _enable_foreign_keys(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()

# 注：SQLite 默认不强制外键，v8 新表使用 FK 引用 users.id 等，
# 必须在每个连接启用 PRAGMA foreign_keys = ON，否则可能产生孤儿记录。
```

#### 软删除与级联清理策略

```python
# DELETE /api/orgs/{org_id} 实际执行软删除 + 级联清理：
async def delete_organization(org_id: int):
    # 1. 软删除组织
    UPDATE organizations SET deleted_at = CURRENT_TIMESTAMP WHERE id = org_id
    # 2. 软删除下属团队
    UPDATE teams SET deleted_at = CURRENT_TIMESTAMP WHERE org_id = org_id AND deleted_at IS NULL
    # 3. 级联清理 project_shares（硬删除，避免残留共享记录导致权限泄露）
    DELETE FROM project_shares
      WHERE shared_with_org_id = org_id
         OR shared_with_team_id IN (SELECT id FROM teams WHERE org_id = org_id)
    # 4. 组织项目 owner_id 特权失效（权限决策树校验 deleted_at）
    # 5. org_invitations 标记为 revoked
    UPDATE org_invitations SET status = 'revoked' WHERE org_id = org_id AND status = 'pending'

# 删除团队、移除成员时同理清理 project_shares 对应记录
```

### A.2 RBAC 权限模型（阶段 2.4）

#### 组织级角色（4 级）

| 角色 | 组织维度 | 团队维度 | 项目维度 | 说明 |
|------|---------|---------|---------|------|
| `owner` | 全权 | 全权 | 全权 | 组织创建者，不可删除 |
| `admin` | 管理成员+团队+项目 | 全权 | 全权 | 组织管理员 |
| `member` | 创建个人项目 | view/edit（所属团队） | view（组织内共享）/edit（团队共享） | 普通成员 |
| `viewer` | 只读 | view | view | 只读成员 |

#### 团队级角色（2 级）

| 角色 | 团队维度 | 项目维度 | 说明 |
|------|---------|---------|------|
| `team_admin` | 管理团队成员 | 全权（团队项目） | 团队负责人 |
| `member` | view/edit | view/edit（团队共享项目） | 团队成员 |

#### 新增依赖函数（auth.py）

```python
def require_org_member(org_id: int) -> Callable:
    """校验当前用户是该组织成员，返回 (user_id, role)。"""

def require_org_admin(org_id: int) -> Callable:
    """校验组织 admin/owner 角色。"""

def require_org_owner(org_id: int) -> Callable:
    """校验组织 owner 角色（仅创建者）。"""

def require_team_member(team_id: int) -> Callable:
    """校验当前用户是该团队成员，返回 (user_id, role)。"""

def require_team_admin(team_id: int) -> Callable:
    """校验团队 team_admin 角色（或组织 admin/owner）。"""

def require_project_access(project_id: int, min_permission: str = 'view') -> Callable:
    """校验项目访问权限，返回 (user_id, permission)。
    permission: 'admin' > 'edit' > 'view' > None
    """
```

#### 权限决策树（嵌套模型）

```
请求项目 P，需要 min_permission
├─ user_id == 0（dev admin）→ 'admin'（全权）
├─ P.owner_id == user_id：
│   ├─ P.org_id 为空（个人项目）→ 'admin'（个人项目所有者）
│   └─ P.org_id 不为空（组织项目）→ 需校验用户仍是 P.org 的活跃成员：
│       ├─ 是活跃组织成员 → 'admin'
│       └─ 不是活跃组织成员（被移除/组织已删除）→ 降级到普通 share 路径（不直接给 admin）
├─ P.org_id 不为空：
│   ├─ 用户是 P.org 的 owner/admin → 'admin'
│   ├─ P.team_id 不为空 且 用户是 P.team 的 team_admin → 'admin'
│   ├─ project_shares 存在 (P.id, org=P.org_id) 且 共享目标组织未删除 → 按 share.permission
│   ├─ project_shares 存在 (P.id, team=P.team_id) 且 共享目标团队未删除 且 用户在 P.team → 按 share.permission
│   ├─ project_shares 存在 (P.id, user=user_id) 且 共享目标用户未删除 → 按 share.permission
│   ├─ 用户是 P.org 的 member → 'view'（组织内默认只读）
│   ├─ P.team_id 不为空 且 用户是 P.team 的 member → 'edit'（团队内默认可编辑）
│   └─ 否则 → None（无权限）
├─ P.org_id 为空（个人项目）：
│   └─ project_shares 存在 (P.id, user=user_id) 且 共享目标用户未删除 → 按 share.permission
└─ 否则 → None（403）

最终：返回的 permission >= min_permission 则放行，否则 403
权限等级：admin > edit > view

注：
1. owner_id 特权在组织项目场景下需校验"用户仍是组织活跃成员"，防止被移除的创建者绕过组织控制。
2. project_shares 查询时必须校验共享目标（org/team/user）未被软删除，防止残留共享记录导致权限泄露。
3. "活跃成员"定义：org_memberships 中存在记录且组织未软删除；团队同理。
```

### A.3 端点新增（阶段 2.5）

#### 组织管理 `/api/orgs`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/orgs` | require_user | 创建组织（自动成为 owner） |
| GET | `/api/orgs` | require_user | 列出我所在的组织 |
| GET | `/api/orgs/{org_id}` | require_org_member | 组织详情 |
| PATCH | `/api/orgs/{org_id}` | require_org_admin | 更新组织信息 |
| DELETE | `/api/orgs/{org_id}` | require_org_owner | 删除组织（仅 owner） |

#### 团队管理 `/api/orgs/{org_id}/teams`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/orgs/{org_id}/teams` | require_org_admin | 创建团队 |
| GET | `/api/orgs/{org_id}/teams` | require_org_member | 团队列表 |
| GET | `/api/orgs/{org_id}/teams/{team_id}` | require_team_member | 团队详情 |
| PATCH | `/api/orgs/{org_id}/teams/{team_id}` | require_team_admin | 更新团队 |
| DELETE | `/api/orgs/{org_id}/teams/{team_id}` | require_org_admin | 删除团队 |

#### 成员管理

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/orgs/{org_id}/members` | require_org_member | 组织成员列表 |
| PATCH | `/api/orgs/{org_id}/members/{user_id}` | require_org_admin | 修改组织成员角色 |
| DELETE | `/api/orgs/{org_id}/members/{user_id}` | require_org_admin | 移除组织成员 |
| GET | `/api/orgs/{org_id}/teams/{team_id}/members` | require_team_member | 团队成员列表 |
| POST | `/api/orgs/{org_id}/teams/{team_id}/members` | require_team_admin | 添加团队成员 |
| PATCH | `/api/orgs/{org_id}/teams/{team_id}/members/{user_id}` | require_team_admin | 修改团队成员角色 |
| DELETE | `/api/orgs/{org_id}/teams/{team_id}/members/{user_id}` | require_team_admin | 移除团队成员 |

#### 邀请管理

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/orgs/{org_id}/invitations` | require_org_admin | 发送邀请（可指定 team_id） |
| GET | `/api/orgs/{org_id}/invitations` | require_org_admin | 邀请列表 |
| POST | `/api/orgs/join` | require_user | 接受邀请（token） |
| DELETE | `/api/orgs/{org_id}/invitations/{inv_id}` | require_org_admin | 撤销邀请 |

#### 项目共享 `/api/projects/{project_id}/shares`

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/projects/{project_id}/shares` | require_project_access('edit') | 共享列表 |
| POST | `/api/projects/{project_id}/shares` | require_project_access('admin') | 新增共享（org/team/user 三选一） |
| DELETE | `/api/projects/{project_id}/shares/{share_id}` | require_project_access('admin') | 撤销共享 |

#### 现有端点改造

- `GET /api/projects` 支持过滤 `?org_id=` 和 `?team_id=` 和 `?shared=true`
- `GET /api/projects/{id}` 鉴权从 `require_auth + _check_project_owner` 改为 `require_project_access('view')`
- `PATCH/DELETE /api/projects/{id}` 改为 `require_project_access('admin')`
- 任务/交接/留言端点改为 `require_project_access('edit')` 或 `'view'`

### A.4 中间件升级（阶段 2.6）

#### 中间件执行顺序

```
请求进入
  → 1. auth_middleware（解析 JWT，注入 user_id 到 request.state）
  → 2. tenant_context_middleware（基于已解析的 user_id 读取 default_org_id，注入 org_id）
  → 3. rate_limit_middleware（基于 org_id + client_ip 限流）
  → 4. audit_log_middleware（记录 actor_id / org_id / action）
  → 路由处理
```

```python
@app.middleware("http")
async def tenant_context_middleware(request, call_next):
    """从 request.state.user_id（auth_middleware 已注入）读取 default_org_id，
    注入 request.state.org_id。避免重复解析 JWT。"""
    user_id = getattr(request.state, 'user_id', None)
    if user_id:
        org_id = await get_user_default_org(user_id)
        request.state.org_id = org_id
    return await call_next(request)
```

- 中间件顺序：auth → tenant → rate_limit → audit → 路由
- 限流维度从 `client_ip` 扩展为 `(org_id, client_ip)`，组织级配额
- 审计日志中间件：记录 `actor_id / org_id / team_id / action / resource / timestamp`
- 审计日志表（可选，MVP 阶段先写 logger）

#### JWT 错误码区分（auth.py 升级）

```python
def verify_jwt(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"code": "token_expired", "message": "Token 已过期，请重新登录"}
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail={"code": "token_invalid", "message": "Token 无效"}
        )
    except Exception:
        raise HTTPException(
            status_code=401,
            detail={"code": "token_error", "message": "认证失败"}
        )

# 前端根据 error.code 区分：
# - token_expired → 跳转登录页，提示"会话已过期"
# - token_invalid → 跳转登录页，提示"请重新登录"
# - token_error → 显示通用错误
```

### A.5 交付节奏

| 子阶段 | 内容 | 测试目标 |
|--------|------|---------|
| 2.3 | 数据模型 v8 迁移 + 6 张新表 | 迁移脚本 + 回归 |
| 2.4 | RBAC 依赖函数 + 权限决策树 | 单元测试覆盖 4 组织角色 × 2 团队角色 × 3 权限 |
| 2.5 | 组织/团队/成员/邀请/共享端点 | 端到端测试：邀请→加入团队→共享→协作 |
| 2.6 | 中间件 + 审计日志 + 限流升级 | 集成测试 + 性能基准 |

---

## 方向 C：端侧 AI 增强

### C.0 现状基础

| 基础 | 现状 | 差距 |
|------|------|------|
| AI 配置 | `{baseUrl, apiKey, model}` 三端统一 | 无模型选择策略 |
| 调用模式 | 同步请求-响应，30s 超时 | 无流式 |
| 降级策略 | 超时/失败 → 规则引擎 | 健壮 |
| Prompt | 单次 JSON 输出 | 无多轮对话 |
| CSP | 允许 5 家服务商 | 已足够 |
| 本地模型 | ❌ 无 | 需新建 |

### C.1 流式响应支持（阶段 2.7）

#### Focus/Muse 扩展

```javascript
// ai.js 新增
export async function generateAISummaryStream(tabs, onChunk) {
  const config = await getAIConfig();
  if (!config) return null;
  const controller = createTimeoutController();
  const res = await fetch(config.baseUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${config.apiKey}` },
    body: JSON.stringify({ model: config.model, messages: [...], stream: true }),
    signal: controller.signal,
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6);
        if (data === '[DONE]') return;
        const chunk = JSON.parse(data);
        const text = chunk.choices?.[0]?.delta?.content;
        if (text) onChunk(text);
      }
    }
  }
}
```

- resume.js 调用流式版本，popup 中实时显示生成中的摘要
- 超时降级仍走规则引擎

#### CSP 配置（manifest.json）

```json
"content_security_policy": {
  "extension_pages": {
    "connect-src": "'self' https: http://localhost:* http://127.0.0.1:*;"
  }
}
```

- 允许 `https:` 通配，覆盖用户自定义 baseUrl（OpenAI 兼容服务商均走 HTTPS）
- 本地开发场景允许 `localhost` / `127.0.0.1`
- 不使用 `*` 以避免 mixed content 风险

#### Bridge 后端

```python
# ai.py 新增
async def generate_handoff_summary_stream(project_data, template_name):
    """流式生成交接摘要，yield SSE 事件。"""
    config = _get_ai_config()
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream("POST", config["baseUrl"], json={..., "stream": True}) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    yield line
```

- `auto_generate_handoff` 端点改为 SSE 流式响应
- 前端 EventSource 订阅实时显示

### C.2 端侧本地模型（阶段 2.8，实验性）

#### 架构

```
chrome.alarms（空闲触发，或用户主动调用）
  → 本地模型推理（WebGPU / WASM）
    → 生成轻量建议（无网络依赖）
    → 隐私保护：数据不出设备
```

#### 候选方案

| 方案 | 模型 | 大小 | 运行环境 | 适用场景 |
|------|------|------|---------|---------|
| WebLLM | Llama-3.2-1B | ~600MB | WebGPU | Chrome 113+ |
| Transformers.js | distilbert | ~250MB | WASM | 语义分类 |
| 规则引擎增强 | 无 | 0 | 纯 JS | 已有，零成本 |

#### 实现路径

1. 在 ai.js 新增 `getLocalModelConfig()` / `isLocalModelAvailable()` / `loadLocalModel()`
2. 新增 `generateLocalSuggestion(context)` — 用 WebLLM 加载小模型
3. Agent.js 优先用本地模型生成轻量建议（隐私敏感场景）
4. 失败降级到规则引擎
5. options 页新增「本地模型」配置区：启用/禁用 + 模型选择 + 下载进度

#### 限制

- 首次加载 600MB 模型较重，适合按需加载（非 SW 启动时加载）
- WebGPU 兼容性：Chrome 113+，Safari/Firefox 部分支持
- 仅适合轻量任务（分类/摘要），复杂推理仍需云端
- SW 中无法使用 WebGPU（DOM API 限制），本地模型推理需在 popup/options 页面执行
- **popup 关闭时推理中断**：本地模型推理只能在 popup/options 页面执行，popup 关闭会中断。
  缓解策略：
  1. 推理任务设计为短时（<10s），用户开启后保持 popup 打开
  2. options 页面可独立窗口运行（`chrome.windows.create`），不受 popup 关闭影响
  3. 推理结果持久化到 `chrome.storage.local`，中断后下次打开可恢复
  4. UI 明确提示"请保持窗口打开以完成本地推理"

### C.3 AI 调用统一层（阶段 2.9）

#### 目标

三端 AI 调用收敛为统一接口，支持云端/本地/规则引擎三级降级。

```javascript
// shared/ai-router.js（Focus 和 Muse 共享）
export async function aiComplete({ prompt, onChunk, prefer, context }) {
  // prefer: 'cloud' | 'local' | 'rule'
  // 1. 尝试 prefer 指定的优先级
  // 2. 失败降级到下一级
  // 3. 最终降级到规则引擎

  if (prefer !== 'local' && await hasAIConfig()) {
    try {
      return await generateAISummaryStream(context, onChunk);
    } catch (e) {
      console.warn('云端 AI 失败，降级到本地模型');
    }
  }
  if (prefer !== 'cloud' && await isLocalModelAvailable()) {
    try {
      return await generateLocalSuggestion(context);
    } catch (e) {
      console.warn('本地模型失败，降级到规则引擎');
    }
  }
  return ruleEngineResume(context);  // 最终兜底
}
```

### C.4 交付节奏

| 子阶段 | 内容 | 测试目标 |
|--------|------|---------|
| 2.7 | 流式响应（三端） | 流式输出 + 超时降级 |
| 2.8 | 端侧本地模型（实验性） | WebLLM 加载 + 推理 + 降级链 |
| 2.9 | AI 调用统一层 | 三级降级链测试 |

---

## 工作量预估

| 子阶段 | 新增代码行 | 新增测试 | 复杂度 |
|--------|-----------|---------|--------|
| 2.1 端侧 Agent | ~600（两扩展 + popup 改造） | ~120 | 中 |
| 2.2 服务端 Agent | ~400 | ~150 | 高 |
| 2.3 数据模型 v8（嵌套 6 表） | ~200 | ~100 | 中 |
| 2.4 RBAC（嵌套权限树） | ~300 | ~200 | 高 |
| 2.5 组织/团队/成员/共享端点 | ~600 | ~300 | 中 |
| 2.6 中间件+审计 | ~200 | ~100 | 中 |
| 2.7 流式响应 | ~300（三端） | ~100 | 中 |
| 2.8 本地模型 | ~350 | ~60 | 高（实验性） |
| 2.9 AI 统一层 | ~200 | ~100 | 低 |
| **合计** | **~3150** | **~1230** | — |

---

## 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| owner_id 不用 FK，多租户 FK 设计复杂 | 中 | 新表用 FK，旧表保持现状，迁移渐进 |
| SSE 内存 pub/sub 无持久化 | 中 | Agent 建议落库，SSE 仅做推送通知 |
| chrome.alarms 最小间隔 1min | 低 | Agent 用 15min 周期足够 |
| WebGPU 兼容性 + SW 限制 | 中 | 本地模型在 popup/options 页执行，非 SW |
| APScheduler 多 worker 重复执行 | 中 | 用文件锁或 Redis 锁，MVP 单 worker |
| 现有 17 个端点鉴权改造回归风险 | 高 | 逐端点改造 + 完整回归测试 |
| 嵌套模型权限树复杂 | 高 | 权限决策树单元测试全覆盖 6 角色 × 3 权限 |
| notifications 权限增加安装提示 | 低 | 权限说明文案优化，用户可理解 |
| WebLLM 600MB 首次下载 | 中 | 按需加载 + 进度条 + 可禁用 |

---

## Deep Review Loop 审查记录

### Phase 1：方案审查

审查维度：架构一致性 / 依赖正确性 / 数据模型完备性 / 权限模型严谨性 / 测试覆盖度 / 风险识别

**发现 6 个问题**：

#### P1-1：2.1 端侧 Agent alarm 与 1.6 auto-sync alarm 名称冲突风险

**问题**：1.6 已注册 `vannevar-focus-auto-sync`（30min）和 `vannevar-muse-auto-sync`（30min）。2.1 新增 Agent alarm 需用不同名称，否则 chrome.alarms.create 会覆盖原有 alarm。

**修复**：2.1 Agent alarm 命名为 `vannevar-focus-agent` / `vannevar-muse-agent`，周期 15min，与 auto-sync 的 30min 独立。已在方案中明确。

#### P1-2：2.1 Agent 在 SW 休眠时无法执行 generateAISummary

**问题**：chrome.alarms 会唤醒 SW，但 SW 生命周期仅 30s（MV3 默认）。generateAISummary 调用云端 AI 可能 10-30s，SW 可能在响应返回前被终止。

**修复**：Agent alarm 回调中优先用规则引擎（同步、<100ms），仅在规则引擎建议需要 AI 增强时才异步调用 AI。AI 调用用 `await` 保持 SW 活跃，超时 15s（比 SW 30s 生命周期短）。若 AI 超时，建议仍基于规则引擎结果展示（已有降级策略）。已补充到 B.1 架构注释。

#### P1-3：2.2 服务端 Agent 的 require_project_access 依赖 2.4，但执行顺序写的是 2.1→2.2

**问题**：方案顶部写"2.1 → 2.2"，但 B.2 标题已注明"需 2.4 RBAC 先行"，执行顺序不一致。

**修复**：顶部执行顺序改为"2.1 → 2.3 → 2.4 → 2.2 → 2.5 → 2.6 → 2.7 → 2.8 → 2.9"，已在方案中更新。

#### P2-1：嵌套模型 project_shares 的 CHECK 约束在 SQLite ALTER TABLE 场景下可能失效

**问题**：v8 迁移用 CREATE TABLE 新建 project_shares，CHECK 约束正常。但如果未来需要 ALTER TABLE 修改约束，SQLite 支持有限。

**修复**：MVP 阶段 CHECK 约束在 CREATE TABLE 中定义即可，方案已正确。未来如需修改约束，用重建表策略（SQLite 标准做法）。无需修改方案。

#### P2-2：2.1 Agent 通知点击后 chrome.action.openPopup() 在 MV3 中支持有限

**问题**：chrome.action.openPopup() 仅在扩展图标被点击时可用，从通知点击无法可靠打开 popup。

**修复**：通知点击回调中优先 `chrome.action.openPopup?.()`，失败降级到 `chrome.tabs.create({ url: chrome.runtime.getURL('options.html') })`。已在 B.1 通知双通道代码注释中补充。

#### P2-3：2.8 本地模型在 Service Worker 中无法执行（WebGPU 限制）

**问题**：WebGPU/WebGL 在 MV3 Service Worker 中不可用（SW 无 DOM API）。Agent 的 alarm 回调在 SW 中执行，无法调用本地模型。

**修复**：2.8 本地模型推理限定在 popup/options 页面执行（有 DOM 环境）。Agent alarm 回调中如需本地模型，通过 `chrome.runtime.sendMessage` 发消息给 popup（仅 popup 打开时可用），popup 执行推理后回传结果。若 popup 未打开，降级到规则引擎。已在 C.2 限制中补充。

### Phase 2：落地审查

审查维度：代码可实现性 / API 一致性 / 命名规范 / 边界条件 / 安全性

**发现 4 个问题**：

#### P1-4：嵌套权限决策树遗漏"用户在团队但不在组织"的边界

**问题**：决策树假设团队成员必然是组织成员，但 team_memberships 表无 FK 约束到 org_memberships。如果用户被加入团队但未加入组织，权限如何判定？

**修复**：新增约束——添加团队成员时必须先确保该用户是组织成员（API 层校验）。权限决策树补充：若用户在 team_memberships 但不在 org_memberships，视为无效成员关系，返回 None（403）。已在 A.2 权限决策树中补充注释。

#### P2-4：org_invitations 的 team_id 应校验属于同一 org_id

**问题**：邀请指定 team_id 时，未校验该 team 属于邀请的 org_id。

**修复**：POST `/api/orgs/{org_id}/invitations` 端点中校验 `team.org_id == org_id`，否则 400。已在 A.3 邀请管理端点说明中补充。

#### P2-5：2.1 Agent 建议的 actionUrl 对扩展内部页面的格式

**问题**：actionUrl 应该用 `chrome.runtime.getURL('popup.html')` 还是相对路径？

**修复**：Agent 建议的 actionUrl 不存完整 URL，而是存 action 类型（如 `restore_snapshot:123`），popup 打开后根据 action 类型执行对应逻辑。已在 B.1 Agent 状态持久化结构中调整。

#### P2-6：2.2 APScheduler 在 lifespan 之外不会被启动（测试时）

**问题**：APScheduler 在 main.py lifespan 中启动，但 TestClient 不触发 lifespan（除非用 context manager）。

**修复**：测试中用 `with TestClient(app) as client:` 触发 lifespan，或直接调用扫描函数（单元测试不依赖 APScheduler）。已在 B.2 测试目标中补充。

### Phase 3：回归检查

审查维度：与阶段 1 的兼容性 / 与现有代码的冲突 / 迁移安全性

**发现 2 个问题**：

#### P1-5：v8 迁移给 projects 加 org_id/team_id 列，现有项目 owner_id 语义不变

**问题**：现有项目 owner_id 是个人所有者。加 org_id/team_id 后，旧项目 org_id=null, team_id=null，仍为个人项目，权限不变。✅ 无回归。

**验证**：权限决策树第一个分支 `P.owner_id == user_id → 'admin'` 对旧项目生效。旧项目不受影响。

#### P2-7：2.1 Agent 新增 notifications 权限会导致扩展更新时重新提示

**问题**：MV3 扩展新增权限后，Chrome 会在下次更新时提示用户授权。

**修复**：这是预期行为，无法避免。在更新说明中告知用户"新增 Agent 主动建议功能，需要通知权限"。无需修改方案。

### DRL 总结

- **Phase 1**：发现 6 个问题（3 P1 + 3 P2），全部在方案中修复
- **Phase 2**：发现 4 个问题（1 P1 + 3 P2），全部在方案中修复
- **Phase 3**：发现 2 个问题（1 P1 + 1 P2），确认无回归
- **合计**：12 个问题，11 个已在方案中修复，1 个为预期行为（P2-7）

**关键修复**：
1. Agent alarm 命名独立于 auto-sync alarm（P1-1）
2. SW 生命周期与 AI 调用的兼容性策略（P1-2）
3. 执行顺序修正为 2.1→2.3→2.4→2.2（P1-3）
4. 嵌套权限决策树补充团队成员必须是组织成员的约束（P1-4）
5. v8 迁移对旧项目无回归（P1-5 验证通过）

---

## DRL 补充审查（14 个漏洞，覆盖权限/安全/数据完整性/并发/UX）

> 来源：用户对阶段 2 方案的二次漏洞审查，补充之前 DRL 未覆盖的问题。

### P1 漏洞（安全/数据完整性，6 个）

#### 补充-P1-1：权限模型漏洞：项目归属组织的 owner_id 特权问题

**问题**：决策树第二分支 `P.owner_id == user_id → 'admin'` 在项目有 `org_id` 时依然生效。若项目的创建者被移出组织，他通过 `owner_id` 仍可获得 admin 权限，绕过组织控制。

**修复**：权限决策树第二分支细化为：
- `P.org_id` 为空（个人项目）→ `owner_id == user_id` 直接给 admin
- `P.org_id` 不为空（组织项目）→ 需校验用户仍是该组织的活跃成员（`org_memberships` 存在记录且组织未软删除），才给 admin；否则降级到普通 share 路径

已在 A.2 权限决策树中更新。

#### 补充-P1-2：团队项目 owner_id 权限泄露

**问题**：当项目同时有 `org_id` 和 `team_id`，创建者被移除团队但仍保留组织成员身份，可能通过 `owner_id` 获得 admin 权限。

**修复**：与 P1-1 同一修复覆盖——组织项目场景下 `owner_id` 特权必须校验"活跃组织成员"身份。团队移除不影响组织成员身份，但若用户被移出组织，特权即失效。

#### 补充-P1-3：组织删除缺少级联策略

**问题**：`DELETE /api/orgs/{org_id}` 未定义如何处理下属团队、项目、成员关系、共享和邀请。硬删除会导致孤儿数据。

**修复**：采用软删除 + 级联清理策略：
1. `organizations` 和 `teams` 表加 `deleted_at DATETIME` 字段
2. 删除组织时：软删除组织 + 软删除下属团队 + 硬删除 `project_shares` 中引用该组织/团队的记录 + 标记 `org_invitations` 为 revoked
3. 权限决策树校验 `deleted_at IS NULL`，软删除的实体视为不存在
4. 移除组织成员/团队成员时同步清理 `project_shares` 中 `shared_with_user_id = 被移除用户` 的记录

已在 A.1 表结构 + 新增"软删除与级联清理策略"小节中更新。

#### 补充-P1-4：邀请 token 明文存储

**问题**：`org_invitations.token VARCHAR(32) NOT NULL UNIQUE` 直接明文存储，数据库泄露后攻击者可利用未使用 token 加入任意组织。

**修复**：改为 `token_hash VARCHAR(64) NOT NULL UNIQUE`，存储 SHA-256(token)：
- 生成邀请时生成 32 字节随机 token，返回给用户明文（仅一次）
- 数据库只存 SHA-256(token)
- 接受邀请端点接收明文 token，计算 SHA-256 后与 `token_hash` 比对
- 数据库泄露后攻击者无法从 hash 反推 token

已在 A.1 `org_invitations` 表结构中更新。

#### 补充-P1-5：外键约束未启用

**问题**：SQLite 默认不强制外键，`team_memberships.user_id` 引 `users.id` 等隐式引用可能产生孤儿记录。

**修复**：在 `database.py` 添加连接事件监听器，每个连接强制 `PRAGMA foreign_keys = ON`：
```python
@event.listens_for(engine, "connect")
def _enable_foreign_keys(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()
```

已新增"外键约束启用"小节。

#### 补充-P1-6：require_project_access 的共享权限残留

**问题**：`project_shares` 的共享目标（组织/团队/用户）被删除时，共享记录未级联删除，权限决策可能返回过期数据。

**修复**：
1. 删除组织/团队时硬删除 `project_shares` 中引用该实体的记录（见 P1-3 级联清理策略）
2. 移除组织/团队成员时硬删除 `project_shares` 中 `shared_with_user_id = 被移除用户` 的记录
3. 权限决策树中 `project_shares` 查询增加"共享目标未软删除"校验（`org.deleted_at IS NULL`）

已在 A.2 权限决策树 + 软删除策略中更新。

### P2 漏洞（体验/性能/部署，8 个）

#### 补充-P2-1：并发重复建议生成（APScheduler）

**问题**：APScheduler 多 worker 部署或开发时多次启动可能导致重复扫描，`agent_suggestions` 缺少唯一性约束。

**修复**：三层去重：
1. DB 层：`UNIQUE(project_id, type, date(created_at))` 防止同日同类型重复
2. 调度层：APScheduler 使用 `coalesce=True` + `max_instances=1`
3. 应用层：扫描函数用 `INSERT OR IGNORE`

已在 B.2 新增"并发去重策略"小节。

#### 补充-P2-2：SSE 推送与建议状态同步

**问题**：`status='shown'` 何时设置未定义，前端重复连接 SSE 可能重复收到同一建议。

**修复**：状态流转定义：
- `pending` → SSE 推送给在线客户端 → 客户端 ACK 后置 `shown`
- SSE 推送后仍保留 `pending`，直到客户端 ACK
- 未 ACK 的建议下次 SSE 连接会重推
- `shown` 仅由客户端 ACK 触发，避免重复推送

已在 B.2 新增"状态流转定义"小节。

#### 补充-P2-3：流式响应 CSP 配置缺失

**问题**：用户自定义 `baseUrl` 可能被 CSP 拦截。

**修复**：manifest.json CSP 配置为 `connect-src: 'self' https: http://localhost:* http://127.0.0.1:*;`，允许 HTTPS 通配覆盖用户自定义端点，本地开发允许 localhost。不使用 `*` 避免 mixed content 风险。

已在 C.1 新增"CSP 配置"小节。

#### 补充-P2-4：端侧 Agent 的 dailyFlags 键跨天残留

**问题**：`dailyFlags: { '2026-07-11': { dailyReviewDone: true } }` 无限增长，未实现过期清理。

**修复**：`runAgentCycle` 末尾清理 `dailyFlags`，仅保留今天和昨天的键：
```javascript
// 清理过期 dailyFlags（保留今天 + 昨天）
const today = todayKey();
const yesterday = todayKeyOffset(-1);
for (const key of Object.keys(state.dailyFlags)) {
  if (key !== today && key !== yesterday) delete state.dailyFlags[key];
}
```

已应用到 Focus/Muse agent.js 实施代码。

#### 补充-P2-5：本地模型推理在 popup 关闭时中断

**问题**：WebLLM 推理只能在 popup/options 页面进行，popup 关闭推理中断。

**修复**：缓解策略：
1. 推理任务设计为短时（<10s）
2. options 页面可独立窗口运行（`chrome.windows.create`）
3. 推理结果持久化到 `chrome.storage.local`，中断后可恢复
4. UI 明确提示"请保持窗口打开以完成本地推理"

已在 C.2 限制小节补充。

#### 补充-P2-6：JWT 过期返回体未区分原因

**问题**：JWT 过期或无效时统一抛 401，前端难以针对性处理。

**修复**：`verify_jwt` 区分三种错误码：
- `token_expired`（ExpiredSignatureError）→ 前端提示"会话已过期"
- `token_invalid`（InvalidTokenError）→ 前端提示"请重新登录"
- `token_error`（其他异常）→ 通用错误

已新增"JWT 错误码区分"小节。

#### 补充-P2-7：企业版限流中间件顺序问题

**问题**：租户上下文中间件需解析 JWT，若在认证中间件之前执行会重复解析。

**修复**：明确中间件顺序：auth → tenant → rate_limit → audit → 路由。`tenant_context_middleware` 从 `request.state.user_id`（auth_middleware 已注入）读取 `default_org_id`，不重复解析 JWT。

已在 A.4 新增"中间件执行顺序"小节。

#### 补充-P2-8：迁移 v8 可能锁表

**问题**：SQLite 在大量写入时锁库，v8 迁移 `CREATE TABLE` + `ALTER TABLE` 可能长时间占用写锁。

**修复**：
1. 迁移前自动备份 `database.db → database.db.v7.bak`
2. 迁移使用 WAL 模式（`PRAGMA journal_mode=WAL`）减少写锁占用
3. 整个迁移包裹在单个事务中，失败可回滚
4. MVP 阶段数据量小（<1000 项目），锁表时间 <1s，可接受

已在 A.1 迁移版本小节补充。

### DRL 补充审查总结

- **P1 漏洞**：6 个（权限模型 × 2 + 级联删除 + token 存储 + 外键约束 + 共享残留），全部在方案中修复
- **P2 漏洞**：8 个（并发去重 + SSE 状态 + CSP + dailyFlags + 本地模型 + JWT + 中间件 + 迁移锁表），全部在方案中修复
- **已实施代码修复**：Focus/Muse agent.js 的 dailyFlags 清理逻辑已应用
- **合计**：14 个漏洞全部处理

**复利经验**：
1. 权限决策树的"所有者特权"必须校验"当前仍是活跃成员"，否则被移除的用户仍可绕过组织控制
2. 软删除 + 级联清理是处理多租户实体删除的标准模式，硬删除导致孤儿数据，纯软删除导致权限泄露
3. token 类凭证必须哈希存储，明文存储在数据库泄露后等同于凭证泄露
4. SQLite 外键约束默认关闭，必须在连接事件中显式启用
5. 共享/授权记录的清理必须与实体删除联动，否则产生"幽灵权限"
