import asyncio
import os
import random
import re
import time
import unicodedata
from datetime import datetime

import openpyxl
from playwright.async_api import async_playwright

from workbook_utils import (
    clean_text,
    rebuild_summary_sheet,
    workbook_data_sheet_names,
    worksheet_partner_column_indexes,
    worksheet_row_partners,
)


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

LAST_UPDATE_HEADER = "Cập nhật lần cuối"

COUNT_PATTERNS = {
    "Views": r'"playCount"\s*:\s*"?(\d+)"?',
    "Likes": r'"diggCount"\s*:\s*"?(\d+)"?',
    "Comments": r'"commentCount"\s*:\s*"?(\d+)"?',
    "Saves": r'"collectCount"\s*:\s*"?(\d+)"?',
    "Shares": r'"shareCount"\s*:\s*"?(\d+)"?',
}

CHANNEL_PATTERNS = [
    r'"authorName"\s*:\s*"([^"]+)"',
    r'"uniqueId"\s*:\s*"([^"]+)"',
    r'"nickname"\s*:\s*"([^"]+)"',
]

MAX_WORKERS = 50
DEFAULT_WORKERS = 5
DEFAULT_RETRIES = 2
DEFAULT_SAVE_EVERY = 25


def normalize_selected_partners(selected_partner=None, selected_partners=None):
    values = []
    if isinstance(selected_partners, (list, tuple, set)):
        values.extend(selected_partners)
    elif selected_partners:
        values.append(selected_partners)
    if isinstance(selected_partner, (list, tuple, set)):
        values.extend(selected_partner)
    elif selected_partner:
        values.append(selected_partner)

    partners = []
    seen = set()
    for value in values:
        name = clean_text(value)
        key = name.casefold()
        if name and key not in seen:
            partners.append(name)
            seen.add(key)
    return partners


def selected_partner_label(partners):
    if not partners:
        return ""
    if len(partners) == 1:
        return partners[0]
    return f"{len(partners)} đối tác"


def clamp_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def normalize_text(value):
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text)


def build_sheet_contexts(workbook):
    contexts = {}
    for sheet_name in workbook_data_sheet_names(workbook):
        sheet = workbook[sheet_name]
        contexts[sheet_name] = {
            "worksheet": sheet,
            "columns": find_columns(sheet),
        }
    return contexts


def find_columns(sheet):
    column_map = {key: None for key in METRIC_HEADERS}
    column_map["url"] = None
    column_map["channel"] = None
    column_map["date"] = None
    column_map["last_update"] = None

    for cell in sheet[1]:
        header = normalize_text(cell.value)
        if not header:
            continue
        if "URL" in header or "LINK" in header:
            column_map["url"] = cell.column
        if "TÊN KÊNH" in header or "TEN KENH" in header:
            column_map["channel"] = cell.column
        if "NGÀY" in header or "NGAY" in header:
            column_map["date"] = cell.column
        if "CẬP NHẬT LẦN CUỐI" in header or "CAP NHAT LAN CUOI" in header:
            column_map["last_update"] = cell.column
        for key, expected_header in METRIC_HEADERS.items():
            if normalize_text(expected_header) in header:
                column_map[key] = cell.column

    max_column = sheet.max_column
    if not column_map["url"]:
        for row in range(2, min(sheet.max_row, 25) + 1):
            for col in range(1, max_column + 1):
                value = str(sheet.cell(row=row, column=col).value or "")
                if "tiktok.com" in value or "vt.tiktok.com" in value:
                    column_map["url"] = col
                    break
            if column_map["url"]:
                break

    for key, fallback in {"views": 5, "likes": 6, "comments": 7, "saves": 8, "shares": 9}.items():
        if not column_map[key] and max_column >= fallback and normalize_text(sheet.cell(row=1, column=fallback).value):
            column_map[key] = fallback

    if not column_map["channel"]:
        channel_col = max_column + 1
        sheet.cell(row=1, column=channel_col).value = "Tên Kênh"
        column_map["channel"] = channel_col
        max_column = channel_col

    for key, header in METRIC_HEADERS.items():
        if not column_map[key]:
            max_column += 1
            sheet.cell(row=1, column=max_column).value = header
            column_map[key] = max_column

    if not column_map["last_update"]:
        max_column += 1
        sheet.cell(row=1, column=max_column).value = LAST_UPDATE_HEADER
        column_map["last_update"] = max_column

    return column_map


def collect_rows(workbook, selected_partner=None, selected_partners=None):
    selected_names = normalize_selected_partners(selected_partner, selected_partners)
    selected_keys = {partner.casefold() for partner in selected_names}
    rows = []

    for sheet_name in workbook_data_sheet_names(workbook):
        worksheet = workbook[sheet_name]
        partner_columns = worksheet_partner_column_indexes(worksheet)
        url_column = find_columns(worksheet)["url"]
        if not url_column:
            continue

        for row_index in range(2, worksheet.max_row + 1):
            url = clean_text(worksheet.cell(row=row_index, column=url_column).value)
            if not url or ("tiktok.com" not in url and "vt.tiktok.com" not in url):
                continue

            partners = []
            if partner_columns:
                partners = worksheet_row_partners(worksheet, row_index, partner_columns)

            if selected_keys:
                if not partners:
                    continue
                if not selected_keys.intersection({partner.casefold() for partner in partners}):
                    continue

            rows.append({
                "sequence": len(rows) + 1,
                "sheet_name": sheet_name,
                "row": row_index,
                "url": url,
                "partners": partners,
            })

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


def parse_channel_name(content):
    for pattern in CHANNEL_PATTERNS:
        match = re.search(pattern, content)
        if match:
            value = clean_text(match.group(1))
            if value:
                return value
    return ""


def extract_profile_username(url):
    match = re.search(r"tiktok\.com/@([^/?]+)", url or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


async def read_channel_name_from_dom(page, profile_username):
    if not profile_username:
        return ""

    selectors = [
        f'a[href*="@{profile_username}"]',
        f'a[href="/@{profile_username}"]',
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            if count == 0:
                continue
            for index in range(min(count, 8)):
                try:
                    text = clean_text(await locator.nth(index).inner_text(timeout=1500))
                except Exception:
                    continue
                if not text:
                    continue
                normalized = text.lstrip("@").strip()
                if normalize_text(normalized) == normalize_text(profile_username):
                    continue
                if text.startswith("@"):
                    return normalized
        except Exception:
            continue
    return ""


async def block_heavy_resources(route):
    if route.request.resource_type in {"image", "media", "font"}:
        await route.abort()
    else:
        await route.continue_()


async def scrape_single_link(page, url, timeout_ms=45000):
    data = {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"}
    channel_name = ""
    profile_username = extract_profile_username(url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        content = ""
        found = False
        for _ in range(24):
            content = await page.content()
            data, found = parse_counts(content)
            dom_channel = ""
            if profile_username:
                dom_channel = await read_channel_name_from_dom(page, profile_username)
                if dom_channel:
                    channel_name = dom_channel
            if not channel_name:
                channel_name = parse_channel_name(content)
            if found and (dom_channel or not profile_username):
                break
            await page.wait_for_timeout(500)

        if not found:
            return data, channel_name, "Error: Không đọc được số liệu"

        return data, channel_name, "Success"
    except Exception as error:
        return data, channel_name, f"Error: {str(error)}"


async def scrape_with_retries(page, url, retries):
    last_data = {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"}
    last_status = "Error: Chưa chạy"
    last_channel = ""

    for attempt in range(retries + 1):
        if attempt > 0:
            await asyncio.sleep(random.uniform(1.5, 3.5))
        data, channel_name, status = await scrape_single_link(page, url)
        last_data, last_channel, last_status = data, channel_name, status
        if status == "Success":
            return data, channel_name, status, attempt + 1

    return last_data, last_channel, last_status, retries + 1


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
            data, channel_name, status, attempts = await scrape_with_retries(page, item["url"], retries)
            elapsed = time.perf_counter() - started_at
            await result_queue.put({
                **item,
                "worker": worker_id,
                "data": data,
                "channel_name": channel_name,
                "status": status,
                "attempts": attempts,
                "elapsed": elapsed,
            })
            work_queue.task_done()
            await asyncio.sleep(random.uniform(0.3, 1.1))
    finally:
        await context.close()


def write_result(sheet_contexts, item, data, channel_name, status):
    context = sheet_contexts[item["sheet_name"]]
    sheet = context["worksheet"]
    columns = context["columns"]
    row_index = item["row"]
    update_time = datetime.now().strftime("%d/%m/%Y %H:%M")

    if status == "Success":
        for metric_key, data_key in METRIC_KEYS.items():
            column_index = columns.get(metric_key)
            if column_index:
                sheet.cell(row=row_index, column=column_index).value = int(data.get(data_key) or 0)

    if columns.get("channel"):
        if channel_name:
            sheet.cell(row=row_index, column=columns["channel"]).value = channel_name
        elif status != "Success":
            sheet.cell(row=row_index, column=columns["channel"]).value = "Lỗi"

    if columns.get("last_update"):
        sheet.cell(row=row_index, column=columns["last_update"]).value = update_time


async def save_workbook(workbook, file_path, websocket_manager=None):
    try:
        await asyncio.to_thread(workbook.save, file_path)
        return True
    except PermissionError:
        if websocket_manager:
            await websocket_manager.broadcast_log("CẢNH BÁO: File Excel đang mở, không thể ghi đè. Vui lòng đóng file rồi quét lại hoặc chờ lần lưu tiếp theo.")
    except Exception as error:
        if websocket_manager:
            await websocket_manager.broadcast_log(f"CẢNH BÁO: Lỗi lưu file ({str(error)})")
    return False


def progress_payload(total, processed, success_count, error_count, worker_count, started_at, done=False, mode="full", partner="", phase="scanning"):
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
        "mode": mode,
        "partner": partner,
        "phase": phase,
    }


async def run_scraper(file_path, websocket_manager=None, worker_count=DEFAULT_WORKERS, retries=DEFAULT_RETRIES, save_every=DEFAULT_SAVE_EVERY, selected_partner=None, selected_partners=None):
    if not os.path.exists(file_path):
        if websocket_manager:
            await websocket_manager.broadcast_log(f"Lỗi: Không tìm thấy file {file_path}")
        return

    worker_count = clamp_int(worker_count, DEFAULT_WORKERS, 1, MAX_WORKERS)
    retries = clamp_int(retries, DEFAULT_RETRIES, 0, 5)
    save_every = clamp_int(save_every, DEFAULT_SAVE_EVERY, 5, 100)
    selected_names = normalize_selected_partners(selected_partner, selected_partners)
    partner_label = selected_partner_label(selected_names)

    workbook = openpyxl.load_workbook(file_path)
    sheet_contexts = build_sheet_contexts(workbook)
    rows_to_process = collect_rows(workbook, selected_partners=selected_names)
    total = len(rows_to_process)
    started_at = time.perf_counter()
    mode = "partner" if selected_names else "full"

    if total == 0:
        if websocket_manager:
            await websocket_manager.broadcast_status(progress_payload(0, 0, 0, 0, worker_count, started_at, done=True, mode=mode, partner=partner_label, phase="done"))
            message = "Không tìm thấy link TikTok nào phù hợp để quét."
            if selected_names:
                message = f"Không tìm thấy link nào cho {partner_label}."
            await websocket_manager.broadcast_log(message)
        return

    worker_count = min(worker_count, total)
    if websocket_manager:
        if selected_names:
            await websocket_manager.broadcast_log(
                f"Bắt đầu cập nhật {partner_label}: {total} links, {worker_count} luồng, retry {retries} lần."
            )
        else:
            await websocket_manager.broadcast_log(
                f"Bắt đầu quét nhanh {total} links bằng {worker_count} luồng, retry {retries} lần, lưu mỗi {save_every} kết quả."
            )
        await websocket_manager.broadcast_status(
            progress_payload(total, 0, 0, 0, worker_count, started_at, mode=mode, partner=partner_label, phase="starting")
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

    async with async_playwright() as playwright:
        if websocket_manager:
            await websocket_manager.broadcast_log(f"Đang khởi tạo trình duyệt và {worker_count} luồng...")
        browser = await playwright.chromium.launch(headless=True)
        workers = [
            asyncio.create_task(worker_loop(index + 1, browser, work_queue, result_queue, retries))
            for index in range(worker_count)
        ]
        if websocket_manager:
            await websocket_manager.broadcast_status(
                progress_payload(total, 0, 0, 0, worker_count, started_at, mode=mode, partner=partner_label, phase="scanning")
            )

        try:
            while processed < total:
                result = await result_queue.get()
                processed += 1
                data = result["data"]
                status = result["status"]

                if status == "Success":
                    success_count += 1
                else:
                    error_count += 1
                write_result(sheet_contexts, result, data, result["channel_name"], status)
                pending_save_count += 1

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
                        "channelName": result["channel_name"],
                        "sheetName": result["sheet_name"],
                    })
                    await websocket_manager.broadcast_status(
                        progress_payload(
                            total,
                            processed,
                            success_count,
                            error_count,
                            worker_count,
                            started_at,
                            mode=mode,
                            partner=partner_label,
                            phase="scanning",
                        )
                    )

                if pending_save_count >= save_every:
                    saved = await save_workbook(workbook, file_path, websocket_manager)
                    pending_save_count = 0 if saved else pending_save_count
                    if websocket_manager and saved:
                        await websocket_manager.broadcast_log(f"Đã lưu tạm workbook tại {processed}/{total} links.")

                result_queue.task_done()

            await work_queue.join()
            await asyncio.gather(*workers)
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            await browser.close()

    try:
        summary_update_time = datetime.now().strftime("%d/%m/%Y %H:%M")
        summary_count = rebuild_summary_sheet(
            workbook,
            summary_update_time=summary_update_time,
            selected_partners=selected_names,
        )
        if websocket_manager:
            await websocket_manager.broadcast_log(f"Đã cập nhật sheet Tổng kết cho {summary_count} đối tác.")
    except Exception as error:
        if websocket_manager:
            await websocket_manager.broadcast_log(f"CẢNH BÁO: Không cập nhật được sheet Tổng kết ({str(error)})")

    await save_workbook(workbook, file_path, websocket_manager)
    if websocket_manager:
        await websocket_manager.broadcast_status(
            progress_payload(
                total,
                processed,
                success_count,
                error_count,
                worker_count,
                started_at,
                done=True,
                mode=mode,
                partner=partner_label,
                phase="done",
            )
        )
        if selected_names:
            await websocket_manager.broadcast_log(
                f"HOÀN THÀNH: Đã cập nhật {partner_label} với {processed} links, thành công {success_count}, lỗi {error_count}."
            )
        else:
            await websocket_manager.broadcast_log(
                f"HOÀN THÀNH: Đã quét {processed}/{total} links, thành công {success_count}, lỗi {error_count}."
            )
