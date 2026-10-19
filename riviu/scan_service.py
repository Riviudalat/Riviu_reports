"""Validate, dispatch and finish one scan: the transaction behind the `/ws` start action.

Platform differences live in the `SCAN_RUNNERS` and `PROXY_VALIDATORS` tables (a test
asserts they cover the same platforms as `workbook_utils.PLATFORMS`).
"""

import asyncio
import os

from riviu import scan_state
from riviu import server_context as context
from riviu.google_sheets_sync import GoogleLoginRequired, push_rows_to_new_sheet
from riviu.platforms.threads import resolve_threads_proxies, run_threads_scraper
from riviu.platforms.threads_session import SessionError, chromium_proxy_unsupported
from riviu.platforms.tiktok import clamp_worker_count, run_scraper
from riviu.platforms.tiktok_session import TikTokSessionError
from riviu.proxy_utils import resolve_proxy_configs
from riviu.reports import build_google_push_rows
from riviu.source_files import (
    SourceRequestError,
    file_display_label,
    file_id_for_path,
    resolve_source_file,
    source_sheet_default,
    validate_platform,
)
from riviu.workbook_utils import build_workbook_rows, clean_text, find_data_sheet_names, google_sheet_source_for_file


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
    # Only a scan started with "Cookie TikTok" on passes cookies; the default call is unchanged.
    session_options = {"session_cookies": options["tiktok_cookies"]} if options.get("tiktok_cookies") else {}
    await run_scraper(
        target_path,
        events,
        worker_count=options["worker_count"],
        selected_partner=options["partner"],
        selected_partners=options["partners"],
        create_result_sheet=options["create_result_sheet"],
        base_dir=context.EXCEL_DIR,
        file_label=options["file_label"],
        sheet_name=options["sheet_name"],
        use_request=options["scrape_mode"] != "browser",
        browser_fallback=options["scrape_mode"] == "hybrid",
        use_proxy=options["use_proxy"],
        proxy_text=options["proxy_text"],
        **session_options,
    )


async def run_threads_scan(target_path, events, options):
    session_options = {}
    if options.get("threads_cookies") is not None:
        session_options = {"session_cookies": options["threads_cookies"], "proxy_configs": options.get("threads_proxies") or []}
    await run_threads_scraper(
        target_path, events, worker_count=options["worker_count"],
        selected_partners=options["partners"] or ([options["partner"]] if options["partner"] else []),
        sheet_name=options["sheet_name"], mode=options["scrape_mode"],
        base_dir=context.EXCEL_DIR, use_proxy=options["use_proxy"], proxy_text=options["proxy_text"], **session_options,
    )


SCAN_RUNNERS = {"tiktok": run_tiktok_scan, "threads": run_threads_scan}


def scan_options(**overrides):
    """Defaults for one scan; the websocket start handler overrides them from the validated payload."""
    options = {
        "platform": "tiktok", "file_id": "", "sheet_name": "", "worker_count": 1, "scrape_mode": "request",
        "use_proxy": False, "proxy_text": "", "partner": None, "partners": [],
        "create_result_sheet": False, "push_to_google": False,
    }
    unknown = set(overrides) - set(options) - {"threads_cookies", "threads_proxies", "threads_generation", "tiktok_cookies"}
    if unknown:
        raise TypeError(f"Unknown scan options: {sorted(unknown)}")
    return {**options, **overrides}


async def push_scan_results(target_path, file_id, scan_sheet, platform):
    manager = scan_state.manager
    spreadsheet_id = google_sheet_source_for_file(context.EXCEL_DIR, file_id).get("spreadsheetId", "")
    if not spreadsheet_id:
        await manager.broadcast_log("BỎ QUA đẩy Google: file hiện tại chưa gắn với Google Sheet gốc.")
        return
    try:
        rows = await asyncio.to_thread(build_workbook_rows, target_path, sheet_name=scan_sheet, platform=platform)
        # Off the loop on the fresh row copies; a cancel still waits for it before the scan ends.
        values = await context.complete_blocking(build_google_push_rows, rows, platform=platform)
        sheet_title = await context.complete_blocking(context.google_io, push_rows_to_new_sheet, context.EXCEL_DIR, spreadsheet_id, values, source_sheet_name=scan_sheet, platform=platform)
    except GoogleLoginRequired as error:
        await manager.broadcast_log(f"Quét đã lưu xong nhưng chưa đẩy lên Google: {error}", level="WARN")
    except Exception as error:
        # The scan itself already saved; a push failure must not be reported as a failed scan.
        await manager.broadcast_log(f"Quét đã lưu xong nhưng đẩy Google Sheet thất bại: {error}", level="WARN")
    else:
        await manager.broadcast_log(f"Đã đẩy kết quả lên Google Sheet: {sheet_title}.")


async def run_scraper_safely(target_path, options, run_context=None):
    """Run one scan transaction; `options` holds the validated start payload (see websocket start)."""
    manager = scan_state.manager
    platform = options["platform"]
    events = RunEvents(manager)
    try:
        file_id = file_id_for_path(target_path)
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
        if options.get("tiktok_cookies") and not isinstance(error, ValueError):
            message = "Phiên quét TikTok dùng cookie gặp lỗi kỹ thuật; cookie vẫn được giữ, hãy thử lại."
        if isinstance(error, SessionError) and options.get("threads_cookies") is not None:
            # Only a session failure says anything about the cookies; other errors leave them valid.
            try:
                await context.complete_blocking(context.threads_session_manager().invalidate, options.get("threads_generation"), error.state)
            except Exception:
                pass
        await manager.broadcast_log(f"Lỗi quét: {message}")
        await manager.broadcast_status({**manager.last_status, "error": max(1, manager.last_status.get("error", 0)), "done": True, "phase": "failed", "message": message})


def finalize_unstarted_run(task, run_id):
    # Cancellation may arrive before the coroutine executes even its first line.
    manager = scan_state.manager
    if manager.context.get("runId") != run_id or not manager.running:
        return
    phase = "cancelled" if task.cancelled() else "failed"
    asyncio.create_task(manager.broadcast_status({
        **manager.last_status, "done": True, "phase": phase,
        "cancelled": task.cancelled(), "error": 0 if task.cancelled() else 1,
    }))


async def prepare_scan_start(payload):
    """Validate a start request against the workbook the client names; never the last /select-file."""
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
    proxy_error = validate_proxy_start(use_proxy, proxy_text, context.EXCEL_DIR, platform=platform, mode=scrape_mode)
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
        with scan_state.scan_starting():
            try:
                options["threads_cookies"] = await context.complete_blocking(context.threads_session_manager().snapshot, generation)
                options["threads_proxies"] = resolve_threads_proxies(context.EXCEL_DIR, proxy_text, scrape_mode) if use_proxy else []
                options["threads_generation"] = generation
                # Cookie sessions are verified through Chromium, which cannot authenticate to SOCKS5.
                if options["threads_proxies"] and chromium_proxy_unsupported(options["threads_proxies"][0]):
                    raise SessionError("proxy_unsupported")
            except SessionError as error:
                raise ValueError(str(error)) from error
            except Exception as error:
                raise ValueError("Không nạp được phiên Threads đã lưu.") from error
    if platform == "tiktok" and payload.get("use_tiktok_session") is True:
        try:
            options["tiktok_cookies"] = await context.complete_blocking(context.tiktok_session_manager().snapshot)
        except TikTokSessionError as error:
            raise ValueError(str(error)) from error
        except Exception as error:
            raise ValueError("Không nạp được cookie TikTok đã lưu.") from error
    return target_path, options
