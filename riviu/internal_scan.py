"""Scans requested by Riviu Data (POST /internal/scan) on the Postgres backend.

One scan per platform across processes: `pg_try_advisory_lock(hashtext('scan:'||platform))`
on a dedicated connection held for the whole scan (released and closed at the end, so it
never leaks into a pool, review M-25). Inside this process the usual single-scan gate
(`scan_state`) applies too: a scan started from the UI and one requested here never overlap.

Progress is recorded in `scan_run` (phase dang_quet -> hoan_tat / dung_som / loi / huy,
totals, diagnostic) and `scan_run_item` (one row per written or skipped DB row).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from riviu import scan_state
from riviu import server_context as context
from riviu.platforms.tiktok.lifecycle import finish_pending_task
from riviu.store import config as store_config
from riviu.store import postgres
from riviu.store.rules import current_thang, parse_thang

log = logging.getLogger("riviu.internal_scan")

PLATFORMS = ("tiktok", "threads")
TRIGGERS = ("timer", "thu_cong")


class ScanBusy(Exception):
    """Another scan of this platform (or any scan in this process) is running."""


class ScanRequestError(ValueError):
    pass


@dataclass(frozen=True)
class ScanRequest:
    platform: str
    thang: str
    trigger: str
    started_by: str
    scan_run_id: int | None = None


def parse_request(body) -> ScanRequest:
    if not isinstance(body, dict):
        raise ScanRequestError("Body phải là JSON object")
    unknown = set(body) - {"platform", "thang", "trigger", "started_by", "scan_run_id"}
    if unknown:
        raise ScanRequestError(f"Trường không hỗ trợ: {', '.join(sorted(unknown))}")
    platform = body.get("platform")
    if platform not in PLATFORMS:
        raise ScanRequestError("platform phải là 'tiktok' hoặc 'threads'")
    # Riviu Data may omit thang ("the scanner's default"): the current month in Vietnam.
    thang = body.get("thang") or current_thang()
    if not isinstance(thang, str):
        raise ScanRequestError("thang phải có dạng YYYY-MM")
    parse_thang(thang)  # raises MonthError (a ValueError)
    trigger = body.get("trigger") or "thu_cong"
    if trigger not in TRIGGERS:
        raise ScanRequestError("trigger phải là 'timer' hoặc 'thu_cong'")
    started_by = body.get("started_by")
    if started_by is None:
        started_by = "systemd:rda-daily-scan" if trigger == "timer" else "riviu-data"
    if not isinstance(started_by, str) or not started_by.strip() or len(started_by) > 200:
        raise ScanRequestError("started_by phải là chuỗi 1-200 ký tự")
    scan_run_id = body.get("scan_run_id")
    if scan_run_id is not None and (isinstance(scan_run_id, bool) or not isinstance(scan_run_id, int) or scan_run_id <= 0):
        raise ScanRequestError("scan_run_id phải là số nguyên dương")
    return ScanRequest(platform, thang, trigger, started_by.strip(), scan_run_id)


def make_store(platform):
    return postgres.PostgresStore(platform, store_config.ACTIVE.database_url)


async def scan_tiktok(session, events):
    from riviu.platforms.tiktok import run_scraper

    config = store_config.ACTIVE
    await run_scraper(
        session.label, events, worker_count=config.tiktok_workers, base_dir=context.EXCEL_DIR,
        file_label=f"Riviu Data {session.label}", sheet_name=session.label,
        use_request=config.tiktok_mode != "browser", browser_fallback=config.tiktok_mode == "hybrid",
        use_proxy=config.use_proxy, proxy_text="", store_session=session,
    )


async def scan_threads(session, events):
    from riviu.platforms.threads import run_threads_scraper

    config = store_config.ACTIVE
    await run_threads_scraper(
        session.label, events, worker_count=config.threads_workers, sheet_name=session.label,
        mode=config.threads_mode, base_dir=context.EXCEL_DIR, use_proxy=config.use_proxy, proxy_text="",
        store_session=session,
    )


# Patched in tests; keyed like scan_service.SCAN_RUNNERS.
SCANNERS = {"tiktok": scan_tiktok, "threads": scan_threads}


async def start_scan(request: ScanRequest, *, wait: bool):
    """Claim the platform, then run (wait=True: until done, returning the summary) or return at once."""
    if scan_state.source_mutation_blocked():
        raise ScanBusy("Máy quét đang bận (đang có phiên quét hoặc thao tác dữ liệu khác).")
    # SCAN_STARTING is set before the first await, so no other start slips in meanwhile.
    with scan_state.scan_starting():
        store = make_store(request.platform)
        month_start, _ = parse_thang(request.thang)
        lock_conn = await store.connect()
        try:
            if not await postgres.try_scan_lock(lock_conn, request.platform):
                raise ScanBusy(f"Đang có phiên quét {request.platform} khác (advisory lock).")
            try:
                run_id = await postgres.begin_scan_run(
                    lock_conn, platform=request.platform, month_start=month_start, trigger=request.trigger,
                    started_by=request.started_by, scan_run_id=request.scan_run_id,
                )
            except BaseException:
                await postgres.release_scan_lock(lock_conn, request.platform)
                raise
        except BaseException:
            await lock_conn.close()
            raise
        run_context = scan_state.manager.begin_run(request.platform, f"riviu-data:{request.platform}", request.thang)
        task = asyncio.create_task(_run_scan(store, lock_conn, request, run_id))
        scan_state.SCRAPE_TASK = task
    log.info("scan %s/%s started: run %s (%s, %s)", request.platform, request.thang, run_id, request.trigger, run_context["runId"])
    if not wait:
        return {"scan_run_id": run_id, "platform": request.platform, "thang": request.thang, "phase": postgres.PHASE_RUNNING}
    try:
        # Shielded: a dropped HTTP connection must not cancel a scan that is writing.
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if task.cancelled():
            return {"scan_run_id": run_id, "platform": request.platform, "thang": request.thang, "phase": postgres.PHASE_CANCELLED}
        raise


async def _run_scan(store, lock_conn, request, run_id):
    from riviu.scan_service import RunEvents

    manager = scan_state.manager
    events = RunEvents(manager)
    session = None
    phase, stop_reason, error_message = postgres.PHASE_FAILED, None, None
    try:
        await manager.broadcast_status(manager.last_status)
        await manager.broadcast_log(
            f"Riviu Data: quét {request.platform} tháng {request.thang} ({request.trigger}, scan_run {run_id})."
        )
        session = await store.open_scan(request.thang, run_id=run_id, events=events)
        if session.locked_count:
            await manager.broadcast_log(f"Bỏ qua {session.locked_count} dòng đã khoá chỉ số (khoa_chi_so).")
        await SCANNERS[request.platform](session, events)
        stop_reason = (events.terminal or {}).get("stopReason") or session.diagnostic.get("stopReason")
        phase = postgres.PHASE_STOPPED if stop_reason else postgres.PHASE_DONE
        await manager.broadcast_status({**(events.terminal or manager.last_status), "done": True, "phase": "completed"})
    except asyncio.CancelledError:
        phase, stop_reason = postgres.PHASE_CANCELLED, "huy"
        await manager.broadcast_log("Đã hủy phiên quét.")
        await manager.broadcast_status({**manager.last_status, "done": True, "cancelled": True, "phase": "cancelled"})
        raise
    except Exception as error:  # noqa: BLE001 - reported in scan_run and the response
        stop_reason = f"loi: {type(error).__name__}"
        error_message = str(error)[:500]
        log.warning("scan %s/%s failed: %s", request.platform, request.thang, type(error).__name__)
        await manager.broadcast_log(f"Lỗi quét: {error_message}")
        await manager.broadcast_status({
            **manager.last_status, "error": max(1, manager.last_status.get("error", 0)),
            "done": True, "phase": "failed", "message": error_message,
        })
    finally:
        summary = await finish_pending_task(asyncio.create_task(
            _close_scan(lock_conn, session, request, run_id, phase, stop_reason, error_message)
        ))
    return summary


def session_totals(session):
    if session is None:
        return {}
    return {
        **session.totals,
        "written": session.written,
        "skipped": len(session.skipped),
        "lockedRows": session.locked_count,
        "invalidLinks": len(session.invalid_links),
        "rows": len(session.rows),
    }


async def _close_scan(lock_conn, session, request, run_id, phase, stop_reason, error_message):
    totals = session_totals(session)
    diagnostic = {}
    if session is not None:
        diagnostic = {**session.diagnostic, "skippedRows": session.skipped[:200]}
    if error_message:
        diagnostic["error"] = error_message
    try:
        await postgres.update_scan_run(
            lock_conn, run_id, totals=totals, phase=phase, stop_reason=stop_reason, diagnostic=diagnostic, finished=True,
        )
    finally:
        try:
            if session is not None:
                await session.close()
        finally:
            try:
                await postgres.release_scan_lock(lock_conn, request.platform)
            finally:
                await lock_conn.close()
    return {
        "scan_run_id": run_id, "platform": request.platform, "thang": request.thang, "trigger": request.trigger,
        "phase": phase, "stop_reason": stop_reason, "totals": totals,
        **({"error": error_message} if error_message else {}),
    }
