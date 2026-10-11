"""Worker loops: Request workers on the run's thread pool and Chromium workers (browser mode or fallback)."""
import asyncio
import random
import time
from functools import partial

from riviu.proxy_utils import assign_worker_proxy
from riviu.platforms.tiktok import channels, fetch_browser
from riviu.platforms.tiktok.constants import DEFAULT_RETRIES
from riviu.platforms.tiktok.fetch_request import scrape_link_with_retries_request
from riviu.platforms.tiktok.lifecycle import finish_pending_task
from riviu.platforms.tiktok.parsing import empty_metrics
from riviu.platforms.tiktok.statuses import should_clear_stale_metrics


MAX_BROWSER_FALLBACK_WORKERS = 15


def hybrid_browser_worker_count(request_workers, total_links):
    if total_links <= 0:
        return 0
    if total_links <= 40:
        return min(max(request_workers // 2, 3), 8)
    return min(max(request_workers // 3, 5), MAX_BROWSER_FALLBACK_WORKERS)


def select_fallback_result(request_result, browser_result):
    if browser_result.get("status") == "Success":
        return browser_result
    if should_clear_stale_metrics(request_result.get("status")) and not should_clear_stale_metrics(
        browser_result.get("status")
    ):
        return request_result
    return browser_result


async def worker_loop(
    worker_id,
    browser,
    scrape_queue,
    result_queue,
    retries,
    channel_cache=None,
    channel_overrides=None,
    profile_lookup_attempted=None,
    cache_lock=None,
    startup_semaphore=None,
    websocket_manager=None,
    worker_label=None,
    proxy_config=None,
    proxy_configs=None,
    executor=None,
    open_on_first_link=False,
):
    """Scrape queued links in Chromium; `browser` is the run's _SharedBrowser.

    With open_on_first_link (hybrid fallback) the context opens when the first
    link arrives, so a run whose Request results all stand never starts Chromium.
    """
    RECYCLE_AFTER = 100
    loop = asyncio.get_running_loop()
    display_worker = worker_label if worker_label is not None else worker_id
    browser_proxy_configs = proxy_configs if proxy_configs is not None else ([proxy_config] if proxy_config else [])

    async def make_context():
        return await fetch_browser.make_browser_context(await browser.get(), proxy_configs=browser_proxy_configs)

    async def open_first_context():
        if startup_semaphore is None:
            new_context = await make_context()
        else:
            async with startup_semaphore:
                await asyncio.sleep(min(worker_id - 1, 10) * 0.25)
                new_context = await make_context()
        try:
            return new_context, await new_context.new_page()
        except BaseException:
            await asyncio.gather(new_context.close(), return_exceptions=True)
            raise

    async def report_start_failure(error):
        if websocket_manager:
            await websocket_manager.broadcast_log(f"Worker {display_worker} không khởi tạo được context: {str(error)}")

    current_item = None
    context = None
    page = None
    browser_unavailable = False
    links_in_current_context = 0

    if not open_on_first_link:
        try:
            context, page = await open_first_context()
        except Exception as error:
            await report_start_failure(error)
            return

    try:
        while True:
            item = await scrape_queue.get()
            current_item = item
            if item is None:
                scrape_queue.task_done()
                current_item = None
                break

            if context is None and not browser_unavailable:
                try:
                    context, page = await open_first_context()
                except Exception as error:
                    browser_unavailable = True
                    await report_start_failure(error)
            if context is None:
                # No browser for the fallback: keep the Request verdict for this link.
                result_item = {key: value for key, value in item.items() if key != "_request_result"}
                await result_queue.put({
                    **result_item,
                    "worker": display_worker,
                    "data": empty_metrics(),
                    "channel_name": "",
                    "status": f"Error: Worker {display_worker} không mở được trình duyệt",
                    "attempts": 0,
                    "elapsed": 0.0,
                    "resolved_url": "",
                    **(item.get("_request_result") or {}),
                })
                scrape_queue.task_done()
                current_item = None
                continue

            started_at = time.perf_counter()
            try:
                data, channel_name, status, attempts, resolved_url = await fetch_browser.scrape_with_retries(page, item["url"], retries, channel_cache=channel_cache)
            except Exception as error:
                data = empty_metrics()
                channel_name = ""
                status = f"Error: Worker {display_worker} crash ({str(error)})"
                attempts = 0
                resolved_url = ""
                try:
                    if page.is_closed():
                        page = await context.new_page()
                except Exception:
                    pass
            elapsed = time.perf_counter() - started_at
            browser_result = {
                "data": data,
                "channel_name": channel_name,
                "status": status,
                "attempts": attempts,
                "resolved_url": resolved_url,
                "elapsed": elapsed,
                "worker": display_worker,
            }
            selected_result = select_fallback_result(item.get("_request_result") or {}, browser_result)
            data = selected_result.get("data", data)
            channel_name = selected_result.get("channel_name", channel_name)
            status = selected_result.get("status", status)
            attempts = selected_result.get("attempts", attempts)
            resolved_url = selected_result.get("resolved_url", resolved_url)
            elapsed = selected_result.get("elapsed", elapsed)
            selected_worker = selected_result.get("worker", display_worker)
            # Profile lookups are blocking HTTP: keep them off the event loop.
            channel_name = await finish_pending_task(loop.run_in_executor(executor, partial(
                channels.enrich_channel_name,
                item["url"],
                channel_name,
                resolved_url=resolved_url if status == "Success" else "",
                existing_channel=item.get("existing_channel", ""),
                channel_cache=channel_cache,
                channel_overrides=channel_overrides,
                profile_lookup_attempted=profile_lookup_attempted,
                cache_lock=cache_lock,
            )))
            result_item = {key: value for key, value in item.items() if key != "_request_result"}
            await result_queue.put({
                **result_item,
                "worker": selected_worker,
                "data": data,
                "channel_name": channel_name,
                "status": status,
                "attempts": attempts,
                "elapsed": elapsed,
                "resolved_url": resolved_url,
            })
            scrape_queue.task_done()
            current_item = None
            links_in_current_context += 1

            if links_in_current_context >= RECYCLE_AFTER:
                try:
                    await context.close()
                except Exception:
                    pass
                try:
                    context = await make_context()
                    page = await context.new_page()
                    links_in_current_context = 0
                except Exception as error:
                    if websocket_manager:
                        await websocket_manager.broadcast_log(f"Worker {display_worker} không khởi tạo lại được context: {str(error)}")
                    return

            await asyncio.sleep(random.uniform(0.3, 1.1))
    except Exception as error:
        if websocket_manager:
            await websocket_manager.broadcast_log(f"Worker {display_worker} dừng do lỗi: {str(error)}")
        if current_item is not None:
            await scrape_queue.put(current_item)
            scrape_queue.task_done()
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


def _run_request_scrape(
    worker_index,
    url,
    retries=DEFAULT_RETRIES,
    channel_cache=None,
    channel_overrides=None,
    profile_lookup_attempted=None,
    cache_lock=None,
    existing_channel="",
):
    # Gán proxy round-robin ngay trên thread thực thi hiện tại (thread pool của
    # asyncio có thể đổi thread giữa các item, nên phải gán lại mỗi lần, không
    # chỉ 1 lần khi worker khởi động).
    assign_worker_proxy(worker_index)
    return scrape_link_with_retries_request(
        url,
        retries=retries,
        channel_cache=channel_cache,
        channel_overrides=channel_overrides,
        profile_lookup_attempted=profile_lookup_attempted,
        cache_lock=cache_lock,
        existing_channel=existing_channel,
    )


async def request_worker_loop(
    worker_id,
    scrape_queue,
    browser_queue,
    result_queue,
    retries,
    websocket_manager=None,
    browser_fallback=True,
    channel_cache=None,
    channel_overrides=None,
    profile_lookup_attempted=None,
    cache_lock=None,
    executor=None,
):
    loop = asyncio.get_running_loop()
    worker_label = f"R{worker_id}"
    worker_index = worker_id - 1
    current_item = None

    try:
        while True:
            item = await scrape_queue.get()
            current_item = item
            if item is None:
                scrape_queue.task_done()
                current_item = None
                break

            started_at = time.perf_counter()
            try:
                request_future = loop.run_in_executor(executor, partial(
                    _run_request_scrape,
                    worker_index,
                    item["url"],
                    retries=retries,
                    channel_cache=channel_cache,
                    channel_overrides=channel_overrides,
                    profile_lookup_attempted=profile_lookup_attempted,
                    cache_lock=cache_lock,
                    existing_channel=item.get("existing_channel", ""),
                ))
                data, channel_name, status, attempts, resolved_url = await finish_pending_task(request_future)
            except Exception as error:
                # A failed HTTP future must not turn a pending cancellation into
                # an ordinary result and allow the worker to dequeue more URLs.
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError() from error
                data = empty_metrics()
                channel_name = ""
                status = f"Error: Request worker {worker_label} crash ({str(error)})"
                attempts = 0
                resolved_url = ""

            elapsed = time.perf_counter() - started_at
            # Confirmed unavailable/hidden pages are final; a browser retry cannot change them.
            if status == "Success" or not browser_fallback or should_clear_stale_metrics(status):
                await result_queue.put({
                    **item,
                    "worker": worker_label,
                    "data": data,
                    "channel_name": channel_name,
                    "status": status,
                    "attempts": attempts,
                    "elapsed": elapsed,
                    "resolved_url": resolved_url,
                })
            else:
                await browser_queue.put({
                    **item,
                    "_request_result": {
                        "worker": worker_label,
                        "data": data,
                        "channel_name": channel_name,
                        "status": status,
                        "attempts": attempts,
                        "elapsed": elapsed,
                        "resolved_url": resolved_url,
                    },
                })
            scrape_queue.task_done()
            current_item = None
    except Exception as error:
        if websocket_manager:
            await websocket_manager.broadcast_log(f"Request worker {worker_label} dừng do lỗi: {str(error)}")
        if current_item is not None:
            await browser_queue.put(current_item)
            scrape_queue.task_done()
