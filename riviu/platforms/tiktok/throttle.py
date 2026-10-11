"""Process-wide HTTP limiter and back-off state shared by every request of a run.

The semaphore and block/streak values are rebound by configure_request_concurrency,
so other modules read them through this module at call time.
"""
import threading
import time

from riviu.proxy_utils import get_session_proxies
from riviu.platforms.tiktok.constants import DEFAULT_WORKERS, MAX_WORKERS


MAX_CONCURRENT_REQUESTS = 20


REQUEST_SHELL_RETRY_DELAY = 0.35
REQUEST_SHELL_RETRY_DELAY_PROXY = 0.12
REQUEST_CANDIDATE_LIMIT_DIRECT = 4
REQUEST_CANDIDATE_LIMIT_PROXY = 3


_request_semaphore = threading.Semaphore(MAX_CONCURRENT_REQUESTS)
_request_block_lock = threading.Lock()
_request_block_until = 0.0
_network_fail_streak = 0
NETWORK_FAIL_STREAK_PAUSE = 3
NETWORK_FAIL_PAUSE_SECONDS = 5.0
# Direct (no proxy) runs pause every request after TikTok answers 403/429.
RATE_LIMIT_PAUSE_SECONDS = {403: 8.0, 429: 4.0}


def session_uses_proxy():
    return bool(get_session_proxies())


def clamp_worker_count(worker_count, proxy_count=None):
    """Chỉ bó số luồng vào khoảng hợp lệ (1..MAX_WORKERS).

    Không tự giảm luồng dù không có proxy hay ít proxy — luôn chạy đúng số
    luồng người dùng chọn. proxy_count bị bỏ qua; chỉ giữ để caller cũ không lỗi.
    """
    return clamp_int(worker_count, DEFAULT_WORKERS, 1, MAX_WORKERS)


def configure_request_concurrency(worker_count):
    """Reset the module-level HTTP limiter and back-off state for a new run."""
    global _request_semaphore, _network_fail_streak, _request_block_until
    limit = clamp_worker_count(worker_count)
    _request_semaphore = threading.Semaphore(limit)
    with _request_block_lock:
        _network_fail_streak = 0
        _request_block_until = 0.0


def wait_if_request_blocked():
    with _request_block_lock:
        until = _request_block_until
    remaining = until - time.time()
    if remaining > 0:
        time.sleep(remaining)


def note_request_rate_limit(http_code):
    global _request_block_until
    if http_code not in (403, 429) or session_uses_proxy():
        return
    pause = RATE_LIMIT_PAUSE_SECONDS[http_code]
    with _request_block_lock:
        _request_block_until = max(_request_block_until, time.time() + pause)


def note_network_failure():
    """Tạm dừng toàn phiên khi DNS/mạng fail hàng loạt (tránh đốt hết queue)."""
    global _request_block_until, _network_fail_streak
    with _request_block_lock:
        _network_fail_streak += 1
        if _network_fail_streak >= NETWORK_FAIL_STREAK_PAUSE:
            _request_block_until = max(
                _request_block_until,
                time.time() + NETWORK_FAIL_PAUSE_SECONDS,
            )
            _network_fail_streak = 0


def note_network_success():
    global _network_fail_streak
    with _request_block_lock:
        _network_fail_streak = 0


def request_shell_retry_delay():
    return REQUEST_SHELL_RETRY_DELAY_PROXY if session_uses_proxy() else REQUEST_SHELL_RETRY_DELAY


def request_candidate_limit():
    return REQUEST_CANDIDATE_LIMIT_PROXY if session_uses_proxy() else REQUEST_CANDIDATE_LIMIT_DIRECT


def clamp_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))
