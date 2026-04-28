import asyncio
import os
import random
import re
import time
import unicodedata

import openpyxl
from playwright.async_api import async_playwright


USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPad; CPU OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
]

METRIC_HEADERS = {
    "views": "LƯỢT XEM",
    "likes": "TIM",
    "comments": "BÌNH LUẬN",
    "saves": "LƯỢT LƯU",
    "shares": "CHIA SẺ",
}

METRIC_KEYS = {
    "views": "Views",
    "likes": "Likes",
    "comments": "Comments",
    "saves": "Saves",
    "shares": "Shares",
}

FALLBACK_COLUMNS = {
    "views": 4,
    "likes": 5,
    "comments": 6,
    "saves": 7,
    "shares": 8,
}

COUNT_PATTERNS = {
    "Views": r'"playCount"\s*:\s*"?(\d+)"?',
    "Likes": r'"diggCount"\s*:\s*"?(\d+)"?',
    "Comments": r'"commentCount"\s*:\s*"?(\d+)"?',
    "Saves": r'"collectCount"\s*:\s*"?(\d+)"?',
    "Shares": r'"shareCount"\s*:\s*"?(\d+)"?',
}

MAX_WORKERS = 50
DEFAULT_WORKERS = 5
DEFAULT_RETRIES = 2
DEFAULT_SAVE_EVERY = 25


def normalize_text(value):
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text)


def clamp_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def find_columns(sheet):
    column_map = {key: None for key in METRIC_HEADERS}
    column_map["url"] = None

    for cell in sheet[1]:
        header = normalize_text(cell.value)
        if not header:
            continue
        if "URL" in header or "LINK" in header:
            column_map["url"] = cell.column
        for key, expected_header in METRIC_HEADERS.items():
            if normalize_text(expected_header) in header:
                column_map[key] = cell.column

    for key, fallback_col in FALLBACK_COLUMNS.items():
        if not column_map[key]:
            column_map[key] = fallback_col

    if not column_map["url"]:
        for row in range(2, min(sheet.max_row, 25) + 1):
            for col in range(1, sheet.max_column + 1):
                value = str(sheet.cell(row=row, column=col).value or "")
                if "tiktok.com" in value:
                    column_map["url"] = col
                    break
            if column_map["url"]:
                break

    if not column_map["url"]:
        column_map["url"] = 2

    return column_map


def collect_rows(sheet, url_column):
    rows = []
    for row in range(2, sheet.max_row + 1):
        url_cell = sheet.cell(row=row, column=url_column).value
        url = str(url_cell).strip() if url_cell else ""
        if url and "tiktok.com" in url:
            rows.append({"sequence": len(rows) + 1, "row": row, "url": url})
    return rows


def parse_counts(content):
    data = {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"}
    found = False
    for key, pattern in COUNT_PATTERNS.items():
        match = re.search(pattern, content)
        if match:
            data[key] = match.group(1)
            found = True
    return data, found


async def block_heavy_resources(route):
    if route.request.resource_type in {"image", "media", "font"}:
        await route.abort()
    else:
        await route.continue_()


async def scrape_single_link(page, url, timeout_ms=45000):
    data = {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        content = ""
        found = False
        for _ in range(24):
            content = await page.content()
            data, found = parse_counts(content)
            if found:
                break
            await page.wait_for_timeout(500)

        if not found:
            return data, "Error: Không đọc được số liệu"

        return data, "Success"
    except Exception as e:
        return data, f"Error: {str(e)}"


async def scrape_with_retries(page, url, retries):
    last_data = {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"}
    last_status = "Error: Chưa chạy"

    for attempt in range(retries + 1):
        if attempt > 0:
            await asyncio.sleep(random.uniform(1.5, 3.5))
        data, status = await scrape_single_link(page, url)
        last_data, last_status = data, status
        if status == "Success":
            return data, status, attempt + 1

    return last_data, last_status, retries + 1


async def worker_loop(worker_id, browser, work_queue, result_queue, retries):
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 390, "height": 844},
    )
    await context.route("**/*", block_heavy_resources)
    page = await context.new_page()

    try:
        while True:
            item = await work_queue.get()
            if item is None:
                work_queue.task_done()
                break

            started_at = time.perf_counter()
            data, status, attempts = await scrape_with_retries(page, item["url"], retries)
            elapsed = time.perf_counter() - started_at
            await result_queue.put({
                **item,
                "worker": worker_id,
                "data": data,
                "status": status,
                "attempts": attempts,
                "elapsed": elapsed,
            })
            work_queue.task_done()
            await asyncio.sleep(random.uniform(0.3, 1.1))
    finally:
        await context.close()


def write_result(sheet, column_map, row_idx, data):
    for metric_key, data_key in METRIC_KEYS.items():
        value = int(data.get(data_key) or 0)
        sheet.cell(row=row_idx, column=column_map[metric_key]).value = value


async def save_workbook(wb, file_path, websocket_manager=None):
    try:
        await asyncio.to_thread(wb.save, file_path)
        return True
    except PermissionError:
        if websocket_manager:
            await websocket_manager.broadcast_log("CẢNH BÁO: File Excel đang mở, không thể ghi đè. Vui lòng đóng file rồi quét lại hoặc chờ lần lưu tiếp theo.")
    except Exception as e:
        if websocket_manager:
            await websocket_manager.broadcast_log(f"CẢNH BÁO: Lỗi lưu file ({str(e)})")
    return False


def progress_payload(total, processed, success_count, error_count, worker_count, started_at, done=False):
    elapsed = max(time.perf_counter() - started_at, 0.001)
    rate = processed / elapsed * 60 if processed else 0
    remaining = max(total - processed, 0)
    eta_seconds = int((remaining / rate) * 60) if rate > 0 else None
    return {
        "total": total,
        "processed": processed,
        "success": success_count,
        "error": error_count,
        "workers": worker_count,
        "rate": round(rate, 1),
        "etaSeconds": eta_seconds,
        "done": done,
    }


async def run_scraper(file_path, websocket_manager=None, worker_count=DEFAULT_WORKERS, retries=DEFAULT_RETRIES, save_every=DEFAULT_SAVE_EVERY):
    if not os.path.exists(file_path):
        if websocket_manager:
            await websocket_manager.broadcast_log(f"Lỗi: Không tìm thấy file {file_path}")
        return

    worker_count = clamp_int(worker_count, DEFAULT_WORKERS, 1, MAX_WORKERS)
    retries = clamp_int(retries, DEFAULT_RETRIES, 0, 5)
    save_every = clamp_int(save_every, DEFAULT_SAVE_EVERY, 5, 100)

    wb = openpyxl.load_workbook(file_path)
    sheet = wb.active
    column_map = find_columns(sheet)
    rows_to_process = collect_rows(sheet, column_map["url"])
    total = len(rows_to_process)
    started_at = time.perf_counter()

    if total == 0:
        if websocket_manager:
            await websocket_manager.broadcast_status(progress_payload(0, 0, 0, 0, worker_count, started_at, done=True))
            await websocket_manager.broadcast_log("Không tìm thấy link TikTok nào trong file đang chọn.")
        return

    worker_count = min(worker_count, total)
    if websocket_manager:
        await websocket_manager.broadcast_status(progress_payload(total, 0, 0, 0, worker_count, started_at))
        await websocket_manager.broadcast_log(
            f"Bắt đầu quét nhanh {total} links bằng {worker_count} luồng, retry {retries} lần, lưu mỗi {save_every} kết quả."
        )

    work_queue = asyncio.Queue()
    result_queue = asyncio.Queue()
    for item in rows_to_process:
        await work_queue.put(item)
    for _ in range(worker_count):
        await work_queue.put(None)

    processed = 0
    success_count = 0
    error_count = 0
    pending_save_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        workers = [
            asyncio.create_task(worker_loop(index + 1, browser, work_queue, result_queue, retries))
            for index in range(worker_count)
        ]

        try:
            while processed < total:
                result = await result_queue.get()
                processed += 1
                data = result["data"]
                status = result["status"]

                if status == "Success":
                    success_count += 1
                    write_result(sheet, column_map, result["row"], data)
                    pending_save_count += 1
                else:
                    error_count += 1

                if websocket_manager:
                    await websocket_manager.broadcast_data({
                        "id": result["sequence"],
                        "url": result["url"],
                        "views": data["Views"],
                        "likes": data["Likes"],
                        "comments": data["Comments"],
                        "saves": data["Saves"],
                        "shares": data["Shares"],
                        "status": status,
                        "worker": result["worker"],
                    })
                    await websocket_manager.broadcast_status(
                        progress_payload(total, processed, success_count, error_count, worker_count, started_at)
                    )

                if pending_save_count >= save_every:
                    saved = await save_workbook(wb, file_path, websocket_manager)
                    pending_save_count = 0 if saved else pending_save_count
                    if websocket_manager and saved:
                        await websocket_manager.broadcast_log(f"Đã lưu tạm Excel tại {processed}/{total} links.")

                result_queue.task_done()

            await work_queue.join()
            await asyncio.gather(*workers)
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            await browser.close()

    await save_workbook(wb, file_path, websocket_manager)
    if websocket_manager:
        await websocket_manager.broadcast_status(
            progress_payload(total, processed, success_count, error_count, worker_count, started_at, done=True)
        )
        await websocket_manager.broadcast_log(
            f"HOÀN THÀNH: Đã quét {processed}/{total} links, thành công {success_count}, lỗi {error_count}."
        )
