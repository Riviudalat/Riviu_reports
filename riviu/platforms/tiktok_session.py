"""Opt-in TikTok cookie session ("Cookie TikTok"): import, secure storage and a login check.

Some posts (mostly photo posts) answer "Video không khả dụng" to anonymous viewers but
open for a logged-in viewer. With the toggle on, a scan sends these cookies with its
TikTok page fetches (riviu/platforms/tiktok/cookies.py); without it nothing changes.

Same rules as the Threads vault (riviu/platforms/threads_session.py): Cookie-Editor JSON
only, exact cookie validation, OS secure store on desktop (DPAPI/Keychain/SecretService)
or the AES-GCM file in server mode, no plaintext fallback. Cookie values never appear in
exceptions, logs, API status, WebSocket messages, history files or workbooks.
"""
from __future__ import annotations

import copy
import json
import threading
import urllib.request
import uuid

from riviu.platforms import threads_session as shared
from riviu.platforms.tiktok import cookies as scan_cookies

MAX_IMPORT_BYTES = shared.MAX_IMPORT_BYTES
VERIFY_TIMEOUT_SECONDS = 20
# A logged-in viewer gets {"message": "success", "data": {"user_id": ...}}; an anonymous or
# expired one {"message": "error", "data": {"name": "session_expired", "error_code": 13}}
# (checked logged-out on 2026-10-19). One small JSON GET, nothing is scanned.
ACCOUNT_INFO_URL = "https://www.tiktok.com/passport/web/account/info/?aid=1459&app_name=tiktok_web"
SESSION_COOKIE_NAMES = ("sessionid", "sessionid_ss", "sid_tt")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_MESSAGES = {
    "none": "Chưa lưu cookie TikTok.",
    "unchecked": "Cookie TikTok đã lưu an toàn, chưa kiểm tra trong lần mở app này.",
    "valid": "Đã xác nhận phiên đăng nhập TikTok tại thời điểm kiểm tra.",
    "expired": "Cookie TikTok đã hết hạn. Import phiên mới.",
    "invalid": "TikTok không xác nhận đăng nhập với cookie này (hết hạn hoặc đã đăng xuất).",
    "unknown": "Chưa xác minh được đăng nhập TikTok; kiểm tra kết nối và thử kiểm lại.",
    "storage_unavailable": "Kho bảo mật không khả dụng. Phiên đã lưu trước đó không bị thay đổi.",
    "bad_import": "JSON cookie không hợp lệ hoặc không được hỗ trợ.",
    "too_large": "JSON cookie vượt giới hạn dung lượng.",
    "missing_session": "Không có cookie sessionid / sessionid_ss / sid_tt của tiktok.com.",
}


class TikTokSessionError(ValueError):
    """A fixed, non-secret error suitable for API responses."""

    def __init__(self, state: str = "bad_import"):
        self.state = state if state in _MESSAGES else "bad_import"
        super().__init__(_MESSAGES[self.state])


def tiktok_cookie_domain(domain: str) -> bool:
    """tiktok.com and its subdomains only; an export usually holds many other sites."""
    host = domain.lstrip(".")
    return host == "tiktok.com" or (host.endswith(".tiktok.com") and ".." not in host)


def _normalize(payload, *, allow_expired=False):
    try:
        return shared._normalize(payload, allow_expired=allow_expired, domain_allowed=tiktok_cookie_domain,
                                 session_names=SESSION_COOKIE_NAMES)
    except shared.SessionError as error:
        raise TikTokSessionError(error.state) from None
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise TikTokSessionError() from None


def normalize_tiktok_cookies(payload) -> list[dict]:
    """Cookie-Editor JSON (array or {cookies: [...]}) -> tiktok.com cookies with a live session cookie."""
    return _normalize(payload)


def expired(cookies) -> bool:
    return shared._expired(cookies, SESSION_COOKIE_NAMES)


TIKTOK_STORE = shared.StoreSpec(
    "Riviu.TikTokSession", "tiktok_session", b"RVTK1",
    normalize_tiktok_cookies, lambda raw: _normalize(raw, allow_expired=True),
)


def default_store(base_dir):
    return shared.SecureStore(base_dir, spec=TIKTOK_STORE)


def account_state(payload) -> str:
    """valid / invalid / unknown from the account-info JSON; never from HTTP 200 alone."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return "unknown"
    data = payload["data"]
    if payload.get("message") == "success":
        user_id = data.get("user_id_str", data.get("user_id"))
        if isinstance(user_id, bool):
            return "unknown"
        text = str(user_id or "")
        return "valid" if text.isdigit() and text.strip("0") else "unknown"
    if payload.get("message") == "error" and ("error_code" in data or data.get("name") == "session_expired"):
        return "invalid"
    return "unknown"


def verify_cookies(cookies, timeout=VERIFY_TIMEOUT_SECONDS) -> str:
    """One direct GET of TikTok's account-info JSON with these cookies; the body is never logged."""
    from riviu.proxy_utils import urlopen_with_config
    from riviu.workbook_utils import is_scrapable_tiktok_url

    try:
        normalized = normalize_tiktok_cookies(cookies)
    except TikTokSessionError as error:
        return error.state
    header = scan_cookies.cookie_header_for(ACCOUNT_INFO_URL, normalized)
    request = urllib.request.Request(ACCOUNT_INFO_URL, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json", "Referer": "https://www.tiktok.com/",
        "Cookie": header,
    })
    try:
        with urlopen_with_config(request, None, timeout=timeout, redirect_validator=is_scrapable_tiktok_url) as response:
            body = response.read(256 * 1024)
        return account_state(json.loads(body.decode("utf-8", errors="replace")))
    except Exception:
        return "unknown"


class TikTokSession:
    """Cached vault state for the UI and scan start. Blocking: call from a worker thread."""

    def __init__(self, base_dir, store=None, verifier=None):
        self.store = store if store is not None else default_store(base_dir)
        self.verifier = verifier or verify_cookies
        self._loaded = False
        self._cookies = None
        self._generation = uuid.uuid4().hex
        self._state = "none"
        self._checked_at = None
        self._lock = threading.RLock()

    def _load(self):
        if self._loaded:
            return
        try:
            loaded = self.store.load()
            self._cookies = _normalize(loaded, allow_expired=True) if loaded is not None else None
            self._state = "unchecked" if self._cookies else "none"
            self._loaded = True
        except Exception:
            # Stay unloaded: a briefly locked keychain is retried on the next access.
            self._state = "storage_unavailable"
            self._cookies = None

    def status(self) -> dict:
        with self._lock:
            self._load()
            state = "expired" if self._cookies and expired(self._cookies) else self._state
            return {"configured": self._cookies is not None, "state": state, "count": len(self._cookies or ()),
                    "generation": self._generation, "checkedAt": self._checked_at, "message": _MESSAGES[state]}

    def snapshot(self) -> tuple[dict, ...]:
        """The saved cookies for one scan; raises when there is nothing usable."""
        with self._lock:
            self._load()
            if not self._cookies:
                raise TikTokSessionError("storage_unavailable" if self._state == "storage_unavailable" else "none")
            if expired(self._cookies):
                raise TikTokSessionError("expired")
            return tuple(copy.deepcopy(self._cookies))

    def _check(self, state):
        return {"state": state, "checkedAt": shared._timestamp(), "message": _MESSAGES[state]}

    def import_cookie(self, payload) -> dict:
        """Normalize, verify with one request, and only then replace the saved session.

        The check runs outside the lock so status reads never wait on TikTok.
        """
        with self._lock:
            self._load()
            generation = self._generation
        try:
            cookies = normalize_tiktok_cookies(payload)
        except TikTokSessionError as error:
            return {"success": False, "status": self.status(), "check": self._check(error.state)}
        check = self._check(self.verifier(cookies))
        if check["state"] != "valid":
            return {"success": False, "status": self.status(), "check": check}
        with self._lock:
            if generation != self._generation:
                return {"success": False, "status": self.status(), "check": self._check("unknown")}
            try:
                self.store.save(cookies)
            except Exception:
                raise TikTokSessionError("storage_unavailable") from None
            self._cookies = copy.deepcopy(cookies)
            self._loaded = True
            self._generation = uuid.uuid4().hex
            self._state = "valid"
            self._checked_at = check["checkedAt"]
            return {"success": True, "status": self.status(), "check": check}

    def verify(self) -> dict:
        with self._lock:
            generation = self._generation
            try:
                cookies = self.snapshot()
            except TikTokSessionError as error:
                return {"success": False, "status": self.status(), "check": self._check(error.state)}
        check = self._check(self.verifier(cookies))
        with self._lock:
            if generation == self._generation:
                self._state = check["state"]
                self._checked_at = check["checkedAt"]
            return {"success": check["state"] == "valid", "status": self.status(), "check": check}

    def clear(self) -> dict:
        with self._lock:
            try:
                self.store.delete()
            except Exception:
                raise TikTokSessionError("storage_unavailable") from None
            self._cookies = None
            self._loaded = True
            self._generation = uuid.uuid4().hex
            self._state = "none"
            self._checked_at = None
            return self.status()
