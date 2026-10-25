# database.py — Bridge MVP 本地 SQLite 数据库
import logging
import os
import shutil
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bridge_mvp.db"
DATABASE_URL = os.environ.get("BRIDGE_DATABASE_URL", f"sqlite:///{DB_PATH}")

# 按数据库类型条件化 connect_args：SQLite 需要 check_same_thread 与 timeout
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False, "timeout": 30}
else:
    connect_args = {}

if ":memory:" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        connect_args=connect_args,
        poolclass=StaticPool,
    )
else:
    engine = create_engine(DATABASE_URL, connect_args=connect_args)


# P1-1: SQLite 默认不启用外键约束，需在每次连接时显式开启 PRAGMA。
# 这样 cascade delete / ondelete SET NULL 等约束才会真正生效。
# W087a: 同时启用 WAL 模式 + busy_timeout，解决多志愿者并发写入锁竞争：
#   - journal_mode=WAL: 读不阻塞写、写不阻塞读，写入并发性能提升 10x+
#   - busy_timeout=5000: 遇到锁时等待 5s 而非立即抛 SQLITE_BUSY，覆盖瞬时锁竞争
#   - synchronous=NORMAL: WAL 模式下 NORMAL 足够安全，减少 fsync 开销
#   - foreign_keys=ON: 启用外键约束
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def _try_chmod(path, mode: int) -> None:
    """DRL-R1-V12: 设置文件/目录权限，Windows 下静默忽略（chmod 语义不同）。

    Unix 下 0o600 = 仅所有者可读写，0o700 = 仅所有者可进入。
    """
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):
        # Windows 不支持 Unix 权限位，静默跳过
        pass


def get_db(request: Request = None):
    """DRL-R1-V22: 优先复用中间件缓存的 Session（request.state._cached_db），
    避免每认证请求创建两个 DB Session。无缓存时回退到新建 Session。

    request 参数由 FastAPI 自动注入（Depends(get_db) 调用方无需改动）。
    """
    cached_db = None
    if request is not None:
        cached_db = getattr(getattr(request, "state", None), "_cached_db", None)
    if cached_db is not None:
        # 复用中间件缓存的 Session；由中间件负责关闭，此处不 close
        yield cached_db
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# P2-11: 迁移版本管理采用 PRAGMA user_version，比 schema_version 表更轻量。
# 每个版本号对应一批迁移；新迁移通过追加 _run_migration_vN 函数并提升 TARGET_VERSION 即可。
# DRL-R1-V7: v10 新增 users.token_revoked_at 列（JWT 撤销机制）
# Phase 4.4 收尾: v12 新增 user_crypto_profiles 表（加密档案，跨设备解密）
TARGET_SCHEMA_VERSION = 12


def _column_exists(dbapi_conn, table: str, column: str) -> bool:
    """通过 PRAGMA table_info 检查列是否存在，避免依赖 sqlalchemy.inspect。"""
    cur = dbapi_conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    rows = cur.fetchall()
    cur.close()
    return any(row[1] == column for row in rows)


def _table_exists(dbapi_conn, table: str) -> bool:
    cur = dbapi_conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    row = cur.fetchone()
    cur.close()
    return row is not None


def _run_migration_v1(dbapi_conn) -> None:
    """v1: 向旧库的 handoffs / projects 表补齐新增列。

    create_all 不会向已存在的表追加列，因此需要显式 ALTER TABLE。
    旧版本逻辑保留并整合进 v1 迁移，确保从任意历史版本升级都能正确执行。
    """
    if _table_exists(dbapi_conn, "handoffs"):
        if not _column_exists(dbapi_conn, "handoffs", "status"):
            dbapi_conn.execute("ALTER TABLE handoffs ADD COLUMN status VARCHAR(20) DEFAULT 'draft' NOT NULL")
        if not _column_exists(dbapi_conn, "handoffs", "confirmed_at"):
            dbapi_conn.execute("ALTER TABLE handoffs ADD COLUMN confirmed_at DATETIME NULL")
        if not _column_exists(dbapi_conn, "handoffs", "confirmed_by"):
            dbapi_conn.execute("ALTER TABLE handoffs ADD COLUMN confirmed_by VARCHAR(60) NULL")
    if _table_exists(dbapi_conn, "projects"):
        if not _column_exists(dbapi_conn, "projects", "status"):
            dbapi_conn.execute("ALTER TABLE projects ADD COLUMN status VARCHAR(20) DEFAULT 'active' NOT NULL")


def _run_migration_v2(dbapi_conn) -> None:
    """v2: 向 projects 表补齐 template_id 列，支持通用交接模板系统。

    server_default='volunteer' 保证旧库现有行获得默认值，
    让 Bridge 从公益工具平滑升级为通用交接工具而不破坏现有数据。
    """
    if _table_exists(dbapi_conn, "projects"):
        if not _column_exists(dbapi_conn, "projects", "template_id"):
            dbapi_conn.execute(
                "ALTER TABLE projects ADD COLUMN template_id VARCHAR(20) DEFAULT 'volunteer' NOT NULL"
            )


def _run_migration_v3(dbapi_conn) -> None:
    """v3: 向 messages 表补齐内容审核 + 软删除字段。

    - moderation_flagged: 敏感词命中标记（Boolean, default 0, NOT NULL）
    - moderation_warning: 给用户的提示文本（VARCHAR(500), nullable）
    - is_deleted: 管理员软删除标记（Boolean, default 0, NOT NULL）

    server_default 保证旧库现有行获得默认值；保留审计痕迹，不真正删除数据。
    """
    if _table_exists(dbapi_conn, "messages"):
        if not _column_exists(dbapi_conn, "messages", "moderation_flagged"):
            dbapi_conn.execute(
                "ALTER TABLE messages ADD COLUMN moderation_flagged BOOLEAN DEFAULT 0 NOT NULL"
            )
        if not _column_exists(dbapi_conn, "messages", "moderation_warning"):
            dbapi_conn.execute(
                "ALTER TABLE messages ADD COLUMN moderation_warning VARCHAR(500) NULL"
            )
        if not _column_exists(dbapi_conn, "messages", "is_deleted"):
            dbapi_conn.execute(
                "ALTER TABLE messages ADD COLUMN is_deleted BOOLEAN DEFAULT 0 NOT NULL"
            )


def _run_migration_v4(dbapi_conn) -> None:
    """v4: 向 projects / tasks 表补齐乐观锁 version 列。

    - version: 整数版本号，每次成功更新 +1，客户端更新时回传做冲突检测（默认 1，NOT NULL）

    server_default='1' 保证旧库现有行获得默认值，从无锁版本平滑升级不破坏现有数据。
    """
    if _table_exists(dbapi_conn, "projects"):
        if not _column_exists(dbapi_conn, "projects", "version"):
            dbapi_conn.execute(
                "ALTER TABLE projects ADD COLUMN version INTEGER DEFAULT 1 NOT NULL"
            )
    if _table_exists(dbapi_conn, "tasks"):
        if not _column_exists(dbapi_conn, "tasks", "version"):
            dbapi_conn.execute(
                "ALTER TABLE tasks ADD COLUMN version INTEGER DEFAULT 1 NOT NULL"
            )


def _run_migration_v5(dbapi_conn) -> None:
    """v5: 新增 users 表（统一账号体系）。

    create_all 通常已通过 SQLAlchemy 模型建表，此迁移用于旧库升级场景：
    确保从 v4 升级的库也能获得 users 表。幂等（表已存在则跳过）。
    """
    if not _table_exists(dbapi_conn, "users"):
        dbapi_conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                display_name VARCHAR(60) DEFAULT '' NOT NULL,
                created_at DATETIME,
                is_active BOOLEAN DEFAULT 1 NOT NULL
            )
        """)
        dbapi_conn.execute("CREATE INDEX ix_users_email ON users (email)")


def _run_migration_v6(dbapi_conn) -> None:
    """v6: 向 projects 表补齐 owner_id 列（统一账号体系数据隔离基础）。

    nullable=True：旧数据无 owner，dev 模式创建的 owner_id=NULL。
    不加 ForeignKey 约束：dev 模式 user_id=0 不存在于 users 表。
    """
    if _table_exists(dbapi_conn, "projects"):
        if not _column_exists(dbapi_conn, "projects", "owner_id"):
            dbapi_conn.execute(
                "ALTER TABLE projects ADD COLUMN owner_id INTEGER NULL"
            )
            dbapi_conn.execute("CREATE INDEX ix_projects_owner_id ON projects (owner_id)")


def _run_migration_v7(dbapi_conn) -> None:
    """v7: 新增 sync_items 表（跨设备数据同步）。

    存储扩展端推送的 Focus 快照 / Muse 灵感 / 氛围标签数据。
    用 (owner_id, app, client_uuid) 唯一索引去重，client_updated_at 做 LWW 合并。
    """
    if not _table_exists(dbapi_conn, "sync_items"):
        dbapi_conn.execute("""
            CREATE TABLE sync_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                app VARCHAR(20) NOT NULL,
                item_type VARCHAR(30) NOT NULL,
                client_uuid VARCHAR(64) NOT NULL,
                data TEXT NOT NULL,
                client_created_at INTEGER NOT NULL,
                client_updated_at INTEGER NOT NULL,
                server_updated_at DATETIME
            )
        """)
        dbapi_conn.execute("CREATE INDEX ix_sync_items_owner_id ON sync_items (owner_id)")
        dbapi_conn.execute(
            "CREATE UNIQUE INDEX ux_sync_items_owner_app_uuid ON sync_items (owner_id, app, client_uuid)"
        )


def _run_migration_v8(dbapi_conn) -> None:
    """v8: 嵌套多租户（organizations + teams + org_memberships + team_memberships + project_shares + org_invitations）。

    补充-P2-8: 迁移前自动备份 + WAL 模式（已在 _enable_pragmas 启用）+ 事务包裹。
    新表主要由 create_all 创建，此处补 ALTER TABLE 加列 + 幂等 CREATE TABLE IF NOT EXISTS 兜底。
    """
    # 1. 6 张新表（幂等，create_all 通常已创建，此处兜底旧库升级场景）
    if not _table_exists(dbapi_conn, "organizations"):
        dbapi_conn.execute("""
            CREATE TABLE organizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(120) NOT NULL,
                slug VARCHAR(60) NOT NULL UNIQUE,
                owner_id INTEGER NOT NULL,
                plan VARCHAR(20) DEFAULT 'free' NOT NULL,
                max_members INTEGER DEFAULT 50 NOT NULL,
                deleted_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
    if not _table_exists(dbapi_conn, "teams"):
        dbapi_conn.execute("""
            CREATE TABLE teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id INTEGER NOT NULL REFERENCES organizations(id),
                name VARCHAR(120) NOT NULL,
                slug VARCHAR(60) NOT NULL,
                description TEXT DEFAULT '' NOT NULL,
                deleted_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(org_id, slug)
            )
        """)
    if not _table_exists(dbapi_conn, "org_memberships"):
        dbapi_conn.execute("""
            CREATE TABLE org_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id INTEGER NOT NULL REFERENCES organizations(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                role VARCHAR(20) DEFAULT 'member' NOT NULL,
                joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(org_id, user_id)
            )
        """)
    if not _table_exists(dbapi_conn, "team_memberships"):
        dbapi_conn.execute("""
            CREATE TABLE team_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL REFERENCES teams(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                role VARCHAR(20) DEFAULT 'member' NOT NULL,
                joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(team_id, user_id)
            )
        """)
    if not _table_exists(dbapi_conn, "project_shares"):
        dbapi_conn.execute("""
            CREATE TABLE project_shares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                shared_with_org_id INTEGER REFERENCES organizations(id),
                shared_with_team_id INTEGER REFERENCES teams(id),
                shared_with_user_id INTEGER REFERENCES users(id),
                permission VARCHAR(20) DEFAULT 'view' NOT NULL,
                shared_by_user_id INTEGER NOT NULL,
                shared_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                CHECK(
                    (shared_with_org_id IS NOT NULL AND shared_with_team_id IS NULL AND shared_with_user_id IS NULL)
                    OR (shared_with_org_id IS NULL AND shared_with_team_id IS NOT NULL AND shared_with_user_id IS NULL)
                    OR (shared_with_org_id IS NULL AND shared_with_team_id IS NULL AND shared_with_user_id IS NOT NULL)
                )
            )
        """)
    if not _table_exists(dbapi_conn, "org_invitations"):
        dbapi_conn.execute("""
            CREATE TABLE org_invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id INTEGER NOT NULL REFERENCES organizations(id),
                team_id INTEGER REFERENCES teams(id),
                email VARCHAR(255) NOT NULL,
                token_hash VARCHAR(64) NOT NULL UNIQUE,
                role VARCHAR(20) DEFAULT 'member' NOT NULL,
                invited_by INTEGER NOT NULL,
                status VARCHAR(20) DEFAULT 'pending' NOT NULL,
                expires_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

    # 2. 现有表加列（ALTER TABLE，幂等）
    if _table_exists(dbapi_conn, "projects"):
        if not _column_exists(dbapi_conn, "projects", "org_id"):
            dbapi_conn.execute("ALTER TABLE projects ADD COLUMN org_id INTEGER NULL")
            dbapi_conn.execute("CREATE INDEX IF NOT EXISTS ix_projects_org_id ON projects (org_id)")
        if not _column_exists(dbapi_conn, "projects", "team_id"):
            dbapi_conn.execute("ALTER TABLE projects ADD COLUMN team_id INTEGER NULL")
            dbapi_conn.execute("CREATE INDEX IF NOT EXISTS ix_projects_team_id ON projects (team_id)")

    if _table_exists(dbapi_conn, "users"):
        if not _column_exists(dbapi_conn, "users", "default_org_id"):
            dbapi_conn.execute("ALTER TABLE users ADD COLUMN default_org_id INTEGER NULL")


def _run_migration_v9(dbapi_conn) -> None:
    """v9: 服务端 Agent 建议（agent_suggestions 表）。

    补充-P2-1: UNIQUE(project_id, type, date(created_at)) 防止并发重复生成。
    """
    if not _table_exists(dbapi_conn, "agent_suggestions"):
        dbapi_conn.execute("""
            CREATE TABLE agent_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                type VARCHAR(30) NOT NULL,
                title VARCHAR(200) NOT NULL,
                body TEXT,
                severity VARCHAR(10) DEFAULT 'info' NOT NULL,
                status VARCHAR(20) DEFAULT 'pending' NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                acted_at DATETIME
            )
        """)
        dbapi_conn.execute(
            "CREATE INDEX idx_agent_suggestions_project ON agent_suggestions (project_id, status)"
        )
        dbapi_conn.execute(
            "CREATE UNIQUE INDEX ux_agent_suggestions_project_type_date "
            "ON agent_suggestions (project_id, type, date(created_at))"
        )


def _run_migration_v10(dbapi_conn) -> None:
    """v10: DRL-R1-V7 — 向 users 表补齐 token_revoked_at 列（JWT 撤销机制）。

    nullable=True：未撤销用户为 NULL。每次签发 JWT 时校验 iat > token_revoked_at，
    早于此时间签发的 JWT 视为已撤销。/api/auth/logout 端点设置此字段为当前 unix 时间戳。
    """
    if _table_exists(dbapi_conn, "users"):
        if not _column_exists(dbapi_conn, "users", "token_revoked_at"):
            dbapi_conn.execute(
                "ALTER TABLE users ADD COLUMN token_revoked_at INTEGER NULL"
            )


def _run_migration_v11(dbapi_conn) -> None:
    """v11: Phase 4.1 — 跨产品联动表（Focus 快照 / Muse 灵感 → Bridge 项目）。

    创建 project_context_snapshots 和 project_context_inspirations 两张表，
    用于存储 Focus/Muse 扩展推送到 Bridge 项目的上下文元数据。
    使用 CREATE TABLE IF NOT EXISTS 保证幂等（与 create_all 互补）。
    """
    if not _table_exists(dbapi_conn, "project_context_snapshots"):
        dbapi_conn.execute(
            """
            CREATE TABLE project_context_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                snapshot_uuid VARCHAR(64) NOT NULL,
                title VARCHAR(200) NOT NULL,
                summary TEXT DEFAULT '' NOT NULL,
                urls_json TEXT DEFAULT '[]' NOT NULL,
                tab_count INTEGER DEFAULT 0 NOT NULL,
                created_at INTEGER NOT NULL,
                pushed_at INTEGER NOT NULL,
                pushed_by INTEGER NOT NULL,
                UNIQUE (project_id, snapshot_uuid)
            )
            """
        )
        dbapi_conn.execute(
            "CREATE INDEX idx_pcs_project_created ON project_context_snapshots (project_id, created_at)"
        )
    if not _table_exists(dbapi_conn, "project_context_inspirations"):
        dbapi_conn.execute(
            """
            CREATE TABLE project_context_inspirations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                inspiration_uuid VARCHAR(64) NOT NULL,
                title VARCHAR(200) NOT NULL,
                url VARCHAR(2048) NOT NULL,
                note TEXT DEFAULT '' NOT NULL,
                mood VARCHAR(60) DEFAULT '' NOT NULL,
                created_at INTEGER NOT NULL,
                pushed_at INTEGER NOT NULL,
                pushed_by INTEGER NOT NULL,
                UNIQUE (project_id, inspiration_uuid)
            )
            """
        )
        dbapi_conn.execute(
            "CREATE INDEX idx_pci_project_created ON project_context_inspirations (project_id, created_at)"
        )


def _run_migration_v12(dbapi_conn) -> None:
    """v12: Phase 4.4 收尾 — 加密档案表（跨设备解密支持）。

    存储 PBKDF2 salt（公开参数）、派生参数与 verification 密文。
    **不含任何密钥材料**：passphrase 永不落库，档案也无法反推 passphrase。

    CREATE TABLE IF NOT EXISTS 保证幂等（与 create_all 互补）。
    """
    if not _table_exists(dbapi_conn, "user_crypto_profiles"):
        dbapi_conn.execute("""
            CREATE TABLE user_crypto_profiles (
                user_id INTEGER PRIMARY KEY,
                salt VARCHAR(64) NOT NULL,
                iterations INTEGER NOT NULL DEFAULT 100000,
                kdf VARCHAR(40) NOT NULL DEFAULT 'PBKDF2-SHA256-100000',
                salt_fingerprint VARCHAR(16) NOT NULL DEFAULT '',
                verification_ciphertext VARCHAR(512) NOT NULL,
                verification_nonce VARCHAR(64) NOT NULL,
                created_at DATETIME,
                updated_at DATETIME
            )
        """)


_MIGRATIONS = {
    1: _run_migration_v1,
    2: _run_migration_v2,
    3: _run_migration_v3,
    4: _run_migration_v4,
    5: _run_migration_v5,
    6: _run_migration_v6,
    7: _run_migration_v7,
    8: _run_migration_v8,
    9: _run_migration_v9,
    10: _run_migration_v10,
    11: _run_migration_v11,
    12: _run_migration_v12,
}


def _ensure_schema_version(dbapi_conn) -> None:
    """P2-11: 基于 PRAGMA user_version 执行增量迁移。

    读取当前 user_version，依次执行所有 (current, TARGET_SCHEMA_VERSION] 的迁移，
    每完成一个版本就更新 user_version，确保迁移幂等且可断点续跑。
    """
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA user_version")
    row = cur.fetchone()
    current = row[0] if row else 0
    cur.close()

    for version in range(current + 1, TARGET_SCHEMA_VERSION + 1):
        migration = _MIGRATIONS.get(version)
        if migration is None:
            continue
        migration(dbapi_conn)
        # 迁移成功后再写入版本号，失败时由事务回滚保证一致性
        cur = dbapi_conn.cursor()
        cur.execute(f"PRAGMA user_version = {version}")
        cur.close()


def _migrate_handoffs_columns() -> None:
    """P0: 向旧库的 handoffs 表补齐 status / confirmed_at / confirmed_by 列。

    create_all 不会向已存在的表追加列，因此需要显式 ALTER TABLE。
    P2-11: 现已由 _ensure_schema_version(v1) 统一管理，保留此函数仅为向后兼容。
    """
    insp = inspect(engine)
    if not insp.has_table("handoffs"):
        return
    existing = {col["name"] for col in insp.get_columns("handoffs")}
    with engine.begin() as conn:
        if "status" not in existing:
            conn.execute(text("ALTER TABLE handoffs ADD COLUMN status VARCHAR(20) DEFAULT 'draft' NOT NULL"))
        if "confirmed_at" not in existing:
            conn.execute(text("ALTER TABLE handoffs ADD COLUMN confirmed_at DATETIME NULL"))
        if "confirmed_by" not in existing:
            conn.execute(text("ALTER TABLE handoffs ADD COLUMN confirmed_by VARCHAR(60) NULL"))


def _migrate_projects_status() -> None:
    """第二批升级：向旧库的 projects 表补齐 status 列。

    create_all 不会向已存在的表追加列，因此需要显式 ALTER TABLE。
    P2-11: 现已由 _ensure_schema_version(v1) 统一管理，保留此函数仅为向后兼容。
    """
    insp = inspect(engine)
    if not insp.has_table("projects"):
        return
    existing = {col["name"] for col in insp.get_columns("projects")}
    with engine.begin() as conn:
        if "status" not in existing:
            conn.execute(text("ALTER TABLE projects ADD COLUMN status VARCHAR(20) DEFAULT 'active' NOT NULL"))


def _seed_demo_data() -> None:
    """演示种子数据：空库时预置 1 个示例项目 + 3 个任务 + 1 份规则引擎交接摘要。

    幂等性：通过 vannevar_meta 表的 'seeded' 标记保证只播种一次。
    即使用户删空所有项目后重启，也不会重新播种（避免覆盖用户意愿）。
    让用户首次登录即可看到完整看板效果与停滞提醒。
    """
    import secrets as _secrets
    from datetime import datetime, timedelta, timezone

    from models import Handoff, Project, Task, VannevarMeta

    db = SessionLocal()
    try:
        # B-P2-1: 用 meta 表标记幂等，避免用户删空项目后重启被重新播种
        seeded = db.query(VannevarMeta).filter_by(key="seeded").first()
        if seeded is not None and seeded.value == "true":
            return

        if db.query(Project).count() > 0:
            return

        now = datetime.now(timezone.utc)
        project = Project(
            name="周末探访老人院",
            description="每周六下午探访社区老人院，陪伴聊天、读报、简单文娱活动",
            status="active",
            qr_token=_secrets.token_urlsafe(16),
            created_at=now,
            updated_at=now,
        )
        db.add(project)
        db.flush()

        # 任务1: 待办
        db.add(Task(
            project_id=project.id,
            title="本周读报素材准备",
            status="todo",
            owner_name="小李",
            created_at=now,
            updated_at=now,
        ))
        # 任务2: 进行中，updated_at=7天前（为停滞提醒做铺垫）
        seven_days_ago = now - timedelta(days=7)
        db.add(Task(
            project_id=project.id,
            title="周六下午探访执行",
            status="in_progress",
            owner_name="小王",
            created_at=seven_days_ago,
            updated_at=seven_days_ago,
        ))
        # 任务3: 已完成
        db.add(Task(
            project_id=project.id,
            title="上周活动总结归档",
            status="done",
            owner_name="小张",
            created_at=now,
            updated_at=now,
        ))

        # 交接摘要：规则引擎格式（【自动生成于 ...】前缀），UI 会显示"⚙️ 规则引擎"徽章
        ts_str = now.strftime("%Y-%m-%d %H:%M")
        summary = (
            f"【自动生成于 {ts_str}】\n"
            "\n"
            "未完成任务（2）：\n"
            "- [待办] 本周读报素材准备（小李）\n"
            "- [进行中] 周六下午探访执行（小王）\n"
            "\n"
            "近 7 天留言（0）：\n"
            "- 近 7 天无留言\n"
            "\n"
            "任务进度：1/3 已完成"
        )
        db.add(Handoff(
            project_id=project.id,
            summary=summary,
            outgoing_volunteer="小王",
            incoming_volunteer="小李",
            status="draft",
            created_at=now,
        ))

        # B-P2-1: 标记已播种，下次启动即使项目被删空也不重新播种
        db.add(VannevarMeta(key="seeded", value="true"))

        db.commit()
    except Exception:
        # B-P1-1: 种子插入失败时回滚并记日志，不阻断应用启动
        db.rollback()
        logger.exception("种子数据插入失败，跳过")
    finally:
        db.close()


def _backup_db_before_migration(current_version: int) -> None:
    """DRL-P1: 迁移前自动备份文件数据库，防止迁移失败导致数据丢失。

    仅对文件数据库备份（跳过 :memory:），备份文件名格式：bridge_mvp.db.v{N}.bak。
    DRL-R1-V20: 改用 VACUUM INTO（与 backup.py 一致），保证备份一致性，
    避免 shutil.copy2 在 WAL 模式下复制到不一致的 .db/.db-wal 组合。
    迁移失败时可通过 shutil.copy2 回滚。
    """
    if ":memory:" in DATABASE_URL:
        return
    if not DB_PATH.exists():
        return
    bak_path = DB_PATH.parent / f"bridge_mvp.db.v{current_version}.bak"
    try:
        # DRL-R1-V20: 优先用 VACUUM INTO（在线一致性备份）
        # DRL-R1-V28: 转义 backup_path 中的单引号，防止 SQL 注入
        escaped_path = str(bak_path).replace("'", "''")
        with engine.connect() as conn:
            conn.execute(text(f"VACUUM INTO '{escaped_path}'"))
        # DRL-R1-V12: 备份文件设置 0o600 权限（仅所有者可读写）
        _try_chmod(bak_path, 0o600)
        logger.info("迁移前备份：%s → %s（源版本 v%d）", DB_PATH, bak_path, current_version)
    except Exception as e:
        # VACUUM INTO 失败时回退到 shutil.copy2 + wal_checkpoint
        logger.warning("VACUUM INTO 备份失败，回退到 copy2：%s", e)
        try:
            # 先 checkpoint WAL，确保所有数据写入主 .db 文件
            with engine.connect() as conn:
                conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            shutil.copy2(DB_PATH, bak_path)
            _try_chmod(bak_path, 0o600)
            logger.info("迁移前备份（copy2 回退）：%s → %s", DB_PATH, bak_path)
        except OSError as copy_err:
            logger.warning("迁移前备份失败（继续迁移）：%s", copy_err)


def init_db():
    # 显式导入模型：models 模块在被导入时才把表注册到 Base.metadata。
    # 此前依赖 main.py 先 import routers → models 的副作用，导致
    # 「单独调用 init_db()」会报 no such table: vannevar_meta。
    # 函数内导入以避免 database ↔ models 的模块级循环依赖。
    import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # P2-11: 通过 PRAGMA user_version 追踪迁移版本，增量执行
    if DATABASE_URL.startswith("sqlite"):
        with engine.begin() as conn:
            dbapi_conn = conn.connection
            # DRL-P1: 迁移前备份文件数据库（方案 A.1 要求）
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA user_version")
            row = cur.fetchone()
            current_ver = row[0] if row else 0
            cur.close()
            if current_ver < TARGET_SCHEMA_VERSION:
                _backup_db_before_migration(current_ver)
            _ensure_schema_version(dbapi_conn)
    else:
        # 非 SQLite 后备路径：保留旧逻辑以兼容其他数据库
        _migrate_handoffs_columns()
        _migrate_projects_status()
    # DRL-R1-V12: 数据库文件设置 0o600 权限（仅所有者可读写），Windows 下静默忽略
    if ":memory:" not in DATABASE_URL and DB_PATH.exists():
        _try_chmod(DB_PATH, 0o600)
    # 演示数据增强：空库时预置示例项目 + 任务 + 交接摘要
    _seed_demo_data()
