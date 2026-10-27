"""The run journal: what one scan has already saved, so an interrupted run can continue.

`data/scan_journal.json` (atomic writes) describes the scan in progress:

    {"version": 1, "journalId", "platform", "fileId", "sheetName", "startedAt", "scheduled",
     "payload": {...},          # the start payload, without proxy text or cookies (see below)
     "proxyFingerprint": {...}, # salted hash of the proxy route(s) the run used, never the list
     "resumeAttempts": 0,       # stall restarts plus process-restart resumes, capped
     "mainDone": false,         # the main pass finished (a retry pass may follow)
     "stopReason": "",          # e.g. Threads HTTP 429: never resumed, never retried
     "rows": [[sheet, row, url, code, retried], ...]}

A row enters `rows` only after the workbook save that wrote it succeeded (the scanners'
`on_rows_saved` hook), so a resumed run skips exactly the rows whose result is on disk.
`code` is "s" success, "h" hidden/no metrics, "r" retryable error, "l" Threads HTTP 429 on a
proxy route the run retired (retried only after the 429 wait), "p" a Threads post the anonymous
page did not show (`invalid_post`: often visible only when logged in; retried only through the
Cookie Threads session), "e" other error.

The journal is removed when the run ends normally, fails, or is cancelled by the user. It stays
behind only when the process dies or is stopped mid-run (crash, container restart, reboot); the
server then resumes it once at startup (`scan_service.resume_interrupted_scan`).

Never persisted: cookies, proxy text or credentials. A cookie session is recorded as a flag
and reloaded from the secure store on resume; proxies are re-read from the saved list.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets

from riviu.platforms.tiktok.statuses import (
    STATUS_METRICS_UNREADABLE,
    is_hidden_stats_status,
    is_request_rate_limited_status,
    is_suspected_gone_status,
    is_transient_network_status,
)
from riviu.workbook_utils import clean_text, format_display_datetime, write_json_atomic

JOURNAL_VERSION = 1
JOURNAL_FILENAME = "scan_journal.json"
# Stall restarts and process-restart resumes together; a crash loop cannot rescan forever.
MAX_RESUME_ATTEMPTS = 2
# No new result for this long: the watchdog cancels the pass (results saved) and resumes it.
STALL_SECONDS = 10 * 60
STALL_CHECK_SECONDS = 30
# Wait after the main pass before rescanning the rows that failed with a transient error.
RETRY_COOLDOWN_SECONDS = {"tiktok": 2 * 60, "threads": 5 * 60}
# Wait when the only rows to retry are Threads login-only posts: the cookie session is the fix.
LOGIN_RETRY_COOLDOWN_SECONDS = 30
# Start-payload keys kept for a resume. No proxy text (it may hold credentials), no cookies.
PAYLOAD_KEYS = (
    "platform", "file_id", "sheet_name", "workers", "scrape_mode", "use_proxy", "partners", "partner",
    "create_result_sheet", "push_to_google", "retry_failed", "use_threads_session", "use_tiktok_session",
)

# Threads error kinds worth one more try after a cooldown. invalid_post is not one of them: the
# same anonymous request fails again, so it is retried only through the cookie session (code "p").
# A rate_limit row (its route was
# retired, others went on) is retried too, but only after the 429 wait and only when the run
# was not stopped by 429 on every route. Never retried: access_denied / proxy_auth /
# geo_restricted / audience (a block is never worked around), redirect / profile_redirect,
# and auth_* (the session is the problem).
THREADS_RETRYABLE_KINDS = frozenset({"transport", "http", "payload", "browser_start"})
THREADS_LOGIN_KINDS = frozenset({"invalid_post"})
_TIKTOK_HTTP_CODE = re.compile(r"\bhttp\s+(\d{3})\b", re.IGNORECASE)

logger = logging.getLogger("riviu.scan_journal")


def journal_path(base_dir):
    return os.path.join(base_dir, "data", JOURNAL_FILENAME)


def load_journal(base_dir):
    try:
        with open(journal_path(base_dir), encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        logger.warning("Không đọc được nhật ký phiên quét: %s", error)
        return None
    if not isinstance(data, dict) or data.get("version") != JOURNAL_VERSION or not isinstance(data.get("payload"), dict):
        return None
    return data


def clear_journal(base_dir):
    try:
        os.remove(journal_path(base_dir))
    except FileNotFoundError:
        pass


def tiktok_retryable(status):
    """Transient TikTok failures: unconfirmed "not found", network/SSL, HTTP 5xx/400/408, a
    stopped worker. Never hidden stats, a confirmed-gone post, a redirect mismatch or 403/429."""
    text = clean_text(status)
    if not text.casefold().startswith("error"):
        return False
    if is_request_rate_limited_status(text):
        return False
    if is_suspected_gone_status(text) or is_transient_network_status(text) or text == STATUS_METRICS_UNREADABLE:
        return True
    match = _TIKTOK_HTTP_CODE.search(text)
    if match:
        code = int(match.group(1))
        return code >= 500 or code in (400, 408)
    return "worker" in text.casefold()


def threads_retryable(status, kind):
    return clean_text(status).startswith("Error:") and kind in THREADS_RETRYABLE_KINDS


def result_code(platform, status, kind=""):
    status = clean_text(status)
    if status == "Success":
        return "s"
    if platform == "threads":
        if not status.startswith("Error:"):
            return "h"
        if kind == "rate_limit":
            return "l"
        if kind in THREADS_LOGIN_KINDS:
            return "p"
        return "r" if threads_retryable(status, kind) else "e"
    if is_hidden_stats_status(status):
        return "h"
    return "r" if tiktok_retryable(status) else "e"


def fingerprint(value, salt):
    """Salted hash of the run's proxy route(s), to refuse a resume on a different route."""
    return hashlib.sha256((salt + json.dumps(value, sort_keys=True, default=str)).encode("utf-8")).hexdigest()


class RunJournal:
    """One run's journal in memory; `save()` writes it, `record()` is the scanners' save hook."""

    def __init__(self, data, base_dir):
        self.data = data
        self.base_dir = base_dir
        self.rows = {}
        for entry in data.get("rows") or []:
            if isinstance(entry, list) and len(entry) == 5:
                sheet, row, url, code, retried = entry
                self.rows[(str(sheet), int(row), str(url))] = [code, bool(retried)]
        self._write_lock = asyncio.Lock()

    @classmethod
    def begin(cls, base_dir, options, payload, *, proxy_hash=None, salt=""):
        data = {
            "version": JOURNAL_VERSION, "journalId": secrets.token_hex(8),
            "platform": options["platform"], "fileId": options["file_id"], "sheetName": options["sheet_name"],
            "startedAt": format_display_datetime(), "scheduled": bool(options.get("scheduled")),
            "payload": {key: payload[key] for key in PAYLOAD_KEYS if key in payload},
            "proxyFingerprint": {"salt": salt, "hash": proxy_hash} if proxy_hash else None,
            "resumeAttempts": 0, "mainDone": False, "stopReason": "", "rows": [],
        }
        return cls(data, base_dir)

    # ---- state ----
    @property
    def platform(self):
        return self.data["platform"]

    @property
    def resume_attempts(self):
        return int(self.data.get("resumeAttempts") or 0)

    @property
    def main_done(self):
        return bool(self.data.get("mainDone"))

    def mark(self, **fields):
        self.data.update(fields)

    def saved_keys(self):
        return set(self.rows)

    def retry_candidates(self):
        """Rows whose saved result in this run is a retryable error and that no retry pass rescanned."""
        return {key for key, (code, retried) in self.rows.items() if code in ("r", "l") and not retried}

    def login_rows(self):
        """Rows whose saved result is a Threads post the page did not show (code "p")."""
        return {key for key, (code, _retried) in self.rows.items() if code == "p"}

    def login_candidates(self):
        """Login-only rows that no cookie-session retry pass rescanned yet."""
        return {key for key, (code, retried) in self.rows.items() if code == "p" and not retried}

    def has_rate_limited(self, keys):
        return any(self.rows.get(key, ["", False])[0] == "l" for key in keys)

    def counts(self, keys=None):
        """success / hidden / error per unique URL, over all saved rows or the given keys."""
        by_url = {}
        for key, (code, _retried) in self.rows.items():
            if keys is None or key in keys:
                by_url[key[2].casefold()] = code
        codes = list(by_url.values())
        return {"processed": len(codes), "success": codes.count("s"), "hidden": codes.count("h"),
                "error": codes.count("r") + codes.count("l") + codes.count("p") + codes.count("e")}

    # ---- hooks / persistence ----
    def recorder(self, *, retry_pass=False):
        async def on_rows_saved(rows):
            for row in rows:
                key = (row["sheet_name"], int(row["row"]), row["url"])
                self.rows[key] = [result_code(self.platform, row.get("status", ""), row.get("kind", "")), retry_pass]
            await self.save()
        return on_rows_saved

    def snapshot(self):
        return {**self.data, "rows": [[*key, code, retried] for key, (code, retried) in self.rows.items()]}

    async def save(self):
        """Write the journal off the event loop. A failed write only costs a resume, never the scan."""
        async with self._write_lock:
            payload = self.snapshot()
            try:
                os.makedirs(os.path.dirname(journal_path(self.base_dir)), exist_ok=True)
                await asyncio.to_thread(write_json_atomic, journal_path(self.base_dir), payload)
            except Exception as error:
                logger.warning("Không ghi được nhật ký phiên quét: %s", error)

    async def clear(self):
        async with self._write_lock:
            try:
                await asyncio.to_thread(clear_journal, self.base_dir)
            except Exception as error:
                logger.warning("Không xóa được nhật ký phiên quét: %s", error)
