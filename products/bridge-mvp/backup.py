# backup.py — Bridge MVP 数据库备份与恢复工具
#
# W087b: 应对 bridge_mvp.db 损坏风险，提供：
#   - backup_db(): 启动时自动备份（保留最近 N 份）
#   - restore_db(backup_name): 从指定备份恢复
#   - list_backups(): 列出可用备份
#   - integrity_check(): 数据库完整性检查（启动时自动执行）
#
# 备份策略：
#   - 启动时自动备份到 backups/ 目录，保留最近 7 份
#   - 备份文件名格式：bridge_mvp_YYYYMMDD_HHMMSS.db
#   - 使用 SQLite Online Backup API（VACUUM INTO），不阻塞读写
#   - 启动时执行 PRAGMA integrity_check，损坏则自动从最近备份恢复

import logging
import os
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from database import DB_PATH as _DEFAULT_DB_PATH, engine

logger = logging.getLogger("bridge.backup")


def _resolve_db_path() -> Path:
    """推导**实际使用**的数据库文件路径。

    开源发布改造（2026-10-25，源自独立盲审 P1-3）：
    `database.DB_PATH` 只是「默认路径」。当 `BRIDGE_DATABASE_URL` 指向别处时
    （典型如 Docker 部署的 `sqlite:///data/bridge.db`），备份与完整性检查
    仍针对默认路径 —— 表现为 backup_db() 静默不备份、list_backups() 恒空，
    整套备份/恢复体系失效。故此处以 engine 的实际 URL 为准。
    """
    try:
        url = engine.url
        if url.drivername.startswith("sqlite"):
            db = url.database
            # 相对路径按进程工作目录解析（与 SQLite 语义一致）
            if db and db != ":memory:":
                return Path(db).expanduser().resolve()
    except Exception as e:
        logger.warning("[Backup] 无法从 engine.url 推导数据库路径: %s", e)
    return Path(_DEFAULT_DB_PATH).resolve()


# 实际数据库文件路径（可被测试以 monkeypatch 覆盖）
DB_PATH = _resolve_db_path()
# 备份目录：与数据库同级，保证「库可写则备份可写」
# Docker 部署下库在 /app/data/bridge.db（卷挂载，可写）→ 备份落在 /app/data/backups
BACKUP_DIR = DB_PATH.parent / "backups"
# DRL-R1-V18: MAX_BACKUPS 范围校验，限制在 [1, 100]，防止环境变量误配为 0 或极大值
MAX_BACKUPS = max(1, min(int(os.environ.get("BRIDGE_MAX_BACKUPS", "7")), 100))

# 恢复是「独占」操作：同一时刻只允许一个恢复流程（进程内互斥）。
# 并发恢复会导致两个流程同时替换同一个库文件。
_RESTORE_LOCK = threading.Lock()


def integrity_check() -> bool:
    """执行 PRAGMA integrity_check，返回 True 表示数据库健康。

    SQLite 官方推荐的健康检查方式，检测结果：
        - "ok" 表示数据库完整
        - 其他多行输出表示存在损坏，需要恢复
    """
    if not DB_PATH.exists():
        # 数据库文件不存在是正常情况（首次启动），不算损坏
        return True
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA integrity_check")).fetchone()
            return result is not None and result[0] == "ok"
    except Exception as e:
        logger.error("[Backup] integrity_check 执行失败: %s", e)
        return False


def _file_integrity_ok(db_path: Path) -> bool:
    """对任意 SQLite 文件做完整性检查（独立连接，不依赖全局 engine）。

    为什么需要独立连接：恢复流程中全局 engine 可能仍持有旧文件的连接，
    用它做校验会读到「替换前」的内容，导致恢复被误判为失败。
    """
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            conn.close()
    except Exception as e:
        logger.error("[Backup] 完整性检查失败 %s: %s", db_path.name, e)
        return False


def _dispose_engine() -> None:
    """释放引擎连接池中的所有连接，解除对数据库文件的占用。

    Windows 上只要还有连接句柄未关闭，对该文件的 rename / 覆盖就可能失败
    或产生不可靠的写入结果（本次缺陷的直接成因）。
    """
    for name, eng in (("backup.engine", engine),):
        try:
            eng.dispose()
        except Exception as e:
            logger.warning("[Backup] 释放引擎连接失败 (%s): %s", name, e)
    try:
        from database import engine as _db_engine
        _db_engine.dispose()
    except Exception as e:
        logger.warning("[Backup] 释放 database.engine 失败: %s", e)


def _clear_sidecars(db_path: Path) -> None:
    """删除 WAL 模式下的侧车文件（.db-wal / .db-shm）。

    侧车文件属于「被替换前的旧数据库」。恢复时若不清除，SQLite 可能用旧
    WAL 回放页，把刚恢复好的库再次写坏——这正是「恢复报告成功但结果仍损坏」
    的机制之一。

    注意后缀写法：侧车是在库文件名**之后**追加 `-wal` / `-shm`，
    即 `bridge_mvp.db-wal`；若写成 `.db-wal` 会拼出 `bridge_mvp.db.db-wal`，
    永远匹配不到真实文件（该缺陷曾让本函数静默空转）。
    """
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
                logger.info("[Backup] 已清理侧车文件: %s", sidecar.name)
            except OSError as e:
                logger.warning("[Backup] 清理侧车文件失败 %s: %s", sidecar.name, e)


def backup_db() -> Path | None:
    """创建数据库备份，返回备份文件路径。

    使用 SQLite 的 VACUUM INTO（SQLite 3.27+）实现在线备份：
        - 不阻塞读写（WAL 模式下）
        - 生成紧凑的备份文件（自动 VACUUM）
        - 比文件复制更安全（保证一致性）

    备份后清理旧备份，只保留最近 MAX_BACKUPS 份。
    """
    if not DB_PATH.exists():
        logger.info("[Backup] 数据库文件不存在，跳过备份")
        return None

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # 只读文件系统（如 Docker read_only: true 且备份目录不在可写卷内）
        # 会让 mkdir 直接抛出未捕获异常，把整个启动流程带崩。
        logger.error("[Backup] 无法创建备份目录 %s: %s", BACKUP_DIR, e)
        return None
    # DRL-R1-V12: 备份目录设置 0o700 权限（仅所有者可进入），Windows 下静默忽略
    try:
        os.chmod(BACKUP_DIR, 0o700)
    except (OSError, NotImplementedError):
        pass

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"bridge_mvp_{timestamp}.db"

    try:
        # VACUUM INTO: 在线备份，不阻塞读写，生成紧凑文件
        # DRL-R1-V28: 转义 backup_path 中的单引号，防止 SQL 注入（路径来自文件系统，但仍需防御）
        escaped_path = str(backup_path).replace("'", "''")
        with engine.connect() as conn:
            conn.execute(text(f"VACUUM INTO '{escaped_path}'"))
        # DRL-R1-V12: 备份文件设置 0o600 权限（仅所有者可读写）
        try:
            os.chmod(backup_path, 0o600)
        except (OSError, NotImplementedError):
            pass
        logger.info("[Backup] 备份成功: %s (%d bytes)", backup_path.name, backup_path.stat().st_size)
    except Exception as e:
        logger.error("[Backup] VACUUM INTO 失败，回退到文件复制: %s", e)
        # 回退方案：直接文件复制。
        # 复制前先把 WAL 事务落盘（wal_checkpoint TRUNCATE）：否则未 checkpoint 的
        # 事务只存在于 -wal 侧车里，而恢复流程只复制主 .db —— 会静默丢事务
        # 且 integrity_check 仍返回 ok（不报错），极难察觉。
        try:
            with engine.connect() as conn:
                conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        except Exception as ck_err:
            logger.warning("[Backup] wal_checkpoint 失败，回退复制可能丢事务: %s", ck_err)
        try:
            shutil.copy2(DB_PATH, backup_path)
            wal_path = Path(str(DB_PATH) + "-wal")
            if wal_path.exists():
                shutil.copy2(wal_path, Path(str(backup_path) + "-wal"))
            # DRL-R1-V19: 同时复制 .db-shm 文件（WAL 共享内存映射），缺失会导致恢复后首次打开异常
            shm_path = Path(str(DB_PATH) + "-shm")
            if shm_path.exists():
                shutil.copy2(shm_path, Path(str(backup_path) + "-shm"))
            # DRL-R1-V12: 备份文件设置 0o600 权限
            try:
                os.chmod(backup_path, 0o600)
            except (OSError, NotImplementedError):
                pass
            logger.info("[Backup] 文件复制备份成功: %s", backup_path.name)
        except Exception as copy_err:
            logger.error("[Backup] 文件复制也失败: %s", copy_err)
            return None

    # 清理旧备份，只保留最近 MAX_BACKUPS 份
    _cleanup_old_backups()
    return backup_path


def _cleanup_old_backups() -> None:
    """清理旧备份，只保留最近 MAX_BACKUPS 份。"""
    if not BACKUP_DIR.exists():
        return
    backups = sorted(
        [f for f in BACKUP_DIR.iterdir() if f.name.startswith("bridge_mvp_") and f.suffix == ".db"],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[MAX_BACKUPS:]:
        try:
            old_backup.unlink()
            # 同时清理对应的 wal/shm 文件（如果存在）
            for suffix in [".db-wal", ".db-shm"]:
                sidecar = old_backup.with_suffix(suffix)
                if sidecar.exists():
                    sidecar.unlink()
            logger.info("[Backup] 清理旧备份: %s", old_backup.name)
        except Exception as e:
            logger.warning("[Backup] 清理旧备份失败 %s: %s", old_backup.name, e)


def list_backups() -> list[dict]:
    """列出所有可用备份，按时间倒序排列。

    开源发布改造（2026-10-25，源自独立盲审 P2-3）：原先每项都带服务器上的
    **绝对路径**，而该列表经 `/api/backups` 端点原样返回给前端 —— 等于把
    服务器目录结构泄露出去。现只返回文件名、大小与时间。
    """
    if not BACKUP_DIR.exists():
        return []
    backups = []
    for f in BACKUP_DIR.iterdir():
        if f.name.startswith("bridge_mvp_") and f.suffix == ".db":
            stat = f.stat()
            backups.append({
                "name": f.name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "_ts": stat.st_mtime,
            })
    # 按真实修改时间倒序（用数值而非格式化字符串，避免同秒文件的排序不稳定）
    backups.sort(key=lambda b: b["_ts"], reverse=True)
    for b in backups:
        b.pop("_ts", None)
    return backups


def restore_db(backup_name: str) -> bool:
    """从指定备份恢复数据库。

    恢复流程（每一步都有对应的回归测试，见 tests/test_backup_recovery.py）：
        1. 校验备份文件本身完整——损坏的备份一律拒绝，避免二次损坏
        2. 释放引擎连接池，解除对数据库文件的占用（Windows 文件锁）
        3. 保留原损坏库的审计副本（.corrupt_YYYYMMDD_HHMMSS.db）
        4. 复制备份到临时文件 → 校验临时文件 → 原子替换（os.replace）
        5. 清除属于旧库的 .db-wal / .db-shm 侧车文件
        6. 用独立连接复核恢复结果

    Args:
        backup_name: 备份文件名（如 bridge_mvp_20260708_120000.db）

    Returns:
        True 表示恢复成功且恢复后的数据库通过完整性检查
    """
    # 恢复是独占操作：并发恢复会让两个流程同时替换同一个库文件。
    # 进程内互斥 + 非阻塞：已有恢复在跑时立即拒绝，而不是排队挂起请求。
    if not _RESTORE_LOCK.acquire(blocking=False):
        logger.error("[Restore] 已有恢复流程在进行中，拒绝并发恢复")
        return False
    try:
        return _restore_db_locked(backup_name)
    finally:
        _RESTORE_LOCK.release()


def _restore_db_locked(backup_name: str) -> bool:
    """restore_db 的实际实现（调用方需持有 _RESTORE_LOCK）。"""
    backup_path = BACKUP_DIR / backup_name
    if not backup_path.exists():
        logger.error("[Restore] 备份文件不存在: %s", backup_name)
        return False

    # 1) 备份自身必须完整。backups/ 目录实测混有损坏副本，
    #    用损坏备份覆盖当前库会把「可恢复」变成「不可恢复」。
    if not _file_integrity_ok(backup_path):
        logger.error("[Restore] 备份文件自身损坏，拒绝恢复: %s", backup_name)
        return False

    # 2) 先释放连接池再动文件。连接未释放时，Windows 上 rename 会失败，
    #    而直接覆盖一个仍被打开的文件会产生不可靠的写入结果。
    _dispose_engine()

    # 3) 保留原损坏库的审计副本（重命名失败时退回复制）
    corrupt_path = None
    if DB_PATH.exists():
        corrupt_name = f"bridge_mvp.corrupt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        corrupt_path = DB_PATH.parent / corrupt_name
        try:
            DB_PATH.rename(corrupt_path)
            logger.info("[Restore] 损坏数据库已保留: %s", corrupt_name)
        except Exception as e:
            logger.warning("[Restore] 重命名损坏数据库失败: %s — 改用复制保留审计副本", e)
            try:
                shutil.copy2(DB_PATH, corrupt_path)
                logger.info("[Restore] 损坏数据库已复制保留: %s", corrupt_name)
            except Exception as copy_err:
                logger.warning("[Restore] 保留损坏数据库副本失败: %s", copy_err)
                corrupt_path = None

    # 4) 复制到临时文件 → 校验 → 原子替换。直接覆盖目标路径会在中途失败时
    #    留下半成品库，且无法回滚。
    tmp_path = DB_PATH.with_name(f"{DB_PATH.name}.restore_tmp_{os.getpid()}")
    try:
        shutil.copy2(backup_path, tmp_path)
        if not _file_integrity_ok(tmp_path):
            raise RuntimeError("恢复用的临时副本完整性检查未通过")
        os.replace(tmp_path, DB_PATH)
        logger.info("[Restore] 原子替换完成: %s -> %s", backup_name, DB_PATH.name)
    except Exception as e:
        logger.error("[Restore] 恢复失败: %s", e)
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        # 回滚：把保留的损坏副本放回原位，避免把原始文件也弄丢
        if corrupt_path and corrupt_path.exists() and not DB_PATH.exists():
            try:
                shutil.copy2(corrupt_path, DB_PATH)
                logger.info("[Restore] 已回滚到原始损坏库副本")
            except Exception as rb_err:
                logger.error("[Restore] 回滚失败: %s", rb_err)
        return False

    # 5) 清除属于旧库的 WAL 侧车文件
    _clear_sidecars(DB_PATH)

    # 6) 权限 + 释放可能残留的连接
    try:
        os.chmod(DB_PATH, 0o600)
    except (OSError, NotImplementedError):
        pass
    _dispose_engine()

    # 7) 用独立连接复核（不复用连接池，避免读到替换前的内容）
    if not _file_integrity_ok(DB_PATH):
        logger.error("[Restore] 恢复后完整性检查仍失败: %s", backup_name)
        return False

    logger.info("[Restore] 从备份恢复成功: %s -> %s", backup_name, DB_PATH.name)
    return True


def auto_backup_on_startup() -> None:
    """启动时自动执行：完整性检查 + 备份。

    流程：
        1. integrity_check() — 检查数据库完整性
        2. 如果损坏且存在备份：自动从最近备份恢复
        3. 如果完整或无备份：创建新备份
    """
    if not DB_PATH.exists():
        logger.info("[Backup] 数据库文件不存在，首次启动跳过备份")
        return

    if not integrity_check():
        logger.error("[Backup] 数据库完整性检查失败，尝试从备份恢复")
        backups = list_backups()
        if backups:
            # 逐个尝试：backups/ 目录实测混有损坏副本，只取最新一份会因一次
            # 坏备份而彻底阻断恢复。坏备份由 restore_db 内部校验并拒绝。
            for candidate in backups:
                logger.info("[Backup] 尝试从备份恢复: %s", candidate["name"])
                if restore_db(candidate["name"]):
                    logger.info("[Backup] 恢复成功，继续启动")
                    backup_db()
                    return
                logger.warning("[Backup] 备份 %s 恢复失败，尝试下一份", candidate["name"])
            logger.error("[Backup] 全部 %d 份备份均恢复失败", len(backups))
        else:
            logger.error("[Backup] 无可用备份，数据库可能损坏且无法恢复")
        return

    # 完整性检查通过，创建新备份
    backup_db()
