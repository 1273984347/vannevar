"""chrome.storage.local 压力测试：模拟 Focus/Muse 存储性能。

实际存储方案（读 muse-mvp/core/db.js & focus-mvp/db.js 确认）：
- Muse：SQLite (sql.js) 二进制 → Base64 字符串 → chrome.storage.local['vannevar_muse_db']
  - 表 inspirations(id, title, url, note, mood, selected_text, created_at, pinned)
  - 表 moods(id, name, color, sort_order)
- Focus：SQLite (sql.js) 二进制 → Base64 字符串 → chrome.storage.local['vannevar_focus_db']
  - 表 snapshots(id, title, created_at, tab_count, tabs JSON)
  - 注：任务描述说 Focus 用 IndexedDB 是错误的，实际也是 SQLite + chrome.storage.local

测试维度：
- 100/500/1000 条数据的 JSON 序列化/反序列化耗时（chrome.storage.local 若直接存 JSON 的对照基线）
- SQLite 写入 + 导出 + Base64 编码耗时（模拟真实 persistDb 路径）
- Base64 解码 + SQLite 加载耗时（模拟真实 initDatabase 路径）
- 客户端搜索（模拟 filterSnapshotsByKeyword 逻辑）
"""
import base64
import json
import random
import sqlite3
import string
import sys
import time
from pathlib import Path

random.seed(20260707)  # 固定种子，结果可复现

MOODS = ['克制', '热血', '治愈', '荒诞', '深思', '温暖', '孤独', '辽阔']
DOMAINS = ['douban.com', 'zhihu.com', 'github.com', 'medium.com', 'mp.weixin.qq.com']
CN_POOL = '中文测试内容abcdefg笔记内容hijklmn观点想法灵感记录'


def _rand_str(pool: str, k: int) -> str:
    return ''.join(random.choices(pool, k=k))


def generate_mock_inspiration(i: int) -> dict:
    """生成一条模拟灵感数据（对齐 muse-mvp/core/db.js 的 inspirations 表 schema）。"""
    return {
        'id': i + 1,  # SQLite AUTOINCREMENT 从 1 开始
        'title': f'灵感 #{i:06d}：' + _rand_str(string.ascii_letters, 20),
        'url': f'https://{random.choice(DOMAINS)}/article/{i}',
        'selectedText': _rand_str(CN_POOL, 200),
        'note': _rand_str(CN_POOL, 100),
        'mood': random.choice(MOODS),
        'createdAt': int(time.time() * 1000) - random.randint(0, 30 * 86400000),
        'pinned': 1 if random.random() < 0.1 else 0,
    }


def generate_mock_snapshot(i: int) -> dict:
    """生成一条模拟快照数据（对齐 focus-mvp/db.js 的 snapshots 表 schema）。"""
    tab_count = random.randint(3, 15)
    tabs = []
    for j in range(tab_count):
        tabs.append({
            'title': f'Tab {j} - ' + _rand_str(string.ascii_letters, 15),
            'url': f'https://example.com/{j}',
            'favIconUrl': 'https://example.com/favicon.ico',
        })
    return {
        'id': i + 1,
        'title': f'快照 #{i:06d}',
        'tabs': tabs,
        'createdAt': int(time.time() * 1000) - random.randint(0, 90 * 86400000),
        'contextTags': ['深度专注', '紧急', '创造性'],
    }


# ============ JSON 序列化路径（chrome.storage.local 直接存 JSON 的对照基线） ============

def measure_json_serialize(data):
    start = time.perf_counter()
    json_str = json.dumps(data, ensure_ascii=False)
    elapsed = (time.perf_counter() - start) * 1000
    size_kb = len(json_str.encode('utf-8')) / 1024
    return elapsed, size_kb, json_str


def measure_json_deserialize(json_str):
    start = time.perf_counter()
    json.loads(json_str)
    return (time.perf_counter() - start) * 1000


# ============ SQLite + Base64 路径（muse/focus 真实 persistDb 路径） ============

def _create_muse_schema(conn):
    """对齐 muse-mvp/core/db.js 的 inspirations 表 schema。

    Phase 6.6（2026-07-21）同步 db.js：
      - 基础列：id/title/url/note/mood/selected_text/created_at
      - ALTER 追加列：pinned（旧库兼容）/ updated_at（R5-FE-1 LWW 同步）
      - Phase 4.2 媒体会话：media_type / media_artist / media_album / media_artwork_url
    """
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS inspirations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            mood TEXT NOT NULL DEFAULT '',
            selected_text TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            pinned INTEGER DEFAULT 0,
            updated_at INTEGER,
            media_type TEXT,
            media_artist TEXT,
            media_album TEXT,
            media_artwork_url TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_inspirations_created_at
            ON inspirations(created_at DESC);
    ''')


def _create_focus_schema(conn):
    """对齐 focus-mvp/db.js 的 snapshots 表 schema。

    Phase 6.6（2026-07-21）同步 db.js：
      - 基础列：id/title/created_at/tab_count/tabs
      - ALTER 追加列：updated_at（R5-FE-1 LWW 同步）
    """
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            tab_count INTEGER NOT NULL,
            tabs TEXT NOT NULL,
            updated_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_created_at
            ON snapshots(created_at DESC);
    ''')


def measure_sqlite_persist_muse(data):
    """模拟 muse-mvp/core/db.js 的 persistDb：SQLite 写入 + export + Base64 编码。"""
    start = time.perf_counter()
    conn = sqlite3.connect(':memory:')
    try:
        _create_muse_schema(conn)
        conn.executemany(
            'INSERT INTO inspirations (title, url, note, mood, selected_text, created_at, pinned) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            [(d['title'], d['url'], d['note'], d['mood'], d['selectedText'], d['createdAt'], d['pinned'])
             for d in data],
        )
        conn.commit()
        # 模拟 db.export()：把内存数据库 dump 为二进制
        dump = conn.serialize()
    finally:
        conn.close()
    # 模拟 uint8ArrayToBase64
    b64 = base64.b64encode(dump)
    elapsed = (time.perf_counter() - start) * 1000
    size_kb = len(b64) / 1024
    return elapsed, size_kb, b64


def measure_sqlite_persist_focus(data):
    """模拟 focus-mvp/db.js 的 persistDb：SQLite 写入 + export + Base64 编码。"""
    start = time.perf_counter()
    conn = sqlite3.connect(':memory:')
    try:
        _create_focus_schema(conn)
        conn.executemany(
            'INSERT INTO snapshots (title, created_at, tab_count, tabs) VALUES (?, ?, ?, ?)',
            [(d['title'], d['createdAt'], len(d['tabs']), json.dumps(d['tabs'], ensure_ascii=False))
             for d in data],
        )
        conn.commit()
        dump = conn.serialize()
    finally:
        conn.close()
    b64 = base64.b64encode(dump)
    elapsed = (time.perf_counter() - start) * 1000
    size_kb = len(b64) / 1024
    return elapsed, size_kb, b64


def measure_sqlite_load(b64, schema: str):
    """模拟 initDatabase：Base64 解码 + SQLite 加载。"""
    start = time.perf_counter()
    dump = base64.b64decode(b64)
    conn = sqlite3.connect(':memory:')
    try:
        conn.deserialize(dump)
        # 触发一次真实读取（模拟应用启动时 SELECT）
        if schema == 'muse':
            conn.execute('SELECT COUNT(*) FROM inspirations').fetchone()
        else:
            conn.execute('SELECT COUNT(*) FROM snapshots').fetchone()
    finally:
        conn.close()
    return (time.perf_counter() - start) * 1000


# ============ 搜索性能 ============

def measure_search_muse(data, keyword):
    """模拟 muse 客户端搜索（按 title/url/selectedText/note 模糊匹配）。"""
    kw = keyword.lower()
    start = time.perf_counter()
    results = []
    for item in data:
        text = f"{item.get('title', '')} {item.get('url', '')} {item.get('selectedText', '')} {item.get('note', '')}"
        if kw in text.lower():
            results.append(item)
    return (time.perf_counter() - start) * 1000, len(results)


def measure_search_focus(data, keyword):
    """模拟 focus-mvp/db.js 的 searchSnapshots（SQL LIKE 标题）。

    Python 端用 sqlite3 重放相同 SQL，得到的耗时与 sql.js 同数量级。
    P3.6 schema 对齐（DRL R2 class-level）：SELECT 列数与 db.js searchSnapshots
    完全一致（6 列：id/title/created_at/updated_at/tab_count/tabs），避免少列
    导致测量偏低。
    """
    conn = sqlite3.connect(':memory:')
    try:
        _create_focus_schema(conn)
        conn.executemany(
            'INSERT INTO snapshots (title, created_at, tab_count, tabs) VALUES (?, ?, ?, ?)',
            [(d['title'], d['createdAt'], len(d['tabs']), json.dumps(d['tabs'], ensure_ascii=False))
             for d in data],
        )
        conn.commit()
        escaped = keyword.replace('%', r'\%').replace('_', r'\_')
        start = time.perf_counter()
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at, tab_count, tabs "
            "FROM snapshots WHERE title LIKE ? ESCAPE '\\' ORDER BY created_at DESC LIMIT 50",
            (f'%{escaped}%',),
        ).fetchall()
        elapsed = (time.perf_counter() - start) * 1000
    finally:
        conn.close()
    return elapsed, len(rows)


# ============ 主流程 ============

def _fmt_row(values):
    return ' | '.join(f'{v:>12}' if isinstance(v, float) else f'{v:>12}' for v in values)


def run_stress_test():
    print('=' * 78)
    print('Vannevar 存储压力测试报告')
    print('=' * 78)
    print('测试场景：100 / 500 / 1000 条数据')
    print('真实存储路径：SQLite (sql.js) 二进制 → Base64 → chrome.storage.local')
    print(f'Python: {sys.version.split()[0]} | 复现种子: 20260707')
    print()

    results = {}  # 收集实际数字供报告使用

    # ---------------- Muse ----------------
    print('=' * 78)
    print('Muse（灵感数据，存 vannevar_muse_db）')
    print('=' * 78)
    header = f'{"数量":>6} | {"JSON序列化(ms)":>14} | {"JSON反序列化(ms)":>16} | {"JSON大小(KB)":>12} | {"SQLite持久化(ms)":>16} | {"SQLite加载(ms)":>14} | {"SQLite大小(KB)":>14} | {"搜索(ms)":>10}'
    print(header)
    print('-' * len(header))
    results['muse'] = []
    for count in [100, 500, 1000]:
        data = [generate_mock_inspiration(i) for i in range(count)]
        j_ser, j_size, j_str = measure_json_serialize(data)
        j_de = measure_json_deserialize(j_str)
        s_per, s_size, b64 = measure_sqlite_persist_muse(data)
        s_load = measure_sqlite_load(b64, 'muse')
        search_ms, match = measure_search_muse(data, 'test')
        results['muse'].append({
            'count': count, 'j_ser': j_ser, 'j_de': j_de, 'j_size': j_size,
            's_per': s_per, 's_load': s_load, 's_size': s_size, 'search': search_ms, 'match': match,
        })
        print(f'{count:>6} | {j_ser:>14.2f} | {j_de:>16.2f} | {j_size:>12.1f} | {s_per:>16.2f} | {s_load:>14.2f} | {s_size:>14.1f} | {search_ms:>10.2f}')

    # ---------------- Focus ----------------
    print()
    print('=' * 78)
    print('Focus（快照数据，存 vannevar_focus_db）')
    print('=' * 78)
    print(header)
    print('-' * len(header))
    results['focus'] = []
    for count in [100, 500, 1000]:
        data = [generate_mock_snapshot(i) for i in range(count)]
        j_ser, j_size, j_str = measure_json_serialize(data)
        j_de = measure_json_deserialize(j_str)
        s_per, s_size, b64 = measure_sqlite_persist_focus(data)
        s_load = measure_sqlite_load(b64, 'focus')
        search_ms, match = measure_search_focus(data, '快照')
        results['focus'].append({
            'count': count, 'j_ser': j_ser, 'j_de': j_de, 'j_size': j_size,
            's_per': s_per, 's_load': s_load, 's_size': s_size, 'search': search_ms, 'match': match,
        })
        print(f'{count:>6} | {j_ser:>14.2f} | {j_de:>16.2f} | {j_size:>12.1f} | {s_per:>16.2f} | {s_load:>14.2f} | {s_size:>14.1f} | {search_ms:>10.2f}')

    # ---------------- 配额检查 ----------------
    print()
    print('=' * 78)
    print('chrome.storage.local 配额检查')
    print('=' * 78)
    QUOTA_BYTES = 10485760  # chrome.storage.local.QUOTA_BYTES
    # P2 修复（DRL R2 F3）：收集 FAIL 状态用于最终 sys.exit，使 CI 能基于退出码 gate
    fail_count = 0
    for product in ['muse', 'focus']:
        for r in results[product]:
            pct = r['s_size'] * 1024 / QUOTA_BYTES * 100
            status = 'OK 远低于上限' if pct < 50 else ('WARN 接近上限' if pct < 90 else 'FAIL 超限风险')
            if status.startswith('FAIL'):
                fail_count += 1
            count = r['count']
            size = r['s_size']
            print(f'{product:>6} {count:>5} 条：{size:>8.1f} KB / 10240 KB ({pct:>5.2f}%)  {status}')

    # ---------------- 结论 ----------------
    print()
    print('=' * 78)
    print('结论')
    print('=' * 78)
    muse_1000 = next(r for r in results['muse'] if r['count'] == 1000)
    focus_1000 = next(r for r in results['focus'] if r['count'] == 1000)
    print(f"""
1. chrome.storage.local 容量限制：10 MB（QUOTA_BYTES = 10485760）
2. 1000 条灵感数据 SQLite+Base64 体积约 {muse_1000['s_size']:.1f} KB，远低于 10 MB 限制
3. 1000 条快照数据 SQLite+Base64 体积约 {focus_1000['s_size']:.1f} KB，远低于 10 MB 限制
4. SQLite 持久化（写+导出+编码）1000 条耗时：
   - Muse 灵感 {muse_1000['s_per']:.2f} ms
   - Focus 快照 {focus_1000['s_per']:.2f} ms
5. SQLite 加载（解码+读索引）1000 条耗时：
   - Muse 灵感 {muse_1000['s_load']:.2f} ms
   - Focus 快照 {focus_1000['s_load']:.2f} ms
6. 搜索 1000 条数据耗时：
   - Muse 客户端过滤 {muse_1000['search']:.2f} ms
   - Focus SQL LIKE {focus_1000['search']:.2f} ms
7. 建议：1000 条以内无需分页即可保证体验；Focus 已实现 LIMIT/OFFSET 分页，
   Muse 当前查询走 SQLite 索引，未来数据量增长可直接加分页。
""")

    # 把结果也以 JSON 写入 stdout 末尾，方便后续报告自动提取
    print('--- JSON RESULTS ---')
    print(json.dumps(results, ensure_ascii=False))

    # P2 修复（DRL R2 F3）：基于阈值的最终 PASS/FAIL 退出码
    # 任何配额超限风险（FAIL）→ sys.exit(1)，使 qa_all.py / CI 能基于 returncode gate
    if fail_count > 0:
        print(f'\n[STRESS FAIL] {fail_count} 项配额超限风险，退出码 1')
        sys.exit(1)
    print('\n[STRESS PASS] 所有配额检查均 OK/WARN，退出码 0')


if __name__ == '__main__':
    run_stress_test()
