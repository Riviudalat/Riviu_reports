"""Request protection.

Source mode: loopback-only Host/Origin checks and the per-process session cookie.

Server mode (`riviu.server_mode.ACTIVE`): Host/Origin must match RIVIU_PUBLIC_ORIGIN (or
loopback, for health checks on the box). Anyone may use the read-only viewer allowlist
(`viewer_request_allowed`: the page at `/`, its assets and the read APIs it calls); every
other route, `/admin` included, needs a login session from the shared password. Sessions are
random tokens kept server side (only their SHA-256 is stored), in an HttpOnly, Secure,
SameSite=Strict cookie with an expiry; logout revokes the token. Failed logins are
rate-limited per client IP (uvicorn resolves X-Forwarded-For only from trusted proxies).
"""

import hashlib
import json
import os
import secrets
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from riviu import server_mode
from riviu.workbook_utils import write_json_atomic


LOCAL_SESSION_TOKEN = secrets.token_urlsafe(32)
LOCAL_SESSION_COOKIE = f"riviu_session_{secrets.token_hex(6)}"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

AUTH_COOKIE = "__Host-riviu_auth"
LOGIN_PATH = "/login"
LOGOUT_PATH = "/logout"
HEALTH_PATH = "/healthz"
# A link from another site arrives without the SameSite=Strict cookie and lands on the
# login page; its same-origin probe of this path then carries the cookie.
AUTH_STATUS_PATH = "/auth-status"
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
PUBLIC_ASSET_PATHS = {"/logo.png", "/riviu-logo.png", "/favicon.ico"}
# Server mode: what the login page loads (it is also inside the viewer allowlist below).
SERVER_PUBLIC_ASSET_PATHS = PUBLIC_ASSET_PATHS | {"/static/styles.css"}
ADMIN_PATH = "/admin"
# Where a login may send the browser back to; anything else would be an open redirect.
LOGIN_NEXT_PATHS = {"/", ADMIN_PATH}
# Server mode, no login: the read-only viewer at `/` and exactly the reads it makes. The data
# behind them (workbooks, previews, partner reports, scan history) is public by the owner's
# decision; uploads, selection, Google, proxy, cookies, workbook download, the scheduler and
# scan start/cancel (checked in riviu/api/scan.py, since this middleware never sees /ws) are not.
VIEWER_GET_PATHS = frozenset({
    "/", "/api/version", "/list-files", "/preview-excel", "/summary-dashboard",
    "/source-preferences", "/report-partners", "/scrape-history",
})
VIEWER_POST_PATHS = frozenset({"/export-report"})


def _port_of(parts):
    return parts.port or (443 if parts.scheme == "https" else 80)


def trusted_local_request(request, *, require_origin=False):
    """Reject rebinding/foreign origins before issuing or accepting local credentials."""
    if server_mode.ACTIVE is not None:
        return trusted_server_request(request, require_origin=require_origin)
    try:
        target = urlsplit(f"http://{request.headers.get('host', '')}")
        if target.hostname not in LOCAL_HOSTS or target.username or target.password:
            return False
        origin = request.headers.get("origin")
        if not origin:
            return not require_origin
        source = urlsplit(origin)
        return (
            source.scheme in {"http", "https"}
            and source.hostname in LOCAL_HOSTS
            and not source.username and not source.password
            and (source.port or (443 if source.scheme == "https" else 80)) == (target.port or 80)
            and source.path in {"", "/"}
        )
    except ValueError:
        return False


def trusted_server_request(request, *, require_origin=False):
    """Host is the public name (or loopback); an Origin, when sent, is exactly the public origin."""
    config = server_mode.ACTIVE
    try:
        target = urlsplit(f"//{request.headers.get('host', '')}")
        if target.username or target.password or not target.hostname:
            return False
        host = target.hostname.lower()
        loopback = host in LOCAL_HOSTS
        if not loopback and (host != config.public_host or target.port not in (None, config.public_port)):
            return False
        origin = request.headers.get("origin")
        if not origin:
            return not require_origin
        source = urlsplit(origin)
        if source.username or source.password or source.path not in {"", "/"} or not source.hostname:
            return False
        if loopback:
            return source.hostname in LOCAL_HOSTS and source.scheme in {"http", "https"}
        return (
            source.scheme == config.public_scheme
            and source.hostname.lower() == config.public_host
            and _port_of(source) == config.public_port
        )
    except ValueError:
        return False


# ---- Server-mode login sessions ----------------------------------------------------

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginSessions:
    """Session tokens persisted as SHA-256 digests, so restarts keep people logged in."""

    def __init__(self, path: str, lifetime_seconds: int, password_fingerprint: str):
        self.path = path
        self.lifetime = lifetime_seconds
        self.fingerprint = password_fingerprint
        self._lock = threading.Lock()
        self._sessions = None

    def _load(self):
        if self._sessions is None:
            try:
                with open(self.path, "r", encoding="utf-8") as stream:
                    payload = json.load(stream)
                sessions = payload.get("sessions", {}) if isinstance(payload, dict) else {}
            except (OSError, ValueError):
                sessions = {}
            now = time.time()
            self._sessions = {
                key: value for key, value in sessions.items()
                if isinstance(value, dict) and value.get("password") == self.fingerprint
                and isinstance(value.get("expires"), (int, float)) and value["expires"] > now
            }
        return self._sessions

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        write_json_atomic(self.path, {"sessions": self._sessions})

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            sessions = self._load()
            now = time.time()
            for key in [key for key, value in sessions.items() if value["expires"] <= now]:
                del sessions[key]
            sessions[_digest(token)] = {"expires": now + self.lifetime, "password": self.fingerprint}
            self._save()
        return token

    def valid(self, token: str) -> bool:
        if not token or len(token) > 256:
            return False
        with self._lock:
            record = self._load().get(_digest(token))
            return bool(record) and record["expires"] > time.time()

    def revoke(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            if self._load().pop(_digest(token), None) is not None:
                self._save()


class LoginRateLimiter:
    def __init__(self, limit=LOGIN_FAILURE_LIMIT, window=LOGIN_FAILURE_WINDOW_SECONDS, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._failures = {}
        self._lock = threading.Lock()

    def _recent(self, client):
        cutoff = self.clock() - self.window
        failures = self._failures.get(client)
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if failures is not None and not failures:
            del self._failures[client]
            return None
        return failures

    def blocked(self, client) -> bool:
        with self._lock:
            failures = self._recent(client)
            return bool(failures) and len(failures) >= self.limit

    def record_failure(self, client) -> None:
        with self._lock:
            self._recent(client)
            self._failures.setdefault(client, deque()).append(self.clock())

    def reset(self, client) -> None:
        with self._lock:
            self._failures.pop(client, None)


LOGIN_RATE_LIMITER = LoginRateLimiter()
_SESSIONS = {}


def login_sessions():
    config = server_mode.ACTIVE
    key = (config.sessions_path, config.session_seconds, config.password_hash)
    if key not in _SESSIONS:
        _SESSIONS.clear()
        _SESSIONS[key] = LoginSessions(
            config.sessions_path, config.session_seconds, server_mode.password_fingerprint(config.password_hash),
        )
    return _SESSIONS[key]


def client_address(request) -> str:
    return request.client.host if request.client else "unknown"


def valid_server_session(request) -> bool:
    return login_sessions().valid(request.cookies.get(AUTH_COOKIE, ""))


def viewer_request_allowed(method: str, path: str) -> bool:
    """Server mode: may this request run without a login (the public read-only viewer)?"""
    if method in {"GET", "HEAD"}:
        return (
            path in VIEWER_GET_PATHS or path in SERVER_PUBLIC_ASSET_PATHS
            or (path.startswith("/static/") and ".." not in path)
        )
    return method == "POST" and path in VIEWER_POST_PATHS


def safe_login_next(value) -> str:
    return value if isinstance(value, str) and value in LOGIN_NEXT_PATHS else ADMIN_PATH


def request_role(request) -> str:
    """`admin` with a valid login (or outside server mode, where the local cookie is the gate), else `viewer`."""
    if server_mode.ACTIVE is None:
        return "admin"
    return "admin" if valid_server_session(request) else "viewer"


def valid_local_session(request):
    if server_mode.ACTIVE is not None:
        return valid_server_session(request)
    supplied = request.cookies.get(LOCAL_SESSION_COOKIE, "")
    return bool(supplied) and secrets.compare_digest(supplied, LOCAL_SESSION_TOKEN)


def set_auth_cookie(response, token: str) -> None:
    response.set_cookie(
        AUTH_COOKIE, token, max_age=server_mode.ACTIVE.session_seconds,
        httponly=True, secure=True, samesite="strict", path="/",
    )


def clear_auth_cookie(response) -> None:
    response.delete_cookie(AUTH_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


# ---- Middleware ----------------------------------------------------------------------

def _wants_html(request) -> bool:
    if request.headers.get("sec-fetch-dest") == "document":
        return True
    return request.method in {"GET", "HEAD"} and "text/html" in request.headers.get("accept", "")


def _security_headers(response):
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


async def protect_server_api(request: Request, call_next):
    path = request.url.path
    if not trusted_server_request(request):
        return JSONResponse({"error": "Nguồn truy cập không hợp lệ"}, status_code=403)
    public = (
        path == HEALTH_PATH
        or path in {LOGIN_PATH, LOGOUT_PATH, AUTH_STATUS_PATH}
        or viewer_request_allowed(request.method, path)
    )
    navigation = (
        request.method in {"GET", "HEAD"}
        and request.headers.get("sec-fetch-mode") == "navigate"
        and request.headers.get("sec-fetch-dest") == "document"
    )
    cross_site = request.headers.get("sec-fetch-site") == "cross-site"
    if cross_site and not navigation:
        return JSONResponse({"error": "Nguồn truy cập không hợp lệ"}, status_code=403)
    if not public and not valid_server_session(request):
        if _wants_html(request):
            target = f"{LOGIN_PATH}?next={ADMIN_PATH}" if path == ADMIN_PATH else LOGIN_PATH
            return _security_headers(RedirectResponse(target, status_code=303))
        return _security_headers(JSONResponse({"error": "Cần đăng nhập"}, status_code=401))
    response = await call_next(request)
    if path.startswith("/threads-session") or path in {"/", ADMIN_PATH, LOGIN_PATH}:
        response.headers["Cache-Control"] = "no-store"
    return _security_headers(response)


async def protect_local_api(request: Request, call_next):
    if server_mode.ACTIVE is not None:
        return await protect_server_api(request, call_next)
    if not trusted_local_request(request):
        return JSONResponse({"error": "Nguồn truy cập không hợp lệ"}, status_code=403)
    bootstrap = request.method == "GET" and request.url.path == "/"
    public_asset = request.method in {"GET", "HEAD"} and (
        request.url.path.startswith("/static/") or request.url.path in PUBLIC_ASSET_PATHS
    )
    if not (bootstrap or public_asset) and not valid_local_session(request):
        return JSONResponse({"error": "Mở lại trang ứng dụng để kết nối phiên local"}, status_code=403)
    top_navigation = bootstrap and request.headers.get("sec-fetch-mode") == "navigate" and request.headers.get("sec-fetch-dest") == "document"
    if request.headers.get("sec-fetch-site") == "cross-site" and not top_navigation:
        return JSONResponse({"error": "Nguồn truy cập không hợp lệ"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/threads-session"):
        response.headers["Cache-Control"] = "no-store"
    if bootstrap:
        response.set_cookie(LOCAL_SESSION_COOKIE, LOCAL_SESSION_TOKEN, httponly=True, samesite="strict", path="/")
        response.headers["Cache-Control"] = "no-store"
    return response
