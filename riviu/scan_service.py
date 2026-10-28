"""Validate, dispatch and finish one scan: the transaction behind the `/ws` start action.

Platform differences live in the `SCAN_RUNNERS` and `PROXY_VALIDATORS` tables (a test
asserts they cover the same platforms as `workbook_utils.PLATFORMS`).

One scan is a main pass over the sheet, then (option `retry_failed`, on by default) one retry
pass over the rows that ended with a transient error, after a cooldown. Each pass runs under a
stall watchdog, and `riviu.scan_journal` records the rows each save wrote, so a stalled pass or
a restarted server continues with only the rows not yet saved (`resume_interrupted_scan`).
"""

import asyncio
import logging
import os
import secrets
import time

from riviu import scan_journal, scan_state, server_mode
from riviu import server_context as context
from riviu.google_sheets_sync import GoogleLoginRequired, push_rows_to_new_sheet
from riviu.platforms.threads import THREADS_RATE_LIMIT_WAIT_MINUTES, resolve_threads_proxies, run_threads_scraper
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
from riviu.workbook_utils import (
    PLATFORMS,
    build_workbook_rows,
    clean_text,
    default_data_sheet,
    find_data_sheet_names,
    google_sheet_source_for_file,
)


logger = logging.getLogger("riviu.scan_service")
# Clock of the stall watchdog and sleep of the retry cooldown; tests replace them.
_clock = time.monotonic
_cooldown_sleep = asyncio.sleep

COOKIE_LABELS = {"threads": "Cookie Threads", "tiktok": "Cookie TikTok"}
# Saved-session states that cannot log a scan in; the scan is refused instead of running anonymously.
UNUSABLE_COOKIE_STATES = frozenset({"none", "expired", "invalid", "checkpoint", "storage_unavailable", "missing_session"})


def cookie_problem_message(platform, detail=""):
    """The fail-fast message for a scan started with a cookie session that cannot be used."""
    label = COOKIE_LABELS[platform]
    where = f"vào /admin › {label} để nạp lại" if server_mode.ACTIVE is not None else f"mở {label} › Cấu hình để nạp lại"
    detail = clean_text(detail).rstrip(".")
    return f"{label} đã hết hạn hoặc chưa nạp{f' ({detail})' if detail else ''} — {where}."


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
    """Forward progress, but reserve terminal events for the transaction wrapper.

    `last_progress` (the `_clock` time of the last status or result) feeds the stall watchdog;
    logs do not count, since a stuck TikTok pass still logs "Đang chờ kết quả...".
    """
    def __init__(self, connection_manager):
        self.manager = connection_manager
        self.terminal = None
        self.retry_pass = False
        self.retry_count = 0
        self.last_progress = _clock()

    def touch(self):
        self.last_progress = _clock()

    async def broadcast_status(self, status):
        self.touch()
        extra = {"retryPass": True, "retryCount": self.retry_count} if self.retry_pass else {}
        if status.get("done"):
            self.terminal = dict(status)
            await self.manager.broadcast_status({**status, **extra, "done": False, "phase": "saving"})
        else:
            phase = "starting" if status.get("phase") == "starting" else "running"
            await self.manager.broadcast_status({**status, **extra, "phase": phase})

    async def broadcast_log(self, *args, **kwargs):
        await self.manager.broadcast_log(*args, **kwargs)

    async def broadcast_data(self, row):
        self.touch()
        await self.manager.broadcast_data(row)

    async def broadcast_duplicates(self, data):
        await self.manager.broadcast_duplicates(data)


def pass_options(options):
    """Row limits and the save hook of one pass (set by run_scan_passes; absent: every row)."""
    return {key: options[key] for key in ("skip_rows", "only_rows", "on_rows_saved") if options.get(key) is not None}


async def run_tiktok_scan(target_path, events, options):
    # Only a scan started with "Cookie TikTok" on passes cookies; the default call is unchanged.
    session_options = {"session_cookies": options["tiktok_cookies"]} if options.get("tiktok_cookies") else {}
    session_options.update(pass_options(options))
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
    session_options = pass_options(options)
    if options.get("threads_cookies") is not None:
        session_options.update(session_cookies=options["threads_cookies"], proxy_configs=options.get("threads_proxies") or [])
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
        # One more pass over transient failures after a cooldown; `scheduled`: started by the scheduler.
        "retry_failed": True, "scheduled": False,
    }
    # `resume`: the journal of an interrupted run this start continues (resume_interrupted_scan).
    unknown = set(overrides) - set(options) - {"threads_cookies", "threads_proxies", "threads_generation", "tiktok_cookies", "resume"}
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


def journal_payload(options):
    """The start payload a resume replays: no proxy text, no cookies (flags only)."""
    return {
        "platform": options["platform"], "file_id": options["file_id"], "sheet_name": options["sheet_name"],
        "workers": options["worker_count"], "scrape_mode": options["scrape_mode"], "use_proxy": options["use_proxy"],
        "partners": list(options["partners"]), "partner": options["partner"] or "",
        "create_result_sheet": options["create_result_sheet"], "push_to_google": options["push_to_google"],
        "retry_failed": options.get("retry_failed", True),
        "use_threads_session": options.get("threads_cookies") is not None,
        "use_tiktok_session": bool(options.get("tiktok_cookies")),
    }


def proxy_route(platform, proxy_text, mode):
    """The proxies a scan with this text would use (the saved list when the text is empty)."""
    if platform == "threads":
        return resolve_threads_proxies(context.EXCEL_DIR, proxy_text, mode)
    return [config for config in resolve_proxy_configs(context.EXCEL_DIR, proxy_text) if config.get("enabled", True)]


async def begin_journal(options):
    resume = options.get("resume")
    if resume:
        journal = scan_journal.RunJournal(resume, context.EXCEL_DIR)
    else:
        proxy_hash = salt = None
        if options["use_proxy"]:
            salt = secrets.token_hex(16)
            try:
                route = options.get("threads_proxies") if options.get("threads_cookies") is not None else proxy_route(
                    options["platform"], options["proxy_text"], options["scrape_mode"])
                proxy_hash = scan_journal.fingerprint(route, salt)
            except Exception:
                proxy_hash = None  # no fingerprint: a resume is refused rather than guessing the route
        journal = scan_journal.RunJournal.begin(
            context.EXCEL_DIR, options, journal_payload(options), proxy_hash=proxy_hash, salt=salt or "")
    await journal.save()
    return journal


async def run_watched_pass(runner, target_path, events, options):
    """Run one pass under the stall watchdog. True: the watchdog stopped it (results saved)."""
    events.terminal = None
    events.touch()
    pass_task = asyncio.create_task(runner(target_path, events, options))
    stalled = False

    async def watchdog():
        nonlocal stalled
        while not pass_task.done():
            await asyncio.sleep(scan_journal.STALL_CHECK_SECONDS)
            if not pass_task.done() and _clock() - events.last_progress >= scan_journal.STALL_SECONDS:
                stalled = True
                pass_task.cancel()  # the scanner saves what it has before it ends
                return

    watcher = asyncio.create_task(watchdog())
    try:
        await pass_task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if not stalled or (current is not None and current.cancelling()):
            raise
        return True
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
    return False


async def handle_stall(journal):
    """Count one stall restart; past the cap the run fails (its saved results stay)."""
    attempts = journal.resume_attempts + 1
    journal.mark(resumeAttempts=attempts)
    await journal.save()
    minutes = max(1, scan_journal.STALL_SECONDS // 60)
    if attempts > scan_journal.MAX_RESUME_ATTEMPTS:
        raise RuntimeError(
            f"Phiên quét không có kết quả mới trong {minutes} phút và đã tự tiếp tục {scan_journal.MAX_RESUME_ATTEMPTS} lần; "
            "dừng hẳn. Kết quả đã quét vẫn được lưu.")
    await scan_state.manager.broadcast_log(
        f"CẢNH BÁO: Phiên quét không có kết quả mới trong {minutes} phút. Đã dừng lượt bị treo (kết quả đã lưu) và tự tiếp tục "
        f"với các link còn lại (lần {attempts}/{scan_journal.MAX_RESUME_ATTEMPTS}).", level="WARN")


def _url_count(keys):
    return len({key[2].casefold() for key in keys})


def _wait_text(seconds):
    return f"{seconds // 60} phút" if seconds >= 60 and seconds % 60 == 0 else f"{seconds} giây"


async def usable_threads_session():
    """Generation of the stored Threads cookie session, or None when it cannot log a scan in."""
    try:
        status = await context.complete_blocking(context.threads_session_manager().status)
    except Exception:
        return None
    if not status.get("configured") or not status.get("generation") or status.get("state") in UNUSABLE_COOKIE_STATES:
        return None
    return status["generation"]


async def threads_session_overrides(options, generation):
    """The stored cookies and their fixed route, as a scan started with Cookie Threads uses them."""
    cookies = await context.complete_blocking(context.threads_session_manager().snapshot, generation)
    proxies = resolve_threads_proxies(context.EXCEL_DIR, options["proxy_text"], options["scrape_mode"]) if options["use_proxy"] else []
    if proxies and chromium_proxy_unsupported(proxies[0]):
        raise SessionError("proxy_unsupported")
    return {"threads_cookies": cookies, "threads_proxies": proxies, "threads_generation": generation}


def login_hint():
    return "nạp Cookie Threads ở /admin" if server_mode.ACTIVE is not None else "nạp Cookie Threads (Cấu hình)"


async def run_retry_pass(runner, target_path, events, options, journal, rows_of):
    """Rescan the rows `rows_of()` names (re-read after a stall) under the watchdog."""
    while True:
        only = rows_of()
        if not only:
            return
        stalled = await run_watched_pass(runner, target_path, events, {
            # A result tab is a snapshot of the main pass; the retry pass never adds another.
            **options, "create_result_sheet": False, "only_rows": only,
            "on_rows_saved": journal.recorder(retry_pass=True),
        })
        if not stalled:
            return
        await handle_stall(journal)


async def run_scan_passes(target_path, events, options, journal):
    """Main pass (resumed past saved rows), then the retry pass. Returns the terminal status.

    The retry pass covers two kinds of rows. Transient errors are rescanned the same way after
    the platform cooldown. Threads `invalid_post` rows of an anonymous run (posts Threads shows
    only to logged-in viewers) are rescanned only through the stored Cookie Threads session and
    its fixed route; without a usable session they are not retried and the run reports them
    (`loginRequired`, `notice`), and a run that already used the session never retries them.
    """
    manager = scan_state.manager
    platform = options["platform"]
    runner = SCAN_RUNNERS[platform]
    combined = False  # counts come from the journal once a resume or a retry pass happened
    prior = None
    main_terminal = None
    if not journal.main_done:
        while True:
            skip = journal.saved_keys()
            if skip:
                prior, combined = journal.counts(), True
                await manager.broadcast_log(
                    f"Tiếp tục phiên quét: bỏ qua {len(skip)} dòng đã lưu trong lượt này, chỉ quét các link còn lại.")
            stalled = await run_watched_pass(runner, target_path, events, {
                **options, "skip_rows": skip or None, "on_rows_saved": journal.recorder(),
            })
            if not stalled:
                break
            await handle_stall(journal)
        main_terminal = dict(events.terminal) if events.terminal else None
        journal.mark(mainDone=True, stopReason=(main_terminal or {}).get("stopReason") or "")
        await journal.save()
    else:
        combined = True
        await manager.broadcast_log("Lượt quét chính đã xong trước khi gián đoạn; chuyển sang quét lại link lỗi.")

    allowed = bool(options.get("retry_failed", True) and not journal.data.get("stopReason")
                   and not journal.data.get("retrySkipped"))
    transient = journal.retry_candidates() if allowed else set()
    login_rows = journal.login_candidates()
    run_on_cookie = options.get("threads_cookies") is not None
    session_generation = None
    if allowed and login_rows and not run_on_cookie:
        session_generation = await usable_threads_session()
    login_retry = login_rows if session_generation else set()
    retry = bool(transient or login_retry)
    transient_count, login_count = _url_count(transient), _url_count(login_retry)
    count = transient_count + login_count
    if retry:
        combined = True
        if transient:
            cooldown = int(scan_journal.RETRY_COOLDOWN_SECONDS.get(platform, 120))
            if journal.has_rate_limited(transient):
                # Links that got HTTP 429 on a retired route wait out the rate limit first.
                cooldown = max(cooldown, THREADS_RATE_LIMIT_WAIT_MINUTES * 60)
        else:
            # Only login-only posts: switching to the cookie session is the fix, not waiting.
            cooldown = scan_journal.LOGIN_RETRY_COOLDOWN_SECONDS
        parts = [f"{transient_count} link lỗi tạm thời"] if transient_count else []
        if login_count:
            parts.append(f"{login_count} bài Threads chỉ xem được khi đăng nhập (quét lại bằng Cookie Threads)")
        await manager.broadcast_log(f"Còn {' và '.join(parts)}. Chờ {_wait_text(cooldown)} rồi quét lại một lần.")
        await manager.broadcast_status({
            **(main_terminal or manager.last_status), "done": False, "phase": "running",
            # retryAt (epoch seconds) lets a page that reconnects mid-wait show the real countdown.
            "retryPass": True, "retryWaitSeconds": cooldown, "retryAt": round(time.time() + cooldown, 3),
            "retryCount": count,
        })
        if not await wait_retry_cooldown(cooldown):
            # The admin chose "Bỏ qua quét lại": the run ends now, as completed with the main-pass results.
            journal.mark(retrySkipped=True)
            await journal.save()
            await manager.broadcast_log(
                f"Đã bỏ qua quét lại {count} link lỗi theo yêu cầu; giữ kết quả lượt chính.", level="WARN")
            retry = False
    if retry:
        await manager.broadcast_log(f"Quét lại {count} link lỗi...")
        events.retry_pass = True
        events.retry_count = count
        if transient:
            await run_retry_pass(runner, target_path, events, options, journal, journal.retry_candidates)
        if login_retry and not (events.terminal or {}).get("stopReason"):
            try:
                session = await threads_session_overrides(options, session_generation)
                await run_retry_pass(runner, target_path, events, {**options, **session}, journal, journal.login_candidates)
            except SessionError as error:
                # Only a session failure says anything about the cookies; the main-pass results stay.
                try:
                    await context.complete_blocking(context.threads_session_manager().invalidate, session_generation, error.state)
                except Exception:
                    pass
                await manager.broadcast_log(
                    f"Không quét lại được bằng Cookie Threads ({error}); giữ kết quả lượt chính.", level="WARN")
        retry_counts = journal.counts(transient | login_retry)
        fixed = retry_counts["success"] + retry_counts["hidden"]
        await manager.broadcast_log(
            f"Quét lại xong: {fixed}/{count} link đọc được, {count - fixed} link vẫn lỗi (giữ số cũ).",
            level="OK" if fixed == count else "WARN")
        if (events.terminal or {}).get("stopReason"):
            await manager.broadcast_log("Lượt quét lại dừng sớm; các link chưa quét lại giữ kết quả lượt chính.", level="WARN")

    terminal = dict(main_terminal or events.terminal or manager.last_status)
    for key in ("retryPass", "retryWaitSeconds", "retryAt", "retryCount", "stopReason", "loginRequired", "notice"):
        terminal.pop(key, None)
    # Only the main pass decides whether the run is complete (a 429 there stops it).
    if main_terminal and main_terminal.get("stopReason"):
        terminal["stopReason"] = main_terminal["stopReason"]
    if combined:
        counts = journal.counts()
        total = (prior or {}).get("processed", 0) + main_terminal.get("total", 0) if main_terminal else counts["processed"]
        terminal.update(counts, total=max(total, counts["processed"]))
    login_left = _url_count(journal.login_rows())
    if login_left and not run_on_cookie and not login_retry:
        notice = f"{login_left} bài Threads chỉ xem được khi đăng nhập (hoặc đã bị xoá) — {login_hint()} rồi quét lại."
        terminal.update(loginRequired=login_left, notice=notice)
        await manager.broadcast_log(notice, level="WARN")
    elif login_left:
        await manager.broadcast_log(
            f"{login_left} link Threads không xem được kể cả khi đăng nhập (có thể đã bị xoá); giữ số cũ.", level="WARN")
    return terminal


async def wait_retry_cooldown(seconds):
    """Sleep the cooldown before the retry pass. Returns False when the admin skipped the retry
    pass meanwhile (`skip_retry_wait`), True when the cooldown ran out."""
    skip = asyncio.Event()
    scan_state.RETRY_SKIP = skip
    sleeper = asyncio.ensure_future(_cooldown_sleep(seconds))
    skipped = asyncio.ensure_future(skip.wait())
    try:
        await asyncio.wait({sleeper, skipped}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in (sleeper, skipped):
            waiter.cancel()
        if scan_state.RETRY_SKIP is skip:
            scan_state.RETRY_SKIP = None
    return not skip.is_set()


def skip_retry_wait():
    """End the cooldown of the running scan without a retry pass. The run then finishes as
    completed with the main-pass results (unlike a cancel, which is final and keeps nothing pending)."""
    skip = scan_state.RETRY_SKIP
    if skip is None or skip.is_set():
        return False
    skip.set()
    return True


def user_cancel_scan():
    """Cancel the running scan for the user: its journal is dropped and it is never resumed."""
    task = scan_state.SCRAPE_TASK
    if not task or task.done():
        return False
    run_id = scan_state.manager.context.get("runId")
    if run_id:
        scan_state.USER_CANCELLED_RUNS.add(run_id)
    # Repeated cancel must not interrupt the pending save/cleanup.
    if not task.cancelling():
        task.cancel()
    return True


async def run_scraper_safely(target_path, options, run_context=None):
    """Run one scan transaction; `options` holds the validated start payload (see websocket start)."""
    manager = scan_state.manager
    platform = options["platform"]
    events = RunEvents(manager)
    journal = None
    run_id = (run_context or {}).get("runId")
    try:
        file_id = file_id_for_path(target_path)
        if run_context is None:
            run_id = manager.begin_run(platform, file_id, options["sheet_name"])["runId"]
        sheets = await asyncio.to_thread(find_data_sheet_names, target_path)
        scan_sheet = clean_text(options["sheet_name"]) or default_data_sheet(sheets)
        if not scan_sheet or scan_sheet not in sheets:
            raise ValueError("Sheet không tồn tại trong file đã chọn")
        options = {**options, "sheet_name": scan_sheet, "file_label": file_display_label(file_id)}
        journal = await begin_journal(options)
        terminal = await run_scan_passes(target_path, events, options, journal)
        if options["push_to_google"] and terminal.get("stopReason"):
            # A run stopped early (Threads HTTP 429) is incomplete: never push it as a result tab.
            await manager.broadcast_log("BỎ QUA đẩy Google: phiên quét đã dừng sớm, kết quả chưa đầy đủ.", level="WARN")
        elif options["push_to_google"]:
            await push_scan_results(target_path, file_id, scan_sheet, platform)
        await journal.clear()
        await manager.broadcast_status({**terminal, "done": True, "phase": "completed"})
    except asyncio.CancelledError:
        if journal is not None and run_id in scan_state.USER_CANCELLED_RUNS:
            await journal.clear()
        # Any other cancellation (server shutdown) keeps the journal: the next start resumes the run.
        await manager.broadcast_log("Đã hủy phiên quét.")
        await manager.broadcast_status({**manager.last_status, "done": True, "cancelled": True, "phase": "cancelled"})
        raise
    except Exception as error:
        if journal is not None:
            await journal.clear()
        message = str(error)
        if options.get("threads_cookies") is not None and not isinstance(error, (SessionError, ValueError)):
            # Unexpected errors from a logged-in run may echo request details; keep them out of the log.
            message = "Phiên quét Threads đăng nhập gặp lỗi kỹ thuật; cookie vẫn được giữ, hãy thử lại."
        if options.get("tiktok_cookies") and not isinstance(error, ValueError):
            message = "Phiên quét TikTok dùng cookie gặp lỗi kỹ thuật; cookie vẫn được giữ, hãy thử lại."
        if isinstance(error, SessionError) and options.get("threads_cookies") is not None:
            if error.state in UNUSABLE_COOKIE_STATES:
                message = cookie_problem_message("threads", message)
            # Only a session failure says anything about the cookies; other errors leave them valid.
            try:
                await context.complete_blocking(context.threads_session_manager().invalidate, options.get("threads_generation"), error.state)
            except Exception:
                pass
        await manager.broadcast_log(f"Lỗi quét: {message}")
        await manager.broadcast_status({**manager.last_status, "error": max(1, manager.last_status.get("error", 0)), "done": True, "phase": "failed", "message": message})
    finally:
        scan_state.USER_CANCELLED_RUNS.discard(run_id)


def start_scan_task(target_path, options):
    """Begin the run context and start the one scan task (the `/ws` start action and the scheduler)."""
    manager = scan_state.manager
    run_context = manager.begin_run(options["platform"], options["file_id"], options["sheet_name"])
    task = asyncio.create_task(run_scraper_safely(target_path, options, run_context=run_context))
    scan_state.SCRAPE_TASK = task
    task.add_done_callback(lambda task, run_id=run_context["runId"]: finalize_unstarted_run(task, run_id))
    return task, run_context


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
    sheet_name = clean_text(payload.get("sheet_name", "")) or (legacy_sheet if legacy_sheet in sheets else default_data_sheet(sheets))
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
        retry_failed=payload.get("retry_failed", True) is not False,
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
                # Missing/expired cookies fail here; a rejected login fails in the run's preflight,
                # which verifies the session on the chosen route before any row is touched.
                detail = str(error)
                raise ValueError(cookie_problem_message("threads", detail) if error.state in UNUSABLE_COOKIE_STATES else detail) from error
            except Exception as error:
                raise ValueError("Không nạp được phiên Threads đã lưu.") from error
    if platform == "tiktok" and payload.get("use_tiktok_session") is True:
        with scan_state.scan_starting():
            session = context.tiktok_session_manager()
            try:
                options["tiktok_cookies"] = await context.complete_blocking(session.snapshot)
                # One small account-info request: an expired login fails now instead of scanning anonymously.
                check = (await context.complete_blocking(session.verify)).get("check") or {}
            except TikTokSessionError as error:
                detail = str(error)
                raise ValueError(cookie_problem_message("tiktok", detail) if error.state in UNUSABLE_COOKIE_STATES else detail) from error
            except Exception as error:
                raise ValueError("Không nạp được cookie TikTok đã lưu.") from error
        if check.get("state") in UNUSABLE_COOKIE_STATES:
            raise ValueError(cookie_problem_message("tiktok", check.get("message", "")))
        if check.get("state") != "valid":
            await scan_state.manager.broadcast_log(
                "Chưa xác minh được cookie TikTok lúc bắt đầu (lỗi kết nối); vẫn quét với cookie đã lưu.", level="WARN")
    return target_path, options


RESUME_TASK = None


async def resume_interrupted_scan():
    """Server start: continue a scan the previous process left unfinished (its journal remains).

    Only the rows not yet saved in that run are scanned, through the normal prepare/start path
    (cookie check included). Never after a user cancel or a Threads HTTP 429 stop (no journal or
    `stopReason`), at most `MAX_RESUME_ATTEMPTS` times, and never on another proxy route.
    """
    manager = scan_state.manager
    if scan_state.source_mutation_blocked():
        return None
    # Held from before the first await until the task exists: a scheduled slot is skipped meanwhile.
    with scan_state.scan_starting():
        base_dir = context.EXCEL_DIR
        data = await asyncio.to_thread(scan_journal.load_journal, base_dir)
        if data is None:
            return None
        platform = data.get("platform")
        label = PLATFORMS[platform].label if platform in PLATFORMS else str(platform)
        attempts = int(data.get("resumeAttempts") or 0)

        async def give_up(reason, level="WARN"):
            logger.warning("Không tự tiếp tục phiên quét %s: %s", label, reason)
            await manager.broadcast_log(f"Không tự tiếp tục phiên quét {label} bị gián đoạn: {reason}", level=level)
            await asyncio.to_thread(scan_journal.clear_journal, base_dir)
            return None

        if data.get("stopReason"):
            return await give_up("phiên trước đã dừng vì Threads giới hạn tần suất (HTTP 429).")
        if attempts >= scan_journal.MAX_RESUME_ATTEMPTS:
            return await give_up(f"đã tự tiếp tục {scan_journal.MAX_RESUME_ATTEMPTS} lần.")
        # Counted before starting, so a crash during the resumed run still uses up this attempt.
        data["resumeAttempts"] = attempts + 1
        await asyncio.to_thread(scan_journal.write_json_atomic, scan_journal.journal_path(base_dir), data)
        payload = {**data["payload"], "action": "start", "proxy_text": ""}
        try:
            if payload.get("use_threads_session"):
                status = await context.complete_blocking(context.threads_session_manager().status)
                payload["threads_session_generation"] = status.get("generation") or ""
                if not status.get("configured"):
                    raise ValueError(cookie_problem_message("threads", status.get("message", "")))
            target_path, options = await prepare_scan_start(payload)
            fingerprint = data.get("proxyFingerprint")
            if options["use_proxy"]:
                route = options.get("threads_proxies") if options.get("threads_cookies") is not None else proxy_route(
                    platform, "", options["scrape_mode"])
                if not fingerprint or scan_journal.fingerprint(route, fingerprint.get("salt", "")) != fingerprint.get("hash"):
                    raise ValueError("danh sách proxy đã lưu khác tuyến của phiên trước; không tự đổi proxy.")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return await give_up(str(error), level="ERROR")
        options["resume"] = data
        options["scheduled"] = bool(data.get("scheduled"))
        if options["scheduled"]:
            options["push_to_google"] = False  # a scheduled run never pushes, resumed or not
        task, _run_context = start_scan_task(target_path, options)
        await manager.broadcast_log(
            f"Tự tiếp tục phiên quét {label} bị gián đoạn (sheet \"{options['sheet_name']}\", lần {attempts + 1}/"
            f"{scan_journal.MAX_RESUME_ATTEMPTS}): chỉ quét các link chưa lưu.", level="WARN")
        await manager.broadcast_status(manager.last_status)
        return task


def schedule_resume():
    """Start resume_interrupted_scan in the background (server mode lifespan)."""
    global RESUME_TASK
    RESUME_TASK = asyncio.create_task(resume_interrupted_scan())
    return RESUME_TASK


async def stop_resume():
    global RESUME_TASK
    task, RESUME_TASK = RESUME_TASK, None
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
