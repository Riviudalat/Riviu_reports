"""The local FastAPI server: the `app` instance, its middleware, static files and routers.

Routes live in `riviu/api/` (one router per concern), scan dispatch in `riviu.scan_service`,
the single-scan/source gates in `riviu.scan_state`, and the data directory plus legacy
selection in `riviu.server_context`. This module re-exports their public names so that
`riviu.app.<name>` keeps working; the mutable state names in `_STATE_OWNERS` are forwarded
to their owning module on both read and write (e.g. `riviu.app.EXCEL_DIR = path` updates
`riviu.server_context.EXCEL_DIR`), so every router sees one value.
"""

import os
import sys
import types
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from riviu import scan_state, scheduler, server_context, server_mode
from riviu.api import auth, export, google, pages, proxy, scan, schedule, security, sources, threads_session, tiktok_session
from riviu.workbook_utils import ensure_data_dir

# Public names kept importable from `riviu.app` (tests, desktop_server, older callers).
from riviu.api.export import export_report, report_partners  # noqa: F401
from riviu.api.google import (  # noqa: F401
    delete_google_service_account,
    google_oauth_login,
    google_oauth_status,
    push_google_sheet,
    sync_download_google_sheet,
    sync_google_sheet,
    upload_google_oauth_client,
    upload_google_service_account,
)
from riviu.api.pages import api_version, favicon, index, logo_png, riviu_logo, static_asset_version, templates  # noqa: F401
from riviu.api.proxy import get_proxy_list, save_proxy_list, test_proxy_list  # noqa: F401
from riviu.api.scan import scrape_history, websocket_endpoint  # noqa: F401
from riviu.api.security import (  # noqa: F401
    LOCAL_HOSTS,
    LOCAL_SESSION_COOKIE,
    LOCAL_SESSION_TOKEN,
    protect_local_api,
    trusted_local_request,
    valid_local_session,
)
from riviu.api.sources import (  # noqa: F401
    download_excel,
    list_files,
    load_source_preferences,
    normalize_source_preferences,
    preview_excel,
    save_source_preferences,
    select_file,
    source_preferences,
    source_preferences_path,
    summary_dashboard,
    update_source_preferences,
    upload_excel,
)
from riviu.api.threads_session import (  # noqa: F401
    bounded_session_body,
    delete_threads_session,
    import_threads_session,
    threads_session_status,
    verify_threads_session,
)
from riviu.api.tiktok_session import (  # noqa: F401
    delete_tiktok_session,
    import_tiktok_session,
    tiktok_session_status,
    verify_tiktok_session,
)
from riviu.google_sheets_sync import GoogleLoginRequired  # noqa: F401
from riviu.scan_service import (  # noqa: F401
    PROXY_VALIDATORS,
    SCAN_RUNNERS,
    RunEvents,
    finalize_unstarted_run,
    prepare_scan_start,
    push_scan_results,
    run_scraper_safely,
    run_threads_scan,
    run_tiktok_scan,
    scan_options,
    threads_proxy_error,
    tiktok_proxy_error,
    validate_proxy_start,
)
from riviu.scan_state import ConnectionManager, scan_running, source_mutation_blocked  # noqa: F401
from riviu.server_context import complete_blocking, google_io, threads_session_manager, tiktok_session_manager  # noqa: F401
from riviu.source_files import (  # noqa: F401
    SourceRequestError,
    content_disposition,
    current_google_sheet_source,
    default_sheet_for_file,
    ensure_selected_file,
    file_display_label,
    file_entries,
    publish_new_workbook,
    resolve_file_path,
    resolve_source_file,
    source_sheet_default,
    validate_platform,
    validate_workbook,
)


_STATE_OWNERS = {
    **{name: server_context for name in (
        "APP_RESOURCE_DIR", "EXCEL_DIR", "LOGO_PATH",
        "CURRENT_SELECTED_FILE", "CURRENT_SELECTED_SHEET", "CURRENT_SCAN_SHEET",
        "THREADS_SESSION", "THREADS_SESSION_ROOT", "TIKTOK_SESSION", "TIKTOK_SESSION_ROOT",
        "GOOGLE_IO_LOCK", "GOOGLE_LOGIN_LOCK",
    )},
    **{name: scan_state for name in (
        "manager", "SCRAPE_TASK", "SCAN_STARTING", "SOURCE_BUSY", "DESKTOP_UPDATE_PENDING",
    )},
}


class _StateForwardingModule(types.ModuleType):
    def __getattr__(self, name):
        owner = _STATE_OWNERS.get(name)
        if owner is None:
            raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")
        return getattr(owner, name)

    def __setattr__(self, name, value):
        owner = _STATE_OWNERS.get(name)
        if owner is None:
            super().__setattr__(name, value)
        else:
            setattr(owner, name, value)

    def __delattr__(self, name):
        if name in _STATE_OWNERS:
            raise AttributeError(f"cannot delete forwarded state {name!r}")
        super().__delattr__(name)


sys.modules[__name__].__class__ = _StateForwardingModule


# Off unless RIVIU_SERVER_MODE=1; a server-mode process with missing settings must not
# come up without its login, so ServerModeError propagates and stops the import.
server_mode.activate_from_environment()

os.makedirs(server_context.EXCEL_DIR, exist_ok=True)

@asynccontextmanager
async def lifespan(_app):
    # Scheduled automatic runs exist only on a server (riviu/scheduler.py).
    if server_mode.ACTIVE is not None:
        scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(lifespan=lifespan)

_STATIC_DIR = os.path.join(server_context.APP_RESOURCE_DIR, "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

app.middleware("http")(security.protect_local_api)

for _router in (
    auth.router,
    pages.router,
    sources.router,
    google.router,
    proxy.router,
    export.router,
    threads_session.router,
    tiktok_session.router,
    scan.router,
    schedule.router,
):
    app.include_router(_router)

ensure_data_dir(server_context.EXCEL_DIR)
