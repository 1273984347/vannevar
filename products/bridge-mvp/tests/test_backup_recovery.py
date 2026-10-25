# test_backup_recovery.py — Bridge 数据库恢复流程回归测试
#
# 覆盖 P-01：主库页级损坏时，backup.py 的自动恢复流程「报告成功但结果仍损坏」，
# 最终把服务卡死在维护模式。本文件用**合成**损坏场景锁定恢复流程的四个不变量：
#
#   INV-1  恢复成功后，DB_PATH 必须通过 PRAGMA integrity_check
#   INV-2  恢复成功后，旧的 -wal / -shm 侧车文件必须被清除
#   INV-3  损坏的备份不得覆盖当前可用的库（恢复前必须校验备份自身）
#   INV-4  恢复流程不得静默丢失原损坏库的审计副本
#
# INV-2 是关键：WAL 模式下 .db-wal / .db-shm 属于「被替换前的旧数据库」，
# 残留时 SQLite 可能用旧 WAL 回放页，把刚恢复好的库再次写坏。

import os
import sqlite3
from pathlib import Path

import pytest

import backup
from backup import restore_db


def _make_healthy_db(path: Path, table: str = "probe") -> None:
    """创建一个结构完整、可通过 integrity_check 的 SQLite 库。"""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute(f"INSERT INTO {table} (v) VALUES ('ok')")
        conn.commit()
    finally:
        conn.close()


def _make_corrupt_db(path: Path) -> None:
    """合成一个页级损坏的 SQLite 库。

    做法：先建健康库写入数据页，再把文件中部（page 1 之后）覆盖为随机字节，
    使 b-tree 页结构失效 => PRAGMA integrity_check 返回非 ok。
    """
    _make_healthy_db(path)
    with open(path, "r+b") as f:
        f.seek(4096)
        f.write(b"\xde\xad\xbe\xef" * 512)


def _make_wal_sidecars(db_path: Path) -> None:
    """让库进入 WAL 模式并留下未 checkpoint 的 -wal / -shm 侧车文件。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE wal_probe (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO wal_probe (v) VALUES ('dirty')")
        conn.commit()
    finally:
        # 关闭最后一个连接会 checkpoint 并删除 wal；要留下侧车必须在连接存活时复制
        pass


def _integrity(path: Path) -> str:
    """完整性检查；库损坏到连 PRAGMA 都执行不了时返回错误描述而非抛异常。"""
    try:
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        return f"ERROR: {e}"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """隔离的 Bridge 数据库沙箱：重定向 DB_PATH / BACKUP_DIR / engine。"""
    db_path = tmp_path / "bridge_mvp.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr(backup, "DB_PATH", db_path)
    monkeypatch.setattr(backup, "BACKUP_DIR", backup_dir)

    # backup.integrity_check() 使用模块级 engine；指向沙箱库
    import sqlalchemy

    eng = sqlalchemy.create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(backup, "engine", eng)
    eng.dispose()

    return {"db": db_path, "backups": backup_dir, "tmp": tmp_path}


# ---------------------------------------------------------------- INV-1


def test_restore_from_good_backup_yields_healthy_db(sandbox):
    """损坏库 + 好备份 -> 恢复后 integrity_check 必须为 ok。"""
    db = sandbox["db"]
    _make_corrupt_db(db)
    assert _integrity(db) != "ok", "前置条件：主库应为损坏状态"

    good = sandbox["backups"] / "bridge_mvp_20260101_000000.db"
    _make_healthy_db(good)

    assert restore_db("bridge_mvp_20260101_000000.db") is True
    assert _integrity(db) == "ok", "恢复后数据库必须通过完整性检查"
    assert backup.integrity_check() is True


def test_restore_keeps_original_corrupt_copy_for_audit(sandbox):
    """INV-4：恢复不得静默丢弃原损坏库，须留下 .corrupt_*.db 审计副本。"""
    db = sandbox["db"]
    _make_corrupt_db(db)
    good = sandbox["backups"] / "bridge_mvp_20260101_000000.db"
    _make_healthy_db(good)

    assert restore_db("bridge_mvp_20260101_000000.db") is True
    corrupt_copies = list(sandbox["tmp"].glob("bridge_mvp.corrupt_*.db"))
    assert len(corrupt_copies) >= 1, "必须保留原损坏库的审计副本"


# ---------------------------------------------------------------- INV-2


def test_restore_removes_stale_wal_sidecars(sandbox):
    """INV-2：恢复后残留的 -wal 侧车必须被清除。

    侧车属于被替换前的旧数据库；残留时 SQLite 可能用旧 WAL 回放页，
    把刚恢复好的库再次写坏——这正是「恢复报告成功但结果仍损坏」的机制。
    （-shm 是共享内存映射文件，由 SQLite 自行重建，无需纳入断言。）
    """
    db = sandbox["db"]
    _make_healthy_db(db)

    # 造出未 checkpoint 的 -wal（保持连接存活以便读取）
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE wal_probe (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO wal_probe (v) VALUES ('dirty')")
    conn.commit()
    wal_path = Path(str(db) + "-wal")
    assert wal_path.exists(), "前置条件：应存在 .db-wal 侧车文件"
    conn.close()

    good = sandbox["backups"] / "bridge_mvp_20260101_000000.db"
    _make_healthy_db(good)

    assert restore_db("bridge_mvp_20260101_000000.db") is True
    assert not wal_path.exists(), "恢复后不得残留 .db-wal"
    assert not Path(str(db) + "-shm").exists(), "恢复后不得残留 .db-shm"


# ---------------------------------------------------------------- INV-3


def test_restore_refuses_corrupt_backup_and_keeps_current_db(sandbox):
    """INV-3：备份自身损坏时必须拒绝恢复，且不得破坏当前库。"""
    db = sandbox["db"]
    _make_healthy_db(db)

    corrupt_backup = sandbox["backups"] / "bridge_mvp_20260101_000000.db"
    _make_corrupt_db(corrupt_backup)

    assert restore_db("bridge_mvp_20260101_000000.db") is False
    assert _integrity(db) == "ok", "拒绝恢复后当前库必须保持可用"


def test_restore_missing_backup_returns_false(sandbox):
    assert restore_db("does_not_exist.db") is False


# ---------------------------------------------------------------- 自动恢复编排


def test_startup_recovery_skips_corrupt_backup_and_uses_next_good(sandbox):
    """自动恢复必须跳过损坏备份，尝试下一份可用备份。

    backups/ 目录实测混有损坏副本；若只取「最新一份」，
    一次坏备份就会让整个恢复流程失败（本次事故的放大器）。
    """
    db = sandbox["db"]
    _make_corrupt_db(db)

    newest = sandbox["backups"] / "bridge_mvp_20260101_020000.db"
    _make_corrupt_db(newest)
    older = sandbox["backups"] / "bridge_mvp_20260101_010000.db"
    _make_healthy_db(older)

    # 制造时间差，确保 newest 更新
    os.utime(newest, (2_000_000_000, 2_000_000_000))
    os.utime(older, (1_000_000_000, 1_000_000_000))

    backup.auto_backup_on_startup()

    assert _integrity(db) == "ok", "应跳过损坏备份、用次新备份恢复成功"


def test_startup_with_no_backups_does_not_crash(sandbox):
    """无可用备份时不得抛出异常（由调用方决定是否进入维护模式）。"""
    db = sandbox["db"]
    _make_corrupt_db(db)
    backup.auto_backup_on_startup()  # 不应抛
