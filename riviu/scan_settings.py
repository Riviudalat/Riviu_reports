"""The admin's scan settings per platform (the "Cấu hình quét" toolbar), kept on the server.

Server mode shares one admin login across machines, so the toolbar's choices live in
`data/scan_settings.json` rather than in one browser: per platform the worker count, the scrape
mode and the on/off flags of "Proxy xoay" and the platform's cookie ("Cookie Threads" /
"Cookie TikTok"). Only these flags are stored, never the proxy list or a cookie; unknown keys
are refused so nothing else can be smuggled in. The scheduler seeds a new schedule's options
from them (`riviu.scheduler.load_schedule`).
"""

import json
import logging
import os

from riviu import server_context as context
from riviu.workbook_utils import PLATFORMS, write_json_atomic


SCRAPE_MODES = ("request", "hybrid", "browser")
WORKER_CHOICES = (1, 3, 5, 8, 10, 20, 25, 30, 50)
DEFAULT_WORKERS = 10
# The page's defaults (PLATFORMS[*].defaultScrapeMode in app.js).
DEFAULT_SCRAPE_MODES = {"tiktok": "request", "threads": "hybrid"}
SETTING_KEYS = ("workers", "scrapeMode", "useProxy", "useSession")
MAX_BODY_BYTES = 4096

logger = logging.getLogger("riviu.scan_settings")


def settings_path(base_dir=None):
    return os.path.join(base_dir or context.EXCEL_DIR, "data", "scan_settings.json")


def default_settings(platform):
    return {"workers": DEFAULT_WORKERS, "scrapeMode": DEFAULT_SCRAPE_MODES.get(platform, "request"), "useProxy": False, "useSession": False}


def _invalid():
    return ValueError("Cấu hình quét không hợp lệ.")


def normalize_platform_settings(platform, raw):
    """One platform's settings; missing keys take the defaults, unknown keys or bad values raise."""
    if raw is None:
        return default_settings(platform)
    if not isinstance(raw, dict) or set(raw) - set(SETTING_KEYS):
        raise _invalid()
    result = default_settings(platform)
    workers = raw.get("workers", result["workers"])
    if isinstance(workers, bool) or not isinstance(workers, int) or workers not in WORKER_CHOICES:
        raise _invalid()
    mode = raw.get("scrapeMode", result["scrapeMode"])
    if mode not in SCRAPE_MODES:
        raise _invalid()
    result.update(workers=workers, scrapeMode=mode)
    for key in ("useProxy", "useSession"):
        value = raw.get(key, result[key])
        if not isinstance(value, bool):
            raise _invalid()
        result[key] = value
    return result


def normalize_settings(payload):
    """`{"platforms": {platform: {"workers", "scrapeMode", "useProxy", "useSession"}}}`; raises ValueError."""
    if not isinstance(payload, dict) or set(payload) - {"platforms"} or not isinstance(payload.get("platforms", {}), dict):
        raise _invalid()
    platforms = payload.get("platforms", {})
    if set(platforms) - set(PLATFORMS):
        raise _invalid()
    return {"platforms": {key: normalize_platform_settings(key, platforms.get(key)) for key in PLATFORMS}}


def load_settings(base_dir=None):
    try:
        with open(settings_path(base_dir), encoding="utf-8") as stream:
            return normalize_settings(json.load(stream))
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError) as error:
        logger.warning("Không đọc được cấu hình quét: %s", error)
    return normalize_settings({})


def save_settings(payload, base_dir=None):
    settings = normalize_settings(payload)
    path = settings_path(base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json_atomic(path, settings)
    return settings
