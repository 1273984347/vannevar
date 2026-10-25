# routers/_token_rate_limit.py — qr_token 校验失败限流共享模块
"""DRL-R1 V1/V10/V17: qr_token 校验失败限流。

提取自 projects.py，供 tasks.py、projects.py、events.py 共用。
按 client_ip 维度统计 token 校验失败次数，超过阈值则临时封禁，
防止 qr_token 被暴力枚举扫描。

注意：
- 多 worker 部署下各进程独立计数（需 Redis 才能跨 worker 共享，本期不改）。
- DRL-R1-V17: 增加 _qr_token_fail_store 最大 key 数限制，防止无界增长。
"""
import logging
import os
import time as _time
from collections import defaultdict as _defaultdict

logger = logging.getLogger(__name__)

# W088c: qr_token 失败限流——防恶意枚举扫描
# 按 client_ip 维度统计 token 校验失败次数，超过阈值则临时封禁
# 默认 5 次失败/5 分钟封禁（可通过环境变量配置）
_QR_TOKEN_FAIL_LIMIT = int(os.getenv("BRIDGE_QR_TOKEN_FAIL_LIMIT", "5"))
_QR_TOKEN_FAIL_WINDOW = int(os.getenv("BRIDGE_QR_TOKEN_FAIL_WINDOW", "300"))  # 5 分钟
_qr_token_fail_store: dict = _defaultdict(list)  # {client_ip: [fail_timestamps, ...]}

# DRL-R1-V17: 限制 _qr_token_fail_store 最大 key 数，防止无界增长
# 超限时驱逐"最近活动最早"的 key（最久未活跃的 IP）
_MAX_FAIL_STORE_KEYS = int(os.getenv("BRIDGE_QR_TOKEN_FAIL_MAX_KEYS", "5000"))


def _evict_oldest_if_needed() -> None:
    """DRL-R1-V17: 当 _qr_token_fail_store key 数超过上限时驱逐最旧 key。

    策略：找到"最近一次失败时间最早"的 IP 驱逐（最久未活跃）。
    """
    if len(_qr_token_fail_store) <= _MAX_FAIL_STORE_KEYS:
        return
    oldest_key = None
    oldest_latest = None
    for ip, timestamps in _qr_token_fail_store.items():
        if not timestamps:
            continue
        # 该 IP 最近一次失败时间（列表末尾是最新追加的，但保险起见取 max）
        latest = max(timestamps)
        if oldest_latest is None or latest < oldest_latest:
            oldest_latest = latest
            oldest_key = ip
    if oldest_key is not None:
        _qr_token_fail_store.pop(oldest_key, None)
        logger.warning(
            "token_fail_store_evicted client_ip=%s reason=max_keys_exceeded cur_size=%d",
            oldest_key,
            len(_qr_token_fail_store),
        )


def _check_token_fail_limit(client_ip: str) -> bool:
    """检查 client_ip 是否已超过 token 失败次数限制。

    Returns:
        True 表示允许访问，False 表示已被临时封禁
    """
    now = _time.time()
    window_start = now - _QR_TOKEN_FAIL_WINDOW
    # 清理过期记录
    recent = [t for t in _qr_token_fail_store[client_ip] if t > window_start]
    if not recent:
        _qr_token_fail_store.pop(client_ip, None)
    else:
        _qr_token_fail_store[client_ip] = recent
    return len(recent) < _QR_TOKEN_FAIL_LIMIT


def _record_token_fail(client_ip: str) -> None:
    """记录一次 token 校验失败。"""
    now = _time.time()
    _qr_token_fail_store[client_ip].append(now)
    # 清理过期记录，避免无界增长
    window_start = now - _QR_TOKEN_FAIL_WINDOW
    _qr_token_fail_store[client_ip] = [t for t in _qr_token_fail_store[client_ip] if t > window_start]
    # DRL-R1-V17: 全局 key 数限制，超限驱逐最旧
    _evict_oldest_if_needed()
