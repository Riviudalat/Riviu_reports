import asyncio
import html
import json
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
    is_generated_username_channel,
    is_generic_tiktok_channel_name,
    rebuild_summary_sheet,
    result_sheet_display_name,
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

MAX_WORKERS = 50
DEFAULT_WORKERS = 5
DEFAULT_RETRIES = 2
DEFAULT_SAVE_EVERY = 25
RESULT_SHEET_HEADERS = [
    "Stt",
    "Ngày",
    "Link",
    "Tên Kênh",
    "LƯỢT XEM",
    "TIM",
    "BÌNH LUẬN",
    "LƯỢT LƯU",
    "CHIA SẺ",
    "Đối tác",
    LAST_UPDATE_HEADER,
]


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


def username_override_map(file_path):
    return {}


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


def is_total_row(sheet, row_index, url_column):
    value = clean_text(sheet.cell(row=row_index, column=url_column).value)
    return normalize_text(value) == "TỔNG" or normalize_text(value) == "TONG"


def clear_existing_total_rows(workbook):
    for sheet_name in workbook_data_sheet_names(workbook):
        sheet = workbook[sheet_name]
        url_column = find_columns(sheet)["url"]
        if not url_column:
            continue
        for row_index in range(sheet.max_row, 1, -1):
            if is_total_row(sheet, row_index, url_column):
                sheet.delete_rows(row_index, 1)


def append_sheet_total_rows(workbook):
    for sheet_name in workbook_data_sheet_names(workbook):
        sheet = workbook[sheet_name]
        columns = find_columns(sheet)
        url_column = columns.get("url")
        if not url_column:
            continue

        last_link_row = 1
        for row_index in range(2, sheet.max_row + 1):
            url = clean_text(sheet.cell(row=row_index, column=url_column).value)
            if "tiktok.com" in url or "vt.tiktok.com" in url:
                last_link_row = row_index
        if last_link_row < 2:
            continue

        total_row = last_link_row + 1
        sheet.cell(row=total_row, column=url_column).value = "TỔNG"
        for metric_key in METRIC_HEADERS:
            column_index = columns.get(metric_key)
            if column_index:
                col_letter = openpyxl.utils.get_column_letter(column_index)
                sheet.cell(row=total_row, column=column_index).value = f"=SUM({col_letter}2:{col_letter}{last_link_row})"


def build_result_sheet(workbook, rows_to_process, summary_update_time):
    timestamp_label = datetime.now().strftime("%d-%m-%Y-%H-%M")
    sheet_name = result_sheet_display_name(timestamp_label)[:31]
    if sheet_name in workbook.sheetnames:
        base_name = sheet_name[:28]
        suffix = 2
        while f"{base_name}-{suffix}" in workbook.sheetnames:
            suffix += 1
        sheet_name = f"{base_name}-{suffix}"

    worksheet = workbook.create_sheet(title=sheet_name)
    for column_index, header in enumerate(RESULT_SHEET_HEADERS, start=1):
        worksheet.cell(row=1, column=column_index).value = header

    row_lookup = {}
    for item in rows_to_process:
        row_lookup[(item["sheet_name"], item["row"])] = item

    output_row = 2
    sequence = 1
    for source_sheet_name in workbook_data_sheet_names(workbook):
        source_sheet = workbook[source_sheet_name]
        columns = find_columns(source_sheet)
        url_column = columns.get("url")
        if not url_column:
            continue

        partner_columns = worksheet_partner_column_indexes(source_sheet)
        for source_row_index in range(2, source_sheet.max_row + 1):
            item = row_lookup.get((source_sheet_name, source_row_index))
            if not item:
                continue

            url = clean_text(source_sheet.cell(row=source_row_index, column=url_column).value)
            if not url or is_total_row(source_sheet, source_row_index, url_column):
                continue

            partner_names = worksheet_row_partners(source_sheet, source_row_index, partner_columns) if partner_columns else []
            worksheet.cell(row=output_row, column=1).value = sequence
            worksheet.cell(row=output_row, column=2).value = source_sheet.cell(row=source_row_index, column=columns.get("date") or 1).value if columns.get("date") else ""
            worksheet.cell(row=output_row, column=3).value = url
            worksheet.cell(row=output_row, column=4).value = clean_text(source_sheet.cell(row=source_row_index, column=columns.get("channel")).value) if columns.get("channel") else ""
            worksheet.cell(row=output_row, column=5).value = int(source_sheet.cell(row=source_row_index, column=columns.get("views")).value or 0) if columns.get("views") else 0
            worksheet.cell(row=output_row, column=6).value = int(source_sheet.cell(row=source_row_index, column=columns.get("likes")).value or 0) if columns.get("likes") else 0
            worksheet.cell(row=output_row, column=7).value = int(source_sheet.cell(row=source_row_index, column=columns.get("comments")).value or 0) if columns.get("comments") else 0
            worksheet.cell(row=output_row, column=8).value = int(source_sheet.cell(row=source_row_index, column=columns.get("saves")).value or 0) if columns.get("saves") else 0
            worksheet.cell(row=output_row, column=9).value = int(source_sheet.cell(row=source_row_index, column=columns.get("shares")).value or 0) if columns.get("shares") else 0
            worksheet.cell(row=output_row, column=10).value = "\n".join(partner_names)
            worksheet.cell(row=output_row, column=11).value = clean_text(source_sheet.cell(row=source_row_index, column=columns.get("last_update")).value) if columns.get("last_update") else summary_update_time
            output_row += 1
            sequence += 1

    widths = [10, 16, 72, 24, 14, 12, 14, 14, 12, 28, 20]
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[openpyxl.utils.get_column_letter(index)].width = width

    if output_row > 2:
        total_row = output_row
        worksheet.cell(row=total_row, column=3).value = "TỔNG"
        for column_index in range(5, 10):
            letter = openpyxl.utils.get_column_letter(column_index)
            worksheet.cell(row=total_row, column=column_index).value = f"=SUM({letter}2:{letter}{total_row - 1})"

    worksheet.freeze_panes = "A2"
    return sheet_name


def parse_counts(content):
    data = {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"}
    found = False
    for key, pattern in COUNT_PATTERNS.items():
        match = re.search(pattern, content)
        if match:
            data[key] = match.group(1)
            found = True
    return data, found


def json_loads_safe(raw_value):
    try:
        return json.loads(html.unescape(raw_value))
    except (TypeError, json.JSONDecodeError):
        return None


def iter_nested_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_nested_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_nested_dicts(child)


def author_name_from_object(obj, profile_username):
    if not isinstance(obj, dict) or not profile_username:
        return ""

    unique_id = clean_text(obj.get("uniqueId") or obj.get("unique_id") or obj.get("uniqueID"))
    if normalize_text(unique_id.lstrip("@")) != normalize_text(profile_username.lstrip("@")):
        return ""

    for key in ("nickname", "authorName", "name", "displayName"):
        candidate = clean_text(obj.get(key))
        if (
            candidate
            and not is_generated_username_channel(candidate, f"https://www.tiktok.com/@{profile_username}")
            and not is_generic_tiktok_channel_name(candidate)
        ):
            return candidate
    return ""


def valid_channel_candidate(value, profile_username):
    candidate = clean_text(value)
    if not candidate:
        return ""

    profile_url = f"https://www.tiktok.com/@{profile_username}"
    normalized_candidate = normalize_text(candidate.lstrip("@"))
    normalized_username = normalize_text(profile_username.lstrip("@"))
    blocked_labels = {
        "FOLLOW",
        "MESSAGE",
        "FOLLOWING",
        "FOLLOWERS",
        "LIKES",
    }
    if (
        candidate.startswith("@")
        or normalized_candidate == normalized_username
        or normalized_candidate in blocked_labels
        or "FOLLOWERS" in normalized_candidate
        or "FOLLOWING" in normalized_candidate
        or is_generated_username_channel(candidate, profile_url)
        or is_generic_tiktok_channel_name(candidate)
    ):
        return ""
    return candidate


def parse_channel_name(content, profile_username):
    if not profile_username:
        return ""

    script_patterns = [
        r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r'<script[^>]+id="SIGI_STATE"[^>]*>(.*?)</script>',
    ]
    for pattern in script_patterns:
        match = re.search(pattern, content, flags=re.DOTALL)
        if not match:
            continue
        data = json_loads_safe(match.group(1))
        for obj in iter_nested_dicts(data):
            candidate = author_name_from_object(obj, profile_username)
            if candidate:
                return candidate

    author_blocks = re.finditer(r'\{[^{}]*"uniqueId"\s*:\s*"([^"]+)"[^{}]*\}', content)
    for block_match in author_blocks:
        if normalize_text(block_match.group(1).lstrip("@")) != normalize_text(profile_username.lstrip("@")):
            continue
        data = json_loads_safe(block_match.group(0))
        candidate = author_name_from_object(data, profile_username)
        if candidate:
            return candidate
    return ""


def strip_html_text(value):
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(re.sub(r"\s+", " ", text))


def profile_title_candidate(raw_title, profile_username):
    title = strip_html_text(raw_title)
    if not title:
        return ""

    candidate = re.split(
        r"\s+\|\s+TikTok|\s+-\s+TikTok|\s+on\s+TikTok",
        title,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    candidate = re.sub(
        rf"\s*\(@?{re.escape(profile_username)}\)\s*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        rf"\s*@{re.escape(profile_username)}\s*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = clean_text(candidate.strip(" -|"))
    return valid_channel_candidate(candidate, profile_username)


def parse_profile_channel_name(content, profile_username):
    candidate = parse_channel_name(content, profile_username)
    if candidate:
        return candidate

    for tag in re.findall(r"<meta\b[^>]*>", content or "", flags=re.IGNORECASE):
        if not re.search(r'(?:property|name)=["\'](?:og:)?title["\']', tag, flags=re.IGNORECASE):
            continue
        match = re.search(r'content=["\']([^"\']+)["\']', tag, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = profile_title_candidate(match.group(1), profile_username)
        if candidate:
            return candidate

    match = re.search(r"<title[^>]*>(.*?)</title>", content or "", flags=re.DOTALL | re.IGNORECASE)
    if match:
        candidate = profile_title_candidate(match.group(1), profile_username)
        if candidate:
            return candidate
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
                    continue
                candidate = valid_channel_candidate(text, profile_username)
                if candidate:
                    return candidate
        except Exception:
            continue
    return ""


def profile_text_candidates(text):
    for line in str(text or "").splitlines():
        line = clean_text(line)
        if line:
            yield line


async def read_profile_channel_name_from_dom(page, profile_username):
    selectors = [
        '[data-e2e="user-title"]',
        'h1[data-e2e="user-title"]',
        "main h1",
        "h1",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            for index in range(min(count, 5)):
                try:
                    text = await locator.nth(index).inner_text(timeout=1200)
                except Exception:
                    continue
                for line in profile_text_candidates(text):
                    candidate = valid_channel_candidate(line, profile_username)
                    if candidate:
                        return candidate
        except Exception:
            continue
    return ""


async def read_profile_channel_name(page, profile_username, channel_cache=None, timeout_ms=18000):
    if not profile_username:
        return ""

    cache_key = profile_username.casefold()
    if isinstance(channel_cache, dict):
        cached = clean_text(channel_cache.get(cache_key))
        if (
            cached
            and not is_generated_username_channel(cached, f"https://www.tiktok.com/@{profile_username}")
            and not is_generic_tiktok_channel_name(cached)
        ):
            return cached

    profile_url = f"https://www.tiktok.com/@{profile_username}"
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=timeout_ms)
        for _ in range(20):
            content = await page.content()
            candidate = parse_profile_channel_name(content, profile_username)
            if not candidate:
                candidate = await read_profile_channel_name_from_dom(page, profile_username)
            if candidate:
                if isinstance(channel_cache, dict):
                    channel_cache[cache_key] = candidate
                return candidate
            await page.wait_for_timeout(500)
    except Exception:
        return ""
    return ""


async def block_heavy_resources(route):
    if route.request.resource_type in {"image", "media", "font"}:
        await route.abort()
    else:
        await route.continue_()


async def scrape_single_link(page, url, channel_cache=None, timeout_ms=45000):
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
            parsed_channel = parse_channel_name(content, profile_username)
            if parsed_channel:
                channel_name = parsed_channel
            if profile_username and not channel_name:
                dom_channel = await read_channel_name_from_dom(page, profile_username)
                if dom_channel and not is_generated_username_channel(dom_channel, url) and not is_generic_tiktok_channel_name(dom_channel):
                    channel_name = dom_channel
            if found and channel_name:
                break
            await page.wait_for_timeout(500)

        if profile_username:
            profile_channel = await read_profile_channel_name(page, profile_username, channel_cache=channel_cache)
            if profile_channel:
                channel_name = profile_channel

        if not found:
            return data, channel_name, "Error: Không đọc được số liệu"

        return data, channel_name, "Success"
    except Exception as error:
        return data, channel_name, f"Error: {str(error)}"


async def scrape_with_retries(page, url, retries, channel_cache=None):
    last_data = {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"}
    last_status = "Error: Chưa chạy"
    last_channel = ""

    for attempt in range(retries + 1):
        if attempt > 0:
            await asyncio.sleep(random.uniform(1.5, 3.5))
        data, channel_name, status = await scrape_single_link(page, url, channel_cache=channel_cache)
        last_data, last_channel, last_status = data, channel_name, status
        if status == "Success":
            return data, channel_name, status, attempt + 1

    return last_data, last_channel, last_status, retries + 1


async def worker_loop(worker_id, browser, work_queue, result_queue, retries, channel_cache=None, startup_semaphore=None, websocket_manager=None):
    RECYCLE_AFTER = 100

    async def make_context():
        ctx = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 390, "height": 844},
        )
        await ctx.route("**/*", block_heavy_resources)
        return ctx

    async def drain_with_error(reason):
        while True:
            try:
                pending = work_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if pending is None:
                work_queue.task_done()
                break
            await result_queue.put({
                **pending,
                "worker": worker_id,
                "data": {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"},
                "channel_name": "",
                "status": reason,
                "attempts": 0,
                "elapsed": 0.0,
            })
            work_queue.task_done()

    if startup_semaphore is not None:
        async with startup_semaphore:
            await asyncio.sleep(min(worker_id - 1, 10) * 0.25)
            try:
                context = await make_context()
                page = await context.new_page()
            except Exception as error:
                if websocket_manager:
                    await websocket_manager.broadcast_log(f"Worker {worker_id} không khởi tạo được context: {str(error)}")
                await drain_with_error(f"Error: Worker {worker_id} không khởi tạo được")
                return
    else:
        context = await make_context()
        page = await context.new_page()

    links_in_current_context = 0

    try:
        while True:
            item = await work_queue.get()
            if item is None:
                work_queue.task_done()
                break

            started_at = time.perf_counter()
            try:
                data, channel_name, status, attempts = await scrape_with_retries(page, item["url"], retries, channel_cache=channel_cache)
            except Exception as error:
                data = {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"}
                channel_name = ""
                status = f"Error: Worker {worker_id} crash ({str(error)})"
                attempts = 0
                # Try to recover the page so the worker keeps running
                try:
                    if page.is_closed():
                        page = await context.new_page()
                except Exception:
                    pass
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
            links_in_current_context += 1

            # Recycle context periodically to keep RAM in check on long runs
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
                        await websocket_manager.broadcast_log(f"Worker {worker_id} không khởi tạo lại được context: {str(error)}")
                    await drain_with_error(f"Error: Worker {worker_id} mất context")
                    return

            await asyncio.sleep(random.uniform(0.3, 1.1))
    except Exception as error:
        if websocket_manager:
            await websocket_manager.broadcast_log(f"Worker {worker_id} dừng do lỗi: {str(error)}")
        await drain_with_error(f"Error: Worker {worker_id} đã dừng")
    finally:
        try:
            await context.close()
        except Exception:
            pass


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
        if (
            channel_name
            and not is_generated_username_channel(channel_name, item["url"])
            and not is_generic_tiktok_channel_name(channel_name)
        ):
            sheet.cell(row=row_index, column=columns["channel"]).value = channel_name
        else:
            sheet.cell(row=row_index, column=columns["channel"]).value = "Lỗi"

    if columns.get("last_update"):
        sheet.cell(row=row_index, column=columns["last_update"]).value = update_time


async def save_workbook(workbook, file_path, websocket_manager=None):
    try:
        await asyncio.to_thread(workbook.save, file_path)
        return True, None
    except PermissionError:
        if websocket_manager:
            await websocket_manager.broadcast_log("CẢNH BÁO: File Excel đang mở, không thể ghi đè. Vui lòng đóng file rồi quét lại hoặc chờ lần lưu tiếp theo.")
        return False, "permission"
    except Exception as error:
        if websocket_manager:
            await websocket_manager.broadcast_log(f"CẢNH BÁO: Lỗi lưu file ({str(error)})")
        return False, "other"


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


async def run_scraper(file_path, websocket_manager=None, worker_count=DEFAULT_WORKERS, retries=DEFAULT_RETRIES, save_every=DEFAULT_SAVE_EVERY, selected_partner=None, selected_partners=None, create_result_sheet=False):
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
    clear_existing_total_rows(workbook)
    sheet_contexts = build_sheet_contexts(workbook)
    rows_to_process = collect_rows(workbook, selected_partners=selected_names)

    # Dedup URL: nhiều dòng cùng URL chỉ scrape 1 lần, ghi kết quả về tất cả các dòng
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
            }
            unique_buckets[key] = bucket
            bucket_order.append(bucket)
        bucket["rows"].append({
            "sheet_name": item["sheet_name"],
            "row": item["row"],
            "partners": item["partners"],
        })

    duplicate_count = len(rows_to_process) - len(bucket_order)
    channel_cache = {}
    total_rows = len(rows_to_process)
    total = len(bucket_order)
    started_at = time.perf_counter()
    mode = "partner" if selected_names else "full"

    # Adaptive save_every: file lớn save thưa hơn để giảm I/O
    if total > 500:
        save_every = max(50, min(100, total // 30))

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
        if duplicate_count > 0:
            await websocket_manager.broadcast_log(
                f"Tổng {total_rows} dòng, sau dedup còn {total} URL cần quét (tiết kiệm {duplicate_count} lượt)."
            )
        if selected_names:
            await websocket_manager.broadcast_log(
                f"Bắt đầu cập nhật {partner_label}: {total} URL, {worker_count} luồng, retry {retries} lần."
            )
        else:
            await websocket_manager.broadcast_log(
                f"Bắt đầu quét nhanh {total} URL bằng {worker_count} luồng, retry {retries} lần, lưu mỗi {save_every} kết quả."
            )
        await websocket_manager.broadcast_status(
            progress_payload(total, 0, 0, 0, worker_count, started_at, mode=mode, partner=partner_label, phase="starting")
        )

    work_queue = asyncio.Queue()
    result_queue = asyncio.Queue()
    for bucket in bucket_order:
        await work_queue.put(bucket)
    for _ in range(worker_count):
        await work_queue.put(None)

    processed = 0
    success_count = 0
    error_count = 0
    pending_save_count = 0
    save_skip_until_processed = 0
    last_status_broadcast = 0.0

    async with async_playwright() as playwright:
        if websocket_manager:
            await websocket_manager.broadcast_log(f"Đang khởi tạo trình duyệt và {worker_count} luồng...")
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ],
        )
        startup_semaphore = asyncio.Semaphore(min(worker_count, 6))
        workers = [
            asyncio.create_task(
                worker_loop(
                    index + 1,
                    browser,
                    work_queue,
                    result_queue,
                    retries,
                    channel_cache=channel_cache,
                    startup_semaphore=startup_semaphore,
                    websocket_manager=websocket_manager,
                )
            )
            for index in range(worker_count)
        ]
        if websocket_manager:
            await websocket_manager.broadcast_status(
                progress_payload(total, 0, 0, 0, worker_count, started_at, mode=mode, partner=partner_label, phase="scanning")
            )

        try:
            stalled_seconds = 0
            while processed < total:
                try:
                    result = await asyncio.wait_for(result_queue.get(), timeout=30.0)
                    stalled_seconds = 0
                except asyncio.TimeoutError:
                    stalled_seconds += 30
                    alive_workers = sum(1 for task in workers if not task.done())
                    if alive_workers == 0:
                        if websocket_manager:
                            await websocket_manager.broadcast_log(
                                f"Tất cả worker đã dừng, hủy {total - processed} link còn lại."
                            )
                        # All workers are dead, mark remaining as errors
                        remaining = total - processed
                        for _ in range(remaining):
                            error_count += 1
                            processed += 1
                        break
                    if websocket_manager and stalled_seconds % 60 == 0:
                        await websocket_manager.broadcast_log(
                            f"Đang chờ kết quả... {alive_workers}/{worker_count} luồng còn sống ({processed}/{total})."
                        )
                    continue

                processed += 1
                data = result["data"]
                status = result["status"]
                bucket_rows = result.get("rows") or []

                if status == "Success":
                    success_count += 1
                else:
                    error_count += 1

                # Write the same scrape result to every spreadsheet row that shares this URL
                for target in bucket_rows:
                    write_result(
                        sheet_contexts,
                        {
                            "sheet_name": target["sheet_name"],
                            "row": target["row"],
                            "url": result["url"],
                        },
                        data,
                        result["channel_name"],
                        status,
                    )
                pending_save_count += max(len(bucket_rows), 1)

                if websocket_manager:
                    primary_target = bucket_rows[0] if bucket_rows else {"sheet_name": ""}
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
                        "sheetName": primary_target.get("sheet_name", ""),
                    })
                    now = time.perf_counter()
                    if processed == total or now - last_status_broadcast > 0.25:
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
                        last_status_broadcast = now

                if pending_save_count >= save_every and save_skip_until_processed <= processed:
                    saved, save_reason = await save_workbook(workbook, file_path, websocket_manager)
                    if saved:
                        pending_save_count = 0
                        if websocket_manager:
                            await websocket_manager.broadcast_log(f"Đã lưu tạm workbook tại {processed}/{total} links.")
                    else:
                        # Reset counter and skip saving for the next `save_every` items to avoid log spam
                        pending_save_count = 0
                        save_skip_until_processed = processed + save_every

                result_queue.task_done()

            try:
                await asyncio.wait_for(asyncio.gather(*workers, return_exceptions=True), timeout=20.0)
            except asyncio.TimeoutError:
                pass
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(*workers, return_exceptions=True), timeout=10.0)
            except asyncio.TimeoutError:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    try:
        append_sheet_total_rows(workbook)
        summary_update_time = datetime.now().strftime("%d/%m/%Y %H:%M")
        created_sheet_name = ""
        if create_result_sheet:
            created_sheet_name = build_result_sheet(workbook, rows_to_process, summary_update_time)
            if websocket_manager and created_sheet_name:
                await websocket_manager.broadcast_log(f"Đã tạo sheet kết quả mới: {created_sheet_name}.")
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
                f"HOÀN THÀNH: Đã cập nhật {partner_label} với {processed} URL ({total_rows} dòng), thành công {success_count}, lỗi {error_count}."
            )
        else:
            await websocket_manager.broadcast_log(
                f"HOÀN THÀNH: Đã quét {processed}/{total} URL ({total_rows} dòng), thành công {success_count}, lỗi {error_count}."
            )
