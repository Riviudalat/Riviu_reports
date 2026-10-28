"""The app page, its logo/favicon and the version probe."""

import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from riviu import server_context as context
from riviu import server_mode
from riviu.api import security


router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(context.APP_RESOURCE_DIR, "templates"))
# The React web app built from frontend/ (`npm run build`); while it is missing, `/` keeps
# serving the classic page. The classic page stays reachable at /classic during the switch.
WEB_APP_DIR = os.path.join(context.APP_RESOURCE_DIR, "app")
WEB_APP_INDEX = os.path.join(WEB_APP_DIR, "index.html")
CLASSIC_PATH = "/classic"


def static_asset_version(base_dir: str) -> str:
    paths = [
        os.path.join(base_dir, "static", "app.js"),
        os.path.join(base_dir, "static", "styles.css"),
    ]
    mtimes = [int(os.path.getmtime(path)) for path in paths if os.path.exists(path)]
    return str(max(mtimes)) if mtimes else "1"


def web_app_page():
    """The built React app (one index for `/` and `/admin`; it asks /auth-status for the role), or None."""
    if os.path.isfile(WEB_APP_INDEX):
        return FileResponse(WEB_APP_INDEX, media_type="text/html", headers={"Cache-Control": "no-store"})
    return None


def render_app_page(request: Request, role: str):
    """The classic app page; `role` "viewer" leaves out every admin-only control (server mode `/`)."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "asset_version": static_asset_version(context.APP_RESOURCE_DIR),
            "server_mode": server_mode.ACTIVE is not None,
            "role": role,
            "is_admin": role == "admin",
        },
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # Server mode: `/` is the public read-only viewer for everyone (logged in or not);
    # administration lives at /admin. Source mode keeps the full app here.
    return web_app_page() or render_app_page(request, "viewer" if server_mode.ACTIVE is not None else "admin")


@router.get(security.ADMIN_PATH, response_class=HTMLResponse, include_in_schema=False)
async def admin_page(request: Request):
    if server_mode.ACTIVE is None:
        return JSONResponse({"error": "Not Found"}, status_code=404)
    # The middleware already sent requests without an admin login to /login?next=/admin.
    return web_app_page() or render_app_page(request, "admin")


@router.get(CLASSIC_PATH, response_class=HTMLResponse, include_in_schema=False)
async def classic_page(request: Request):
    """The classic page while the React app is new (admin-only in server mode, like /admin)."""
    return render_app_page(request, "admin")


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@router.get("/logo.png", include_in_schema=False)
async def logo_png():
    if os.path.exists(context.LOGO_PATH):
        return FileResponse(context.LOGO_PATH, media_type="image/png")
    return Response(status_code=404)


@router.get("/riviu-logo.png", include_in_schema=False)
async def riviu_logo():
    return await logo_png()


@router.get("/api/version")
async def api_version():
    from riviu.proxy_utils import PROXY_TEST_BUILD
    from riviu.platforms.tiktok import MAX_WORKERS

    return {
        "proxyTestBuild": PROXY_TEST_BUILD,
        "app": "riviu-reports",
        "maxWorkers": MAX_WORKERS,
    }
