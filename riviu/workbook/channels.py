"""Channel-name display rules: garbage/error detection and per-platform fallbacks (pure)."""

import re

from riviu.workbook.links import extract_threads_username, extract_tiktok_username
from riviu.workbook.text import clean_text, normalize_key


def is_numeric_channel_garbage(value):
    text = clean_text(value)
    if not text:
        return False
    normalized = text.replace(",", "").strip()
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", normalized))


def normalize_channel_display(value):
    text = clean_text(value)
    channel_fixes = {
        "B?o Quy?n": "Bảo Quyên",
    }
    return channel_fixes.get(text, text)


def is_generated_username_channel(value, link=""):
    text = clean_text(value)
    if not text:
        return False
    compact = re.sub(r"\s+", "", text.lstrip("@"))
    if re.fullmatch(r"user\d{4,}.*", compact, flags=re.IGNORECASE):
        return True
    return False


def is_generic_tiktok_channel_name(value):
    key = normalize_key(value)
    if key in {
        "tiktok",
        "make your day",
        "tiktok make your day",
        "tiktok - make your day",
        "screen time breaks",
        "screen time break",
    }:
        return True
    if "tiktok" in key and "make your day" in key:
        return True
    if "screen time" in key:
        return True
    return False


def display_channel_name_from_file(link, raw_channel):
    normalized_channel = normalize_channel_display(raw_channel)
    if not normalized_channel:
        return ""
    if (
        is_numeric_channel_garbage(normalized_channel)
        or is_generated_username_channel(normalized_channel, link)
        or is_generic_tiktok_channel_name(normalized_channel)
    ):
        return "Lỗi"
    return normalized_channel


def resolve_channel_name(link, raw_channel, channel_overrides=None):
    username = extract_tiktok_username(link)
    normalized_channel = display_channel_name_from_file(link, raw_channel)
    if normalized_channel:
        return normalized_channel
    if username and channel_overrides:
        override_name = channel_overrides.get(username.casefold())
        if override_name:
            return override_name
    return ""


def display_threads_channel(link, raw_channel):
    """Channel cell as written, else "@username" from the post URL so the report keeps the row."""
    channel = clean_text(raw_channel)
    if channel:
        return channel
    username = extract_threads_username(link)
    return f"@{username}" if username else ""


def is_failed_channel_name(value):
    text = clean_text(value)
    key = normalize_key(text)
    normalized_for_error = text.casefold().replace("’", "'").replace("‘", "'")
    error_phrases = (
        "couldn't find this account",
        "couldnt find this account",
        "video currently unavailable",
        "video unavailable",
        "page not available",
        "page unavailable",
    )
    return (
        not text
        or key in {"loi", "l?i"}
        or text.casefold().startswith("error:")
        or any(phrase in normalized_for_error for phrase in error_phrases)
        or is_numeric_channel_garbage(text)
        or is_generated_username_channel(text)
        or is_generic_tiktok_channel_name(text)
    )
