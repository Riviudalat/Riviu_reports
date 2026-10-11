"""TikTok and Threads link parsing and the video-link highlight rule (pure)."""

import re
from urllib.parse import urlparse

from riviu.workbook.constants import TIKTOK_MEDIA_PHOTO, TIKTOK_MEDIA_VIDEO
from riviu.workbook.text import clean_text, metric_number


TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "mobile.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}


def extract_tiktok_username(url):
    match = re.search(r"tiktok\.com/@([^/?]+)", str(url or ""), re.IGNORECASE)
    return match.group(1).strip() if match else ""


def normalize_tiktok_url(value):
    """Normalize supported TikTok URLs; reject unsafe schemes/hosts/authorities."""
    text = clean_text(value)
    if not text or re.search(r"[\s\\\x00-\x1f\x7f]", text):
        return ""
    if text.startswith("//"):
        text = f"https:{text}"
    elif not re.match(r"^[a-z][a-z0-9+.-]*:", text, flags=re.IGNORECASE):
        text = f"https://{text}"
    try:
        parsed = urlparse(text)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or (parsed.hostname or "").casefold() not in TIKTOK_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80 if parsed.scheme.casefold() == "http" else 443}
        ):
            return ""
    except ValueError:
        return ""
    return text


def is_tiktok_link(value):
    return bool(normalize_tiktok_url(value))


def is_scrapable_tiktok_url(value):
    """True only for a supported TikTok HTTP(S) URL, including short links."""
    return bool(normalize_tiktok_url(value))


def detect_tiktok_media_type(url, *, resolved_url=""):
    """Nhận diện link TikTok là video hay ảnh (photo carousel) từ path URL.

    Trả về TIKTOK_MEDIA_VIDEO, TIKTOK_MEDIA_PHOTO, hoặc '' nếu chưa rõ (link rút gọn).
    """
    # A direct source path is authoritative. The resolved URL only identifies
    # media type for short links whose own path is ambiguous.
    for candidate in (url, resolved_url):
        text = normalize_tiktok_url(candidate).casefold()
        if not text:
            continue
        if "/video/" in text:
            return TIKTOK_MEDIA_VIDEO
        if "/photo/" in text:
            return TIKTOK_MEDIA_PHOTO
    return ""


def is_tiktok_video_link(url, *, resolved_url=""):
    return detect_tiktok_media_type(url, resolved_url=resolved_url) == TIKTOK_MEDIA_VIDEO


def should_highlight_video_link(
    url,
    *,
    likes=0,
    shares=0,
    resolved_url="",
    resolved_source_url="",
    metrics_readable=True,
    scan_status="",
):
    latest_status = clean_text(scan_status)
    source_url = normalize_tiktok_url(url)
    metadata_source_url = normalize_tiktok_url(resolved_source_url)
    # Colouring only: legacy rows without the hidden source cell keep using their stored resolution.
    trusted_resolved_url = resolved_url
    if metadata_source_url and metadata_source_url.casefold() != source_url.casefold():
        trusted_resolved_url = ""
    return (
        metrics_readable
        and (not latest_status or latest_status == "Success")
        and is_tiktok_video_link(url, resolved_url=trusted_resolved_url)
        and (metric_number(likes) > 0 or metric_number(shares) > 0)
    )


def normalize_threads_url(value):
    text = clean_text(value)
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        return f"https://{text}"
    return text


def is_threads_link(value):
    try:
        parsed = urlparse(normalize_threads_url(value))
        host = (parsed.hostname or "").casefold()
    except ValueError:
        return False
    return host in {"threads.com", "www.threads.com", "threads.net", "www.threads.net"} and bool(
        re.fullmatch(r"(?:/@[^/]+/post/|/share/)[A-Za-z0-9_-]+/?", parsed.path)
    )


def extract_threads_username(url):
    if not is_threads_link(url):
        return ""
    match = re.match(r"^/@([^/]+)/post/", urlparse(normalize_threads_url(url)).path)
    return match.group(1) if match else ""


def is_post_url(value):
    """A TikTok video/photo/short link or a Threads post link (not a profile page)."""
    text = normalize_tiktok_url(value)
    if text:
        parsed = urlparse(text)
        host = (parsed.hostname or "").casefold()
        return bool(detect_tiktok_media_type(text)) or host in {"vm.tiktok.com", "vt.tiktok.com"} or parsed.path.startswith("/t/")
    return is_threads_link(value)
