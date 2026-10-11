"""Loopback-only access: Host/Origin checks and the per-process session cookie."""

import secrets
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse


LOCAL_SESSION_TOKEN = secrets.token_urlsafe(32)
LOCAL_SESSION_COOKIE = f"riviu_session_{secrets.token_hex(6)}"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def trusted_local_request(request, *, require_origin=False):
    """Reject rebinding/foreign origins before issuing or accepting local credentials."""
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


def valid_local_session(request):
    supplied = request.cookies.get(LOCAL_SESSION_COOKIE, "")
    return bool(supplied) and secrets.compare_digest(supplied, LOCAL_SESSION_TOKEN)


async def protect_local_api(request: Request, call_next):
    if not trusted_local_request(request):
        return JSONResponse({"error": "Nguồn truy cập không hợp lệ"}, status_code=403)
    bootstrap = request.method == "GET" and request.url.path == "/"
    public_asset = request.method in {"GET", "HEAD"} and (
        request.url.path.startswith("/static/") or request.url.path in {"/logo.png", "/riviu-logo.png", "/favicon.ico"}
    )
    desktop_control = request.url.path.startswith("/_desktop/")
    if not (bootstrap or public_asset or desktop_control) and not valid_local_session(request):
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
