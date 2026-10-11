"""Scan status strings and the rules that classify them (hidden, suspected gone, confirmed gone)."""
import re

from riviu.workbook_utils import clean_text


def is_request_rate_limited_status(status):
    text = str(status or "")
    return "HTTP 403" in text or "HTTP 429" in text


def is_transient_network_status(status):
    """DNS / connection reset / timeout / SSL đứt giữa chừng — lỗi tạm, nên retry đủ số lần."""
    text = str(status or "").casefold()
    markers = (
        "getaddrinfo failed",
        "errno 11001",
        "winerror 10054",
        "connection was forcibly closed",
        "connection reset",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "name or service not known",
        "nodename nor servname",
        "network is unreachable",
        "connection aborted",
        "broken pipe",
        "unexpected_eof_while_reading",
        "eof occurred in violation of protocol",
        "ssl:",
        "sslerror",
        "wrong version number",
        "connection refused",
        "remote end closed connection",
        "incomplete read",
    )
    return any(marker in text for marker in markers)


# Phân biệt hai loại "không có số liệu":
# - METRICS_UNREADABLE: HTML có dấu hiệu số liệu nhưng đọc lỗi (hiếm, đáng retry).
# - TIKTOK_NO_STATS: TikTok trả trang rỗng, không hề có số liệu (post ẩn view) -> đếm ẨN, không phải BỊ LỖI.
STATUS_METRICS_UNREADABLE = "Error: Không đọc được số liệu"
STATUS_TIKTOK_NO_STATS = "Ẩn số liệu: TikTok không trả lượt xem"
# Chuỗi cũ (trước khi tách ẩn/lỗi) — vẫn nhận diện khi đọc log/Excel cũ.
STATUS_TIKTOK_NO_STATS_LEGACY = "Lỗi: TikTok không trả số liệu"
STATUS_MEDIA_REDIRECT_MISMATCH = "Error: Redirect không khớp video"
# TikTok's post page answers "item not found" (statusCode 10204, rendered "page
# unavailable") intermittently for live posts. That signal alone is only a
# suspicion and keeps the old numbers; STATUS_TIKTOK_UNAVAILABLE (which clears
# them) requires the independent oEmbed check in confirm_tiktok_post_gone.
STATUS_TIKTOK_NOT_FOUND_UNCONFIRMED = "Error: TikTok báo không tìm thấy bài (chưa xác nhận, giữ số cũ)"
STATUS_TIKTOK_UNAVAILABLE = "Error: Trang TikTok không khả dụng"


def is_hidden_stats_status(status):
    """True khi post TikTok ẩn số liệu (không phải lỗi quét mạng/HTTP)."""
    text = clean_text(status)
    if not text:
        return False
    if text in (STATUS_TIKTOK_NO_STATS, STATUS_TIKTOK_NO_STATS_LEGACY):
        return True
    lowered = text.casefold()
    return (
        "ẩn số liệu" in lowered
        or "tiktok không trả số liệu" in lowered
        or "tiktok không trả lượt xem" in lowered
    )


def is_suspected_gone_status(status):
    """Page-level signs that a post is gone (item not found, HTTP 404/410).

    Live posts show them intermittently, so they never clear numbers by
    themselves; confirm_tiktok_post_gone decides.
    """
    lowered = clean_text(status).casefold()
    return (
        lowered == STATUS_TIKTOK_NOT_FOUND_UNCONFIRMED.casefold()
        or bool(re.fullmatch(r"error:\s*http\s+(?:404|410)(?:\s+.*)?", lowered))
    )


def should_clear_stale_metrics(status):
    """Hidden stats, or a deletion confirmed by two independent signals."""
    return is_hidden_stats_status(status) or clean_text(status) == STATUS_TIKTOK_UNAVAILABLE


def terminal_status_priority(status):
    if is_hidden_stats_status(status):
        return 40
    text = clean_text(status).casefold()
    if re.fullmatch(r"error:\s*http\s+410(?:\s+.*)?", text):
        return 30
    if text == STATUS_TIKTOK_NOT_FOUND_UNCONFIRMED.casefold():
        return 20
    if re.fullmatch(r"error:\s*http\s+404(?:\s+.*)?", text):
        return 10
    return 0


def stronger_terminal_result(current, candidate):
    if current is None:
        return candidate
    if terminal_status_priority(candidate[2]) > terminal_status_priority(current[2]):
        return candidate
    return current
