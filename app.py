from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File, Form, Query
from typing import Annotated
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import sys
from collections import deque
from pathlib import Path
import urllib.error
from urllib.parse import quote, urlsplit, parse_qs, urlunsplit

from openpyxl import load_workbook

from reports import build_export_payload, build_google_push_rows
from scraper import clamp_worker_count, run_scraper, read_scrape_history
from threads_scraper import resolve_threads_proxies, run_threads_scraper
from threads_session import ThreadsSession, SessionError, MAX_IMPORT_BYTES, chromium_proxy_unsupported
from google_sheets_sync import (
    GoogleLoginRequired,
    authorize_google,
    download_google_sheet_authenticated,
    oauth_status,
    push_rows_to_new_sheet,
    save_oauth_client,
    try_load_credentials,
)
from proxy_utils import (
    load_proxy_list_text,
    parse_proxy_text,
    resolve_proxy_configs,
    save_proxy_list_text,
    test_proxy_text,
)
from workbook_utils import (
    LEGACY_GOOGLE_SHEET_FILE_ID,
    PLATFORMS,
    build_workbook_rows,
    clean_text,
    download_google_sheet,
    ensure_data_dir,
    find_data_sheet_names,
    google_sheet_source_for_file,
    get_platform,
    list_workbook_partners_with_link_counts,
    parse_google_spreadsheet_id,
    read_summary_dashboard,
    read_sheet_preview,
    register_google_sheet_source,
    safe_join,
    fetch_google_spreadsheet_title,
    format_filename_datetime,
    google_sheet_file_id_from_title,
    google_sheet_sync_label,
    workbook_file_entries,
    workbook_sheet_names,
    data_sheet_name_for_summary_title,
    write_json_atomic,
)


def application_resource_dir() -> str:
    """Return bundled resources when frozen, otherwise the repository directory."""
    return os.path.abspath(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))))


APP_RESOURCE_DIR = application_resource_dir()
EXCEL_DIR = os.path.abspath(os.environ.get("RIVIU_DATA_DIR", APP_RESOURCE_DIR))
os.makedirs(EXCEL_DIR, exist_ok=True)

app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(APP_RESOURCE_DIR, "templates"))

_STATIC_DIR = os.path.join(APP_RESOURCE_DIR, "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


LOCAL_SESSION_TOKEN = secrets.token_urlsafe(32)
LOCAL_SESSION_COOKIE = f"riviu_session_{secrets.token_hex(6)}"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
THREADS_SESSION = None
THREADS_SESSION_ROOT = ""


def threads_session_manager():
    global THREADS_SESSION, THREADS_SESSION_ROOT
    root = os.path.realpath(EXCEL_DIR)
    if THREADS_SESSION is None or THREADS_SESSION_ROOT != root:
        THREADS_SESSION = ThreadsSession(root)
        THREADS_SESSION_ROOT = root
    return THREADS_SESSION

GOOGLE_IO_LOCK = threading.RLock()
# The interactive OAuth flow waits on the user's browser, so it must never hold GOOGLE_IO_LOCK.
GOOGLE_LOGIN_LOCK = threading.Lock()
GOOGLE_LOGIN_TIMEOUT_SECONDS = 300


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


@app.middleware("http")
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


def google_io(function, *args, **kwargs):
    with GOOGLE_IO_LOCK:
        return function(*args, **kwargs)


async def complete_blocking(function, *args, **kwargs):
    """A cancelled coroutine must not release a write gate while its thread runs."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.context = {}
        self.running = False
        self.last_status = {}
        self.results = deque(maxlen=500)

    def begin_run(self, platform, file_id, sheet_name):
        self.context = {"runId": secrets.token_hex(12), "platform": platform, "fileId": file_id, "sheetName": sheet_name}
        self.running = True
        self.last_status = {"total": 0, "processed": 0, "success": 0, "error": 0, "hidden": 0, "done": False, "phase": "running"}
        self.results.clear()
        return dict(self.context)

    def snapshot(self):
        return {**self.context, "running": self.running, "status": {**self.last_status, **self.context}, "results": list(self.results)}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        await websocket.send_json({"type": "session", "data": self.snapshot(), **self.context})

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def _send_all(self, payload):
        # Copy first: a disconnect during an await must not make the loop skip another tab.
        for connection in list(self.active_connections):
            try:
                await connection.send_json(payload)
            except Exception:
                self.disconnect(connection)

    async def broadcast_log(self, message: str, *, level: str = "", details=None):
        payload = {"type": "log", "message": message, **self.context}
        if level:
            payload["level"] = level
        if details:
            payload["details"] = details
        await self._send_all(payload)

    async def broadcast_status(self, status: dict):
        status = {**status, **self.context}
        self.last_status = status
        if status.get("done"):
            self.running = False
        await self._send_all({"type": "status", "data": status, **self.context})

    async def broadcast_data(self, row: dict):
        row = {**row, **self.context}
        self.results.append(row)
        await self._send_all({"type": "data", "row": row, **self.context})

    async def broadcast_duplicates(self, data: dict):
        await self._send_all({"type": "duplicates", "data": data, **self.context})


manager = ConnectionManager()
ensure_data_dir(EXCEL_DIR)
SCRAPE_TASK = None
SCAN_STARTING = False
SOURCE_BUSY = False
DESKTOP_UPDATE_PENDING = False
CURRENT_SELECTED_FILE = ""
CURRENT_SELECTED_SHEET = ""
CURRENT_SCAN_SHEET = ""
LOGO_PATH = os.path.join(APP_RESOURCE_DIR, "logo.png")


def scan_running():
    return SCAN_STARTING or manager.running or bool(SCRAPE_TASK and not SCRAPE_TASK.done())


def source_mutation_blocked():
    return scan_running() or SOURCE_BUSY or DESKTOP_UPDATE_PENDING


def validate_workbook(path):
    book = load_workbook(path, read_only=True)
    try:
        if not book.worksheets:
            raise ValueError("Workbook không có trang tính")
        # Validate the ZIP/XML payload while reading headers, before publishing it.
        for sheet in book:
            next(sheet.iter_rows(values_only=True), None)
    finally:
        book.close()
    return read_sheet_preview(path)


def publish_new_workbook(staged_path, desired_path):
    """Publish validated bytes without ever overwriting an existing path."""
    stem, extension = os.path.splitext(desired_path)
    candidate = desired_path
    suffix = 2
    while True:
        try:
            os.link(staged_path, candidate)
            return candidate
        except FileExistsError:
            candidate = f"{stem} ({suffix}){extension}"
            suffix += 1


def file_entries():
    return workbook_file_entries(EXCEL_DIR)


def resolve_file_path(file_id):
    if not file_id:
        return ""
    return safe_join(EXCEL_DIR, file_id.replace("\\", "/"))


class SourceRequestError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code

    def response(self):
        return JSONResponse({"error": str(self)}, status_code=self.status_code)


def validate_platform(platform="tiktok"):
    try:
        return get_platform(platform).key
    except ValueError as error:
        raise SourceRequestError(str(error)) from error


def resolve_source_file(file_id=None, platform="tiktok", *, allow_empty=False):
    """Capture an exact source; only an omitted ID may use legacy selection."""
    platform = validate_platform(platform)
    if file_id is None:
        file_id = ensure_selected_file()
    elif not isinstance(file_id, str):
        raise SourceRequestError("File không hợp lệ")
    else:
        file_id = file_id.replace("\\", "/")
        if not file_id:
            if allow_empty:
                return "", "", "", platform
            raise SourceRequestError("Chưa chọn workbook hợp lệ")
        if file_id not in {entry["id"] for entry in file_entries()}:
            raise SourceRequestError("File không tồn tại", 404)
    target_path = resolve_file_path(file_id)
    if file_id and (not target_path or not os.path.isfile(target_path)):
        raise SourceRequestError("File không tồn tại", 404)
    return file_id, target_path, file_display_label(file_id), platform


def source_sheet_default(file_id, *, scan=False, explicit=False):
    if not explicit:
        return (CURRENT_SCAN_SHEET if scan else "") or CURRENT_SELECTED_SHEET or ""
    return default_sheet_for_file(file_id)


def ensure_selected_file():
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET, CURRENT_SCAN_SHEET
    entries = file_entries()
    available_ids = {entry["id"] for entry in entries}

    if CURRENT_SELECTED_FILE and CURRENT_SELECTED_FILE in available_ids:
        return CURRENT_SELECTED_FILE

    if not entries:
        CURRENT_SELECTED_FILE = ""
        CURRENT_SELECTED_SHEET = ""
        CURRENT_SCAN_SHEET = ""
        return ""

    google_ids = [
        entry["id"]
        for entry in entries
        if entry.get("source") == "google" and entry["id"] != LEGACY_GOOGLE_SHEET_FILE_ID
    ]
    google_ids = sorted(
        google_ids,
        key=lambda file_id: os.path.getmtime(resolve_file_path(file_id)) if os.path.exists(resolve_file_path(file_id)) else 0,
        reverse=True,
    )
    preferred_ids = google_ids + [LEGACY_GOOGLE_SHEET_FILE_ID, "Report_v1.xlsx", "Report_v1_with_partners.xlsx"]
    CURRENT_SELECTED_FILE = next((file_id for file_id in preferred_ids if file_id in available_ids), entries[0]["id"])
    return CURRENT_SELECTED_FILE


def current_google_sheet_source():
    current_id = ensure_selected_file()
    return google_sheet_source_for_file(EXCEL_DIR, current_id)


def file_display_label(file_id):
    return next((entry["label"] for entry in file_entries() if entry["id"] == file_id), file_id)


def default_sheet_for_file(file_id):
    if not file_id:
        return ""
    try:
        sheets = find_data_sheet_names(resolve_file_path(file_id))
        return sheets[0] if sheets else ""
    except Exception:
        return ""


def content_disposition(filename):
    return f"attachment; filename*=UTF-8''{quote(filename)}"


def tiktok_proxy_error(proxy_text, base_dir, mode):
    if any(config.get("enabled", True) for config in resolve_proxy_configs(base_dir, proxy_text)):
        return None
    return "Bật proxy nhưng chưa có proxy hợp lệ. Mở Cấu hình và dán proxy."


def threads_proxy_error(proxy_text, base_dir, mode):
    try:
        resolve_threads_proxies(base_dir, proxy_text, mode)
    except ValueError as error:
        return str(error)
    return None


PROXY_VALIDATORS = {"tiktok": tiktok_proxy_error, "threads": threads_proxy_error}


def validate_proxy_start(use_proxy: bool, proxy_text: str, base_dir: str, platform: str = "tiktok", mode: str = "request") -> str | None:
    if not use_proxy:
        return None
    return PROXY_VALIDATORS[platform](proxy_text, base_dir, mode)


def static_asset_version(base_dir: str) -> str:
    paths = [
        os.path.join(base_dir, "static", "app.js"),
        os.path.join(base_dir, "static", "styles.css"),
    ]
    mtimes = [int(os.path.getmtime(path)) for path in paths if os.path.exists(path)]
    return str(max(mtimes)) if mtimes else "1"


def sync_download_google_sheet(base_dir, source_url, spreadsheet_id, destination_path):
    creds = try_load_credentials(base_dir)
    if creds and creds.valid:
        try:
            download_google_sheet_authenticated(base_dir, spreadsheet_id, destination_path)
            return
        except Exception:
            pass
    try:
        download_google_sheet(source_url, destination_path)
    except Exception as public_error:
        if creds and creds.valid:
            raise public_error
        message = str(public_error)
        if isinstance(public_error, urllib.error.HTTPError) and public_error.code in (401, 403):
            raise ValueError("Sheet riêng tư — cần đăng nhập Google hợp lệ.") from public_error
        if "401" in message or "403" in message or "private" in message.lower():
            raise ValueError("Sheet riêng tư — cần đăng nhập Google hợp lệ.") from public_error
        raise


class RunEvents:
    """Forward progress, but reserve terminal events for the transaction wrapper."""
    def __init__(self, connection_manager):
        self.manager = connection_manager
        self.terminal = None

    async def broadcast_status(self, status):
        if status.get("done"):
            self.terminal = dict(status)
            await self.manager.broadcast_status({**status, "done": False, "phase": "saving"})
        else:
            phase = "starting" if status.get("phase") == "starting" else "running"
            await self.manager.broadcast_status({**status, "phase": phase})

    async def broadcast_log(self, *args, **kwargs):
        await self.manager.broadcast_log(*args, **kwargs)

    async def broadcast_data(self, row):
        await self.manager.broadcast_data(row)

    async def broadcast_duplicates(self, data):
        await self.manager.broadcast_duplicates(data)


async def run_tiktok_scan(target_path, events, options):
    await run_scraper(
        target_path,
        events,
        worker_count=options["worker_count"],
        selected_partner=options["partner"],
        selected_partners=options["partners"],
        create_result_sheet=options["create_result_sheet"],
        base_dir=EXCEL_DIR,
        file_label=options["file_label"],
        sheet_name=options["sheet_name"],
        use_request=options["scrape_mode"] != "browser",
        browser_fallback=options["scrape_mode"] == "hybrid",
        use_proxy=options["use_proxy"],
        proxy_text=options["proxy_text"],
    )


async def run_threads_scan(target_path, events, options):
    session_options = {}
    if options.get("threads_cookies") is not None:
        session_options = {"session_cookies": options["threads_cookies"], "proxy_configs": options.get("threads_proxies") or []}
    await run_threads_scraper(
        target_path, events, worker_count=options["worker_count"],
        selected_partners=options["partners"] or ([options["partner"]] if options["partner"] else []),
        sheet_name=options["sheet_name"], mode=options["scrape_mode"],
        base_dir=EXCEL_DIR, use_proxy=options["use_proxy"], proxy_text=options["proxy_text"], **session_options,
    )


SCAN_RUNNERS = {"tiktok": run_tiktok_scan, "threads": run_threads_scan}


def scan_options(**overrides):
    """Defaults for one scan; the websocket start handler overrides them from the validated payload."""
    options = {
        "platform": "tiktok", "file_id": "", "sheet_name": "", "worker_count": 1, "scrape_mode": "request",
        "use_proxy": False, "proxy_text": "", "partner": None, "partners": [],
        "create_result_sheet": False, "push_to_google": False,
    }
    unknown = set(overrides) - set(options) - {"threads_cookies", "threads_proxies", "threads_generation"}
    if unknown:
        raise TypeError(f"Unknown scan options: {sorted(unknown)}")
    return {**options, **overrides}


async def push_scan_results(target_path, file_id, scan_sheet, platform):
    spreadsheet_id = google_sheet_source_for_file(EXCEL_DIR, file_id).get("spreadsheetId", "")
    if not spreadsheet_id:
        await manager.broadcast_log("BỎ QUA đẩy Google: file hiện tại chưa gắn với Google Sheet gốc.")
        return
    try:
        rows = await asyncio.to_thread(build_workbook_rows, target_path, sheet_name=scan_sheet, platform=platform)
        values = build_google_push_rows(rows, platform=platform)
        sheet_title = await complete_blocking(google_io, push_rows_to_new_sheet, EXCEL_DIR, spreadsheet_id, values, source_sheet_name=scan_sheet, platform=platform)
    except GoogleLoginRequired as error:
        await manager.broadcast_log(f"Quét đã lưu xong nhưng chưa đẩy lên Google: {error}", level="WARN")
    except Exception as error:
        # The scan itself already saved; a push failure must not be reported as a failed scan.
        await manager.broadcast_log(f"Quét đã lưu xong nhưng đẩy Google Sheet thất bại: {error}", level="WARN")
    else:
        await manager.broadcast_log(f"Đã đẩy kết quả lên Google Sheet: {sheet_title}.")


async def run_scraper_safely(target_path, options, run_context=None):
    """Run one scan transaction; `options` holds the validated start payload (see websocket start)."""
    platform = options["platform"]
    events = RunEvents(manager)
    try:
        file_id = os.path.relpath(target_path, EXCEL_DIR).replace("\\", "/")
        if run_context is None:
            manager.begin_run(platform, file_id, options["sheet_name"])
        sheets = await asyncio.to_thread(find_data_sheet_names, target_path)
        scan_sheet = clean_text(options["sheet_name"]) or (sheets[0] if sheets else "")
        if not scan_sheet or scan_sheet not in sheets:
            raise ValueError("Sheet không tồn tại trong file đã chọn")
        options = {**options, "sheet_name": scan_sheet, "file_label": file_display_label(file_id)}
        await SCAN_RUNNERS[platform](target_path, events, options)
        if options["push_to_google"]:
            await push_scan_results(target_path, file_id, scan_sheet, platform)
        await manager.broadcast_status({**(events.terminal or manager.last_status), "done": True, "phase": "completed"})
    except asyncio.CancelledError:
        await manager.broadcast_log("Đã hủy phiên quét.")
        await manager.broadcast_status({**manager.last_status, "done": True, "cancelled": True, "phase": "cancelled"})
        raise
    except Exception as error:
        message = str(error)
        if options.get("threads_cookies") is not None and not isinstance(error, (SessionError, ValueError)):
            # Unexpected errors from a logged-in run may echo request details; keep them out of the log.
            message = "Phiên quét Threads đăng nhập gặp lỗi kỹ thuật; cookie vẫn được giữ, hãy thử lại."
        if isinstance(error, SessionError) and options.get("threads_cookies") is not None:
            # Only a session failure says anything about the cookies; other errors leave them valid.
            try:
                await complete_blocking(threads_session_manager().invalidate, options.get("threads_generation"), error.state)
            except Exception:
                pass
        await manager.broadcast_log(f"Lỗi quét: {message}")
        await manager.broadcast_status({**manager.last_status, "error": max(1, manager.last_status.get("error", 0)), "done": True, "phase": "failed", "message": message})


def finalize_unstarted_run(task, run_id):
    # Cancellation may arrive before the coroutine executes even its first line.
    if manager.context.get("runId") != run_id or not manager.running:
        return
    phase = "cancelled" if task.cancelled() else "failed"
    asyncio.create_task(manager.broadcast_status({
        **manager.last_status, "done": True, "phase": phase,
        "cancelled": task.cancelled(), "error": 0 if task.cancelled() else 1,
    }))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"asset_version": static_asset_version(APP_RESOURCE_DIR)},
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@app.get("/logo.png", include_in_schema=False)
async def logo_png():
    if os.path.exists(LOGO_PATH):
        return FileResponse(LOGO_PATH, media_type="image/png")
    return Response(status_code=404)


@app.get("/riviu-logo.png", include_in_schema=False)
async def riviu_logo():
    return await logo_png()


@app.get("/list-files")
async def list_files(file_id: str | None = None, platform: str = "tiktok"):
    try:
        selected_id, target_path, file_label, platform = resolve_source_file(file_id, platform, allow_empty=True)
    except SourceRequestError as error:
        return error.response()
    google_source = dict(google_sheet_source_for_file(EXCEL_DIR, selected_id)) if selected_id else {}
    target_sheet_url = google_source.get("url", "")
    target_spreadsheet_id = ""
    if target_sheet_url:
        try:
            target_spreadsheet_id = parse_google_spreadsheet_id(target_sheet_url)
        except Exception:
            target_spreadsheet_id = ""
    try:
        sheets = await asyncio.to_thread(find_data_sheet_names, target_path) if target_path else []
    except Exception:
        sheets = []
    selected_sheet = CURRENT_SELECTED_SHEET if file_id is None else (sheets[0] if sheets else "")
    scan_sheet = (CURRENT_SCAN_SHEET or selected_sheet) if file_id is None else selected_sheet
    state = {
        "files": file_entries(),
        "current": selected_id,
        "file_id": selected_id,
        "currentLabel": file_label,
        "currentSheet": selected_sheet,
        "sheets": sheets,
        "scanSheet": scan_sheet,
        "googleSheetUrl": target_sheet_url,
        "platform": platform,
    }
    oauth = await asyncio.to_thread(google_io, oauth_status, EXCEL_DIR)
    state.update({
        "googlePushReady": bool(target_spreadsheet_id) and oauth.get("valid"),
        "googleOAuthConfigured": oauth.get("configured"),
        "googleOAuthAuthorized": oauth.get("valid"),
    })
    return state


@app.post("/select-file")
async def select_file(data: dict):
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET, CURRENT_SCAN_SHEET, SOURCE_BUSY
    try:
        platform = validate_platform(data.get("platform", "tiktok"))
    except SourceRequestError as error:
        return error.response()
    if source_mutation_blocked():
        return JSONResponse({"error": "Đang xử lý dữ liệu, hãy chờ hoàn tất trước khi đổi file"}, status_code=409)
    try:
        file_id, target_path, _, platform = resolve_source_file(data.get("file_id", data.get("filename", "")), platform)
    except SourceRequestError as error:
        return error.response()
    SOURCE_BUSY = True
    try:
        names = await asyncio.to_thread(find_data_sheet_names, target_path)
        all_names = await asyncio.to_thread(workbook_sheet_names, target_path)
        selected_sheet = clean_text(data.get("display_sheet", data.get("sheet_name", ""))) or (names[0] if names else "")
        scan_sheet = clean_text(data.get("scan_sheet", "")) or (selected_sheet if selected_sheet in names else (names[0] if names else ""))
        if (selected_sheet and selected_sheet not in all_names) or (scan_sheet and scan_sheet not in names):
            return JSONResponse({"error": "Sheet không tồn tại"}, status_code=400)
        CURRENT_SELECTED_FILE = file_id
        CURRENT_SELECTED_SHEET = selected_sheet
        CURRENT_SCAN_SHEET = scan_sheet
        return {"success": True, "selected": file_id, "file_id": file_id, "sheet": selected_sheet, "scanSheet": scan_sheet, "platform": platform}
    finally:
        SOURCE_BUSY = False


@app.post("/sync-google-sheet")
async def sync_google_sheet(data: dict):
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET, CURRENT_SCAN_SHEET, SOURCE_BUSY
    try:
        platform = validate_platform(data.get("platform", "tiktok"))
    except SourceRequestError as error:
        return error.response()
    activate = data.get("activate", True)
    if not isinstance(activate, bool):
        return JSONResponse({"error": "activate phải là boolean"}, status_code=400)
    if source_mutation_blocked():
        return JSONResponse({"error": "Đang xử lý dữ liệu, hãy chờ hoàn tất trước khi đồng bộ"}, status_code=409)
    source_url = (data.get("url") or "").strip()
    if not source_url:
        return JSONResponse(content={"error": "Vui lòng nhập URL Google Sheet"}, status_code=400)

    SOURCE_BUSY = True
    try:
        spreadsheet_id = parse_google_spreadsheet_id(source_url)
        base_dir = EXCEL_DIR
        def download_and_publish():
            spreadsheet_title = fetch_google_spreadsheet_title(source_url)
            timestamp_filename = format_filename_datetime()
            file_label = google_sheet_sync_label(spreadsheet_title)
            desired_id = google_sheet_file_id_from_title(spreadsheet_title, timestamp_filename)
            desired_path = safe_join(base_dir, desired_id)
            os.makedirs(os.path.dirname(desired_path), exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="_import-", suffix=".xlsx", dir=os.path.dirname(desired_path), delete=False) as staged:
                staged_path = staged.name
            try:
                google_io(sync_download_google_sheet, base_dir, source_url, spreadsheet_id, staged_path)
                preview = validate_workbook(staged_path)
                data_sheets = find_data_sheet_names(staged_path)
                preview["currentSheet"] = data_sheets[0] if data_sheets else ""
                target_path = publish_new_workbook(staged_path, desired_path)
            finally:
                os.unlink(staged_path)
            file_id = os.path.relpath(target_path, base_dir).replace("\\", "/")
            register_google_sheet_source(base_dir, file_id, source_url, title=file_label)
            return file_id, file_label, preview
        file_id, file_label, preview = await complete_blocking(download_and_publish)
        selected_sheet = preview.get("currentSheet", "")
        if activate:
            CURRENT_SELECTED_FILE = file_id
            CURRENT_SELECTED_SHEET = selected_sheet
            CURRENT_SCAN_SHEET = selected_sheet
        await manager.broadcast_log(
            f"Đã nạp Google Sheet thành file mới: {file_label} → {os.path.basename(file_id)} • sheet={selected_sheet} • url={source_url}",
            level="OK",
        )
        return {
            "success": True,
            "file": file_id,
            "file_id": file_id,
            "label": file_label,
            "sheets": preview.get("sheets", []),
            "currentSheet": selected_sheet,
            "scanSheet": selected_sheet,
            "spreadsheetId": spreadsheet_id,
            "platform": platform,
            "activate": activate,
        }
    except Exception as error:
        return JSONResponse(content={"error": f"Lỗi đồng bộ Google Sheet: {str(error)}"}, status_code=500)
    finally:
        SOURCE_BUSY = False


@app.get("/google-oauth-status")
async def google_oauth_status():
    source = current_google_sheet_source()
    target_url = source.get("url", "")
    target_id = ""
    if target_url:
        try:
            target_id = parse_google_spreadsheet_id(target_url)
        except Exception:
            target_id = ""
    status = await asyncio.to_thread(google_io, oauth_status, EXCEL_DIR)
    return {
        **status,
        "connectedSheetUrl": target_url,
        "connectedSpreadsheetId": target_id,
    }


@app.get("/proxy-list")
async def get_proxy_list():
    text = load_proxy_list_text(EXCEL_DIR)
    configs = parse_proxy_text(text)
    return {
        "text": text,
        "count": len(configs),
    }


@app.post("/proxy-list")
async def save_proxy_list(data: dict):
    text = str(data.get("text") or "")
    save_proxy_list_text(EXCEL_DIR, text)
    configs = parse_proxy_text(text)
    return {
        "ok": True,
        "count": len(configs),
        "message": f"Đã lưu {len(configs)} proxy" if configs else "Đã lưu danh sách (chưa đọc được proxy hợp lệ)",
    }


@app.get("/api/version")
async def api_version():
    from proxy_utils import PROXY_TEST_BUILD
    from scraper import MAX_WORKERS

    return {
        "proxyTestBuild": PROXY_TEST_BUILD,
        "app": "riviu-reports",
        "maxWorkers": MAX_WORKERS,
    }


@app.post("/proxy-test")
async def test_proxy_list(data: dict | None = None):
    payload = data or {}
    text = str(payload.get("text") or load_proxy_list_text(EXCEL_DIR))
    if payload.get("save"):
        save_proxy_list_text(EXCEL_DIR, text)
    return await asyncio.to_thread(test_proxy_text, text)


@app.post("/google-oauth-client")
async def upload_google_oauth_client(file: UploadFile = File(...)):
    try:
        if not file.filename.lower().endswith(".json"):
            return JSONResponse(content={"error": "Chỉ nhận file JSON OAuth client."}, status_code=400)
        payload = json.loads((await file.read()).decode("utf-8"))
        await asyncio.to_thread(google_io, save_oauth_client, EXCEL_DIR, payload)
        return {"success": True}
    except Exception as error:
        return JSONResponse(content={"error": f"Không lưu được OAuth client: {str(error)}"}, status_code=500)


@app.post("/google-oauth-login")
async def google_oauth_login():
    # Waits on the user's browser (bounded by a timeout); holding GOOGLE_IO_LOCK here would freeze
    # previews, pushes and scans until the user finished or abandoned the login tab.
    if not GOOGLE_LOGIN_LOCK.acquire(blocking=False):
        return JSONResponse(content={"error": "Đang chờ đăng nhập Google ở cửa sổ trình duyệt khác."}, status_code=409)
    try:
        await asyncio.to_thread(authorize_google, EXCEL_DIR)
        return {"success": True}
    except Exception as error:
        return JSONResponse(content={"error": f"Đăng nhập Google thất bại: {str(error)}"}, status_code=500)
    finally:
        GOOGLE_LOGIN_LOCK.release()


@app.post("/push-google-sheet")
async def push_google_sheet(data: dict | None = None):
    global SOURCE_BUSY
    data = data or {}
    try:
        platform = validate_platform(data.get("platform", "tiktok"))
    except SourceRequestError as error:
        return error.response()
    if source_mutation_blocked():
        return JSONResponse({"error": "Đang xử lý dữ liệu, hãy chờ lưu xong trước khi xuất"}, status_code=409)
    try:
        file_id, target_path, _, platform = resolve_source_file(data.get("file_id"), platform)
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)

    source = dict(google_sheet_source_for_file(EXCEL_DIR, file_id))
    request_url = clean_text(data.get("url", ""))
    source_url = request_url or source.get("url", "")
    upload_sheet = clean_text(data.get("sourceSheet", "")) or source_sheet_default(file_id, scan=True, explicit=data.get("file_id") is not None)
    spreadsheet_id = ""
    try:
        spreadsheet_id = parse_google_spreadsheet_id(source_url)
    except Exception:
        spreadsheet_id = ""
    if not spreadsheet_id:
        return JSONResponse(content={"error": "Vui lòng nhập đúng URL Google Sheet đích trước khi tạo sheet."}, status_code=400)

    SOURCE_BUSY = True
    try:
        available_sheets = await asyncio.to_thread(find_data_sheet_names, target_path)
        if upload_sheet and upload_sheet not in available_sheets:
            return JSONResponse(content={"error": f"Sheet {upload_sheet} không tồn tại trong file."}, status_code=400)
        rows = await asyncio.to_thread(build_workbook_rows, target_path, sheet_name=upload_sheet, platform=platform)
        if not rows:
            return JSONResponse(content={"error": f"Sheet \"{upload_sheet or 'đang chọn'}\" không có link {platform} để tạo sheet."}, status_code=400)
        values = build_google_push_rows(rows, platform=platform)
        sheet_title = await complete_blocking(google_io, push_rows_to_new_sheet, EXCEL_DIR, spreadsheet_id, values, source_sheet_name=upload_sheet, platform=platform)
        return {"success": True, "sheetTitle": sheet_title, "sourceSheet": upload_sheet, "file": file_id, "file_id": file_id, "platform": platform}
    except Exception as error:
        return JSONResponse(content={"error": f"Đẩy dữ liệu lên Google Sheet thất bại: {str(error)}"}, status_code=500)
    finally:
        SOURCE_BUSY = False


@app.get("/preview-excel")
async def preview_excel(sheet_name: str = "", file_id: str | None = None, platform: str = "tiktok"):
    global CURRENT_SELECTED_SHEET
    explicit = file_id is not None
    try:
        file_id, target_path, file_label, platform = resolve_source_file(file_id, platform)
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return {"sheets": [], "currentSheet": "", "columns": [], "data": [], "message": f"Không tìm thấy file {CURRENT_SELECTED_FILE}."}
    try:
        requested_sheet = sheet_name or source_sheet_default(file_id, explicit=explicit) or default_sheet_for_file(file_id)

        def _load_preview():
            return read_sheet_preview(target_path, sheet_name=requested_sheet, platform=platform)

        preview = await asyncio.to_thread(_load_preview)
        if not explicit and CURRENT_SELECTED_FILE == file_id:
            CURRENT_SELECTED_SHEET = preview.get("currentSheet", CURRENT_SELECTED_SHEET)
        preview["file"] = file_id
        preview["file_id"] = file_id
        preview["fileLabel"] = file_label
        preview["platform"] = platform
        preview["summarySource"] = data_sheet_name_for_summary_title(preview.get("sheets", []), preview.get("currentSheet", ""))
        return preview
    except Exception as error:
        return {"file": file_id, "sheets": [], "currentSheet": "", "columns": [], "data": [], "message": f"File đang bận hoặc lỗi: {str(error)}"}


@app.get("/summary-dashboard")
async def summary_dashboard(sheet_name: str = "", file_id: str | None = None, platform: str = "tiktok"):
    explicit = file_id is not None
    try:
        file_id, target_path, file_label, platform = resolve_source_file(file_id, platform)
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)
    try:
        requested_sheet = clean_text(sheet_name) or source_sheet_default(file_id, scan=True, explicit=explicit)
        summary = await asyncio.to_thread(read_summary_dashboard, target_path, requested_sheet or None)
        summary["file"] = file_id
        summary["file_id"] = file_id
        summary["fileLabel"] = file_label
        summary["platform"] = platform
        return summary
    except Exception as error:
        return JSONResponse(content={"error": f"Không đọc được sheet Tổng kết: {str(error)}"}, status_code=500)


@app.get("/download-excel")
async def download_excel(file_id: str | None = None, platform: str = "tiktok"):
    try:
        file_id, target_path, _, platform = resolve_source_file(file_id, platform)
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)
    content = await asyncio.to_thread(Path(target_path).read_bytes)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(os.path.basename(file_id))},
    )


@app.get("/scrape-history")
async def scrape_history(limit: int = Query(default=50)):
    safe_limit = max(1, min(int(limit or 50), 200))
    return {"history": read_scrape_history(EXCEL_DIR, limit=safe_limit)}


@app.get("/report-partners")
async def report_partners(
    sheet_name: str = "",
    apply_min_views: bool = True,
    min_views: int = 100,
    platform: str = "tiktok",
    file_id: str | None = None,
):
    try:
        file_id, target_path, file_label, platform = resolve_source_file(file_id, platform)
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)

    try:
        data_sheets = await asyncio.to_thread(find_data_sheet_names, target_path)
        requested_sheet = clean_text(sheet_name) or (data_sheets[0] if data_sheets else "")
        if requested_sheet and requested_sheet not in data_sheets:
            return JSONResponse(content={"error": f"Sheet {requested_sheet} không tồn tại trong file."}, status_code=400)
        safe_min_views = max(int(min_views or 0), 0)

        def _load_partners():
            return list_workbook_partners_with_link_counts(
                target_path,
                sheet_name=requested_sheet,
                apply_min_views=apply_min_views,
                min_views=safe_min_views,
                platform=platform,
            )

        partners = await asyncio.to_thread(_load_partners)
        all_sheets = await asyncio.to_thread(workbook_sheet_names, target_path)
        return {
            "partners": partners,
            "total": len(partners),
            "file": file_id,
            "file_id": file_id,
            "fileLabel": file_label,
            "sheets": data_sheets,
            "currentSheet": requested_sheet,
            "dataSheet": requested_sheet,
            "allSheets": all_sheets,
            "platform": platform,
        }
    except Exception as error:
        return JSONResponse(content={"error": f"Không đọc được danh sách đối tác: {str(error)}"}, status_code=500)


@app.post("/export-report")
async def export_report(data: dict):
    try:
        _, target_path, _, platform = resolve_source_file(data.get("file_id"), data.get("platform", "tiktok"))
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)

    selected_partners = data.get("partners") or []
    if not isinstance(selected_partners, list) or not selected_partners:
        return JSONResponse(content={"error": "Vui lòng chọn ít nhất một đối tác"}, status_code=400)

    apply_min_views = bool(data.get("applyMinViews", True))
    try:
        min_views = int(data.get("minViews", 100))
    except (TypeError, ValueError):
        min_views = 100
    min_views = max(min_views, 0)

    try:
        payload = await asyncio.to_thread(
            build_export_payload,
            target_path,
            selected_partners,
            apply_min_views,
            min_views,
            data.get("sheetName", ""),
            platform,
        )
        return Response(
            content=payload["content"],
            media_type=payload["media_type"],
            headers={"Content-Disposition": content_disposition(payload["filename"])},
        )
    except ValueError as error:
        status_code = 404 if "Không tìm thấy" in str(error) else 400
        return JSONResponse(content={"error": str(error)}, status_code=status_code)
    except Exception as error:
        return JSONResponse(content={"error": f"Lỗi xuất báo cáo: {str(error)}"}, status_code=500)


@app.post("/upload-excel")
async def upload_excel(
    file: UploadFile = File(...),
    platform: Annotated[str, Form()] = "tiktok",
    activate: Annotated[bool, Form()] = True,
):
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET, CURRENT_SCAN_SHEET, SOURCE_BUSY
    try:
        platform = validate_platform(platform)
    except SourceRequestError as error:
        return error.response()
    if source_mutation_blocked():
        return {"success": False, "error": "Đang xử lý dữ liệu, hãy chờ hoàn tất trước khi nạp file"}
    SOURCE_BUSY = True
    staged_path = None
    try:
        original_name = os.path.basename(file.filename or "")
        if not original_name.lower().endswith(".xlsx"):
            return {"success": False, "error": "Chỉ hỗ trợ .xlsx. Hãy lưu file .xls thành .xlsx trước khi nạp."}

        desired_path = safe_join(EXCEL_DIR, original_name)
        if not desired_path:
            return {"success": False, "error": "Tên file không hợp lệ"}
        with tempfile.NamedTemporaryFile(prefix="_upload-", suffix=".xlsx", dir=EXCEL_DIR, delete=False) as staged:
            staged_path = staged.name
        def validate_and_publish():
            with open(staged_path, "wb") as destination:
                file.file.seek(0)
                shutil.copyfileobj(file.file, destination)
            preview = validate_workbook(staged_path)
            sheets = find_data_sheet_names(staged_path)
            published = publish_new_workbook(staged_path, desired_path)
            return published, preview, sheets
        save_path, preview, sheets = await complete_blocking(validate_and_publish)
        file_id = os.path.relpath(save_path, EXCEL_DIR).replace("\\", "/")
        selected_sheet = sheets[0] if sheets else ""
        if activate:
            CURRENT_SELECTED_FILE = file_id
            CURRENT_SELECTED_SHEET = selected_sheet
            CURRENT_SCAN_SHEET = selected_sheet
        return {"success": True, "filename": file_id, "file_id": file_id, "sheet": selected_sheet, "scanSheet": selected_sheet, "sheets": sheets, "platform": platform, "activate": activate}
    except Exception as error:
        return {"success": False, "error": str(error)}
    finally:
        if staged_path and os.path.exists(staged_path):
            os.unlink(staged_path)
        SOURCE_BUSY = False


def normalize_source_preferences(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), dict):
        raise ValueError("Cấu hình nguồn không hợp lệ.")
    sources = {}
    for platform in PLATFORMS:
        item = payload["sources"].get(platform, {})
        if not isinstance(item, dict):
            raise ValueError("Cấu hình nguồn không hợp lệ.")
        result = {}
        for key in ("fileId", "displaySheet", "scanSheet", "pushSheet", "url"):
            value = item.get(key, "")
            if not isinstance(value, str) or len(value) > (4096 if key == "url" else 1024) or any(ord(char) < 32 for char in value):
                raise ValueError("Cấu hình nguồn không hợp lệ.")
            if key == "fileId" and value:
                if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value) or any(part in {"", ".", ".."} for part in value.replace("\\", "/").split("/")):
                    raise ValueError("Cấu hình nguồn không hợp lệ.")
                value = value.replace("\\", "/")
            elif key in {"displaySheet", "scanSheet", "pushSheet"}:
                if len(value) > 31 or re.search(r"[\\[\\]:*?/\\\\]", value):
                    raise ValueError("Cấu hình sheet không hợp lệ.")
            elif key == "url" and value.strip():
                parsed = urlsplit(value.strip())
                match = re.fullmatch(r"/spreadsheets/d/[A-Za-z0-9_-]+(?:/[^?#]*)?", parsed.path)
                if parsed.scheme != "https" or parsed.hostname != "docs.google.com" or parsed.username or parsed.password or parsed.port not in (None, 443) or not match:
                    raise ValueError("Link Google Sheet không hợp lệ.")
                gid = parse_qs(parsed.query).get("gid", parse_qs(parsed.fragment).get("gid", [""]))[0]
                value = urlunsplit(("https", "docs.google.com", parsed.path, "gid=" + gid if gid.isdigit() else "", ""))
            result[key] = value
        sources[platform] = result
    return {"sources": sources}


def source_preferences_path():
    return os.path.join(EXCEL_DIR, "data", "source_preferences.json")


def load_source_preferences():
    try:
        with open(source_preferences_path(), encoding="utf-8") as stream:
            payload = normalize_source_preferences(json.load(stream))
        return payload
    except (OSError, ValueError, TypeError):
        return {"sources": {}}


def save_source_preferences(payload):
    path = source_preferences_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json_atomic(path, payload)


@app.get("/source-preferences")
async def source_preferences():
    return JSONResponse(await asyncio.to_thread(load_source_preferences), headers={"Cache-Control": "no-store"})


@app.post("/source-preferences")
async def update_source_preferences(request: Request):
    global SOURCE_BUSY
    if source_mutation_blocked():
        return JSONResponse({"error": "Đang xử lý phiên; lưu cấu hình nguồn sau."}, status_code=409, headers={"Cache-Control": "no-store"})
    SOURCE_BUSY = True
    try:
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 32768:
                return JSONResponse({"error": "Cấu hình nguồn quá lớn."}, status_code=413)
        payload = normalize_source_preferences(json.loads(data))
        await complete_blocking(save_source_preferences, payload)
        return JSONResponse({"success": True}, headers={"Cache-Control": "no-store"})
    except (ValueError, TypeError):
        return JSONResponse({"error": "Cấu hình nguồn không hợp lệ."}, status_code=400)
    except OSError:
        return JSONResponse({"error": "Không lưu được cấu hình nguồn."}, status_code=503)
    finally:
        SOURCE_BUSY = False


@app.get("/threads-session/status")
async def threads_session_status():
    try:
        return await complete_blocking(threads_session_manager().status)
    except Exception:
        return JSONResponse({"configured": False, "state": "storage_unavailable", "message": "Không truy cập được kho cookie an toàn."}, status_code=503)


async def bounded_session_body(request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_IMPORT_BYTES:
            raise SessionError("too_large")
    return bytes(data)


@app.post("/threads-session/import")
async def import_threads_session(request: Request):
    global SOURCE_BUSY
    if source_mutation_blocked():
        return JSONResponse({"error": "Đang quét hoặc xử lý dữ liệu; không đổi cookie lúc này."}, status_code=409)
    SOURCE_BUSY = True
    try:
        payload = await bounded_session_body(request)
        return await threads_session_manager().import_cookie(payload)
    except SessionError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception:
        return JSONResponse({"error": "Không import được phiên Threads."}, status_code=503)
    finally:
        SOURCE_BUSY = False


@app.post("/threads-session/verify")
async def verify_threads_session():
    global SOURCE_BUSY
    if source_mutation_blocked():
        return JSONResponse({"error": "Đang quét hoặc xử lý dữ liệu; chờ để kiểm tra cookie."}, status_code=409)
    SOURCE_BUSY = True
    try:
        return await threads_session_manager().verify()
    except SessionError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception:
        return JSONResponse({"error": "Không kiểm tra được phiên Threads."}, status_code=503)
    finally:
        SOURCE_BUSY = False


@app.delete("/threads-session")
async def delete_threads_session():
    global SOURCE_BUSY
    if source_mutation_blocked():
        return JSONResponse({"error": "Đang quét hoặc xử lý dữ liệu; không xóa cookie lúc này."}, status_code=409)
    SOURCE_BUSY = True
    try:
        return await complete_blocking(threads_session_manager().clear)
    except SessionError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception:
        return JSONResponse({"error": "Không xóa được phiên Threads trong kho an toàn."}, status_code=503)
    finally:
        SOURCE_BUSY = False


async def prepare_scan_start(payload):
    """Validate a start request against the workbook the client names; never the last /select-file."""
    global SCAN_STARTING
    platform = validate_platform(payload.get("platform", "tiktok"))
    if "file_id" in payload and (not isinstance(payload["file_id"], str) or not payload["file_id"]):
        raise SourceRequestError("Chưa chọn workbook hợp lệ")
    file_id, target_path, _, platform = resolve_source_file(payload.get("file_id"), platform)
    if not target_path or not os.path.isfile(target_path):
        raise SourceRequestError("Chưa chọn workbook hợp lệ")
    sheets = await asyncio.to_thread(find_data_sheet_names, target_path)
    if not sheets:
        raise ValueError("File đã chọn không có sheet dữ liệu để quét")
    legacy_sheet = source_sheet_default(file_id, scan=True) if "file_id" not in payload else ""
    sheet_name = clean_text(payload.get("sheet_name", "")) or (legacy_sheet if legacy_sheet in sheets else sheets[0])
    if sheet_name not in sheets:
        raise ValueError("Sheet không tồn tại")
    scrape_mode = clean_text(payload.get("scrape_mode", "")).lower()
    if scrape_mode not in {"request", "browser", "hybrid"}:
        scrape_mode = "request"
    use_proxy = bool(payload.get("use_proxy", False))
    proxy_text = str(payload.get("proxy_text") or "")
    proxy_error = validate_proxy_start(use_proxy, proxy_text, EXCEL_DIR, platform=platform, mode=scrape_mode)
    if proxy_error:
        raise ValueError(proxy_error)
    partners_payload = payload.get("partners", [])
    options = scan_options(
        platform=platform,
        file_id=file_id,
        sheet_name=sheet_name,
        worker_count=clamp_worker_count(payload.get("workers", 20)),
        scrape_mode=scrape_mode,
        use_proxy=use_proxy,
        proxy_text=proxy_text,
        partner=clean_text(payload.get("partner", "")) or None,
        partners=[clean_text(name) for name in partners_payload if clean_text(name)] if isinstance(partners_payload, list) else [],
        create_result_sheet=bool(payload.get("create_result_sheet", False)),
        push_to_google=bool(payload.get("push_to_google", False)),
    )
    if platform == "threads" and payload.get("use_threads_session") is True:
        generation = payload.get("threads_session_generation")
        if not isinstance(generation, str) or not generation:
            raise ValueError("Tải lại trạng thái cookie Threads trước khi quét.")
        SCAN_STARTING = True
        try:
            options["threads_cookies"] = await complete_blocking(threads_session_manager().snapshot, generation)
            options["threads_proxies"] = resolve_threads_proxies(EXCEL_DIR, proxy_text, scrape_mode) if use_proxy else []
            options["threads_generation"] = generation
            # Cookie sessions are verified through Chromium, which cannot authenticate to SOCKS5.
            if options["threads_proxies"] and chromium_proxy_unsupported(options["threads_proxies"][0]):
                raise SessionError("proxy_unsupported")
        except SessionError as error:
            raise ValueError(str(error)) from error
        except Exception as error:
            raise ValueError("Không nạp được phiên Threads đã lưu.") from error
        finally:
            SCAN_STARTING = False
    return target_path, options


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global SCRAPE_TASK
    if not trusted_local_request(websocket, require_origin=True) or not valid_local_session(websocket):
        await websocket.close(code=1008)
        return
    await manager.connect(websocket)
    async def reject_start(message):
        await websocket.send_json({"type": "log", "message": message, "level": "ERROR"})
        await websocket.send_json({"type": "status", "data": {"total": 0, "processed": 0, "success": 0, "error": 1, "done": True, "phase": "failed", "message": message}})
    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {"action": data}
            if not isinstance(payload, dict):
                await reject_start("Lệnh quét không hợp lệ")
                continue

            action = payload.get("action")
            if action == "start":
                if source_mutation_blocked():
                    await websocket.send_json({"type": "session", "data": manager.snapshot(), **manager.context})
                    await websocket.send_json({"type": "log", "message": "Đang xử lý dữ liệu hoặc cập nhật ứng dụng. Vui lòng chờ hoàn tất."})
                    continue
                try:
                    target_path, options = await prepare_scan_start(payload)
                except (SourceRequestError, ValueError) as error:
                    await reject_start(str(error))
                    continue
                run_context = manager.begin_run(options["platform"], options["file_id"], options["sheet_name"])
                SCRAPE_TASK = asyncio.create_task(run_scraper_safely(target_path, options, run_context=run_context))
                SCRAPE_TASK.add_done_callback(lambda task, run_id=run_context["runId"]: finalize_unstarted_run(task, run_id))
                await manager.broadcast_status(manager.last_status)
            elif action == "cancel":
                if SCRAPE_TASK and not SCRAPE_TASK.done():
                    # Repeated cancel must not interrupt the pending save/cleanup.
                    if not SCRAPE_TASK.cancelling():
                        SCRAPE_TASK.cancel()
                    await manager.broadcast_log("Đang hủy phiên quét hiện tại...")
                else:
                    await manager.broadcast_log("Không có phiên quét nào đang chạy.")
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=1231)
