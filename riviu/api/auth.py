"""Server-mode login: the login page (account or, before any account exists, the shared
password), logout, the health probe and the auth probe (which also reports the role).

The routes exist in every mode but answer 404 outside server mode (the local middleware
also keeps them behind the per-process cookie there), so desktop/source mode is unchanged.
"""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from riviu import server_context as context
from riviu import server_mode, server_users
from riviu.api import security
from riviu.api.pages import static_asset_version, templates


router = APIRouter()
MAX_PASSWORD_LENGTH = 1024
WRONG_PASSWORD = "Sai mật khẩu."
WRONG_LOGIN = "Sai tên đăng nhập hoặc mật khẩu."
TOO_MANY_ATTEMPTS = "Nhập sai quá nhiều lần. Vui lòng thử lại sau 15 phút."


def _not_found():
    return JSONResponse({"error": "Not Found"}, status_code=404)


def _login_page(request: Request, error: str = "", status_code: int = 200, username: str = ""):
    store = server_users.active_store()
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "error": error,
            "username": username,
            "accounts": bool(store and store.has_users()),
            "asset_version": static_asset_version(context.APP_RESOURCE_DIR),
        },
        status_code=status_code,
    )


@router.get(security.HEALTH_PATH, include_in_schema=False)
async def healthz():
    return PlainTextResponse("ok")


@router.get(security.AUTH_STATUS_PATH, include_in_schema=False)
async def auth_status(request: Request):
    if server_mode.ACTIVE is None:
        return _not_found()
    identity = security.request_identity(request)
    return {"authenticated": identity is not None, "user": identity}


@router.get(security.LOGIN_PATH, include_in_schema=False)
async def login_page(request: Request):
    if server_mode.ACTIVE is None:
        return _not_found()
    if security.valid_server_session(request):
        return RedirectResponse("/", status_code=303)
    return _login_page(request)


@router.post(security.LOGIN_PATH, include_in_schema=False)
async def login(request: Request):
    config = server_mode.ACTIVE
    if config is None:
        return _not_found()
    # Login CSRF: a form post always carries Origin in current browsers.
    if not security.trusted_server_request(request, require_origin=True):
        return JSONResponse({"error": "Nguồn truy cập không hợp lệ"}, status_code=403)
    client = security.client_address(request)
    limiter = security.LOGIN_RATE_LIMITER
    if limiter.blocked(client):
        return _login_page(request, TOO_MANY_ATTEMPTS, status_code=429)
    form = await request.form()
    password = form.get("password")
    password = password if isinstance(password, str) else ""
    username = form.get("username")
    username = server_users.normalize_username(username if isinstance(username, str) else "")[:64]
    store = server_users.active_store()
    account = None
    if not 0 < len(password) <= MAX_PASSWORD_LENGTH:
        ok = False
    elif store.has_users():
        # Once accounts exist, every login names one; the shared password no longer works.
        account = await asyncio.to_thread(store.verify, username, password)
        ok = account is not None
    else:
        ok = await asyncio.to_thread(server_mode.verify_password, password, config.password_hash)
    if not ok:
        limiter.record_failure(client)
        if limiter.blocked(client):
            return _login_page(request, TOO_MANY_ATTEMPTS, status_code=429, username=username)
        error = WRONG_LOGIN if store.has_users() else WRONG_PASSWORD
        return _login_page(request, error, status_code=401, username=username)
    limiter.reset(client)
    if account:
        token = await asyncio.to_thread(
            security.login_sessions().create, account["username"], server_mode.password_fingerprint(account["hash"]),
        )
    else:
        token = await asyncio.to_thread(security.login_sessions().create)
    response = RedirectResponse("/", status_code=303)
    security.set_auth_cookie(response, token)
    return response


@router.post(security.LOGOUT_PATH, include_in_schema=False)
async def logout(request: Request):
    if server_mode.ACTIVE is None:
        return _not_found()
    if not security.trusted_server_request(request, require_origin=True):
        return JSONResponse({"error": "Nguồn truy cập không hợp lệ"}, status_code=403)
    await asyncio.to_thread(security.login_sessions().revoke, request.cookies.get(security.AUTH_COOKIE, ""))
    response = RedirectResponse(security.LOGIN_PATH, status_code=303)
    security.clear_auth_cookie(response)
    return response

