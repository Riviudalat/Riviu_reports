"""run_scraper: one TikTok scan from workbook load to final save, summary and history."""
import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import openpyxl
from playwright.async_api import async_playwright

from riviu.proxy_utils import proxy_label, resolve_proxy_configs, set_session_proxies
from riviu.workbook_utils import (
    clean_text,
    format_display_datetime,
    highlight_single_partner_link_rows,
    highlight_video_link_rows,
    load_channel_overrides,
    rebuild_summary_sheet,
    should_highlight_video_link,
    summary_sheet_title_for_data_sheet,
)
from riviu.platforms.tiktok import channels, cookies, history, workbook_io
from riviu.platforms.tiktok.constants import DEFAULT_RETRIES, DEFAULT_SAVE_EVERY, DEFAULT_WORKERS
from riviu.platforms.tiktok.history import _compute_session_totals, _compute_workbook_totals
from riviu.platforms.tiktok.lifecycle import _SharedBrowser, finish_pending_task, playwright_session
from riviu.platforms.tiktok.parsing import empty_metrics, extract_media_id
from riviu.platforms.tiktok.progress import (
    build_duplicate_link_payload,
    format_scrape_result_log,
    progress_payload,
    scrape_metric_details,
)
from riviu.platforms.tiktok.statuses import STATUS_MEDIA_REDIRECT_MISMATCH, is_hidden_stats_status
from riviu.platforms.tiktok.throttle import clamp_int, clamp_worker_count, configure_request_concurrency
from riviu.platforms.tiktok.workbook_io import (
    append_sheet_total_rows,
    build_sheet_contexts,
    collect_rows,
    normalize_selected_partners,
    write_result,
)
from riviu.platforms.tiktok.workers import hybrid_browser_worker_count, request_worker_loop, worker_loop


MAX_RETRIES = 5
MAX_BROWSER_STARTUPS = 6
RESULT_WAIT_SECONDS = 30
WORKER_JOIN_TIMEOUT_SECONDS = 20.0
STATUS_BROADCAST_INTERVAL_SECONDS = 0.25
DIRECT_MAX_WORKERS = 10
MAX_WORKERS_PER_PROXY = 10
# An autosave rewrites the whole workbook: about 10-25 s for a synced report with
# several month sheets (220,000 cells), during which no result is recorded. After
# a save that took S seconds the next autosave waits at least
# AUTOSAVE_PAUSE_PER_SAVE_SECOND * S seconds, so saving takes at most about a fifth
# of the run. A small workbook (S well under a second) still saves every
# save_every results; the final and the stop/cancel saves are never deferred.
AUTOSAVE_PAUSE_PER_SAVE_SECOND = 4
# Clock of the autosave pause; tests replace it (patching time.monotonic would
# also move asyncio's own clock).
_autosave_clock = time.monotonic


def _row_filter(skip_rows=None, only_rows=None):
    if skip_rows is None and only_rows is None:
        return None
    skip = set(skip_rows or ())
    only = set(only_rows) if only_rows is not None else None

    def keep(item):
        key = (item["sheet_name"], item["row"], item["url"])
        return key not in skip and (only is None or key in only)
    return keep


def selected_partner_label(partners):
    if not partners:
        return ""
    if len(partners) == 1:
        return partners[0]
    return f"{len(partners)} đối tác"


def guard_result_media_identity(result):
    if result.get("status") != "Success":
        return result

    raw_expected_ids = result.get("expected_media_ids") or []
    if isinstance(raw_expected_ids, str):
        raw_expected_ids = [raw_expected_ids]
    expected_ids = {clean_text(value) for value in raw_expected_ids if clean_text(value)}
    if not expected_ids:
        return result

    resolved_media_id = extract_media_id(result.get("resolved_url", ""))
    if len(expected_ids) == 1 and resolved_media_id in expected_ids:
        return result

    guarded = dict(result)
    guarded.update({
        "data": empty_metrics(),
        "channel_name": "",
        "status": STATUS_MEDIA_REDIRECT_MISMATCH,
        "resolved_url": "",
    })
    return guarded


def _existing_channel_value(sheet_contexts, item):
    if "channel" in item:
        # Store rows (Postgres) carry the channel read with the row.
        return clean_text(item["channel"])
    context = sheet_contexts.get(item["sheet_name"])
    column_index = context["columns"].get("channel") if context else None
    if not column_index:
        return ""
    return clean_text(context["worksheet"].cell(row=item["row"], column=column_index).value)


def build_url_buckets(rows_to_process, sheet_contexts):
    """Dedup URL: nhiều dòng cùng URL chỉ scrape 1 lần, ghi kết quả về tất cả các dòng."""
    unique_buckets = {}
    bucket_order = []
    for item in rows_to_process:
        key = item["url"].strip().casefold()
        bucket = unique_buckets.get(key)
        if bucket is None:
            bucket = {
                "sequence": len(bucket_order) + 1,
                "url": item["url"],
                "rows": [],
                "expected_media_ids": [],
                # Best channel already in the sheet: lets workers skip a useless profile fetch.
                "existing_channel": "",
            }
            unique_buckets[key] = bucket
            bucket_order.append(bucket)
        bucket["rows"].append({
            "sheet_name": item["sheet_name"],
            "row": item["row"],
            "url": item["url"],
            "partners": item["partners"],
        })
        expected_media_id = clean_text(item.get("expected_media_id", ""))
        if expected_media_id and expected_media_id not in bucket["expected_media_ids"]:
            bucket["expected_media_ids"].append(expected_media_id)
        existing = _existing_channel_value(sheet_contexts, item)
        if channels.channel_name_quality(existing, item["url"]) > channels.channel_name_quality(bucket["existing_channel"], item["url"]):
            bucket["existing_channel"] = existing
    return bucket_order


class _TikTokScan:
    """Workbook, counters and caches of one run_scraper call."""

    store_session = None
    autosave_label = "workbook"
    save_failure_message = "Lưu file Excel khi dừng quét thất bại; kết quả mới chưa được lưu."

    def __init__(self, file_path, websocket_manager, *, sheet_name, selected_names, partner_label, save_every,
                 row_filter=None, on_rows_saved=None):
        self.file_path = file_path
        self.manager = websocket_manager
        self.selected_names = selected_names
        self.partner_label = partner_label
        self.mode = "partner" if selected_names else "full"
        self.workbook = openpyxl.load_workbook(file_path)
        cleared_total_sheets = workbook_io.clear_existing_total_rows(self.workbook, sheet_name=sheet_name)
        self.sheet_contexts = build_sheet_contexts(self.workbook, sheet_name=sheet_name)
        self.rows_to_process = collect_rows(self.workbook, selected_partners=selected_names, sheet_name=sheet_name)
        self._apply_row_filter(row_filter, on_rows_saved)
        # Put TỔNG rows back before any save (autosave, cancel cleanup, final) so no
        # saved file ever lacks them. They sit below every occupied row and their
        # formulas reference cells, so writing results later keeps them correct.
        processed_sheets = (item["sheet_name"] for item in self.rows_to_process)
        for total_sheet in dict.fromkeys([*cleared_total_sheets, *processed_sheets]):
            append_sheet_total_rows(self.workbook, sheet_name=total_sheet)
        self.bucket_order = build_url_buckets(self.rows_to_process, self.sheet_contexts)
        self.total = len(self.bucket_order)
        self.total_rows = len(self.rows_to_process)
        self.scan_sheet = clean_text(sheet_name) or (
            clean_text(self.rows_to_process[0].get("sheet_name", "")) if self.rows_to_process else ""
        )
        # Adaptive save_every: file lớn save thưa hơn để giảm I/O
        self.save_every = max(50, min(100, self.total // 30)) if self.total > 500 else save_every
        self.channel_cache = {}
        self.channel_overrides = load_channel_overrides(file_path)
        self.profile_lookup_attempted = set()
        self.cache_lock = threading.Lock()
        channels.seed_channel_cache_from_workbook(self.rows_to_process, self.sheet_contexts, self.channel_cache)
        self.started_at = time.perf_counter()
        self.active_worker_count = 0
        self.processed = 0
        self.success_count = 0
        self.error_count = 0
        self.hidden_count = 0
        self.pending_save_count = 0
        self.save_skip_until_processed = 0
        self.next_autosave_at = None
        self.last_status_broadcast = 0.0
        self.completed_sequences = set()

    def _apply_row_filter(self, row_filter, on_rows_saved):
        if row_filter is not None:
            # A resumed run or a retry pass: only the rows the scan service names.
            self.rows_to_process = [item for item in self.rows_to_process if row_filter(item)]
            for sequence, item in enumerate(self.rows_to_process, start=1):
                item["sequence"] = sequence
        self.filtered = row_filter is not None
        # Rows written since the last successful save, reported to on_rows_saved after it.
        self.on_rows_saved = on_rows_saved
        self.unsaved_rows = []

    def note_unsaved(self, bucket_rows, url, status):
        for target in bucket_rows:
            self.unsaved_rows.append({
                "sheet_name": target["sheet_name"], "row": target["row"], "url": target.get("url", url), "status": status,
            })

    def channel_kwargs(self):
        return {
            "channel_cache": self.channel_cache,
            "channel_overrides": self.channel_overrides,
            "cache_lock": self.cache_lock,
        }

    def progress(self, *, workers=None, done=False, phase="scanning", uses_browser=False):
        return progress_payload(
            self.total, self.processed, self.success_count, self.error_count,
            self.active_worker_count if workers is None else workers, self.started_at,
            done=done, mode=self.mode, partner=self.partner_label, phase=phase,
            uses_browser=uses_browser, hidden_count=self.hidden_count,
        )

    def write_rows(self, bucket_rows, url, data, channel_name, status, resolved_url=""):
        self.note_unsaved(bucket_rows, url, status)
        for target in bucket_rows:
            write_result(
                self.sheet_contexts,
                {"sheet_name": target["sheet_name"], "row": target["row"], "url": url},
                data,
                channel_name,
                status,
                resolved_url=resolved_url,
                **self.channel_kwargs(),
            )
        self.pending_save_count += max(len(bucket_rows), 1)

    def record_result(self, result):
        """Count one worker result and write it to every row sharing its URL (no network I/O)."""
        result = guard_result_media_identity(result)
        self.processed += 1
        status = result["status"]
        self.completed_sequences.add(result.get("sequence"))
        resolved_url = clean_text(result.get("resolved_url", ""))
        result["resolved_url"] = resolved_url
        result["channel_name"] = channels.enrich_channel_name(
            result["url"],
            result.get("channel_name", ""),
            resolved_url=resolved_url if status == "Success" else "",
            existing_channel=result.get("existing_channel", ""),
            allow_network=False,
            **self.channel_kwargs(),
        )
        if status == "Success":
            self.success_count += 1
        elif is_hidden_stats_status(status):
            self.hidden_count += 1
        else:
            self.error_count += 1
        self.write_rows(result.get("rows") or [], result["url"], result["data"], result["channel_name"], status, resolved_url)
        return result

    async def rows_saved(self):
        """The workbook was just saved: report the rows it now holds for this run."""
        rows, self.unsaved_rows = self.unsaved_rows, []
        if rows and self.on_rows_saved is not None:
            await self.on_rows_saved(rows)

    async def abandon_remaining(self):
        remaining = [bucket for bucket in self.bucket_order if bucket["sequence"] not in self.completed_sequences]
        if self.manager:
            await self.manager.broadcast_log(f"Tất cả worker đã dừng, hủy {len(remaining)} link còn lại.")
        for bucket in remaining:
            self.error_count += 1
            self.processed += 1
            self.completed_sequences.add(bucket["sequence"])
            self.write_rows(bucket.get("rows") or [], bucket["url"], empty_metrics(), "", "Error: Worker đã dừng")

    async def broadcast_result(self, result):
        if not self.manager:
            return
        status = result["status"]
        data = result["data"] or {}
        resolved_url = result["resolved_url"]
        log_message, log_level, log_details = format_scrape_result_log(result, self.processed, self.total)
        await self.manager.broadcast_log(log_message, level=log_level, details=log_details)
        bucket_rows = result.get("rows") or []
        primary_target = bucket_rows[0] if bucket_rows else {"sheet_name": ""}
        await self.manager.broadcast_data({
            "id": result["sequence"],
            "url": result["url"],
            **scrape_metric_details(data),
            "status": status,
            "worker": result["worker"],
            "channelName": channels.channel_name_for_sheet(
                result["url"],
                result["channel_name"],
                resolved_url=resolved_url if status == "Success" else "",
                status=status,
                **self.channel_kwargs(),
            ),
            "sheetName": primary_target.get("sheet_name", ""),
            # Chỉ tô cam khi dòng chính (primary) đúng 1 đối tác — khớp Excel/preview.
            "singlePartner": len(primary_target.get("partners") or []) == 1,
            "videoLink": should_highlight_video_link(
                result["url"],
                resolved_url=resolved_url,
                likes=data.get("Likes"),
                shares=data.get("Shares"),
                metrics_readable=status == "Success",
            ),
        })
        now = time.perf_counter()
        if self.processed == self.total or now - self.last_status_broadcast > STATUS_BROADCAST_INTERVAL_SECONDS:
            await self.manager.broadcast_status(self.progress())
            self.last_status_broadcast = now

    async def save_now(self):
        """Persist the results written so far: (saved, reason)."""
        return await workbook_io.save_workbook(self.workbook, self.file_path, self.manager)

    def close(self):
        self.workbook.close()

    async def autosave_if_due(self):
        if self.pending_save_count < self.save_every or self.save_skip_until_processed > self.processed:
            return
        if self.processed >= self.total:
            # The final save follows at once; a second full write here only doubles the wait.
            return
        if self.next_autosave_at is not None and _autosave_clock() < self.next_autosave_at:
            return
        save_started = _autosave_clock()
        saved, _reason = await self.save_now()
        save_finished = _autosave_clock()
        self.next_autosave_at = save_finished + AUTOSAVE_PAUSE_PER_SAVE_SECOND * (save_finished - save_started)
        if saved:
            self.pending_save_count = 0
            await self.rows_saved()
            if self.manager:
                await self.manager.broadcast_log(f"Đã lưu tạm {self.autosave_label} tại {self.processed}/{self.total} links.")
        else:
            # Keep the dirty count for cancellation/final-save retries.
            self.save_skip_until_processed = self.processed + self.save_every


class _StoreTikTokScan(_TikTokScan):
    """A scan of rows from a storage session (Postgres) instead of a workbook.

    Same counters, URL dedup, channel rules and worker plumbing; writes go to
    `session.write_tiktok_result` (buffered) and saves to `session.flush()`.
    """

    autosave_label = "kết quả vào Postgres"
    save_failure_message = "Ghi kết quả vào Postgres khi dừng quét thất bại; kết quả mới chưa được lưu."

    def __init__(self, session, websocket_manager, *, selected_names, partner_label, save_every,  # noqa: PLW0231
                 row_filter=None, on_rows_saved=None):
        self.store_session = session
        self.file_path = session.label
        self.manager = websocket_manager
        self.selected_names = selected_names
        self.partner_label = partner_label
        self.mode = "partner" if selected_names else "full"
        self.workbook = None
        self.sheet_contexts = {}
        self.rows_to_process = list(session.rows)
        self._apply_row_filter(row_filter, on_rows_saved)
        self.bucket_order = build_url_buckets(self.rows_to_process, self.sheet_contexts)
        self.total = len(self.bucket_order)
        self.total_rows = len(self.rows_to_process)
        self.scan_sheet = session.label
        self.save_every = max(50, min(100, self.total // 30)) if self.total > 500 else save_every
        self.channel_cache = {}
        self.channel_overrides = dict(session.channel_overrides)
        self.profile_lookup_attempted = set()
        self.cache_lock = threading.Lock()
        channels.seed_channel_cache_from_rows(self.rows_to_process, self.channel_cache)
        self.started_at = time.perf_counter()
        self.active_worker_count = 0
        self.processed = 0
        self.success_count = 0
        self.error_count = 0
        self.hidden_count = 0
        self.pending_save_count = 0
        self.save_skip_until_processed = 0
        self.next_autosave_at = None
        self.last_status_broadcast = 0.0
        self.completed_sequences = set()

    def write_rows(self, bucket_rows, url, data, channel_name, status, resolved_url=""):
        self.note_unsaved(bucket_rows, url, status)
        for target in bucket_rows:
            self.store_session.write_tiktok_result(
                target["row"], url, data, channel_name, status, resolved_url=resolved_url, **self.channel_kwargs(),
            )
        self.pending_save_count += max(len(bucket_rows), 1)

    async def save_now(self):
        return await self.store_session.flush()

    def close(self):
        """The session belongs to its caller, which closes it."""


async def _announce_scan_plan(scan, *, worker_count, retries, use_request, browser_fallback, use_proxy, proxy_configs):
    manager = scan.manager
    total = scan.total
    save_every = scan.save_every
    sheet_part = f'Sheet "{scan.scan_sheet}" • ' if scan.scan_sheet else ""
    await manager.broadcast_log(f"{sheet_part}{scan.total_rows} dòng • {total} URL sau dedup sẽ quét.")
    duplicate_count = scan.total_rows - total
    if duplicate_count > 0:
        await manager.broadcast_log(f"Tiết kiệm {duplicate_count} lượt nhờ gộp URL trùng.")
    if scan.selected_names:
        await manager.broadcast_log(
            f"Bắt đầu cập nhật {scan.partner_label}: {total} URL, {worker_count} luồng, retry {retries} lần."
        )
    elif not use_request:
        await manager.broadcast_log(
            f"Bắt đầu quét {total} URL bằng {worker_count} luồng trình duyệt, retry {retries} lần, lưu mỗi {save_every} kết quả."
        )
    elif browser_fallback:
        fallback_count = hybrid_browser_worker_count(worker_count, total)
        await manager.broadcast_log(
            f"Bắt đầu quét hybrid {total} URL: {worker_count} luồng Request + {fallback_count} luồng trình duyệt fallback, retry {retries} lần, lưu mỗi {save_every} kết quả."
        )
    else:
        proxy_note = ""
        per_proxy = (worker_count / len(proxy_configs)) if proxy_configs else 0
        if proxy_configs:
            if len(proxy_configs) == 1:
                proxy_note = f" • proxy {proxy_label(proxy_configs[0])}"
            else:
                proxy_note = f" • {len(proxy_configs)} proxy, chia đều ~{per_proxy:.1f} luồng/proxy"
        elif use_proxy:
            proxy_note = " • proxy: chưa cấu hình"
        if not proxy_configs and worker_count > DIRECT_MAX_WORKERS:
            await manager.broadcast_log(
                f"Lưu ý: Không dùng proxy nhưng chạy {worker_count} luồng — tất cả dùng chung 1 IP máy, "
                f"dễ bị TikTok chặn (khuyến nghị ≤{DIRECT_MAX_WORKERS} luồng khi không có proxy)."
            )
        elif proxy_configs and per_proxy > MAX_WORKERS_PER_PROXY:
            await manager.broadcast_log(
                f"Lưu ý: {len(proxy_configs)} proxy nhưng {worker_count} luồng — mỗi proxy đang gánh trung bình "
                f"~{per_proxy:.1f} luồng (khuyến nghị ≤{MAX_WORKERS_PER_PROXY}/proxy). Vẫn chạy đủ {worker_count} luồng; "
                f"nếu thấy nhiều lỗi HTTP 403, nên thêm proxy hoặc giảm luồng."
            )
        elif total > 500 and worker_count < 25:
            await manager.broadcast_log(
                f"Gợi ý: Sheet lớn + proxy — thử 25–30 luồng để quét nhanh hơn (hiện {worker_count} luồng)."
            )
        await manager.broadcast_log(
            f"Bắt đầu quét {total} URL bằng Request (HTTP), {worker_count} luồng, retry {retries} lần, lưu mỗi {save_every} kết quả{proxy_note}."
        )
    await manager.broadcast_status(
        scan.progress(workers=worker_count, phase="starting", uses_browser=(not use_request) or browser_fallback)
    )


def _start_scan_workers(
    scan,
    *,
    browser,
    work_queue,
    browser_queue,
    result_queue,
    retries,
    use_request,
    browser_fallback,
    request_worker_count,
    browser_worker_count,
    proxy_configs,
    executor,
):
    shared = {
        **scan.channel_kwargs(),
        "profile_lookup_attempted": scan.profile_lookup_attempted,
        "executor": executor,
    }
    workers = [
        asyncio.create_task(request_worker_loop(
            index + 1,
            work_queue,
            browser_queue,
            result_queue,
            retries,
            websocket_manager=scan.manager,
            browser_fallback=browser_fallback,
            **shared,
        ))
        for index in range(request_worker_count)
    ]
    startup_semaphore = asyncio.Semaphore(min(browser_worker_count, MAX_BROWSER_STARTUPS)) if browser_worker_count else None
    workers.extend(
        asyncio.create_task(worker_loop(
            index + 1,
            browser,
            browser_queue if use_request else work_queue,
            result_queue,
            retries,
            startup_semaphore=startup_semaphore,
            websocket_manager=scan.manager,
            worker_label=f"B{index + 1}" if use_request else None,
            proxy_configs=proxy_configs,
            open_on_first_link=use_request,
            **shared,
        ))
        for index in range(browser_worker_count)
    )
    return workers


async def _finalize_hybrid_workers(workers, request_worker_count, browser_worker_count, browser_queue):
    """Once request workers drain, release the browser fallback workers."""
    await asyncio.gather(*workers[:request_worker_count], return_exceptions=True)
    for _ in range(browser_worker_count):
        await browser_queue.put(None)
    browser_tasks = workers[request_worker_count:]
    if browser_tasks:
        await asyncio.gather(*browser_tasks, return_exceptions=True)


async def _consume_scan_results(scan, result_queue, workers):
    stalled_seconds = 0
    while scan.processed < scan.total:
        try:
            result = await asyncio.wait_for(result_queue.get(), timeout=RESULT_WAIT_SECONDS)
        except asyncio.TimeoutError:
            stalled_seconds += RESULT_WAIT_SECONDS
            alive_workers = sum(1 for task in workers if not task.done())
            if alive_workers == 0:
                await scan.abandon_remaining()
                break
            if scan.manager and stalled_seconds % 60 == 0:
                await scan.manager.broadcast_log(
                    f"Đang chờ kết quả... {alive_workers}/{scan.active_worker_count} luồng còn sống ({scan.processed}/{scan.total})."
                )
            continue
        stalled_seconds = 0
        result = scan.record_result(result)
        await scan.broadcast_result(result)
        await scan.autosave_if_due()
        result_queue.task_done()


async def save_pending_results(scan):
    """Write results not yet on disk (a stopped run gets no final save)."""
    if not scan.pending_save_count:
        return
    saved, _reason = await scan.save_now()
    if not saved:
        raise RuntimeError(scan.save_failure_message)
    scan.pending_save_count = 0
    await scan.rows_saved()


async def _shutdown_scan(scan, workers, finalize_task, browser, executor, *, save_pending):
    """Stop workers, then release browser, threads and proxies.

    save_pending: persist unsaved results here (interrupted runs). A completed
    run leaves them to _finish_scan, so the workbook is written once.
    """
    if finalize_task is not None and not finalize_task.done():
        finalize_task.cancel()
    for task in workers:
        if not task.done():
            task.cancel()
    # Workers shield and join their executor work before exiting. Never replace
    # the shared proxy pool while that HTTP work is alive.
    await asyncio.gather(*workers, *([finalize_task] if finalize_task is not None else []), return_exceptions=True)
    try:
        if save_pending:
            await save_pending_results(scan)
    finally:
        # File errors must not skip browser/thread/proxy cleanup.
        await browser.close()
        executor.shutdown(wait=False, cancel_futures=True)
        set_session_proxies([])
        cookies.deactivate()
        if browser.launched:
            # Allow the Playwright Node transport to drain.
            await asyncio.sleep(0.3)


def _prepare_final_workbook(scan, *, create_result_sheet, summary_update_time):
    """Result tab, summary and highlights. Blocking workbook work: run it off the event loop.

    Returns the log lines to broadcast afterwards.
    """
    notes = []
    workbook = scan.workbook
    if create_result_sheet:
        try:
            created_sheet_name = workbook_io.build_result_sheet(workbook, scan.rows_to_process, summary_update_time)
            if created_sheet_name:
                notes.append(f"Đã tạo sheet kết quả mới: {created_sheet_name}.")
        except Exception as error:
            notes.append(f"CẢNH BÁO: Không tạo được sheet kết quả ({str(error)})")
    try:
        summary_count = rebuild_summary_sheet(
            workbook,
            summary_update_time=summary_update_time,
            selected_partners=scan.selected_names,
            data_sheet_name=scan.scan_sheet,
        )
        if scan.scan_sheet:
            summary_title = summary_sheet_title_for_data_sheet(scan.scan_sheet)
            notes.append(f"Đã cập nhật {summary_title} ({summary_count} đối tác).")
    except Exception as error:
        notes.append(f"CẢNH BÁO: Không cập nhật được sheet Tổng kết ({str(error)})")

    try:
        if scan.scan_sheet:
            highlighted_count = highlight_single_partner_link_rows(workbook, scan.scan_sheet)
            if highlighted_count:
                notes.append(f"Đã bôi cam {highlighted_count} dòng link 1 đối tác chưa đủ điều kiện xanh.")
            video_highlighted = highlight_video_link_rows(workbook, scan.scan_sheet)
            if video_highlighted:
                notes.append(f"Đã bôi xanh {video_highlighted} dòng video có hoạt động.")
    except Exception as error:
        notes.append(f"CẢNH BÁO: Không bôi cam được dòng link 1 đối tác ({str(error)})")
    return notes


def _record_scan_history(scan, *, file_label, base_dir):
    """Close the saved workbook and append the run to the scrape history (blocking)."""
    workbook = scan.workbook
    history_entry = {
        "timestamp": format_display_datetime(),
        "fileLabel": file_label or os.path.basename(scan.file_path),
        "scanSheet": scan.scan_sheet,
        "scrapedUrls": scan.total,
        "scrapedRows": scan.total_rows,
        "success": scan.success_count,
        "error": scan.error_count,
        "hidden": scan.hidden_count,
        "workers": scan.active_worker_count,
        "durationSeconds": max(int(time.perf_counter() - scan.started_at), 0),
        "sheetTotalLinks": _compute_workbook_totals(workbook, sheet_name=scan.scan_sheet or None)["totalLinks"],
        **_compute_session_totals(workbook, scan.rows_to_process),
    }
    workbook.close()
    history.append_scrape_history(base_dir or os.path.dirname(os.path.abspath(scan.file_path)), history_entry)


def scan_totals(scan):
    return {
        "scrapedUrls": scan.total,
        "scrapedRows": scan.total_rows,
        "processed": scan.processed,
        "success": scan.success_count,
        "error": scan.error_count,
        "hidden": scan.hidden_count,
        "workers": scan.active_worker_count,
        "durationSeconds": max(int(time.perf_counter() - scan.started_at), 0),
    }


async def _finish_store_scan(scan):
    """Final flush and totals of a store scan: no result tab, summary or highlights (views do that)."""
    manager = scan.manager
    saved, _reason = await scan.save_now()
    if not saved:
        raise RuntimeError("Ghi kết quả vào Postgres thất bại.")
    scan.pending_save_count = 0
    await scan.rows_saved()
    totals = scan_totals(scan)
    scan.store_session.totals = totals
    if not manager:
        return
    await manager.broadcast_status(scan.progress(done=True, phase="done"))
    skipped = len(scan.store_session.skipped)
    skipped_note = f", bỏ qua {skipped} dòng đã bị sửa trong lúc quét" if skipped else ""
    await manager.broadcast_log(
        f"HOÀN THÀNH: Đã quét {scan.processed}/{scan.total} URL ({scan.total_rows} dòng) tháng {scan.scan_sheet}, "
        f"thành công {scan.success_count}, ẩn số liệu {scan.hidden_count}, lỗi {scan.error_count}{skipped_note}, "
        f"thời lượng {totals['durationSeconds']}s.",
        level="OK",
    )


async def _finish_scan(scan, *, create_result_sheet, file_label, base_dir):
    """Result tab, summary, highlights, final save, history and the completion messages.

    Workers are stopped by now, so the worker thread below is the workbook's
    only user; finish_pending_task makes a cancellation wait for it.
    """
    if scan.store_session is not None:
        await _finish_store_scan(scan)
        return
    manager = scan.manager
    workbook = scan.workbook
    prepare = asyncio.create_task(asyncio.to_thread(
        _prepare_final_workbook,
        scan,
        create_result_sheet=create_result_sheet,
        summary_update_time=format_display_datetime(),
    ))
    try:
        notes = await finish_pending_task(prepare)
    except asyncio.CancelledError:
        # Stopped while the summary was rebuilt: the scanned results still reach the file.
        try:
            await save_pending_results(scan)
        finally:
            workbook.close()
        raise
    if manager:
        for note in notes:
            await manager.broadcast_log(note)

    final_saved, final_save_reason = await workbook_io.save_workbook(workbook, scan.file_path, manager)
    if not final_saved:
        workbook.close()
        if final_save_reason == "permission":
            raise RuntimeError("Lưu file Excel thất bại vì file đang mở.")
        raise RuntimeError("Lưu file Excel thất bại.")
    scan.pending_save_count = 0
    await scan.rows_saved()
    await finish_pending_task(asyncio.create_task(asyncio.to_thread(
        _record_scan_history, scan, file_label=file_label, base_dir=base_dir,
    )))
    if not manager:
        return
    await manager.broadcast_status(scan.progress(done=True, phase="done"))
    duration_seconds = max(int(time.perf_counter() - scan.started_at), 0)
    summary_counts = f"thành công {scan.success_count}, ẩn số liệu {scan.hidden_count}, lỗi {scan.error_count}"
    if scan.selected_names:
        message = (
            f"HOÀN THÀNH: Đã cập nhật {scan.partner_label} với {scan.processed} URL ({scan.total_rows} dòng), "
            f"{summary_counts}, thời lượng {duration_seconds}s, file={os.path.basename(scan.file_path)}."
        )
    else:
        message = (
            f"HOÀN THÀNH: Đã quét {scan.processed}/{scan.total} URL ({scan.total_rows} dòng), "
            f"{summary_counts}, thời lượng {duration_seconds}s, file={os.path.basename(scan.file_path)}."
        )
    await manager.broadcast_log(message, level="OK")


async def run_scraper(file_path, websocket_manager=None, worker_count=DEFAULT_WORKERS, retries=DEFAULT_RETRIES, save_every=DEFAULT_SAVE_EVERY, selected_partner=None, selected_partners=None, create_result_sheet=False, base_dir=None, file_label="", sheet_name=None, use_request=True, browser_fallback=False, use_proxy=False, proxy_text="", session_cookies=None, skip_rows=None, only_rows=None, on_rows_saved=None, store_session=None):
    """session_cookies: the saved "Cookie TikTok" session for this run only (None: anonymous, as before).

    skip_rows / only_rows: sets of `(sheet_name, row, url)` keys a resumed run skips or a retry
    pass is limited to (riviu/scan_journal.py). on_rows_saved(rows) is awaited after each
    successful save with the rows (`sheet_name`, `row`, `url`, `status`) that save wrote.

    store_session: scan the rows of a storage session (riviu.store, Postgres) instead of the
    workbook at file_path; file_path is then only a label and base_dir is required.
    """
    if store_session is not None:
        if not base_dir:
            raise ValueError("base_dir is required with store_session")
    elif not os.path.exists(file_path):
        if websocket_manager:
            await websocket_manager.broadcast_log(f"Lỗi: Không tìm thấy file {file_path}")
        return

    retries = clamp_int(retries, DEFAULT_RETRIES, 0, MAX_RETRIES)
    save_every = clamp_int(save_every, DEFAULT_SAVE_EVERY, 5, 100)
    selected_names = normalize_selected_partners(selected_partner, selected_partners)
    partner_label = selected_partner_label(selected_names)
    scrape_base_dir = base_dir or os.path.dirname(os.path.abspath(file_path))
    proxy_configs = resolve_proxy_configs(scrape_base_dir, proxy_text=proxy_text) if use_proxy else []
    proxy_configs = [config for config in proxy_configs if config and config.get("enabled", True)]
    if use_proxy and not proxy_configs:
        raise ValueError("Đã bật proxy nhưng không có proxy enabled hợp lệ; không quét direct.")
    worker_count = clamp_worker_count(worker_count)
    # Back-off/limiter state is module-level: reset it for every run, in every mode
    # (browser runs still use HTTP for channel lookups).
    configure_request_concurrency(worker_count)

    # Loading a large workbook and placing its TỔNG rows takes seconds; keep the
    # server responsive meanwhile. Nothing else holds this workbook yet, so a
    # cancellation may return at once and drop the thread's read-only result.
    row_filter = _row_filter(skip_rows, only_rows)
    if store_session is not None:
        scan = _StoreTikTokScan(
            store_session,
            websocket_manager,
            selected_names=selected_names,
            partner_label=partner_label,
            save_every=save_every,
            row_filter=row_filter,
            on_rows_saved=on_rows_saved,
        )
    else:
        scan = await asyncio.to_thread(
            _TikTokScan,
            file_path,
            websocket_manager,
            sheet_name=sheet_name,
            selected_names=selected_names,
            partner_label=partner_label,
            save_every=save_every,
            row_filter=row_filter,
            on_rows_saved=on_rows_saved,
        )
    if websocket_manager:
        broadcast_duplicates = getattr(websocket_manager, "broadcast_duplicates", None)
        if broadcast_duplicates:
            await broadcast_duplicates(build_duplicate_link_payload(scan.bucket_order))

    if scan.total == 0:
        if websocket_manager:
            await websocket_manager.broadcast_status(
                progress_payload(0, 0, 0, 0, worker_count, scan.started_at, done=True, mode=scan.mode, partner=partner_label, phase="done")
            )
            message = "Không tìm thấy link TikTok nào phù hợp để quét."
            if scan.filtered:
                message = "Không còn link TikTok nào cần quét trong lượt này."
            elif selected_names:
                message = f"Không tìm thấy link nào cho {partner_label}."
            await websocket_manager.broadcast_log(message)
        scan.close()
        return

    worker_count = min(worker_count, scan.total)
    request_worker_count = worker_count if use_request else 0
    if not use_request:
        browser_worker_count = worker_count
    elif browser_fallback:
        browser_worker_count = hybrid_browser_worker_count(worker_count, scan.total)
    else:
        browser_worker_count = 0
    scan.active_worker_count = request_worker_count + browser_worker_count
    if websocket_manager:
        await _announce_scan_plan(
            scan,
            worker_count=worker_count,
            retries=retries,
            use_request=use_request,
            browser_fallback=browser_fallback,
            use_proxy=use_proxy,
            proxy_configs=proxy_configs,
        )

    work_queue = asyncio.Queue()
    browser_queue = asyncio.Queue()
    result_queue = asyncio.Queue()
    for bucket in scan.bucket_order:
        await work_queue.put(bucket)
    for _ in range(request_worker_count if use_request else browser_worker_count):
        await work_queue.put(None)

    # True once every link has a result: the final save in _finish_scan then
    # writes them, so shutdown does not save the same workbook first.
    scan_complete = False
    try:
        async with playwright_session(async_playwright) as playwright:
            # One pool per run, sized to its workers: request scrapes and profile
            # lookups never queue behind other runs or behind workbook saves.
            executor = ThreadPoolExecutor(max_workers=max(scan.active_worker_count, 1), thread_name_prefix="riviu-tiktok")
            browser = _SharedBrowser(
                playwright,
                websocket_manager,
                launch_notice="Khởi động trình duyệt cho link cần fallback..." if use_request else "",
            )
            workers = []
            finalize_task = None
            try:
                set_session_proxies(proxy_configs if use_proxy else [])
                # Always set (possibly to nothing), so a run never inherits another run's cookies.
                cookies.activate(session_cookies or ())
                if websocket_manager and session_cookies:
                    await websocket_manager.broadcast_log(
                        "Đang dùng cookie TikTok đã lưu cho phiên quét này (khuyên dùng tài khoản phụ)."
                    )
                if websocket_manager:
                    if use_request and browser_fallback:
                        startup_message = f"Đang khởi tạo {request_worker_count} luồng Request và {browser_worker_count} luồng trình duyệt fallback..."
                    elif use_request:
                        startup_message = f"Đang khởi tạo {request_worker_count} luồng Request..."
                    else:
                        startup_message = f"Đang khởi tạo trình duyệt và {browser_worker_count} luồng..."
                    await websocket_manager.broadcast_log(startup_message)
                if not use_request:
                    # Browser mode needs Chromium for every link: fail the run now if it cannot start.
                    await browser.get()
                workers = _start_scan_workers(
                    scan,
                    browser=browser,
                    work_queue=work_queue,
                    browser_queue=browser_queue,
                    result_queue=result_queue,
                    retries=retries,
                    use_request=use_request,
                    browser_fallback=browser_fallback,
                    request_worker_count=request_worker_count,
                    browser_worker_count=browser_worker_count,
                    proxy_configs=proxy_configs,
                    executor=executor,
                )
                if websocket_manager:
                    await websocket_manager.broadcast_status(scan.progress())
                if use_request and browser_fallback and browser_worker_count > 0:
                    finalize_task = asyncio.create_task(
                        _finalize_hybrid_workers(workers, request_worker_count, browser_worker_count, browser_queue)
                    )

                await _consume_scan_results(scan, result_queue, workers)

                if finalize_task is not None:
                    await finalize_task
                else:
                    try:
                        await asyncio.wait_for(asyncio.gather(*workers, return_exceptions=True), timeout=WORKER_JOIN_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError:
                        pass
                scan_complete = True
            finally:
                await finish_pending_task(asyncio.create_task(
                    _shutdown_scan(scan, workers, finalize_task, browser, executor, save_pending=not scan_complete)
                ))
    except BaseException:
        try:
            if scan_complete:
                # Stopped during cleanup, after the last result: the final save will
                # not run, so keep the results now.
                await finish_pending_task(asyncio.create_task(save_pending_results(scan)))
        finally:
            scan.close()
        raise

    await _finish_scan(scan, create_result_sheet=create_result_sheet, file_label=file_label, base_dir=base_dir)
