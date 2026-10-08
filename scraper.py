import asyncio
import html
import json
import os
import random
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import urljoin, urlparse

import openpyxl
from playwright.async_api import async_playwright

from workbook_utils import (
    COLUMN_ALIASES,
    clean_text,
    set_cell_literal,
    highlight_single_partner_link_rows,
    highlight_video_link_rows,
    is_generated_username_channel,
    is_generic_tiktok_channel_name,
    is_numeric_channel_garbage,
    is_scrapable_tiktok_url,
    should_highlight_video_link,
    load_channel_overrides,
    metric_number,
    format_display_datetime,
    format_excel_sheet_datetime,
    normalize_tiktok_url,
    rebuild_summary_sheet,
    resolve_channel_name,
    result_sheet_display_name,
    save_workbook_atomic,
    summary_sheet_title_for_data_sheet,
    TTBD_RESOLVED_URL_HEADER,
    TTBD_SCAN_STATUS_HEADER,
    TTBD_SOURCE_URL_HEADER,
    workbook_data_sheet_names,
    worksheet_ensure_column,
    worksheet_find_column_index,
    worksheet_find_link_column_index,
    worksheet_partner_column_indexes,
    worksheet_row_partners,
    write_json_atomic,
)
from proxy_utils import (
    assign_worker_proxy,
    get_session_proxies,
    playwright_proxy_settings,
    proxy_label,
    release_thread_proxy,
    resolve_proxy_configs,
    set_session_proxies,
    urlopen_request,
)


USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPad; CPU OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
]
DEFAULT_USER_AGENT = USER_AGENTS[0]
BROWSER_LOCALE = "vi-VN"
BROWSER_TIMEZONE = "Asia/Ho_Chi_Minh"
BROWSER_GEOLOCATION = {"latitude": 11.9404, "longitude": 108.4583}

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

COUNT_FIELD_MAP = {
    "Views": "playCount",
    "Likes": "diggCount",
    "Comments": "commentCount",
    "Saves": "collectCount",
    "Shares": "shareCount",
}

BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "texttrack"}
UNIVERSAL_DETAIL_KEY_MARKERS = (
    "video-detail",
    "photo-detail",
    "photomode",
    "image-detail",
    "reflow.video.detail",
    "reflow.photo.detail",
)
EMBEDDED_STATE_SCRIPT_PATTERNS = (
    r"<script\b[^>]*\bid\s*=\s*['\"]__UNIVERSAL_DATA_FOR_REHYDRATION__['\"][^>]*>(.*?)</script\s*>",
    r"<script\b[^>]*\bid\s*=\s*['\"]SIGI_STATE['\"][^>]*>(.*?)</script\s*>",
    r"<script\b[^>]*\bid\s*=\s*['\"]api-data['\"][^>]*>(.*?)</script\s*>",
)

MAX_CONCURRENT_REQUESTS = 20
DIRECT_MAX_WORKERS = 10
MAX_WORKERS_PER_PROXY = 10
REQUEST_SHELL_RETRY_DELAY = 0.35
REQUEST_SHELL_RETRY_DELAY_PROXY = 0.12
REQUEST_CANDIDATE_LIMIT_DIRECT = 4
REQUEST_CANDIDATE_LIMIT_PROXY = 3
REQUEST_METRIC_HINT_PATTERN = re.compile(r'"(?:playCount|diggCount)"\s*:\s*"?(\d+)"?')
_request_semaphore = threading.Semaphore(MAX_CONCURRENT_REQUESTS)
_request_block_lock = threading.Lock()
_request_block_until = 0.0
_network_fail_streak = 0
NETWORK_FAIL_STREAK_PAUSE = 3
NETWORK_FAIL_PAUSE_SECONDS = 5.0


def session_uses_proxy():
    return bool(get_session_proxies())


def clamp_worker_count(worker_count, proxy_count=None):
    """Chỉ bó số luồng vào khoảng hợp lệ (1..MAX_WORKERS).

    Không tự giảm luồng dù không có proxy hay ít proxy — luôn chạy đúng số
    luồng người dùng chọn. proxy_count bị bỏ qua; chỉ giữ để caller cũ không lỗi.
    """
    return clamp_int(worker_count, DEFAULT_WORKERS, 1, MAX_WORKERS)


def configure_request_concurrency(worker_count):
    """Reset the module-level HTTP limiter and back-off state for a new run."""
    global _request_semaphore, _network_fail_streak, _request_block_until
    limit = clamp_worker_count(worker_count)
    _request_semaphore = threading.Semaphore(limit)
    with _request_block_lock:
        _network_fail_streak = 0
        _request_block_until = 0.0


def is_request_rate_limited_status(status):
    text = str(status or "")
    return "HTTP 403" in text or "HTTP 429" in text


def is_transient_network_status(status):
    """DNS / connection reset / timeout / SSL đứt giữa chừng — lỗi tạm, nên retry đủ số lần."""
    text = str(status or "").casefold()
    markers = (
        "getaddrinfo failed",
        "errno 11001",
        "winerror 10054",
        "connection was forcibly closed",
        "connection reset",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "name or service not known",
        "nodename nor servname",
        "network is unreachable",
        "connection aborted",
        "broken pipe",
        "unexpected_eof_while_reading",
        "eof occurred in violation of protocol",
        "ssl:",
        "sslerror",
        "wrong version number",
        "connection refused",
        "remote end closed connection",
        "incomplete read",
    )
    return any(marker in text for marker in markers)


def wait_if_request_blocked():
    with _request_block_lock:
        until = _request_block_until
    remaining = until - time.time()
    if remaining > 0:
        time.sleep(remaining)


def note_request_rate_limit(http_code):
    global _request_block_until
    if http_code not in (403, 429) or session_uses_proxy():
        return
    pause = 8.0 if http_code == 403 else 4.0
    with _request_block_lock:
        _request_block_until = max(_request_block_until, time.time() + pause)


def note_network_failure():
    """Tạm dừng toàn phiên khi DNS/mạng fail hàng loạt (tránh đốt hết queue)."""
    global _request_block_until, _network_fail_streak
    with _request_block_lock:
        _network_fail_streak += 1
        if _network_fail_streak >= NETWORK_FAIL_STREAK_PAUSE:
            _request_block_until = max(
                _request_block_until,
                time.time() + NETWORK_FAIL_PAUSE_SECONDS,
            )
            _network_fail_streak = 0


def note_network_success():
    global _network_fail_streak
    with _request_block_lock:
        _network_fail_streak = 0


def request_shell_retry_delay():
    return REQUEST_SHELL_RETRY_DELAY_PROXY if session_uses_proxy() else REQUEST_SHELL_RETRY_DELAY


def request_candidate_limit():
    return REQUEST_CANDIDATE_LIMIT_PROXY if session_uses_proxy() else REQUEST_CANDIDATE_LIMIT_DIRECT

# Phân biệt hai loại "không có số liệu":
# - METRICS_UNREADABLE: HTML có dấu hiệu số liệu nhưng đọc lỗi (hiếm, đáng retry).
# - TIKTOK_NO_STATS: TikTok trả trang rỗng, không hề có số liệu (post ẩn view) -> đếm ẨN, không phải BỊ LỖI.
STATUS_METRICS_UNREADABLE = "Error: Không đọc được số liệu"
STATUS_TIKTOK_NO_STATS = "Ẩn số liệu: TikTok không trả lượt xem"
# Chuỗi cũ (trước khi tách ẩn/lỗi) — vẫn nhận diện khi đọc log/Excel cũ.
STATUS_TIKTOK_NO_STATS_LEGACY = "Lỗi: TikTok không trả số liệu"
STATUS_MEDIA_REDIRECT_MISMATCH = "Error: Redirect không khớp video"

MAX_WORKERS = 50
DEFAULT_WORKERS = 5
DEFAULT_RETRIES = 2
DEFAULT_SAVE_EVERY = 25
DEFAULT_REQUEST_TIMEOUT = 30
MAX_BROWSER_FALLBACK_WORKERS = 15
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
    TTBD_SCAN_STATUS_HEADER,
    TTBD_RESOLVED_URL_HEADER,
    TTBD_SOURCE_URL_HEADER,
]

SCRAPE_HISTORY_FILENAME = "scrape_history.json"
SCRAPE_HISTORY_LIMIT = 200


TOTAL_KEYS = {
    "views": "totalViews",
    "likes": "totalLikes",
    "comments": "totalComments",
    "saves": "totalSaves",
    "shares": "totalShares",
}


def _sum_metric_rows(workbook, sheet_rows):
    """Sum TikTok metrics over (sheet_name, row_index) pairs; blank cells count as 0."""
    totals = {"totalLinks": 0, **{total_key: 0 for total_key in TOTAL_KEYS.values()}}
    column_cache = {}
    for sheet_name, row_index in sheet_rows:
        if not sheet_name or not row_index or sheet_name not in workbook.sheetnames:
            continue
        sheet = workbook[sheet_name]
        if sheet_name not in column_cache:
            column_cache[sheet_name] = detect_columns(sheet)
        columns = column_cache[sheet_name]
        url_column = columns.get("url")
        if not url_column or not is_scrapable_tiktok_url(sheet.cell(row=row_index, column=url_column).value):
            continue
        totals["totalLinks"] += 1
        for metric_key, total_key in TOTAL_KEYS.items():
            column_index = columns.get(metric_key)
            if column_index:
                totals[total_key] += metric_number(sheet.cell(row=row_index, column=column_index).value)
    return totals


def _compute_workbook_totals(workbook, sheet_name=None):
    target_sheet = clean_text(sheet_name)
    if target_sheet and target_sheet in workbook.sheetnames:
        sheet_names = [target_sheet]
    else:
        sheet_names = workbook_data_sheet_names(workbook)
    return _sum_metric_rows(workbook, (
        (current_sheet, row_index)
        for current_sheet in sheet_names
        for row_index in range(2, (workbook[current_sheet].max_row or 1) + 1)
    ))


def _compute_session_totals(workbook, rows_to_process):
    """Sum metrics only for rows included in the current scrape session."""
    return _sum_metric_rows(workbook, ((item.get("sheet_name"), item.get("row")) for item in rows_to_process))


def scrape_history_path(base_dir):
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, SCRAPE_HISTORY_FILENAME)


def append_scrape_history(base_dir, entry):
    if not base_dir:
        return
    path = scrape_history_path(base_dir)
    history = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
            if isinstance(payload, dict):
                history = list(payload.get("history") or [])
        except (OSError, json.JSONDecodeError):
            history = []
    history.insert(0, entry)
    history = history[:SCRAPE_HISTORY_LIMIT]
    try:
        write_json_atomic(path, {"history": history})
    except OSError:
        pass


def read_scrape_history(base_dir, limit=50):
    path = scrape_history_path(base_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return []
    history = payload.get("history") if isinstance(payload, dict) else None
    if not isinstance(history, list):
        return []
    return history[:limit]


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


def selected_data_sheet_names(workbook, sheet_name=None):
    target_sheet = clean_text(sheet_name)
    if target_sheet:
        return [target_sheet] if target_sheet in workbook.sheetnames else []
    return workbook_data_sheet_names(workbook)


def build_sheet_contexts(workbook, sheet_name=None):
    contexts = {}
    for sheet_name in selected_data_sheet_names(workbook, sheet_name):
        sheet = workbook[sheet_name]
        contexts[sheet_name] = {
            "worksheet": sheet,
            "columns": ensure_columns(sheet),
        }
    return contexts


INTERNAL_COLUMN_HEADERS = {
    "scan_status": TTBD_SCAN_STATUS_HEADER,
    "resolved_url": TTBD_RESOLVED_URL_HEADER,
    "source_url": TTBD_SOURCE_URL_HEADER,
}
CHANNEL_HEADER = "Tên Kênh"


TIKTOK_SHORT_LINK_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}


def is_tiktok_post_url(value):
    """True for a TikTok video/photo URL with a media id, or a vm/vt short link.

    Profile URLs (https://www.tiktok.com/@kenh) are not posts, so a "Link kênh"
    column is never mistaken for the link column.
    """
    url = normalize_tiktok_url(value)
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path or ""
    if host in TIKTOK_SHORT_LINK_HOSTS:
        return bool(re.fullmatch(r"/(?:t/)?[A-Za-z0-9_-]+/?", path))
    if re.fullmatch(r"/t/[A-Za-z0-9_-]+/?", path):
        return True
    return bool(re.search(r"/(?:video|photo)/\d+(?:/|$)", path, flags=re.IGNORECASE))


def _find_link_column_by_content(sheet, skip_columns):
    max_column = sheet.max_column or 0
    for row in range(2, min(sheet.max_row or 1, 25) + 1):
        for col in range(1, max_column + 1):
            if col in skip_columns:
                continue
            if is_tiktok_post_url(sheet.cell(row=row, column=col).value):
                return col
    return None


def detect_columns(sheet):
    """Map scanner columns by exact (case/diacritic-insensitive) header aliases.

    No positional guesses: a column that is not found stays None and
    ensure_columns appends it, so notes or channel-link columns are never
    mistaken for metric columns.
    """
    column_map = {
        key: worksheet_find_column_index(sheet, (header,))
        for key, header in INTERNAL_COLUMN_HEADERS.items()
    }
    column_map["url"] = worksheet_find_link_column_index(sheet) or _find_link_column_by_content(
        sheet, {index for index in column_map.values() if index}
    )
    column_map["channel"] = worksheet_find_column_index(sheet, COLUMN_ALIASES[CHANNEL_HEADER])
    column_map["date"] = worksheet_find_column_index(sheet, COLUMN_ALIASES["date"])
    column_map["last_update"] = worksheet_find_column_index(sheet, COLUMN_ALIASES[LAST_UPDATE_HEADER])
    for key, header in METRIC_HEADERS.items():
        column_map[key] = worksheet_find_column_index(sheet, (header,))
    return column_map


def ensure_columns(sheet):
    column_map = detect_columns(sheet)
    column_map["channel"] = worksheet_ensure_column(sheet, CHANNEL_HEADER)
    for key, header in METRIC_HEADERS.items():
        column_map[key] = worksheet_ensure_column(sheet, header, (header,))
    column_map["last_update"] = worksheet_ensure_column(sheet, LAST_UPDATE_HEADER)
    for key, header in INTERNAL_COLUMN_HEADERS.items():
        column_map[key] = worksheet_ensure_column(sheet, header, (header,), hidden=True)
    return column_map


def trusted_resolved_media_id(url, stored_source_url, stored_resolved_url):
    """Media id a short link resolved to last time, only when it still describes this link.

    The stored id is trusted only when the hidden source-URL cell names the
    current link. Legacy rows without that cell cannot prove the link was not
    replaced, so they get no expectation (a link that carries its own media id
    is already guarded by the redirect check).
    """
    resolved_media_id = extract_media_id(stored_resolved_url)
    if not resolved_media_id:
        return ""
    if stored_source_url:
        return resolved_media_id if stored_source_url.casefold() == url.casefold() else ""
    return resolved_media_id if extract_media_id(url) == resolved_media_id else ""


def collect_rows(workbook, selected_partner=None, selected_partners=None, sheet_name=None):
    selected_names = normalize_selected_partners(selected_partner, selected_partners)
    selected_keys = {partner.casefold() for partner in selected_names}
    rows = []

    for sheet_name in selected_data_sheet_names(workbook, sheet_name):
        worksheet = workbook[sheet_name]
        partner_columns = worksheet_partner_column_indexes(worksheet)
        columns = detect_columns(worksheet)
        url_column = columns["url"]
        if not url_column:
            continue

        for row_index in range(2, worksheet.max_row + 1):
            raw_url = worksheet.cell(row=row_index, column=url_column).value
            if not is_scrapable_tiktok_url(raw_url):
                continue
            url = normalize_tiktok_url(raw_url)
            stored_source_url = normalize_tiktok_url(
                worksheet.cell(row=row_index, column=columns["source_url"]).value
            ) if columns.get("source_url") else ""
            stored_resolved_url = normalize_tiktok_url(
                worksheet.cell(row=row_index, column=columns["resolved_url"]).value
            ) if columns.get("resolved_url") else ""
            expected_media_id = trusted_resolved_media_id(url, stored_source_url, stored_resolved_url)

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
                "expected_media_id": expected_media_id,
            })

    return rows


def build_duplicate_link_payload(bucket_order):
    """Build the duplicate-link summary shown in the live UI."""
    items = []
    duplicate_row_count = 0

    for bucket in bucket_order:
        rows = bucket.get("rows", [])
        if len(rows) < 2:
            continue

        locations = [
            {
                "sheetName": row.get("sheet_name", ""),
                "row": row.get("row", ""),
            }
            for row in rows
        ]
        duplicate_row_count += len(locations) - 1
        items.append({
            "id": len(items) + 1,
            "url": bucket.get("url", ""),
            "locations": locations,
        })

    return {
        "duplicateUrlCount": len(items),
        "duplicateRowCount": duplicate_row_count,
        "items": items,
    }


def is_total_row(sheet, row_index, url_column):
    value = clean_text(sheet.cell(row=row_index, column=url_column).value)
    return normalize_text(value) == "TỔNG" or normalize_text(value) == "TONG"


def clear_existing_total_rows(workbook, sheet_name=None):
    """Delete TỔNG rows; return the names of sheets that had one."""
    cleared = []
    for sheet_name in selected_data_sheet_names(workbook, sheet_name):
        sheet = workbook[sheet_name]
        url_column = detect_columns(sheet)["url"]
        if not url_column:
            continue
        for row_index in range(sheet.max_row, 1, -1):
            if is_total_row(sheet, row_index, url_column):
                sheet.delete_rows(row_index, 1)
                if sheet_name not in cleared:
                    cleared.append(sheet_name)
    return cleared


def append_sheet_total_rows(workbook, sheet_name=None):
    for sheet_name in selected_data_sheet_names(workbook, sheet_name):
        sheet = workbook[sheet_name]
        columns = detect_columns(sheet)
        url_column = columns.get("url")
        if not url_column:
            continue

        link_rows = [
            row_index for row_index in range(2, sheet.max_row + 1)
            if is_scrapable_tiktok_url(sheet.cell(row=row_index, column=url_column).value)
        ]
        if not link_rows:
            continue
        columns = ensure_columns(sheet)
        occupied_rows = [
            row_index for row_index in range(2, sheet.max_row + 1)
            if any(cell.value is not None for cell in sheet[row_index])
        ]
        total_row = max(occupied_rows, default=1) + 1
        sheet.cell(row=total_row, column=url_column).value = "TỔNG"
        # Sum only TikTok rows; contiguous ranges keep large-sheet formulas short.
        ranges = []
        start = end = link_rows[0]
        for row_index in link_rows[1:]:
            if row_index == end + 1:
                end = row_index
            else:
                ranges.append((start, end))
                start = end = row_index
        ranges.append((start, end))
        for metric_key in METRIC_HEADERS:
            column_index = columns.get(metric_key)
            if column_index:
                col_letter = openpyxl.utils.get_column_letter(column_index)
                references = ",".join(f"{col_letter}{start}:{col_letter}{end}" for start, end in ranges)
                sheet.cell(row=total_row, column=column_index).value = f"=SUM({references})"


def build_result_sheet(workbook, rows_to_process, summary_update_time):
    sheet_name = result_sheet_display_name(format_excel_sheet_datetime())
    if sheet_name in workbook.sheetnames:
        base_name = sheet_name[:28]
        suffix = 2
        while f"{base_name}-{suffix}" in workbook.sheetnames:
            suffix += 1
        sheet_name = f"{base_name}-{suffix}"

    worksheet = workbook.create_sheet(title=sheet_name)
    try:
        _fill_result_sheet(workbook, worksheet, rows_to_process, summary_update_time)
    except Exception:
        # Never leave a half-built result tab behind.
        workbook.remove(worksheet)
        raise
    return sheet_name


def _fill_result_sheet(workbook, worksheet, rows_to_process, summary_update_time):
    for column_index, header in enumerate(RESULT_SHEET_HEADERS, start=1):
        worksheet.cell(row=1, column=column_index).value = header

    row_lookup = {(item["sheet_name"], item["row"]) for item in rows_to_process}

    output_row = 2
    for source_sheet_name in workbook_data_sheet_names(workbook):
        if source_sheet_name == worksheet.title:
            continue
        source_sheet = workbook[source_sheet_name]
        columns = ensure_columns(source_sheet)
        url_column = columns.get("url")
        if not url_column:
            continue

        def source_value(key, row_index):
            column_index = columns.get(key)
            return source_sheet.cell(row=row_index, column=column_index).value if column_index else None

        partner_columns = worksheet_partner_column_indexes(source_sheet)
        for source_row_index in range(2, source_sheet.max_row + 1):
            if (source_sheet_name, source_row_index) not in row_lookup:
                continue

            url = clean_text(source_sheet.cell(row=source_row_index, column=url_column).value)
            if not url or is_total_row(source_sheet, source_row_index, url_column):
                continue

            partner_names = worksheet_row_partners(source_sheet, source_row_index, partner_columns) if partner_columns else []
            last_update = (
                clean_text(source_value("last_update", source_row_index))
                if columns.get("last_update") else summary_update_time
            )
            values = [
                output_row - 1,
                source_value("date", source_row_index) if columns.get("date") else "",
                url,
                clean_text(source_value("channel", source_row_index)),
                # Blank stays blank; text like "1.234" is read as a number, never crashes.
                *(metric_or_none(source_value(key, source_row_index)) for key in METRIC_HEADERS),
                "\n".join(partner_names),
                last_update,
                *(clean_text(source_value(key, source_row_index)) for key in INTERNAL_COLUMN_HEADERS),
            ]
            for column_index, value in enumerate(values, start=1):
                set_cell_literal(worksheet.cell(row=output_row, column=column_index), value)
            output_row += 1

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
    for column_index in (12, 13, 14):
        worksheet.column_dimensions[openpyxl.utils.get_column_letter(column_index)].hidden = True


def extract_media_id(url):
    text = clean_text(url)
    for pattern in (r"/video/(\d+)", r"/photo/(\d+)"):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def parse_count_value(raw):
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        number = float(raw)
        return str(int(number)) if number.is_integer() else str(int(number))

    text = clean_text(str(raw)).replace(",", "").replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"\d+", text):
        return text

    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMB])", text, flags=re.IGNORECASE)
    if match:
        number = float(match.group(1))
        suffix = match.group(2).upper()
        multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
        return str(int(number * multiplier))

    if re.fullmatch(r"\d+\.0+", text):
        return str(int(float(text)))
    return None


def empty_metrics():
    """Metrics for a scan that confirmed nothing: every value unknown (blank)."""
    return {metric: None for metric in COUNT_FIELD_MAP}


def metric_or_none(value):
    """A confirmed count as int, or None when the value is blank/unknown/not a number."""
    if value is None or isinstance(value, bool):
        return None
    text = clean_text(value)
    if not text:
        return None
    number = metric_number(value)
    if number == 0 and not re.fullmatch(r"0+(?:[.,]0+)?", text.replace(" ", "")):
        return None
    return number


def metrics_from_stats_sources(*sources):
    """Read metrics from stats/statsV2 blobs and take the max per field."""
    field_values = {metric: [] for metric in COUNT_FIELD_MAP}

    def collect_from_dict(obj):
        if not isinstance(obj, dict):
            return
        blobs = []
        if isinstance(obj.get("stats"), dict):
            blobs.append(obj["stats"])
        if isinstance(obj.get("statsV2"), dict):
            blobs.append(obj["statsV2"])
        if not blobs:
            blobs.append(obj)
        for blob in blobs:
            if not isinstance(blob, dict):
                continue
            for metric, field in COUNT_FIELD_MAP.items():
                parsed = parse_count_value(blob.get(field))
                if parsed is not None:
                    field_values[metric].append(int(parsed))

    for source in sources:
        collect_from_dict(source)

    metrics = {}
    for metric in COUNT_FIELD_MAP:
        if not field_values[metric]:
            # Some items omit collectCount: Saves is unknown (blank), not a confirmed 0.
            if metric == "Saves":
                metrics[metric] = None
                continue
            return None
        metrics[metric] = str(max(field_values[metric]))
    return metrics


def extract_embedded_state_blobs(content):
    blobs = []
    for pattern in EMBEDDED_STATE_SCRIPT_PATTERNS:
        for match in re.finditer(pattern, content or "", flags=re.DOTALL | re.IGNORECASE):
            data = json_loads_safe(match.group(1))
            if data:
                blobs.append(data)
    return blobs


def universal_detail_scope_metrics(scope, media_id=""):
    if not isinstance(scope, dict):
        return None
    for key, value in scope.items():
        key_norm = normalize_text(key).casefold()
        if not any(marker in key_norm for marker in UNIVERSAL_DETAIL_KEY_MARKERS):
            continue
        if not isinstance(value, dict):
            continue
        item_struct = item_struct_from_scope_value(value)
        if not isinstance(item_struct, dict):
            continue
        item_id = clean_text(item_struct.get("id") or item_struct.get("awemeId"))
        if media_id and item_id != media_id:
            continue
        metrics = metrics_from_stats_sources(item_struct)
        if metrics:
            return metrics
    return None


def parse_counts_from_universal_data(data, media_id=""):
    if not isinstance(data, dict):
        return None

    scope = data.get("__DEFAULT_SCOPE__")
    metrics = universal_detail_scope_metrics(scope, media_id=media_id)
    if metrics:
        return metrics
    return None


def parse_counts_from_sigi_state(data, media_id=""):
    if not isinstance(data, dict):
        return None

    item_module = data.get("ItemModule")
    if isinstance(item_module, dict) and media_id:
        for module_key in (media_id, str(media_id)):
            item = item_module.get(module_key)
            if isinstance(item, dict):
                item_id = clean_text(item.get("id") or item.get("awemeId") or item.get("itemId"))
                if item_id and item_id != media_id:
                    continue
                metrics = metrics_from_stats_sources(item)
                if metrics:
                    return metrics

    if media_id:
        for obj in iter_nested_dicts(data):
            if not isinstance(obj, dict):
                continue
            item_id = clean_text(obj.get("id") or obj.get("awemeId") or obj.get("itemId"))
            if item_id != media_id:
                continue
            metrics = metrics_from_stats_sources(obj)
            if metrics:
                return metrics
    return None


def parse_counts_from_embedded_json(content, media_id=""):
    for blob in extract_embedded_state_blobs(content):
        metrics = parse_counts_from_universal_data(blob, media_id=media_id)
        if metrics:
            return metrics, True
        metrics = parse_counts_from_sigi_state(blob, media_id=media_id)
        if metrics:
            return metrics, True
    return None, False


def validate_metrics(data):
    """Views must be known; an unknown (None) engagement metric is skipped, not treated as 0."""
    if not data:
        return False

    values = {}
    for metric in COUNT_FIELD_MAP:
        raw = data.get(metric)
        if raw is None:
            values[metric] = None
            continue
        parsed = parse_count_value(raw)
        if parsed is None:
            return False
        values[metric] = int(parsed)

    views = values["Views"]
    if views is None or views < 0:
        return False

    engagement = [values[metric] for metric in ("Likes", "Comments", "Saves", "Shares") if values[metric] is not None]
    if views == 0 and sum(engagement) > 0:
        return False
    return all(value >= 0 and not (views > 0 and value > views * 5) for value in engagement)


def counts_match(left, right):
    if not left or not right:
        return False
    return all(metric_or_none(left.get(metric)) == metric_or_none(right.get(metric)) for metric in COUNT_FIELD_MAP)


def parse_counts(content, media_id):
    """Read metrics only from the embedded item whose id is media_id."""
    if media_id:
        metrics, found = parse_counts_from_embedded_json(content, media_id=media_id)
        if found and metrics and validate_metrics(metrics):
            return metrics, True
    return empty_metrics(), False


def json_loads_safe(raw_value):
    if not isinstance(raw_value, str):
        return None
    candidates = [raw_value]
    unescaped = html.unescape(raw_value)
    if unescaped != raw_value:
        candidates.append(unescaped)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def iter_nested_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_nested_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_nested_dicts(child)


def author_name_from_object(obj, profile_username="", *, require_username_match=True):
    if not isinstance(obj, dict):
        return ""

    unique_id = clean_text(obj.get("uniqueId") or obj.get("unique_id") or obj.get("uniqueID"))
    if require_username_match:
        if not profile_username:
            return ""
        if normalize_text(unique_id.lstrip("@")) != normalize_text(profile_username.lstrip("@")):
            return ""
        handle = profile_username
    else:
        if not unique_id:
            return ""
        handle = unique_id

    profile_url = f"https://www.tiktok.com/@{handle.lstrip('@')}"
    for key in ("nickname", "nickName", "authorName", "name", "displayName"):
        candidate = clean_text(obj.get(key))
        if (
            candidate
            and not is_generated_username_channel(candidate, profile_url)
            and not is_generic_tiktok_channel_name(candidate)
        ):
            validated = valid_channel_candidate(candidate, handle)
            if validated:
                return validated
    return ""


def iter_matching_item_structs(content, media_id=""):
    media_id = clean_text(media_id)
    if not media_id:
        return

    for blob in extract_embedded_state_blobs(content):
        scope = blob.get("__DEFAULT_SCOPE__")
        if isinstance(scope, dict):
            for value in scope.values():
                item_struct = item_struct_from_scope_value(value)
                if not isinstance(item_struct, dict):
                    continue
                item_id = clean_text(item_struct.get("id") or item_struct.get("awemeId"))
                if item_id == media_id:
                    yield item_struct

        item_module = blob.get("ItemModule")
        if isinstance(item_module, dict):
            item = item_module.get(media_id) or item_module.get(str(media_id))
            if isinstance(item, dict):
                item_id = clean_text(item.get("id") or item.get("awemeId") or item.get("itemId"))
                if not item_id or item_id == media_id:
                    yield item

        for obj in iter_nested_dicts(blob):
            if not isinstance(obj, dict):
                continue
            item_id = clean_text(obj.get("id") or obj.get("awemeId") or obj.get("itemId"))
            if item_id != media_id:
                continue
            if isinstance(obj.get("author"), dict) or "stats" in obj or "statsV2" in obj:
                yield obj


def channel_name_from_item_struct(item_struct):
    if not isinstance(item_struct, dict):
        return ""
    author = item_struct.get("author")
    if isinstance(author, dict):
        candidate = author_name_from_object(author, require_username_match=False)
        if candidate:
            return candidate
    return author_name_from_object(item_struct, require_username_match=False)


def parse_channel_name_from_page(content, profile_username="", media_id=""):
    if media_id:
        for item_struct in iter_matching_item_structs(content, media_id):
            candidate = channel_name_from_item_struct(item_struct)
            if candidate:
                return candidate
    return parse_channel_name(content, profile_username)


TIKTOK_NOT_FOUND_PHRASES = (
    "couldn't find this account",
    "couldnt find this account",
    "couldn't find this video",
    "video currently unavailable",
    "this video is currently unavailable",
    "page not available",
    "page unavailable",
    "video unavailable",
    "không tìm thấy tài khoản này",
    "không tìm thấy video này",
    "khong tim thay tai khoan nay",
    "khong tim thay video nay",
    "video này hiện không khả dụng",
    "trang không khả dụng",
    "trang này không khả dụng",
    "this post isn't available",
    "this post is not available",
    "unable to find",
)

# TikTok answers HTTP 200 for deleted/nonexistent posts; the only signal is the
# item status code in the embedded state. Only "item not found" means the post
# is gone; private/restricted codes are deliberately not treated as deleted.
TIKTOK_ITEM_NOT_FOUND_STATUS_CODES = frozenset({10204})


def _status_code_value(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = clean_text(value)
    return int(text) if text.isdigit() else None


def _detail_status_codes(container):
    if not isinstance(container, dict):
        return []
    return [
        code
        for code in (
            _status_code_value(container.get(key))
            for key in ("statusCode", "status_code")
        )
        if code is not None
    ]


def embedded_item_status_codes(content):
    """Status codes TikTok reports for the requested post in its embedded state."""
    codes = []
    for blob in extract_embedded_state_blobs(content):
        if not isinstance(blob, dict):
            continue
        # <script id="api-data">{"videoDetail": {"statusCode": ...}}
        for key in ("videoDetail", "photoDetail"):
            codes.extend(_detail_status_codes(blob.get(key)))
        # __UNIVERSAL_DATA_FOR_REHYDRATION__ -> __DEFAULT_SCOPE__["webapp.video-detail"]
        scope = blob.get("__DEFAULT_SCOPE__")
        if isinstance(scope, dict):
            for key, value in scope.items():
                key_norm = normalize_text(key).casefold()
                if any(marker in key_norm for marker in UNIVERSAL_DETAIL_KEY_MARKERS):
                    codes.extend(_detail_status_codes(value))
    return codes


def is_tiktok_item_not_found(content):
    return any(code in TIKTOK_ITEM_NOT_FOUND_STATUS_CODES for code in embedded_item_status_codes(content))

TIKTOK_ERROR_HINT_WORDS = (
    "couldn't",
    "couldnt",
    "unavailable",
    "trending creators",
    "discover more",
    "try searching",
    "log in",
    "back to home",
    "go home",
)


def is_tiktok_error_page(content):
    if is_tiktok_item_not_found(content):
        return True
    visible_html =re.sub(r"<script\b[^>]*>.*?</script>", " ", content or "", flags=re.DOTALL | re.IGNORECASE)
    visible_html = re.sub(r"<style\b[^>]*>.*?</style>", " ", visible_html, flags=re.DOTALL | re.IGNORECASE)
    surfaces = []
    heading_pattern = re.compile(
        r"<(?P<tag>title|h[1-3])\b[^>]*>(?P<body>.*?)</(?P=tag)>",
        flags=re.DOTALL | re.IGNORECASE,
    )
    for match in heading_pattern.finditer(visible_html):
        surfaces.append(strip_html_text(match.group("body")).casefold())

    alert_pattern = re.compile(
        r"<(?P<tag>[a-z0-9]+)\b(?P<attrs>[^>]*(?:role=[\"']alert[\"']|(?:class|id|data-e2e)=[\"'][^\"']*(?:error|not-found|unavailable|PMsgTitle)[^\"']*[\"'])[^>]*)>"
        r"(?P<body>.*?)</(?P=tag)>",
        flags=re.DOTALL | re.IGNORECASE,
    )
    for match in alert_pattern.finditer(visible_html):
        surfaces.append(strip_html_text(match.group("body")).casefold())

    return any(
        phrase in surface
        for surface in surfaces
        for phrase in TIKTOK_NOT_FOUND_PHRASES
    )


def looks_like_error_text(value):
    text = str(value or "").lower().replace("\u2019", "'").replace("\u2018", "'")
    if not text:
        return False
    if any(phrase in text for phrase in TIKTOK_NOT_FOUND_PHRASES):
        return True
    if any(word in text for word in TIKTOK_ERROR_HINT_WORDS):
        return True
    return False


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
        or len(candidate) > 80
        or looks_like_error_text(candidate)
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

    # Photo posts often expose only the embedded "author": {...} object
    author_blocks_quoted = re.finditer(
        r'"author"\s*:\s*(\{[^{}]*"uniqueId"\s*:\s*"([^"]+)"[^{}]*\})',
        content,
    )
    for block_match in author_blocks_quoted:
        if normalize_text(block_match.group(2).lstrip("@")) != normalize_text(profile_username.lstrip("@")):
            continue
        data = json_loads_safe(block_match.group(1))
        candidate = author_name_from_object(data, profile_username)
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


def parse_profile_channel_name(content, profile_username, media_id=""):
    candidate = parse_channel_name_from_page(content, profile_username, media_id=media_id)
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


def fetch_profile_channel_name_request(profile_username, timeout=DEFAULT_REQUEST_TIMEOUT):
    username = clean_text(profile_username).lstrip("@")
    if not username:
        return ""
    profile_url = f"https://www.tiktok.com/@{username}"
    try:
        _final_url, content = fetch_tiktok_html(profile_url, timeout=timeout)
        return parse_profile_channel_name(content, username)
    except Exception:
        return ""


def is_usable_channel_name(name, url=""):
    text = clean_text(name)
    if not text or text == "Lỗi" or is_numeric_channel_garbage(text):
        return False
    if is_generated_username_channel(text, url) or is_generic_tiktok_channel_name(text):
        return False
    return True


def author_unique_id_from_post(url, resolved_url="", timeout=DEFAULT_REQUEST_TIMEOUT):
    media_id = extract_media_id(resolved_url) or extract_media_id(url)
    if not media_id:
        return ""
    for candidate_url in (resolved_url, url):
        normalized = normalize_tiktok_url(clean_text(candidate_url))
        if not normalized:
            continue
        try:
            _final_url, content = fetch_tiktok_html(normalized, timeout=timeout)
        except Exception:
            continue
        for item_struct in iter_matching_item_structs(content, media_id):
            author = item_struct.get("author")
            if isinstance(author, dict):
                unique_id = clean_text(author.get("uniqueId") or author.get("unique_id"))
                if unique_id:
                    return unique_id
    return ""


def enrich_channel_name(
    url,
    channel_name,
    *,
    resolved_url="",
    existing_channel="",
    channel_cache=None,
    channel_overrides=None,
    profile_lookup_attempted=None,
    cache_lock=None,
    timeout=DEFAULT_REQUEST_TIMEOUT,
    allow_network=True,
):
    """Best channel name for a scraped post.

    Blocking when allow_network is True (profile page fetch), so async code must
    run it in an executor. With allow_network=False it only uses the parsed
    name, overrides, cache, the existing sheet value and the @handle fallback.
    Pass shared (possibly empty) cache/set objects to dedupe lookups across rows.
    """
    source_url = normalize_tiktok_url(url)
    lookup_url = normalize_tiktok_url(resolved_url or url)
    quality_url = lookup_url or source_url
    username = username_for_channel_lookup(source_url, lookup_url)
    overrides = channel_overrides or {}
    parsed = clean_text(channel_name)
    key = username.casefold()

    def locked(action):
        if cache_lock:
            with cache_lock:
                return action()
        return action()

    def read_cache():
        if channel_cache is None or not username:
            return ""
        return clean_text(locked(lambda: channel_cache.get(key, "")))

    def write_cache(name):
        if channel_cache is not None and username and name:
            locked(lambda: channel_cache.__setitem__(key, name))

    def claim_profile_lookup():
        """True when this call should fetch the profile (first caller per username)."""
        if not username:
            return False
        if profile_lookup_attempted is None:
            return True

        def claim():
            if key in profile_lookup_attempted:
                return False
            profile_lookup_attempted.add(key)
            return True

        return locked(claim)

    resolved = resolve_channel_name(source_url, parsed.lstrip("@") if parsed.startswith("@") else parsed, overrides)
    if not resolved and parsed.startswith("@") and is_usable_channel_name(parsed, quality_url):
        resolved = parsed
    if is_usable_channel_name(resolved, quality_url):
        write_cache(resolved)
        return resolved

    cached = read_cache()
    if is_usable_channel_name(cached, quality_url):
        return cached

    # The sheet already holds a real nickname: a profile fetch could not improve it.
    existing = clean_text(existing_channel)
    if channel_name_quality(existing, quality_url) == 2:
        return existing

    if allow_network and claim_profile_lookup():
        fetched = fetch_profile_channel_name_request(username, timeout=timeout)
        if not fetched and is_generated_username_channel(username) and not session_uses_proxy():
            alt_handle = author_unique_id_from_post(source_url, lookup_url, timeout=timeout)
            if alt_handle and alt_handle.casefold() != key:
                fetched = fetch_profile_channel_name_request(alt_handle, timeout=timeout)
        resolved = resolve_channel_name(source_url, fetched, overrides) or fetched
        if is_usable_channel_name(resolved, quality_url):
            write_cache(resolved)
            return resolved

    if username:
        handle = f"@{username.lstrip('@')}"
        if allow_network:
            write_cache(handle)
        return handle

    return ""


def seed_channel_cache_from_workbook(rows_to_process, sheet_contexts, channel_cache):
    for item in rows_to_process:
        username = extract_profile_username(item.get("url", ""))
        if not username:
            continue
        context = sheet_contexts.get(item["sheet_name"])
        if not context:
            continue
        channel_col = context["columns"].get("channel")
        if not channel_col:
            continue
        raw = clean_text(context["worksheet"].cell(row=item["row"], column=channel_col).value)
        if raw and raw != "Lỗi" and is_usable_channel_name(raw, item["url"]):
            channel_cache[username.casefold()] = raw


def channel_name_for_sheet(
    url,
    channel_name,
    *,
    resolved_url="",
    status="Success",
    channel_cache=None,
    channel_overrides=None,
    cache_lock=None,
):
    """Channel value to write; never does network I/O (lookups happen in the workers)."""
    source_url = normalize_tiktok_url(url)
    resolved = enrich_channel_name(
        source_url,
        channel_name,
        resolved_url=resolved_url,
        channel_cache=channel_cache,
        channel_overrides=channel_overrides,
        cache_lock=cache_lock,
        allow_network=False,
    )
    lookup_url = normalize_tiktok_url(resolved_url or url)
    if is_usable_channel_name(resolved, lookup_url or source_url):
        return resolved
    username = username_for_channel_lookup(source_url, lookup_url)
    if status == "Success" and username and not is_generated_username_channel(username):
        return f"@{username.lstrip('@')}"
    return "Lỗi"


def channel_name_quality(name, url=""):
    """Rank a channel cell so re-scrapes never downgrade a better existing value.

    2 = real nickname, 1 = @handle fallback, 0 = failed/garbage/empty.
    """
    text = clean_text(name)
    if not is_usable_channel_name(text, url):
        return 0
    return 1 if text.startswith("@") else 2


def scrape_metric_details(data):
    """Log/broadcast metrics: int for confirmed counts, None for unknown."""
    data = data or {}
    return {metric_key: metric_or_none(data.get(data_key)) for metric_key, data_key in METRIC_KEYS.items()}


def format_metric_log_plain(data):
    metrics = {key: "—" if value is None else value for key, value in scrape_metric_details(data).items()}
    return (
        f"Lượt xem {metrics['views']} • Tim {metrics['likes']} • Bình luận {metrics['comments']} • "
        f"Lưu {metrics['saves']} • Chia sẻ {metrics['shares']}"
    )


def extract_profile_username(url):
    match = re.search(r"tiktok\.com/@([^/?]+)", url or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


def username_for_channel_lookup(source_url, resolved_url=""):
    for candidate in (resolved_url, source_url):
        normalized = normalize_tiktok_url(clean_text(candidate))
        if not normalized:
            continue
        username = extract_profile_username(normalized)
        if username:
            return username
    return ""


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

    profile_url = normalize_tiktok_url(f"https://www.tiktok.com/@{profile_username}")
    if not profile_url:
        return ""
    try:
        await navigate_tiktok_page(page, profile_url, timeout_ms=timeout_ms)
        if not is_scrapable_tiktok_url(page.url):
            return ""
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


def item_struct_from_scope_value(value):
    if not isinstance(value, dict):
        return None
    item_info = value.get("itemInfo")
    if isinstance(item_info, dict):
        item_struct = item_info.get("itemStruct")
        if isinstance(item_struct, dict):
            return item_struct
    item_struct = value.get("itemStruct")
    if isinstance(item_struct, dict):
        return item_struct
    extra_info = value.get("extra_info")
    if isinstance(extra_info, dict):
        nested = item_struct_from_scope_value(extra_info)
        if nested:
            return nested
        nested = extra_info.get("itemStruct")
        if isinstance(nested, dict):
            return nested
    return None


def request_html_has_metric_hints(content):
    return bool(REQUEST_METRIC_HINT_PATTERN.search(content or ""))


def is_hidden_stats_status(status):
    """True khi post TikTok ẩn số liệu (không phải lỗi quét mạng/HTTP)."""
    text = clean_text(status)
    if not text:
        return False
    if text in (STATUS_TIKTOK_NO_STATS, STATUS_TIKTOK_NO_STATS_LEGACY):
        return True
    lowered = text.casefold()
    return (
        "ẩn số liệu" in lowered
        or "tiktok không trả số liệu" in lowered
        or "tiktok không trả lượt xem" in lowered
    )


def should_clear_stale_metrics(status):
    text = clean_text(status)
    lowered = text.casefold()
    return (
        is_hidden_stats_status(status)
        or lowered == "error: trang tiktok không khả dụng"
        or bool(re.fullmatch(r"error:\s*http\s+(?:404|410)(?:\s+.*)?", lowered))
    )


def terminal_status_priority(status):
    if is_hidden_stats_status(status):
        return 40
    text = clean_text(status).casefold()
    if re.fullmatch(r"error:\s*http\s+410(?:\s+.*)?", text):
        return 30
    if text == "error: trang tiktok không khả dụng":
        return 20
    if re.fullmatch(r"error:\s*http\s+404(?:\s+.*)?", text):
        return 10
    return 0


def stronger_terminal_result(current, candidate):
    if current is None:
        return candidate
    if terminal_status_priority(candidate[2]) > terminal_status_priority(current[2]):
        return candidate
    return current


def item_struct_explicitly_hides_stats(item_struct):
    hidden_when_true = {
        "hidestats",
        "hideviewcount",
        "isstatshidden",
        "statshidden",
        "viewcounthidden",
    }
    hidden_when_false = {
        "isstatsvisible",
        "statisticsvisible",
        "statsvisible",
        "viewcountvisible",
    }
    visibility_fields = {"statisticsvisibility", "statsvisibility"}

    for obj in iter_nested_dicts(item_struct):
        for key, value in obj.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized_key not in hidden_when_true | hidden_when_false | visibility_fields:
                continue
            normalized_value = clean_text(value).casefold()
            if normalized_key in hidden_when_true and (
                value is True or normalized_value in {"1", "true", "hidden"}
            ):
                return True
            if normalized_key in hidden_when_false and (
                value is False or normalized_value in {"0", "false", "hidden"}
            ):
                return True
            if normalized_key in visibility_fields and normalized_value in {"hidden", "private", "disabled"}:
                return True
    return False


def no_metrics_status(content, media_id=""):
    """Only classify hidden stats when the matching item says so explicitly."""
    if media_id:
        for item_struct in iter_matching_item_structs(content, media_id):
            if item_struct_explicitly_hides_stats(item_struct):
                return STATUS_TIKTOK_NO_STATS
    return STATUS_METRICS_UNREADABLE


def media_redirect_mismatch(source_url, final_url):
    source_media_id = extract_media_id(source_url)
    final_media_id = extract_media_id(final_url)
    return bool(source_media_id and final_media_id != source_media_id)


def build_request_url_candidates(url):
    url = normalize_tiktok_url(url)
    candidates = []
    seen = set()

    def add(candidate):
        normalized = normalize_tiktok_url(candidate) if candidate else ""
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    add(url)
    base = url.split("?")[0]
    media_id = extract_media_id(url)
    profile_username = extract_profile_username(url)
    if media_id and "/photo/" in url.lower():
        if "_r=1" not in url.lower():
            add(f"{base}?_r=1")
        if profile_username:
            add(f"https://www.tiktok.com/@{profile_username}/video/{media_id}")
    add(base)

    return candidates


def parse_fetched_request_page(source_url, final_url, content):
    empty = empty_metrics()
    source_media_id = extract_media_id(source_url)
    final_media_id = extract_media_id(final_url)
    if media_redirect_mismatch(source_url, final_url):
        return empty, "", STATUS_MEDIA_REDIRECT_MISMATCH

    media_id = source_media_id or final_media_id
    profile_username = extract_profile_username(source_url) or extract_profile_username(final_url)
    if not media_id:
        channel_name = ""
        if profile_username and not is_generated_username_channel(profile_username):
            channel_name = f"@{profile_username.lstrip('@')}"
        return empty, channel_name, STATUS_METRICS_UNREADABLE

    metrics, found = parse_counts(content, media_id=media_id)
    channel_name = parse_profile_channel_name(content, profile_username, media_id=media_id)
    if not channel_name and profile_username and not is_generated_username_channel(profile_username):
        channel_name = f"@{profile_username.lstrip('@')}"
    if found:
        return metrics, channel_name, "Success"
    if is_tiktok_error_page(content):
        return empty, "", "Error: Trang TikTok không khả dụng"
    return empty, channel_name, no_metrics_status(content, media_id=media_id)


def hybrid_browser_worker_count(request_workers, total_links):
    if total_links <= 0:
        return 0
    if total_links <= 40:
        return min(max(request_workers // 2, 3), 8)
    return min(max(request_workers // 3, 5), MAX_BROWSER_FALLBACK_WORKERS)


def fetch_tiktok_html(url, timeout=DEFAULT_REQUEST_TIMEOUT):
    url = normalize_tiktok_url(url)
    if not url:
        raise ValueError("URL TikTok không hợp lệ.")
    wait_if_request_blocked()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.tiktok.com/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        },
    )
    with _request_semaphore:
        with urlopen_request(request, timeout=timeout, redirect_validator=is_scrapable_tiktok_url) as response:
            final_url = normalize_tiktok_url(response.geturl())
            if not final_url:
                raise ValueError("Chuyển hướng TikTok tới URL không được phép.")
            return final_url, response.read().decode("utf-8", errors="replace")


class _RequestScrapeOutcome:
    """Running best/last result across the HTTP candidates of one link."""

    def __init__(self):
        self.data = empty_metrics()
        self.channel = ""
        self.status = STATUS_METRICS_UNREADABLE
        self.resolved_url = ""
        self.saw_metric_hints = False
        self.best_success = None
        self.best_terminal = None

    def note_terminal(self, data, channel, status):
        if should_clear_stale_metrics(status):
            self.best_terminal = stronger_terminal_result(
                self.best_terminal, (data, channel, status, self.resolved_url)
            )

    def note_fetch_error(self, error):
        if isinstance(error, urllib.error.HTTPError):
            self.status = f"Error: HTTP {error.code}"
            self.note_terminal(self.data, self.channel, self.status)
            if error.code in (403, 429):
                note_request_rate_limit(error.code)
                release_thread_proxy()
            return
        self.status = f"Error: {error}"
        if is_transient_network_status(self.status):
            note_network_failure()

    def note_page(self, source_url, final_url, content):
        """Parse one fetched page; return True when it is a final success."""
        note_network_success()
        if final_url and not media_redirect_mismatch(source_url, final_url):
            self.resolved_url = final_url
        if request_html_has_metric_hints(content):
            self.saw_metric_hints = True
        data, channel_name, status = parse_fetched_request_page(source_url, final_url, content)
        self.data, self.channel, self.status = data, channel_name, status
        self.note_terminal(data, channel_name, status)
        if status != "Success":
            return False
        if self.best_success is None or (channel_name and not self.best_success[1]):
            self.best_success = (data, channel_name, status, final_url)
        return bool(channel_name)

    def result(self):
        chosen = self.best_success or self.best_terminal
        if chosen:
            data, channel_name, status, final_url = chosen
            return data, channel_name, status, self.saw_metric_hints, final_url
        return self.data, self.channel, self.status, self.saw_metric_hints, self.resolved_url


def _scrape_link_request_impl(url, timeout=DEFAULT_REQUEST_TIMEOUT):
    url = normalize_tiktok_url(url)
    outcome = _RequestScrapeOutcome()
    try:
        candidate_limit = request_candidate_limit()
        candidates = build_request_url_candidates(url)[:candidate_limit]

        for candidate in candidates:
            final_url = content = None
            # A page that hints at metrics but did not parse is often a shell: re-fetch once.
            for attempt in range(2):
                if attempt:
                    if not request_html_has_metric_hints(content):
                        break
                    time.sleep(request_shell_retry_delay())
                try:
                    final_url, content = fetch_tiktok_html(candidate, timeout=timeout)
                except Exception as error:
                    outcome.note_fetch_error(error)
                    final_url = None
                    break
                if outcome.note_page(url, final_url, content):
                    return outcome.result()
            if final_url is None:
                continue

            redirect_candidate = normalize_tiktok_url(final_url.split("?")[0])
            if (
                redirect_candidate
                and not media_redirect_mismatch(url, final_url)
                and redirect_candidate not in candidates
                and len(candidates) < candidate_limit
            ):
                candidates.append(redirect_candidate)

        return outcome.result()
    except Exception as error:
        status = f"Error: HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else f"Error: {error}"
        return outcome.data, outcome.channel, status, outcome.saw_metric_hints, outcome.resolved_url


def scrape_link_with_retries_request(
    url,
    retries=DEFAULT_RETRIES,
    timeout=DEFAULT_REQUEST_TIMEOUT,
    channel_cache=None,
    channel_overrides=None,
    profile_lookup_attempted=None,
    cache_lock=None,
    existing_channel="",
):
    last_data = empty_metrics()
    last_status = "Error: Chưa chạy"
    last_channel = ""
    last_resolved_url = ""

    attempts_used = 0
    for attempt in range(retries + 1):
        if attempt > 0:
            if is_request_rate_limited_status(last_status):
                if session_uses_proxy():
                    time.sleep(0.4 + attempt * 0.35 + random.uniform(0.1, 0.25))
                else:
                    time.sleep(2.5 + attempt * 2.0 + random.uniform(0.5, 2.0))
            else:
                time.sleep(1.0 + attempt * 0.75 + random.uniform(0.2, 0.8))
        data, channel_name, status, saw_metric_hints, resolved_url = _scrape_link_request_impl(
            url, timeout=timeout
        )
        attempts_used = attempt + 1
        if resolved_url:
            last_resolved_url = resolved_url
        channel_name = enrich_channel_name(
            url,
            channel_name,
            resolved_url=resolved_url if status == "Success" else "",
            existing_channel=existing_channel,
            channel_cache=channel_cache,
            channel_overrides=channel_overrides,
            profile_lookup_attempted=profile_lookup_attempted,
            cache_lock=cache_lock,
            timeout=timeout,
        )
        last_data, last_channel, last_status = data, channel_name, status
        if status == "Success":
            return data, channel_name, status, attempts_used, last_resolved_url
        if should_clear_stale_metrics(status):
            return data, channel_name, status, attempts_used, last_resolved_url
        if is_request_rate_limited_status(status):
            release_thread_proxy()
            continue
        if is_transient_network_status(status):
            continue
        if status == STATUS_METRICS_UNREADABLE:
            continue
        if not saw_metric_hints:
            break

    return last_data, last_channel, last_status, attempts_used or 1, last_resolved_url


async def block_heavy_resources(route):
    request = route.request
    if request.is_navigation_request():
        if not is_scrapable_tiktok_url(request.url):
            await route.abort()
            return
        response = None
        try:
            # Routes are not re-entered for server redirects. Fetch one hop and
            # never fulfill a 30x: Chromium could otherwise follow it unchecked.
            response = await route.fetch(max_redirects=0, timeout=15000)
            if 300 <= response.status < 400:
                await route.abort()
            else:
                await route.fulfill(response=response)
        except Exception:
            await route.abort()
        finally:
            if response is not None:
                await response.dispose()
    elif request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def navigate_tiktok_page(page, url, *, timeout_ms=45000):
    """Resolve only allowed redirect hops, then perform a no-redirect navigation.

    The context's navigation route still checks the final refetch, so a changed
    redirect response fails closed instead of sending Chromium to a new host.
    """
    target = normalize_tiktok_url(url)
    if not target:
        raise ValueError("URL TikTok không hợp lệ.")
    deadline = time.monotonic() + timeout_ms / 1000
    for _hop in range(10):
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            raise TimeoutError("Hết thời gian kiểm tra chuyển hướng TikTok.")
        response = await page.context.request.get(
            target, max_redirects=0, timeout=min(15000, remaining_ms),
            headers={"Accept": "text/html", "Referer": "https://www.tiktok.com/"},
        )
        try:
            if 300 <= response.status < 400:
                location = response.headers.get("location")
                if response.status not in {301, 302, 303, 307, 308} or not location:
                    raise ValueError("Chuyển hướng TikTok không hợp lệ.")
                next_target = normalize_tiktok_url(urljoin(target, location))
                if not next_target:
                    raise ValueError("Chuyển hướng TikTok tới URL không được phép.")
                target = next_target
                continue
        finally:
            await response.dispose()
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            raise TimeoutError("Hết thời gian kiểm tra chuyển hướng TikTok.")
        return await page.goto(target, wait_until="domcontentloaded", timeout=remaining_ms)
    raise ValueError("Quá nhiều chuyển hướng TikTok.")


async def make_browser_context(browser, proxy_configs=None):
    context_kwargs = {
        "user_agent": DEFAULT_USER_AGENT,
        "viewport": {"width": 390, "height": 844},
        "locale": BROWSER_LOCALE,
        "timezone_id": BROWSER_TIMEZONE,
        "geolocation": BROWSER_GEOLOCATION,
        "permissions": ["geolocation"],
        "service_workers": "block",
    }
    configs = [item for item in (proxy_configs or []) if item and item.get("enabled")]
    if configs:
        proxy_config = random.choice(configs)
        context_kwargs["proxy"] = playwright_proxy_settings(proxy_config)
    context = await browser.new_context(**context_kwargs)
    await context.route("**/*", block_heavy_resources)
    return context


async def scrape_single_link(page, url, channel_cache=None, timeout_ms=45000):
    data = empty_metrics()
    channel_name = ""
    url = normalize_tiktok_url(url)
    if not url:
        return data, channel_name, "Error: URL TikTok không hợp lệ", ""
    profile_username = extract_profile_username(url)
    try:
        await navigate_tiktok_page(page, url, timeout_ms=timeout_ms)
        try:
            await page.wait_for_selector(
                'script#__UNIVERSAL_DATA_FOR_REHYDRATION__, script#SIGI_STATE, script#api-data',
                timeout=12000,
            )
        except Exception:
            pass

        # Resolve final URL after redirect (vt.tiktok.com/... -> www.tiktok.com/@user/...)
        page_url = url
        try:
            page_url = page.url or url
        except Exception:
            page_url = url
        page_url = normalize_tiktok_url(page_url)
        if not page_url:
            return data, channel_name, "Error: URL chuyển hướng không thuộc TikTok.", ""
        if not profile_username:
            profile_username = extract_profile_username(page_url)
        source_media_id = extract_media_id(url)
        final_media_id = extract_media_id(page_url)
        if media_redirect_mismatch(url, page_url):
            return data, channel_name, STATUS_MEDIA_REDIRECT_MISMATCH, url
        media_id = source_media_id or final_media_id
        if not media_id:
            if profile_username and not is_generated_username_channel(profile_username):
                channel_name = f"@{profile_username.lstrip('@')}"
            return data, channel_name, STATUS_METRICS_UNREADABLE, page_url

        previous_metrics = None
        found = False
        content = ""
        for _ in range(24):
            content = await page.content()

            candidate, candidate_found = parse_counts(content, media_id=media_id)
            if candidate_found and validate_metrics(candidate):
                if previous_metrics and counts_match(previous_metrics, candidate):
                    data = candidate
                    found = True
                    break
                previous_metrics = candidate
            elif is_tiktok_error_page(content):
                return data, channel_name, "Error: Trang TikTok không khả dụng", page_url
            parsed_channel = parse_channel_name_from_page(content, profile_username, media_id=media_id)
            if parsed_channel:
                channel_name = parsed_channel
            if profile_username and not channel_name:
                dom_channel = await read_channel_name_from_dom(page, profile_username)
                if dom_channel and not is_generated_username_channel(dom_channel, url) and not is_generic_tiktok_channel_name(dom_channel):
                    channel_name = dom_channel
            await page.wait_for_timeout(500)

        if profile_username:
            needs_profile_channel = (
                not channel_name
                or is_generated_username_channel(channel_name, url)
                or is_generic_tiktok_channel_name(channel_name)
            )
            if needs_profile_channel:
                profile_channel = await read_profile_channel_name(page, profile_username, channel_cache=channel_cache)
                if profile_channel:
                    channel_name = profile_channel

        # Final guard against accidentally captured error text
        if looks_like_error_text(channel_name):
            channel_name = ""

        # Fallback to @handle when we can detect the profile but no nickname is parsed.
        # Auto-generated user-ID handles (user1234567890) stay empty -> mark as "Lỗi".
        if not channel_name and profile_username and not is_generated_username_channel(profile_username):
            channel_name = f"@{profile_username}"

        if not found and previous_metrics and validate_metrics(previous_metrics):
            data = previous_metrics
            found = True

        if not found:
            return data, channel_name, no_metrics_status(content, media_id=media_id), page_url

        return data, channel_name, "Success", page_url
    except Exception as error:
        return data, channel_name, f"Error: {str(error)}", url


async def scrape_with_retries(page, url, retries, channel_cache=None):
    last_data = empty_metrics()
    last_status = "Error: Chưa chạy"
    last_channel = ""
    last_resolved_url = ""

    for attempt in range(retries + 1):
        if attempt > 0:
            await asyncio.sleep(random.uniform(1.5, 3.5))
        data, channel_name, status, resolved_url = await scrape_single_link(page, url, channel_cache=channel_cache)
        if resolved_url:
            last_resolved_url = resolved_url
        last_data, last_channel, last_status = data, channel_name, status
        if status == "Success":
            return data, channel_name, status, attempt + 1, last_resolved_url
        if should_clear_stale_metrics(status):
            return data, channel_name, status, attempt + 1, last_resolved_url

    return last_data, last_channel, last_status, retries + 1, last_resolved_url


def select_fallback_result(request_result, browser_result):
    if browser_result.get("status") == "Success":
        return browser_result
    if should_clear_stale_metrics(request_result.get("status")) and not should_clear_stale_metrics(
        browser_result.get("status")
    ):
        return request_result
    return browser_result


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
):
    RECYCLE_AFTER = 100
    loop = asyncio.get_running_loop()
    display_worker = worker_label if worker_label is not None else worker_id
    browser_proxy_configs = proxy_configs if proxy_configs is not None else ([proxy_config] if proxy_config else [])

    async def make_context():
        return await make_browser_context(browser, proxy_configs=browser_proxy_configs)

    current_item = None

    if startup_semaphore is not None:
        async with startup_semaphore:
            await asyncio.sleep(min(worker_id - 1, 10) * 0.25)
            try:
                context = await make_context()
                page = await context.new_page()
            except Exception as error:
                if websocket_manager:
                    await websocket_manager.broadcast_log(f"Worker {display_worker} không khởi tạo được context: {str(error)}")
                return
    else:
        context = await make_context()
        page = await context.new_page()

    links_in_current_context = 0

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
                data, channel_name, status, attempts, resolved_url = await scrape_with_retries(page, item["url"], retries, channel_cache=channel_cache)
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
                enrich_channel_name,
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


def write_result(
    sheet_contexts,
    item,
    data,
    channel_name,
    status,
    *,
    resolved_url="",
    channel_cache=None,
    channel_overrides=None,
    cache_lock=None,
):
    """Write one scan result into its row. Pure workbook work: no network I/O."""
    context = sheet_contexts[item["sheet_name"]]
    sheet = context["worksheet"]
    columns = context["columns"]
    row_index = item["row"]
    update_time = format_display_datetime()

    # Success writes confirmed numbers (unknown ones blank). Terminal "no data"
    # statuses clear stale numbers to blank; other failures keep existing cells.
    if status == "Success" or should_clear_stale_metrics(status):
        for metric_key, data_key in METRIC_KEYS.items():
            column_index = columns.get(metric_key)
            if column_index:
                value = metric_or_none((data or {}).get(data_key)) if status == "Success" else None
                sheet.cell(row=row_index, column=column_index).value = value

    if columns.get("scan_status"):
        set_cell_literal(sheet.cell(row=row_index, column=columns["scan_status"]), clean_text(status))
    if status == "Success" and resolved_url and columns.get("resolved_url"):
        sheet.cell(row=row_index, column=columns["resolved_url"]).value = normalize_tiktok_url(resolved_url)
    if status == "Success" and columns.get("source_url"):
        sheet.cell(row=row_index, column=columns["source_url"]).value = normalize_tiktok_url(item["url"])

    if columns.get("channel"):
        channel_value = channel_name_for_sheet(
            item["url"],
            channel_name,
            resolved_url=resolved_url if status == "Success" else "",
            status=status,
            channel_cache=channel_cache,
            channel_overrides=channel_overrides,
            cache_lock=cache_lock,
        )
        existing = clean_text(sheet.cell(row=row_index, column=columns["channel"]).value)
        if status != "Success" and channel_name_quality(existing, item["url"]) > 0:
            channel_value = existing
        elif channel_name_quality(existing, item["url"]) > channel_name_quality(channel_value, item["url"]):
            channel_value = existing
        set_cell_literal(sheet.cell(row=row_index, column=columns["channel"]), channel_value)

    # Chỉ cập nhật timestamp khi quét thành công — tránh hiểu nhầm đã quét OK.
    if status == "Success" and columns.get("last_update"):
        sheet.cell(row=row_index, column=columns["last_update"]).value = update_time


async def save_workbook(workbook, file_path, websocket_manager=None):
    # Shield the complete save + replace transaction so cancellation cannot
    # close/mutate the workbook while its background writer still uses it.
    # to_thread runs on the loop's default executor, never a run's request pool.
    write_task = asyncio.create_task(asyncio.to_thread(save_workbook_atomic, workbook, file_path))
    try:
        await finish_pending_task(write_task)
        return True, None
    except PermissionError:
        if websocket_manager:
            await websocket_manager.broadcast_log("CẢNH BÁO: File Excel đang mở, không thể ghi đè. Vui lòng đóng file rồi quét lại hoặc chờ lần lưu tiếp theo.")
        return False, "permission"
    except Exception as error:
        if websocket_manager:
            await websocket_manager.broadcast_log(f"CẢNH BÁO: Lỗi lưu file ({str(error)})")
        return False, "other"


async def finish_pending_task(task):
    """Wait for owned cleanup/write work before propagating cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError()
    return result


def format_scrape_result_log(result, processed, total):
    url = clean_text(result.get("url", ""))
    worker = result.get("worker", "?")
    status = clean_text(result.get("status", ""))
    attempts = result.get("attempts", 0)
    elapsed = float(result.get("elapsed") or 0)
    data = result.get("data") or {}
    rows = result.get("rows") or []
    channel = clean_text(result.get("channel_name", "")) or "—"
    row_refs = ", ".join(
        f'{clean_text(row.get("sheet_name", ""))}#{row.get("row", "")}'
        for row in rows[:6]
    )
    if len(rows) > 6:
        row_refs = f"{row_refs} +{len(rows) - 6}"

    base = {
        "processed": processed,
        "total": total,
        "worker": worker,
        "elapsed": round(elapsed, 1),
        "rows": row_refs or "—",
        "url": url,
    }

    if status == "Success":
        metrics = scrape_metric_details(data)
        message = (
            f"[{processed}/{total}] OK • {channel} • {format_metric_log_plain(data)} • "
            f"{elapsed:.1f}s • luồng {worker} • dòng {row_refs or '—'}"
        )
        details = {
            "kind": "scrape_ok",
            **base,
            "channel": channel,
            "metrics": metrics,
        }
        return message, "OK", details

    if is_hidden_stats_status(status):
        message = (
            f"[{processed}/{total}] Ẩn số liệu • {elapsed:.1f}s • luồng {worker} • "
            f"TikTok không trả lượt xem • dòng {row_refs or '—'}"
        )
        details = {
            "kind": "scrape_hidden",
            **base,
            "attempts": attempts,
            "status": status,
        }
        return message, "WARN", details

    message = (
        f"[{processed}/{total}] Lỗi • {elapsed:.1f}s • luồng {worker} • "
        f"thử {attempts} lần • {status} • dòng {row_refs or '—'}"
    )
    details = {
        "kind": "scrape_error",
        **base,
        "attempts": attempts,
        "status": status,
    }
    return message, "ERROR", details


def progress_payload(total, processed, success_count, error_count, worker_count, started_at, done=False, mode="full", partner="", phase="scanning", uses_browser=False, hidden_count=0):
    elapsed = max(time.perf_counter() - started_at, 0.001)
    rate = processed / elapsed * 60 if processed else 0
    remaining = max(total - processed, 0)
    eta_seconds = int((remaining / rate) * 60) if rate > 0 else None
    return {
        "total": total,
        "processed": processed,
        "success": success_count,
        "error": error_count,
        "hidden": hidden_count,
        "workers": worker_count,
        "rate": round(rate, 1),
        "etaSeconds": eta_seconds,
        "done": done,
        "mode": mode,
        "partner": partner,
        "phase": phase,
        "usesBrowser": uses_browser,
    }


def _existing_channel_value(sheet_contexts, item):
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
            "partners": item["partners"],
        })
        expected_media_id = clean_text(item.get("expected_media_id", ""))
        if expected_media_id and expected_media_id not in bucket["expected_media_ids"]:
            bucket["expected_media_ids"].append(expected_media_id)
        existing = _existing_channel_value(sheet_contexts, item)
        if channel_name_quality(existing, item["url"]) > channel_name_quality(bucket["existing_channel"], item["url"]):
            bucket["existing_channel"] = existing
    return bucket_order


class _TikTokScan:
    """Workbook, counters and caches of one run_scraper call."""

    def __init__(self, file_path, websocket_manager, *, sheet_name, selected_names, partner_label, save_every):
        self.file_path = file_path
        self.manager = websocket_manager
        self.selected_names = selected_names
        self.partner_label = partner_label
        self.mode = "partner" if selected_names else "full"
        self.workbook = openpyxl.load_workbook(file_path)
        cleared_total_sheets = clear_existing_total_rows(self.workbook, sheet_name=sheet_name)
        self.sheet_contexts = build_sheet_contexts(self.workbook, sheet_name=sheet_name)
        self.rows_to_process = collect_rows(self.workbook, selected_partners=selected_names, sheet_name=sheet_name)
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
        seed_channel_cache_from_workbook(self.rows_to_process, self.sheet_contexts, self.channel_cache)
        self.started_at = time.perf_counter()
        self.active_worker_count = 0
        self.processed = 0
        self.success_count = 0
        self.error_count = 0
        self.hidden_count = 0
        self.pending_save_count = 0
        self.save_skip_until_processed = 0
        self.last_status_broadcast = 0.0
        self.completed_sequences = set()

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
        result["channel_name"] = enrich_channel_name(
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
            "channelName": channel_name_for_sheet(
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
        if self.processed == self.total or now - self.last_status_broadcast > 0.25:
            await self.manager.broadcast_status(self.progress())
            self.last_status_broadcast = now

    async def autosave_if_due(self):
        if self.pending_save_count < self.save_every or self.save_skip_until_processed > self.processed:
            return
        saved, _reason = await save_workbook(self.workbook, self.file_path, self.manager)
        if saved:
            self.pending_save_count = 0
            if self.manager:
                await self.manager.broadcast_log(f"Đã lưu tạm workbook tại {self.processed}/{self.total} links.")
        else:
            # Keep the dirty count for cancellation/final-save retries.
            self.save_skip_until_processed = self.processed + self.save_every


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


async def _launch_browser(playwright):
    return await playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
        ],
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
    startup_semaphore = asyncio.Semaphore(min(browser_worker_count, 6)) if browser_worker_count else None
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
            result = await asyncio.wait_for(result_queue.get(), timeout=30.0)
        except asyncio.TimeoutError:
            stalled_seconds += 30
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


async def _close_browser(browser):
    if browser is None:
        return
    try:
        for ctx in list(browser.contexts):
            try:
                await ctx.close()
            except Exception:
                pass
    finally:
        try:
            await browser.close()
        except Exception:
            pass


async def _shutdown_scan(scan, workers, finalize_task, browser, executor):
    """Stop workers, persist unsaved results, then release browser, threads and proxies."""
    if finalize_task is not None and not finalize_task.done():
        finalize_task.cancel()
    for task in workers:
        if not task.done():
            task.cancel()
    # Workers shield and join their executor work before exiting. Never replace
    # the shared proxy pool while that HTTP work is alive.
    await asyncio.gather(*workers, *([finalize_task] if finalize_task is not None else []), return_exceptions=True)
    try:
        if scan.pending_save_count:
            saved, _reason = await save_workbook(scan.workbook, scan.file_path, scan.manager)
            if not saved:
                raise RuntimeError("Lưu file Excel khi dừng quét thất bại; kết quả mới chưa được lưu.")
    finally:
        # File errors must not skip browser/thread/proxy cleanup.
        await _close_browser(browser)
        executor.shutdown(wait=False, cancel_futures=True)
        set_session_proxies([])
        # Allow the Playwright Node transport to drain.
        await asyncio.sleep(0.3)


async def _finish_scan(scan, *, create_result_sheet, file_label, base_dir):
    """Result tab, summary, highlights, final save, history and the completion messages."""
    manager = scan.manager
    workbook = scan.workbook
    summary_update_time = format_display_datetime()
    if create_result_sheet:
        try:
            created_sheet_name = build_result_sheet(workbook, scan.rows_to_process, summary_update_time)
            if manager and created_sheet_name:
                await manager.broadcast_log(f"Đã tạo sheet kết quả mới: {created_sheet_name}.")
        except Exception as error:
            if manager:
                await manager.broadcast_log(f"CẢNH BÁO: Không tạo được sheet kết quả ({str(error)})")
    try:
        summary_count = rebuild_summary_sheet(
            workbook,
            summary_update_time=summary_update_time,
            selected_partners=scan.selected_names,
            data_sheet_name=scan.scan_sheet,
        )
        if manager and scan.scan_sheet:
            summary_title = summary_sheet_title_for_data_sheet(scan.scan_sheet)
            await manager.broadcast_log(f"Đã cập nhật {summary_title} ({summary_count} đối tác).")
    except Exception as error:
        if manager:
            await manager.broadcast_log(f"CẢNH BÁO: Không cập nhật được sheet Tổng kết ({str(error)})")

    try:
        if scan.scan_sheet:
            highlighted_count = highlight_single_partner_link_rows(workbook, scan.scan_sheet)
            if manager and highlighted_count:
                await manager.broadcast_log(
                    f"Đã bôi cam {highlighted_count} dòng link 1 đối tác chưa đủ điều kiện xanh."
                )
            video_highlighted = highlight_video_link_rows(workbook, scan.scan_sheet)
            if manager and video_highlighted:
                await manager.broadcast_log(f"Đã bôi xanh {video_highlighted} dòng video có hoạt động.")
    except Exception as error:
        if manager:
            await manager.broadcast_log(f"CẢNH BÁO: Không bôi cam được dòng link 1 đối tác ({str(error)})")

    final_saved, final_save_reason = await save_workbook(workbook, scan.file_path, manager)
    if not final_saved:
        workbook.close()
        if final_save_reason == "permission":
            raise RuntimeError("Lưu file Excel thất bại vì file đang mở.")
        raise RuntimeError("Lưu file Excel thất bại.")
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
    append_scrape_history(base_dir or os.path.dirname(os.path.abspath(scan.file_path)), history_entry)
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


async def run_scraper(file_path, websocket_manager=None, worker_count=DEFAULT_WORKERS, retries=DEFAULT_RETRIES, save_every=DEFAULT_SAVE_EVERY, selected_partner=None, selected_partners=None, create_result_sheet=False, base_dir=None, file_label="", sheet_name=None, use_request=True, browser_fallback=False, use_proxy=False, proxy_text=""):
    if not os.path.exists(file_path):
        if websocket_manager:
            await websocket_manager.broadcast_log(f"Lỗi: Không tìm thấy file {file_path}")
        return

    retries = clamp_int(retries, DEFAULT_RETRIES, 0, 5)
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

    scan = _TikTokScan(
        file_path,
        websocket_manager,
        sheet_name=sheet_name,
        selected_names=selected_names,
        partner_label=partner_label,
        save_every=save_every,
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
            if selected_names:
                message = f"Không tìm thấy link nào cho {partner_label}."
            await websocket_manager.broadcast_log(message)
        scan.workbook.close()
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

    async with async_playwright() as playwright:
        # One pool per run, sized to its workers: request scrapes and profile
        # lookups never queue behind other runs or behind workbook saves.
        executor = ThreadPoolExecutor(max_workers=max(scan.active_worker_count, 1), thread_name_prefix="riviu-tiktok")
        browser = None
        workers = []
        finalize_task = None
        interrupted = False
        try:
            set_session_proxies(proxy_configs if use_proxy else [])
            if websocket_manager:
                if use_request and browser_fallback:
                    startup_message = f"Đang khởi tạo {request_worker_count} luồng Request và {browser_worker_count} luồng trình duyệt fallback..."
                elif use_request:
                    startup_message = f"Đang khởi tạo {request_worker_count} luồng Request..."
                else:
                    startup_message = f"Đang khởi tạo trình duyệt và {browser_worker_count} luồng..."
                await websocket_manager.broadcast_log(startup_message)
            if browser_worker_count > 0:
                browser = await _launch_browser(playwright)
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
                    await asyncio.wait_for(asyncio.gather(*workers, return_exceptions=True), timeout=20.0)
                except asyncio.TimeoutError:
                    pass
        except BaseException:
            interrupted = True
            raise
        finally:
            try:
                await finish_pending_task(asyncio.create_task(
                    _shutdown_scan(scan, workers, finalize_task, browser, executor)
                ))
            except BaseException:
                interrupted = True
                raise
            finally:
                if interrupted:
                    scan.workbook.close()

    await _finish_scan(scan, create_result_sheet=create_result_sheet, file_label=file_label, base_dir=base_dir)
