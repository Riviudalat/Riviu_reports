import hashlib
import html
import json
import os
import re
import tempfile
import unicodedata
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable
from urllib.parse import urlparse

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.packaging.custom import StringProperty


DATA_DIR_NAME = "data"
GOOGLE_SHEET_FILENAME_PREFIX = "google_sheet_"
LEGACY_GOOGLE_SHEET_FILE_ID = "data/google_sheet_main.xlsx"
GOOGLE_SHEET_LABEL = "Google Sheet chính"
GOOGLE_SHEET_REGISTRY_FILENAME = "google_sheet_sources.json"
GOOGLE_SHEET_TITLE_MAP_PROPERTY = "Riviu.GoogleSheetTitles"
REPORT_COLUMNS = ["NGÀY AIR", "TÊN KÊNH", "LINK AIR", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"]
SUMMARY_SHEET_NAME = "Tổng kết"
SUMMARY_SHEET_TITLE_PREFIX = "Tổng kết "
LAST_UPDATE_COLUMN = "Cập nhật lần cuối"
SUMMARY_COLUMNS = ["Stt", "ĐỐI TÁC", "TỔNG LINK", "TỔNG LƯỢT XEM", "TỔNG TIM", "TỔNG BÌNH LUẬN", "TỔNG LƯỢT LƯU", "TỔNG CHIA SẺ", LAST_UPDATE_COLUMN]
SUMMARY_TOTAL_LABEL = "TỔNG"
# Cam cảnh báo đối tác chỉ có đúng 1 link — cần chú ý khi báo cáo/nghiệm thu.
SINGLE_LINK_FILL_COLOR = "FFC000"
# Xanh nhạt cho dòng link dạng video (/video/).
VIDEO_LINK_FILL_COLOR = "BDD7EE"
LEGACY_PHOTO_LINK_FILL_COLOR = "C6EFCE"
TIKTOK_MEDIA_VIDEO = "video"
TIKTOK_MEDIA_PHOTO = "photo"
TTBD_SCAN_STATUS_HEADER = "__TTBD_SCAN_STATUS"
TTBD_RESOLVED_URL_HEADER = "__TTBD_RESOLVED_URL"
TTBD_SOURCE_URL_HEADER = "__TTBD_SOURCE_URL"
TTBD_INTERNAL_HEADERS = {
    TTBD_SCAN_STATUS_HEADER,
    TTBD_RESOLVED_URL_HEADER,
    TTBD_SOURCE_URL_HEADER,
    "__THREADS_SCAN_STATUS",
}
METRIC_COLUMNS = ["LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"]
PARTNER_HEADING_MARKERS = ("DANH SÁCH", "DANH SACH", "BỘ ẢNH", "BO ANH")
CHANNEL_OVERRIDE_FILENAME = "channel_name_overrides.json"
RESULT_SHEET_PREFIX = "report seeding tiktok"
THREADS_RESULT_SHEET_PREFIX = "report seeding threads"
# Same layout as TikTok; REPOST takes the LƯỢT LƯU slot because Threads has no saves.
THREADS_REPORT_COLUMNS = ["NGÀY AIR", "TÊN KÊNH", "LINK AIR", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "REPOST", "CHIA SẺ"]
THREADS_SCAN_STATUS_HEADER = "__THREADS_SCAN_STATUS"
# Single header vocabulary for scanners, previews and reports; matched with normalize_key
# (case/diacritic-insensitive) so a scan never appends a duplicate of a column the report reads.
COLUMN_ALIASES = {
    "link": ("LINK AIR", "Link", "URL"),
    "date": ("NGÀY AIR", "Ngày"),
    "TÊN KÊNH": ("TÊN KÊNH",),
    "Tên Kênh": ("TÊN KÊNH",),
    LAST_UPDATE_COLUMN: (LAST_UPDATE_COLUMN, "Ngày cập nhật"),
}
RESULT_SHEET_TIMESTAMP_RE = re.compile(
    r"^(?:T\d{1,2}\s+)?\d{2}-\d{2}-\d{4}-\d{2}[:-]?\d{2}(?:-\d+)?$"
)
DISPLAY_DATETIME_FORMAT = "%d/%m/%Y-%H:%M"
FILENAME_DATETIME_FORMAT = "%d-%m-%Y-%H-%M"
SHEET_DATETIME_FORMAT = "%d-%m-%Y-%H-%M"
GOOGLE_SHEET_DATETIME_FORMAT = "%d-%m-%Y-%H:%M"


def ensure_data_dir(base_dir):
    data_dir = os.path.join(base_dir, DATA_DIR_NAME)
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def is_internal_workbook_filename(filename):
    """Skip Excel/atomic-save temp files and underscore-prefixed internal/test workbooks."""
    name = os.path.basename(str(filename or ""))
    return not name or name.startswith(("~$", "_", "."))


def _replace_atomically(path, write):
    # Hidden temp name: an interrupted save must never show up as a selectable workbook.
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temp_path = tempfile.mkstemp(prefix=".riviu-", suffix=".tmp", dir=directory)
    os.close(descriptor)
    try:
        write(temp_path)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def save_workbook_atomic(workbook, path):
    _replace_atomically(path, workbook.save)


def write_json_atomic(path, payload):
    def write(temp_path):
        with open(temp_path, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)

    _replace_atomically(path, write)


def normalize_header(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def normalize_key(value):
    text = str(value or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.strip()).casefold()


def clean_text(value):
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def is_numeric_channel_garbage(value):
    text = clean_text(value)
    if not text:
        return False
    normalized = text.replace(",", "").strip()
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", normalized))


def normalize_channel_display(value):
    text = clean_text(value)
    channel_fixes = {
        "B?o Quy?n": "Bảo Quyên",
    }
    return channel_fixes.get(text, text)


def is_generated_username_channel(value, link=""):
    text = clean_text(value)
    if not text:
        return False
    compact = re.sub(r"\s+", "", text.lstrip("@"))
    if re.fullmatch(r"user\d{4,}.*", compact, flags=re.IGNORECASE):
        return True
    return False


def is_generic_tiktok_channel_name(value):
    key = normalize_key(value)
    if key in {
        "tiktok",
        "make your day",
        "tiktok make your day",
        "tiktok - make your day",
        "screen time breaks",
        "screen time break",
    }:
        return True
    if "tiktok" in key and "make your day" in key:
        return True
    if "screen time" in key:
        return True
    return False


def display_channel_name_from_file(link, raw_channel):
    normalized_channel = normalize_channel_display(raw_channel)
    if not normalized_channel:
        return ""
    if (
        is_numeric_channel_garbage(normalized_channel)
        or is_generated_username_channel(normalized_channel, link)
        or is_generic_tiktok_channel_name(normalized_channel)
    ):
        return "Lỗi"
    return normalized_channel


def resolve_channel_name(link, raw_channel, channel_overrides=None):
    username = extract_tiktok_username(link)
    normalized_channel = display_channel_name_from_file(link, raw_channel)
    if normalized_channel:
        return normalized_channel
    if username and channel_overrides:
        override_name = channel_overrides.get(username.casefold())
        if override_name:
            return override_name
    return ""


def set_cell_literal(cell, value):
    """Write untrusted input literally, without altering its displayed text.

    Generated formulas must use normal cell assignment instead of this helper.
    """
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"
    return cell


def clean_preview_value(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    text = clean_text(value)
    if text.endswith(" 00:00:00"):
        return text[:10]
    return text


def parse_google_spreadsheet_id(source_url):
    parsed = urlparse(source_url.strip())
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", parsed.path)
    if not match:
        raise ValueError("URL Google Sheet không hợp lệ")
    return match.group(1)


def extract_tiktok_username(url):
    match = re.search(r"tiktok\.com/@([^/?]+)", str(url or ""), re.IGNORECASE)
    return match.group(1).strip() if match else ""



def safe_workbook_filename(name, *, max_length=80):
    filename = re.sub(r'[\\/:*?"<>|]+', "-", clean_text(name))
    filename = re.sub(r"\s+", " ", filename).strip(" .")
    return filename[:max_length] if filename else "google-sheet"


def fetch_google_spreadsheet_title(source_url):
    spreadsheet_id = parse_google_spreadsheet_id(source_url)
    view_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    request = urllib.request.Request(view_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            page_html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return f"Google Sheet {spreadsheet_id[:12]}"

    for pattern in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r"<title[^>]*>([^<]+)</title>",
    ):
        match = re.search(pattern, page_html, flags=re.IGNORECASE)
        if not match:
            continue
        title = clean_text(html.unescape(match.group(1)))
        title = re.sub(r"\s*-\s*Google Sheets\s*$", "", title, flags=re.IGNORECASE)
        title = re.sub(r"\s*-\s*Google Trang tính\s*$", "", title, flags=re.IGNORECASE)
        if title:
            return title
    return f"Google Sheet {spreadsheet_id[:12]}"


def display_datetime_sort_key(value):
    """Order "dd/mm/YYYY-HH:MM" stamps chronologically; unparseable text sorts first."""
    text = clean_text(value)
    try:
        return (1, datetime.strptime(text, DISPLAY_DATETIME_FORMAT), "")
    except ValueError:
        return (0, datetime.min, text)


def format_display_datetime(moment=None):
    value = moment or datetime.now()
    return value.strftime(DISPLAY_DATETIME_FORMAT)


def format_filename_datetime(moment=None):
    value = moment or datetime.now()
    return value.strftime(FILENAME_DATETIME_FORMAT)


def format_excel_sheet_datetime(moment=None):
    value = moment or datetime.now()
    return value.strftime(SHEET_DATETIME_FORMAT)


def format_google_sheet_datetime(moment=None):
    value = moment or datetime.now()
    return value.strftime(GOOGLE_SHEET_DATETIME_FORMAT)


def safe_excel_sheet_title(title, existing_titles=()):
    """Keep Excel titles legal and unique after truncation, including by case."""
    base = re.sub(r"[\\/*?:\[\]]", "-", clean_text(title)).strip("'") or "Sheet"
    existing = {name.casefold() for name in existing_titles}
    candidate = base[:31].rstrip("'")
    counter = 2
    while candidate.casefold() in existing:
        suffix = f"-{counter}"
        candidate = f"{base[:31 - len(suffix)]}{suffix}"
        counter += 1
    return candidate


def google_to_excel_sheet_titles(original_titles):
    """Assign a stable local title independent of the order returned by Google."""
    mapping = {}
    for original in sorted(original_titles, key=lambda name: (name.casefold(), name)):
        mapping[original] = safe_excel_sheet_title(original, mapping.values())
    return mapping


def store_google_sheet_title_map(workbook, mapping):
    workbook.custom_doc_props.append(StringProperty(
        name=GOOGLE_SHEET_TITLE_MAP_PROPERTY,
        value=json.dumps(mapping, ensure_ascii=False),
    ))


def google_sheet_original_title(file_path, local_title):
    from openpyxl import load_workbook

    workbook = load_workbook(file_path, read_only=True)
    try:
        for prop in workbook.custom_doc_props:
            if prop.name == GOOGLE_SHEET_TITLE_MAP_PROPERTY:
                mapping = json.loads(prop.value)
                return next((original for original, local in mapping.items() if local == local_title), local_title)
        return local_title
    finally:
        workbook.close()


def parse_filename_datetime_stamp(stamp):
    text = clean_text(stamp)
    match = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return text
    day, month, year, hour, minute = match.groups()
    return f"{day}/{month}/{year}-{hour}:{minute}"


def google_sheet_sync_timestamp_display(moment=None):
    return format_display_datetime(moment)



def google_sheet_sync_label(title, timestamp_display=None):
    sheet_title = clean_text(title)
    stamp = clean_text(timestamp_display or google_sheet_sync_timestamp_display())
    return f"{sheet_title} {stamp}".strip()


def google_sheet_file_id_from_title(title, timestamp):
    safe_title = safe_workbook_filename(title)
    safe_stamp = safe_workbook_filename(timestamp, max_length=20)
    return f"{DATA_DIR_NAME}/{safe_title} {safe_stamp}.xlsx"


def google_sheet_filename_to_label(filename):
    base = clean_text(str(filename or "")).removesuffix(".xlsx")
    match = re.fullmatch(r"(.+?) (\d{2})-(\d{2})-(\d{4})-(\d{2})-(\d{2})", base)
    if match:
        title = match.group(1)
        stamp = base[len(title) + 1 :]
        return f"{title} {parse_filename_datetime_stamp(stamp)}"
    legacy = re.fullmatch(r"(.+?)-(\d{2})-(\d{2})-(\d{4})-(\d{2})-(\d{2})", base)
    if legacy:
        title = legacy.group(1)
        stamp = "-".join(legacy.groups()[1:])
        return f"{title} {parse_filename_datetime_stamp(stamp)}"
    return base



def google_sheet_registry_path(base_dir):
    return os.path.join(ensure_data_dir(base_dir), GOOGLE_SHEET_REGISTRY_FILENAME)


def load_google_sheet_registry(base_dir):
    path = google_sheet_registry_path(base_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_google_sheet_registry(base_dir, registry):
    write_json_atomic(google_sheet_registry_path(base_dir), registry)


def register_google_sheet_source(base_dir, file_id, source_url, title=""):
    registry = load_google_sheet_registry(base_dir)
    registry[file_id] = {
        "url": clean_text(source_url),
        "spreadsheetId": parse_google_spreadsheet_id(source_url),
        "title": clean_text(title),
    }
    save_google_sheet_registry(base_dir, registry)
    return registry[file_id]


def google_sheet_source_for_file(base_dir, file_id):
    registry = load_google_sheet_registry(base_dir)
    source = registry.get(file_id)
    if isinstance(source, dict):
        return source

    normalized_file_id = str(file_id or "").replace("\\", "/")
    for registered_file_id, registered_source in registry.items():
        if str(registered_file_id).replace("\\", "/") == normalized_file_id and isinstance(registered_source, dict):
            return registered_source

    return {}


def base_dir_for_file(file_path):
    absolute_path = os.path.abspath(file_path)
    parent = os.path.dirname(absolute_path)
    if os.path.basename(parent).casefold() == DATA_DIR_NAME:
        return os.path.dirname(parent)
    return parent


def channel_override_path(file_path):
    return os.path.join(ensure_data_dir(base_dir_for_file(file_path)), CHANNEL_OVERRIDE_FILENAME)


def load_channel_overrides(file_path):
    path = channel_override_path(file_path)
    overrides = {}
    if not os.path.exists(path):
        return overrides
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return overrides
    if not isinstance(data, dict):
        return overrides
    overrides.update({str(key).casefold(): clean_text(value) for key, value in data.items() if clean_text(value)})
    return overrides


def safe_join(base_dir, relative_path):
    """Resolve a relative path under base_dir; reject traversal outside base."""
    if not relative_path:
        return ""
    normalized = str(relative_path).replace("\\", "/").lstrip("/")
    if ".." in normalized.split("/"):
        return ""
    candidate = os.path.normpath(os.path.join(base_dir, normalized.replace("/", os.sep)))
    base_abs = os.path.abspath(base_dir)
    candidate_abs = os.path.abspath(candidate)
    if candidate_abs != base_abs and not candidate_abs.startswith(base_abs + os.sep):
        return ""
    return candidate


def metric_number(value):
    if pd.isna(value) or value == "" or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(float(value))
    raw_text = str(value).strip()
    if not raw_text:
        return 0
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", raw_text):
        return int(re.sub(r"[.,]", "", raw_text))
    if re.fullmatch(r"\d+\.0+", raw_text):
        return int(float(raw_text))
    compact = re.sub(r"[,\s]", "", raw_text)
    try:
        return int(float(compact))
    except ValueError:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def format_metric(value):
    """Cell value for a metric: unknown stays blank (None), never 0; numbers parse like totals do."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return int(number) if number.is_integer() else number
    text = clean_text(value)
    if not text:
        return None
    if re.fullmatch(r"[\d.,\s]+", text) and re.search(r"\d", text):
        return metric_number(text)
    return text



def google_sheet_export_url(source_url):
    spreadsheet_id = parse_google_spreadsheet_id(source_url)
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=xlsx"


def download_google_sheet(source_url, destination_path):
    export_url = google_sheet_export_url(source_url)
    request = urllib.request.Request(export_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    with open(destination_path, "wb") as file_obj:
        file_obj.write(content)
    return destination_path


def load_excel_file(file_path):
    return pd.ExcelFile(file_path, engine="openpyxl")


def workbook_sheet_names(file_path):
    workbook = load_excel_file(file_path)
    try:
        return list(workbook.sheet_names)
    finally:
        workbook.close()



def summary_sheet_title_for_data_sheet(data_sheet_name):
    """Build a stable per-source summary title without truncation collisions."""
    label = clean_text(data_sheet_name).lower()
    if not label:
        return SUMMARY_SHEET_NAME
    title = f"{SUMMARY_SHEET_TITLE_PREFIX}{label}"
    if len(title) <= 31:
        return title
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
    return f"{title[:22]}-{digest}"


def month_label_for_sheet_name(sheet_name):
    """Extract a 'T<month>' label from a data sheet name (e.g. Tháng 6 -> T6)."""
    match = re.search(r"thang\s*0*(\d{1,2})\b", normalize_key(sheet_name))
    if not match:
        return ""
    month = int(match.group(1))
    if not 1 <= month <= 12:
        return ""
    return f"T{month}"


def data_sheet_name_for_summary_title(workbook_sheet_names, summary_sheet_name):
    """Resolve the source data sheet name from a per-sheet summary tab title."""
    text = clean_text(summary_sheet_name)
    if normalize_key(text) == normalize_key(SUMMARY_SHEET_NAME):
        return ""
    prefix_folded = SUMMARY_SHEET_TITLE_PREFIX.casefold()
    if not text.casefold().startswith(prefix_folded):
        return ""
    suffix = text[len(SUMMARY_SHEET_TITLE_PREFIX) :].strip()
    if not suffix:
        return ""
    for candidate in workbook_sheet_names:
        if is_summary_sheet_name(candidate) or is_result_sheet_name(candidate):
            continue
        if summary_sheet_title_for_data_sheet(candidate).lower() == text.lower():
            return candidate
    return ""


def is_summary_sheet_name(sheet_name):
    sheet_key = normalize_key(sheet_name)
    if sheet_key == normalize_key(SUMMARY_SHEET_NAME):
        return True
    return sheet_key.startswith(normalize_key(SUMMARY_SHEET_TITLE_PREFIX))


def is_result_sheet_timestamp_name(sheet_name):
    return bool(RESULT_SHEET_TIMESTAMP_RE.fullmatch(clean_text(sheet_name)))


def is_result_sheet_name(sheet_name):
    normalized = normalize_key(sheet_name)
    if normalized.startswith(RESULT_SHEET_PREFIX) or normalized.startswith(THREADS_RESULT_SHEET_PREFIX):
        return True
    return is_result_sheet_timestamp_name(sheet_name)


def is_total_label(value):
    return normalize_key(clean_text(value)) == "tong"


def is_tiktok_link(value):
    return bool(normalize_tiktok_url(value))


def normalize_threads_url(value):
    text = clean_text(value)
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        return f"https://{text}"
    return text


def is_threads_link(value):
    try:
        parsed = urlparse(normalize_threads_url(value))
        host = (parsed.hostname or "").casefold()
    except ValueError:
        return False
    return host in {"threads.com", "www.threads.com", "threads.net", "www.threads.net"} and bool(
        re.fullmatch(r"(?:/@[^/]+/post/|/share/)[A-Za-z0-9_-]+/?", parsed.path)
    )


def extract_threads_username(url):
    if not is_threads_link(url):
        return ""
    match = re.match(r"^/@([^/]+)/post/", urlparse(normalize_threads_url(url)).path)
    return match.group(1) if match else ""


def display_threads_channel(link, raw_channel):
    """Channel cell as written, else "@username" from the post URL so the report keeps the row."""
    channel = clean_text(raw_channel)
    if channel:
        return channel
    username = extract_threads_username(link)
    return f"@{username}" if username else ""


TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "mobile.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}


def normalize_tiktok_url(value):
    """Normalize supported TikTok URLs; reject unsafe schemes/hosts/authorities."""
    text = clean_text(value)
    if not text or re.search(r"[\s\\\x00-\x1f\x7f]", text):
        return ""
    if text.startswith("//"):
        text = f"https:{text}"
    elif not re.match(r"^[a-z][a-z0-9+.-]*:", text, flags=re.IGNORECASE):
        text = f"https://{text}"
    try:
        parsed = urlparse(text)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or (parsed.hostname or "").casefold() not in TIKTOK_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80 if parsed.scheme.casefold() == "http" else 443}
        ):
            return ""
    except ValueError:
        return ""
    return text


def is_scrapable_tiktok_url(value):
    """True only for a supported TikTok HTTP(S) URL, including short links."""
    return bool(normalize_tiktok_url(value))


def detect_tiktok_media_type(url, *, resolved_url=""):
    """Nhận diện link TikTok là video hay ảnh (photo carousel) từ path URL.

    Trả về TIKTOK_MEDIA_VIDEO, TIKTOK_MEDIA_PHOTO, hoặc '' nếu chưa rõ (link rút gọn).
    """
    # A direct source path is authoritative. The resolved URL only identifies
    # media type for short links whose own path is ambiguous.
    for candidate in (url, resolved_url):
        text = normalize_tiktok_url(candidate).casefold()
        if not text:
            continue
        if "/video/" in text:
            return TIKTOK_MEDIA_VIDEO
        if "/photo/" in text:
            return TIKTOK_MEDIA_PHOTO
    return ""


def is_tiktok_video_link(url, *, resolved_url=""):
    return detect_tiktok_media_type(url, resolved_url=resolved_url) == TIKTOK_MEDIA_VIDEO



def should_highlight_video_link(
    url,
    *,
    likes=0,
    shares=0,
    resolved_url="",
    resolved_source_url="",
    metrics_readable=True,
    scan_status="",
):
    latest_status = clean_text(scan_status)
    source_url = normalize_tiktok_url(url)
    metadata_source_url = normalize_tiktok_url(resolved_source_url)
    # Colouring only: legacy rows without the hidden source cell keep using their stored resolution.
    trusted_resolved_url = resolved_url
    if metadata_source_url and metadata_source_url.casefold() != source_url.casefold():
        trusted_resolved_url = ""
    return (
        metrics_readable
        and (not latest_status or latest_status == "Success")
        and is_tiktok_video_link(url, resolved_url=trusted_resolved_url)
        and (metric_number(likes) > 0 or metric_number(shares) > 0)
    )


@dataclass(frozen=True)
class PlatformSpec:
    """Everything that differs between scanned platforms; adding a platform means adding one entry."""

    key: str
    label: str
    report_columns: tuple
    normalize_url: Callable[[object], str]
    is_link: Callable[[object], bool]
    display_channel: Callable[[str, str], str]
    highlights_video_links: bool = False
    # False: the report modal hides partners with no link of this platform (e.g. TikTok-only
    # partners in a mixed workbook stay out of the Threads list).
    lists_partners_without_links: bool = False
    file_tag: str = ""
    google_sheet_prefix: str = ""

    @property
    def metric_columns(self):
        return tuple(column for column in self.report_columns if column in ALL_METRIC_COLUMNS)


ALL_METRIC_COLUMNS = ("LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "REPOST", "CHIA SẺ")

PLATFORMS = {
    "tiktok": PlatformSpec(
        key="tiktok",
        label="TikTok",
        report_columns=tuple(REPORT_COLUMNS),
        normalize_url=lambda value: normalize_tiktok_url(value),
        is_link=lambda value: is_tiktok_link(value),
        display_channel=lambda link, raw: display_channel_name_from_file(link, raw),
        highlights_video_links=True,
        lists_partners_without_links=True,
    ),
    "threads": PlatformSpec(
        key="threads",
        label="Threads",
        report_columns=tuple(THREADS_REPORT_COLUMNS),
        normalize_url=lambda value: normalize_threads_url(value),
        is_link=lambda value: is_threads_link(value),
        display_channel=lambda link, raw: display_threads_channel(link, raw),
        file_tag="Threads",
        google_sheet_prefix="Report Seeding Threads",
    ),
}


def get_platform(key):
    spec = PLATFORMS.get(clean_text(key).lower())
    if spec is None:
        raise ValueError("Nền tảng không hợp lệ")
    return spec


def metric_total(values):
    """Sum the known metric values (a confirmed 0 counts); blank only when nothing is known."""
    known = [value for value in values if clean_text(value)]
    if not known:
        return ""
    return sum(metric_number(value) for value in known)


def fill_preview_total_row(frame, link_column, metric_columns, *, platform=None):
    if not link_column or link_column not in frame.columns:
        return frame

    total_positions = [
        index
        for index, value in enumerate(frame[link_column].tolist())
        if is_total_label(value)
    ]
    if not total_positions:
        return frame

    total_position = total_positions[-1]
    if total_position <= 0:
        return frame

    source_frame = frame.iloc[:total_position]
    spec = PLATFORMS.get(platform or "")
    if spec:
        source_frame = frame[frame[link_column].map(spec.is_link)]
    for column in metric_columns:
        if not column or column not in frame.columns:
            continue
        total_value = metric_total(source_frame[column])
        # pandas 3 string columns reject assigning numeric totals. Display rows
        # are intentionally mixed, while the original workbook stays untouched.
        frame[column] = frame[column].astype(object)
        frame.iat[total_position, frame.columns.get_loc(column)] = total_value
    return frame


def fill_missing_dates_from_previous(frame, date_column, link_column):
    if not date_column or date_column not in frame.columns:
        return frame

    last_date = ""
    for index, row in frame.iterrows():
        raw_date = row.get(date_column, "")
        cleaned_date = clean_text(raw_date)
        link = clean_text(row.get(link_column, "")) if link_column else ""

        if not (is_tiktok_link(link) or is_threads_link(link)):
            # Totals, section headings and empty separators delimit date groups.
            last_date = ""
            continue
        if cleaned_date:
            last_date = raw_date
            continue
        if clean_text(last_date):
            frame.at[index, date_column] = last_date
    return frame


def preview_column_labels(frame, raw_headers):
    """Display label per frame column, replacing pandas placeholder headers.

    pandas names a blank header "Unnamed: N" and a repeated one "X.1". Blank-header columns
    without data are dropped (label None); with data they become "Cột <letter>". Repeats keep
    a stable "X (2)" label because preview rows are keyed by column name.
    """
    labels = {}
    used = set()
    for position, column in enumerate(frame.columns):
        raw = raw_headers[position] if position < len(raw_headers) else None
        raw_blank = raw is None or (not isinstance(raw, str) and pd.isna(raw)) or not str(raw).strip()
        text = str(column)
        label = column
        if raw_blank and normalize_header(text).startswith("unnamed:"):
            if all(not clean_text(value) for value in frame.iloc[:, position].tolist()):
                labels[column] = None
                continue
            label = f"Cột {get_column_letter(position + 1)}"
        elif not raw_blank and text != str(raw):
            match = re.fullmatch(re.escape(str(raw)) + r"\.(\d+)", text)
            if match:
                label = f"{raw} ({int(match.group(1)) + 1})"
        while label in used:
            label = f"{label} ({len(used) + 1})"
        used.add(label)
        labels[column] = label
    return labels


def read_sheet_preview(file_path, sheet_name=None, limit=None, *, platform=None):
    workbook = load_excel_file(file_path)
    try:
        sheets = list(workbook.sheet_names)
        current_sheet = sheet_name if sheet_name in sheets else (sheets[0] if sheets else "")
        if not current_sheet:
            return {"sheets": [], "currentSheet": "", "columns": [], "data": [], "message": "Workbook không có sheet nào."}

        frame = workbook.parse(current_sheet)
        header_frame = workbook.parse(current_sheet, header=None, nrows=1)
        raw_headers = header_frame.iloc[0].tolist() if len(header_frame.index) else []
        column_labels = preview_column_labels(frame, raw_headers)
        link_column = find_link_column_name(frame)
        channel_column = find_column_name(frame, COLUMN_ALIASES["TÊN KÊNH"])
        date_column = find_column_name(frame, COLUMN_ALIASES["date"])
        partner_columns = dataframe_partner_columns(frame)
        views_column = find_column_name(frame, ["LƯỢT XEM"])
        likes_column = find_column_name(frame, ["TIM"])
        comments_column = find_column_name(frame, ["BÌNH LUẬN"])
        saves_column = find_column_name(frame, ["LƯỢT LƯU"])
        reposts_column = find_column_name(frame, ["REPOST"])
        shares_column = find_column_name(frame, ["CHIA SẺ"])
        scan_status_column = find_column_name(frame, [TTBD_SCAN_STATUS_HEADER])
        resolved_url_column = find_column_name(frame, [TTBD_RESOLVED_URL_HEADER])
        source_url_column = find_column_name(frame, [TTBD_SOURCE_URL_HEADER])
        public_columns = [
            column
            for column in frame.columns
            if clean_text(column) not in TTBD_INTERNAL_HEADERS and column_labels.get(column) is not None
        ]
        metric_columns = [
            views_column,
            likes_column,
            comments_column,
            saves_column,
            reposts_column,
            shares_column,
        ]
        frame = fill_missing_dates_from_previous(frame, date_column, link_column)
        for column in frame.select_dtypes(include=["datetime"]).columns:
            frame[column] = frame[column].dt.strftime(DISPLAY_DATETIME_FORMAT)
        spec = PLATFORMS.get(platform or "")
        if spec and link_column and not is_summary_sheet_name(current_sheet):
            frame = frame[frame[link_column].map(lambda value: spec.is_link(value) or is_total_label(value))].copy()
            frame.reset_index(drop=True, inplace=True)
        frame = fill_preview_total_row(frame, link_column, metric_columns, platform=platform)

        preview_frame = frame
        if limit and link_column and len(frame.index) > limit:
            total_positions = [
                index
                for index, value in enumerate(frame[link_column].tolist())
                if is_total_label(value)
            ]
            if total_positions and total_positions[-1] >= limit:
                preview_frame = pd.concat(
                    [frame.head(max(limit - 1, 0)), frame.iloc[[total_positions[-1]]]],
                    ignore_index=True,
                )
            else:
                preview_frame = frame.head(limit)
        elif limit:
            preview_frame = frame.head(limit)

        data = []
        for record in preview_frame.to_dict(orient="records"):
            preview_row = {}
            for column in public_columns:
                preview_row[column_labels[column]] = clean_preview_value(record.get(column, ""))
            if link_column and channel_column and column_labels.get(channel_column) is not None:
                channel_label = column_labels[channel_column]
                preview_row[channel_label] = display_channel_name_from_file(
                    record.get(link_column, ""),
                    preview_row.get(channel_label, ""),
                )
            link_value = record.get(link_column, "") if link_column else ""
            if link_column and is_tiktok_link(link_value):
                if partner_columns:
                    row_partners = extract_row_partners(record, partner_columns)
                    if len(row_partners) == 1:
                        preview_row["_singlePartner"] = True
                if should_highlight_video_link(
                    link_value,
                    likes=record.get(likes_column, "") if likes_column else 0,
                    shares=record.get(shares_column, "") if shares_column else 0,
                    resolved_url=record.get(resolved_url_column, "") if resolved_url_column else "",
                    resolved_source_url=record.get(source_url_column, "") if source_url_column else "",
                    scan_status=record.get(scan_status_column, "") if scan_status_column else "",
                ):
                    preview_row["_videoLink"] = True
            data.append(preview_row)

        return {
            "sheets": sheets,
            "currentSheet": current_sheet,
            "columns": [column_labels[column] for column in public_columns],
            "data": data,
            "totalRows": int(len(frame.index)),
            "shownRows": int(len(preview_frame.index)),
        }
    finally:
        workbook.close()


def read_summary_dashboard(file_path, data_sheet_name=None):
    workbook = load_excel_file(file_path)
    try:
        sheet_names = list(workbook.sheet_names)
        requested_data_sheet = clean_text(data_sheet_name)
        if requested_data_sheet:
            summary_sheet = summary_sheet_title_for_data_sheet(requested_data_sheet)
            if summary_sheet not in sheet_names:
                summary_sheet = ""
        else:
            summary_sheet = next((sheet for sheet in sheet_names if is_summary_sheet_name(sheet)), "")
        if not summary_sheet:
            label = requested_data_sheet or "sheet này"
            return {
                "sheet": "",
                "dataSheet": requested_data_sheet,
                "columns": SUMMARY_COLUMNS,
                "rows": [],
                "totals": {},
                "message": f"Workbook chưa có tổng kết cho {label}. Quét sheet đó để tạo.",
            }

        resolved_data_sheet = requested_data_sheet or data_sheet_name_for_summary_title(
            sheet_names, summary_sheet
        )
        frame = workbook.parse(summary_sheet).fillna("")
    finally:
        workbook.close()

    columns = frame.columns.tolist()
    if LAST_UPDATE_COLUMN not in columns:
        columns.append(LAST_UPDATE_COLUMN)
    rows = []
    partner_column = find_column_name(frame, ["ĐỐI TÁC", "Đối tác"])
    total_link_column = find_column_name(frame, ["TỔNG LINK", "Tổng link"])
    summary_metric_map = {
        "views": find_column_name(frame, ["TỔNG LƯỢT XEM", "LƯỢT XEM"]),
        "likes": find_column_name(frame, ["TỔNG TIM", "TIM"]),
        "comments": find_column_name(frame, ["TỔNG BÌNH LUẬN", "BÌNH LUẬN"]),
        "saves": find_column_name(frame, ["TỔNG LƯỢT LƯU", "LƯỢT LƯU"]),
        "shares": find_column_name(frame, ["TỔNG CHIA SẺ", "CHIA SẺ"]),
    }

    numeric_summary_keys = {
        normalize_key(item)
        for item in SUMMARY_COLUMNS
        if normalize_key(item) not in {"doi tac", normalize_key(LAST_UPDATE_COLUMN)}
    }
    def summary_value(column, value):
        if normalize_key(column) not in numeric_summary_keys:
            return clean_text(value)
        # A blank metric total means nothing was known for that partner; it must not become 0.
        return metric_total([value])

    totals_row = None
    for record in frame.to_dict(orient="records"):
        partner_name = clean_text(record.get(partner_column or "", ""))
        if not partner_name:
            continue
        row = {column: summary_value(column, record.get(column, "")) for column in columns}
        row.setdefault(LAST_UPDATE_COLUMN, "")
        if is_total_label(partner_name):
            totals_row = row
            continue
        rows.append(row)

    source_rows = [totals_row] if totals_row else rows
    totals = {
        "partners": len(rows),
        "links": sum(metric_number(row.get(total_link_column, 0)) for row in source_rows) if total_link_column else 0,
    }
    for key, column in summary_metric_map.items():
        totals[key] = metric_total(row.get(column, "") for row in source_rows) if column else 0

    return {
        "sheet": summary_sheet,
        "dataSheet": resolved_data_sheet,
        "columns": columns,
        "rows": rows,
        "totals": totals,
    }


def dataframe_partner_columns(frame):
    columns = list(frame.columns)
    start_index = None
    for index, column in enumerate(columns):
        if normalize_key(column).startswith("doi tac"):
            start_index = index
            break

    if start_index is None:
        return []

    partner_columns = [columns[start_index]]
    for column in columns[start_index + 1:]:
        normalized = normalize_key(column)
        raw_normalized = normalize_header(column)
        if normalized.startswith("doi tac") or raw_normalized.startswith("unnamed:") or raw_normalized == "":
            partner_columns.append(column)
        else:
            break
    return partner_columns


def split_partner_value(value):
    text = clean_text(value)
    if not text:
        return []

    raw_lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
    if len(raw_lines) <= 1 and not any(marker in text.upper() for marker in PARTNER_HEADING_MARKERS) and not text.lstrip().startswith(("-", "*", "•")):
        cleaned = _normalize_partner_token(text)
        return [cleaned] if cleaned else []

    partners = []
    for line in raw_lines:
        cleaned = _normalize_partner_token(line)
        if not cleaned:
            continue
        upper_line = cleaned.upper()
        if any(marker in upper_line for marker in PARTNER_HEADING_MARKERS):
            continue
        partners.append(cleaned)
    return unique_preserve_order(partners)


def _normalize_partner_token(text):
    cleaned = clean_text(text)
    cleaned = unicodedata.normalize("NFC", cleaned)
    cleaned = re.sub(r"[\u200B-\u200D\uFEFF\u00AD]", "", cleaned)
    cleaned = re.sub(r"^[\-\*\u2022]+\s*", "", cleaned)
    cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:\t")
    return cleaned


def partner_dedup_key(value):
    text = unicodedata.normalize("NFC", str(value or ""))
    text = re.sub(r"[\u200B-\u200D\uFEFF\u00AD]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def extract_row_partners(row, partner_columns: Iterable):
    partners = []
    for column in partner_columns:
        for partner in split_partner_value(row.get(column, "")):
            partners.append(partner)
    return unique_preserve_order(partners)


def unique_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        key = partner_dedup_key(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def find_column_name(frame, aliases):
    alias_set = {normalize_key(alias) for alias in aliases}
    for column in frame.columns:
        column_key = normalize_key(column)
        if column_key in alias_set:
            return column
    return None


LINK_COLUMN_SAMPLE_ROWS = 200
# "Link kênh"/"Profile URL" point at an account, never at the post a scan or report needs.
NON_POST_LINK_HEADER_MARKERS = ("kenh", "channel", "profile", "tai khoan")


def is_post_url(value):
    """A TikTok video/photo/short link or a Threads post link (not a profile page)."""
    text = normalize_tiktok_url(value)
    if text:
        parsed = urlparse(text)
        host = (parsed.hostname or "").casefold()
        return bool(detect_tiktok_media_type(text)) or host in {"vm.tiktok.com", "vt.tiktok.com"} or parsed.path.startswith("/t/")
    return is_threads_link(value)


def pick_link_column(headers, sample_values):
    """Index of the post-link column among headers matched by substring ('link'/'url').

    `sample_values(index)` yields cell values of that column. Account/profile link columns are
    skipped unless they are the only ones holding post URLs; a column with post URLs wins.
    """
    internal_keys = {normalize_key(header) for header in TTBD_INTERNAL_HEADERS}
    post_candidates = []
    account_candidates = []
    for index, header in enumerate(headers):
        key = normalize_key(header)
        if key in internal_keys or not ("link" in key or "url" in key):
            continue
        if any(marker in key for marker in NON_POST_LINK_HEADER_MARKERS):
            account_candidates.append(index)
        else:
            post_candidates.append(index)
    for index in post_candidates + account_candidates:
        if any(is_post_url(value) for value in sample_values(index)):
            return index
    return post_candidates[0] if post_candidates else None


def find_link_column_name(frame):
    column = find_column_name(frame, COLUMN_ALIASES["link"])
    if column:
        return column
    columns = list(frame.columns)
    index = pick_link_column(
        columns,
        lambda position: frame.iloc[:LINK_COLUMN_SAMPLE_ROWS, position].tolist(),
    )
    return None if index is None else columns[index]


def find_data_sheet_names(file_path):
    workbook = load_excel_file(file_path)
    try:
        return find_data_sheet_names_in_workbook(workbook)
    finally:
        workbook.close()


def find_data_sheet_names_in_workbook(workbook):
    """Data sheets with visible ones first: callers default to [0], which must not be a hidden old month."""
    sheets = workbook.sheet_names
    data_sheets = [sheet for sheet in sheets if not is_summary_sheet_name(sheet) and not is_result_sheet_name(sheet)]
    try:
        hidden = {ws.title for ws in workbook.book.worksheets if ws.sheet_state != "visible"}
    except AttributeError:
        hidden = set()
    return [sheet for sheet in data_sheets if sheet not in hidden] + [sheet for sheet in data_sheets if sheet in hidden]


def build_workbook_rows(file_path, selected_partner=None, sheet_name=None, *, platform="tiktok"):
    spec = get_platform(platform)
    workbook = load_excel_file(file_path)
    try:
        selected_key = selected_partner.casefold() if selected_partner else None
        requested_sheet = clean_text(sheet_name)
        rows = []
        data_sheets = find_data_sheet_names_in_workbook(workbook)
        if requested_sheet:
            data_sheets = [requested_sheet] if requested_sheet in workbook.sheet_names else []

        for sheet_name in data_sheets:
            frame = workbook.parse(sheet_name)
            partner_columns = dataframe_partner_columns(frame)
            date_column = find_column_name(frame, COLUMN_ALIASES["date"])
            channel_column = find_column_name(frame, COLUMN_ALIASES["TÊN KÊNH"])
            link_column = find_link_column_name(frame)
            last_update_column = find_column_name(frame, COLUMN_ALIASES[LAST_UPDATE_COLUMN])
            metric_columns = {metric: find_column_name(frame, [metric]) for metric in ALL_METRIC_COLUMNS}
            internal_columns = {
                "_scanStatus": find_column_name(frame, [TTBD_SCAN_STATUS_HEADER]),
                "_resolvedUrl": find_column_name(frame, [TTBD_RESOLVED_URL_HEADER]),
                "_resolvedSourceUrl": find_column_name(frame, [TTBD_SOURCE_URL_HEADER]),
            }
            if not link_column:
                continue
            frame = fill_missing_dates_from_previous(frame, date_column, link_column)

            def cell(row, column):
                return row.get(column, "") if column else ""

            for _, row in frame.iterrows():
                partners = extract_row_partners(row, partner_columns) if partner_columns else []
                if selected_key and selected_key not in {partner.casefold() for partner in partners}:
                    continue
                if selected_key and not partners:
                    continue

                link = spec.normalize_url(row.get(link_column, ""))
                if not link or is_total_label(link) or not spec.is_link(link):
                    continue

                rows.append({
                    "sheet_name": sheet_name,
                    "NGÀY AIR": cell(row, date_column),
                    "TÊN KÊNH": spec.display_channel(link, clean_text(cell(row, channel_column))),
                    "LINK AIR": link,
                    **{metric: cell(row, column) for metric, column in metric_columns.items()},
                    LAST_UPDATE_COLUMN: clean_text(cell(row, last_update_column)),
                    "partners": partners,
                    **{key: clean_text(cell(row, column)) for key, column in internal_columns.items()},
                })

        return rows
    finally:
        workbook.close()


def is_exportable_report_row(row, *, apply_min_views=True, min_views=100):
    """Every platform reports the same rows: a readable channel and, when enabled, enough views."""
    if is_failed_channel_name(row.get("TÊN KÊNH", "")):
        return False
    if apply_min_views:
        threshold = max(int(min_views or 0), 0)
        if metric_number(row.get("LƯỢT XEM", 0)) < threshold:
            return False
    return True


def list_workbook_partners_with_link_counts(
    file_path,
    sheet_name=None,
    *,
    apply_min_views=True,
    min_views=100,
    platform="tiktok",
):
    partner_stats = {}
    workbook = load_excel_file(file_path)
    try:
        data_sheets = find_data_sheet_names_in_workbook(workbook)
        requested_sheet = clean_text(sheet_name)
        if requested_sheet:
            data_sheets = [requested_sheet] if requested_sheet in data_sheets else []
        for current_sheet in data_sheets:
            frame = workbook.parse(current_sheet)
            partner_columns = dataframe_partner_columns(frame)
            if not partner_columns:
                continue
            for _, row in frame.iterrows():
                for partner in extract_row_partners(row, partner_columns):
                    key = partner_dedup_key(partner)
                    if key not in partner_stats:
                        partner_stats[key] = {"name": partner, "linkCount": 0, "rawLinkCount": 0}
    finally:
        workbook.close()

    if not partner_stats:
        return []

    for row in build_workbook_rows(file_path, sheet_name=sheet_name, platform=platform):
        row_partners = row.get("partners") or []
        for partner in row_partners:
            key = partner_dedup_key(partner)
            if key not in partner_stats:
                continue
            partner_stats[key]["rawLinkCount"] += 1
            if is_exportable_report_row(row, apply_min_views=apply_min_views, min_views=min_views):
                partner_stats[key]["linkCount"] += 1

    values = partner_stats.values()
    if not get_platform(platform).lists_partners_without_links:
        values = [item for item in values if item["rawLinkCount"] > 0]
    return sorted(values, key=lambda item: item["name"].casefold())


def list_workbook_partners(file_path, sheet_name=None, *, platform="tiktok"):
    return [item["name"] for item in list_workbook_partners_with_link_counts(file_path, sheet_name=sheet_name, platform=platform)]


def worksheet_headers(worksheet):
    max_column = worksheet.max_column or 0
    if max_column < 1:
        return []
    return [worksheet.cell(row=1, column=index).value for index in range(1, max_column + 1)]


def worksheet_find_column_index(worksheet, aliases):
    alias_set = {normalize_key(alias) for alias in aliases}
    for index, header in enumerate(worksheet_headers(worksheet), start=1):
        if normalize_key(header) in alias_set:
            return index
    return None


def worksheet_ensure_column(worksheet, header, aliases=None, *, hidden=False):
    """Find a column the same way previews/reports do, appending it only when truly absent."""
    index = worksheet_find_column_index(worksheet, aliases or COLUMN_ALIASES.get(header) or (header,))
    if index is None:
        index = (worksheet.max_column or 0) + 1
        worksheet.cell(row=1, column=index).value = header
    if hidden:
        worksheet.column_dimensions[get_column_letter(index)].hidden = True
    return index


def worksheet_find_link_column_index(worksheet):
    column_index = worksheet_find_column_index(worksheet, COLUMN_ALIASES["link"])
    if column_index:
        return column_index

    last_row = min(worksheet.max_row or 0, LINK_COLUMN_SAMPLE_ROWS + 1)
    index = pick_link_column(
        worksheet_headers(worksheet),
        lambda position: (worksheet.cell(row=row, column=position + 1).value for row in range(2, last_row + 1)),
    )
    return None if index is None else index + 1


def worksheet_find_last_update_column_index(worksheet):
    return worksheet_find_column_index(worksheet, COLUMN_ALIASES[LAST_UPDATE_COLUMN])



def workbook_data_sheet_names(workbook):
    return [
        sheet for sheet in workbook.sheetnames
        if not is_summary_sheet_name(sheet) and not is_result_sheet_name(sheet)
    ]


def result_sheet_display_name(timestamp_text=None):
    stamp = clean_text(timestamp_text) or format_excel_sheet_datetime()
    return safe_excel_sheet_title(stamp)


def worksheet_partner_column_indexes(worksheet):
    headers = worksheet_headers(worksheet)
    start_index = None
    for index, header in enumerate(headers, start=1):
        if normalize_key(header).startswith("doi tac"):
            start_index = index
            break

    if start_index is None:
        return []

    indexes = [start_index]
    for index in range(start_index + 1, len(headers) + 1):
        header_key = normalize_key(headers[index - 1])
        raw_header_key = normalize_header(headers[index - 1])
        if header_key.startswith("doi tac") or not raw_header_key or raw_header_key.startswith("unnamed:"):
            indexes.append(index)
        else:
            break
    return indexes


def worksheet_row_partners(worksheet, row_index, partner_columns):
    values = {column_index: worksheet.cell(row=row_index, column=column_index).value for column_index in partner_columns}
    partners = []
    for value in values.values():
        partners.extend(split_partner_value(value))
    return unique_preserve_order(partners)


def is_failed_channel_name(value):
    text = clean_text(value)
    key = normalize_key(text)
    normalized_for_error = text.casefold().replace("\u2019", "'").replace("\u2018", "'")
    error_phrases = (
        "couldn't find this account",
        "couldnt find this account",
        "video currently unavailable",
        "video unavailable",
        "page not available",
        "page unavailable",
    )
    return (
        not text
        or key in {"loi", "l?i"}
        or text.casefold().startswith("error:")
        or any(phrase in normalized_for_error for phrase in error_phrases)
        or is_numeric_channel_garbage(text)
        or is_generated_username_channel(text)
        or is_generic_tiktok_channel_name(text)
    )


def read_existing_summary_updates(worksheet):
    updates = {}
    if not worksheet or (worksheet.max_row or 0) < 2:
        return updates
    partner_column = worksheet_find_column_index(worksheet, ["ĐỐI TÁC"])
    update_column = worksheet_find_last_update_column_index(worksheet)
    if not partner_column or not update_column:
        return updates
    for row_index in range(2, (worksheet.max_row or 0) + 1):
        partner = clean_text(worksheet.cell(row=row_index, column=partner_column).value)
        update_value = clean_text(worksheet.cell(row=row_index, column=update_column).value)
        if partner and update_value:
            updates[partner_dedup_key(partner)] = update_value
    return updates


def normalize_selected_partner_keys(selected_partner=None, selected_partners=None):
    values = []
    if isinstance(selected_partners, (list, tuple, set)):
        values.extend(selected_partners)
    elif selected_partners:
        values.append(selected_partners)
    if isinstance(selected_partner, (list, tuple, set)):
        values.extend(selected_partner)
    elif selected_partner:
        values.append(selected_partner)
    return {clean_text(value).casefold() for value in values if clean_text(value)}


def build_partner_summary_rows(
    workbook,
    summary_update_time="",
    selected_partner=None,
    selected_partners=None,
    previous_updates=None,
    data_sheet_name=None,
):
    selected_keys = normalize_selected_partner_keys(selected_partner, selected_partners)
    previous_updates = previous_updates or {}
    summary = {}
    data_sheets = workbook_data_sheet_names(workbook)
    requested_sheet = clean_text(data_sheet_name)
    if requested_sheet:
        data_sheets = [requested_sheet] if requested_sheet in workbook.sheetnames else []
    for sheet_name in data_sheets:
        worksheet = workbook[sheet_name]
        link_column = worksheet_find_link_column_index(worksheet)
        if not link_column:
            continue

        partner_columns = worksheet_partner_column_indexes(worksheet)
        if not partner_columns:
            continue

        metric_columns = {
            header: worksheet_find_column_index(worksheet, [header])
            for header in METRIC_COLUMNS
        }
        last_update_column = worksheet_find_last_update_column_index(worksheet)

        for row_index in range(2, (worksheet.max_row or 0) + 1):
            link = clean_text(worksheet.cell(row=row_index, column=link_column).value)
            if not is_tiktok_link(link):
                continue

            partners = worksheet_row_partners(worksheet, row_index, partner_columns)
            if not partners:
                continue

            for partner in partners:
                # Same grouping as the partner picker, so "Shop A"/"shop a" is one summary row.
                key = partner_dedup_key(partner)
                bucket = summary.setdefault(
                    key,
                    {
                        "ĐỐI TÁC": partner,
                        "TỔNG LINK": 0,
                        # Blank until a known value arrives: a partner whose links are all unknown is not 0.
                        **{f"TỔNG {metric}": "" for metric in METRIC_COLUMNS},
                        LAST_UPDATE_COLUMN: previous_updates.get(key, ""),
                    },
                )
                bucket["TỔNG LINK"] += 1
                if last_update_column:
                    update_value = clean_text(worksheet.cell(row=row_index, column=last_update_column).value)
                    if update_value and display_datetime_sort_key(update_value) > display_datetime_sort_key(bucket[LAST_UPDATE_COLUMN]):
                        bucket[LAST_UPDATE_COLUMN] = update_value
                for metric, column_index in metric_columns.items():
                    if column_index:
                        value = worksheet.cell(row=row_index, column=column_index).value
                        bucket[f"TỔNG {metric}"] = metric_total([bucket[f"TỔNG {metric}"], value])

    result = []
    if summary_update_time:
        for row in summary.values():
            if not selected_keys or row["ĐỐI TÁC"].casefold() in selected_keys:
                row[LAST_UPDATE_COLUMN] = summary_update_time

    for index, row in enumerate(sorted(summary.values(), key=lambda value: value["ĐỐI TÁC"].casefold()), start=1):
        row["Stt"] = index
        result.append(row)
    return result





def rebuild_summary_sheet(
    workbook,
    summary_update_time="",
    selected_partner=None,
    selected_partners=None,
    data_sheet_name=None,
):
    source_sheet = clean_text(data_sheet_name)
    if not source_sheet or source_sheet not in workbook.sheetnames:
        return 0

    summary_sheet = summary_sheet_title_for_data_sheet(source_sheet)
    existing_worksheet = workbook[summary_sheet] if summary_sheet in workbook.sheetnames else None
    previous_updates = read_existing_summary_updates(existing_worksheet)
    insert_index = workbook.sheetnames.index(source_sheet) + 1
    if summary_sheet in workbook.sheetnames:
        worksheet = workbook[summary_sheet]
        worksheet.delete_rows(1, max(worksheet.max_row or 1, 1))
        if worksheet.title != summary_sheet:
            worksheet.title = summary_sheet
        current_index = workbook.sheetnames.index(summary_sheet)
        if current_index != insert_index:
            workbook.move_sheet(worksheet, offset=insert_index - current_index)
    else:
        worksheet = workbook.create_sheet(summary_sheet, insert_index)

    rows = build_partner_summary_rows(
        workbook,
        summary_update_time=summary_update_time,
        selected_partner=selected_partner,
        selected_partners=selected_partners,
        previous_updates=previous_updates,
        data_sheet_name=source_sheet,
    )
    header_fill = PatternFill("solid", fgColor="0B5ED7")
    for column_index, header in enumerate(SUMMARY_COLUMNS, start=1):
        cell = worksheet.cell(row=1, column=column_index, value=header)
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center")

    for row_index, row in enumerate(rows, start=2):
        for column_index, header in enumerate(SUMMARY_COLUMNS, start=1):
            value = row.get(header, "")
            cell = set_cell_literal(worksheet.cell(row=row_index, column=column_index), None if value == "" else value)
            if header == "ĐỐI TÁC":
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            elif header == LAST_UPDATE_COLUMN:
                cell.alignment = Alignment(horizontal="center")
            else:
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="right")

    last_row = max(len(rows) + 1, 1)
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(len(SUMMARY_COLUMNS))}{last_row}"
    widths = [8, 38, 12, 16, 12, 16, 16, 14, 20]
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width

    return len(rows)


def _cell_has_fill(cell, color):
    fill = cell.fill
    if fill is None or fill.fill_type != "solid":
        return False
    return str(fill.fgColor.rgb or "").upper().endswith(color)


def highlight_single_partner_link_rows(workbook, data_sheet_name):
    """Bôi cam link chỉ có 1 đối tác, trừ video đủ điều kiện bôi xanh."""
    source_sheet = clean_text(data_sheet_name)
    if not source_sheet or source_sheet not in workbook.sheetnames:
        return 0

    worksheet = workbook[source_sheet]
    link_column = worksheet_find_link_column_index(worksheet)
    if not link_column:
        return 0

    partner_columns = worksheet_partner_column_indexes(worksheet)
    if not partner_columns:
        return 0
    likes_column = worksheet_find_column_index(worksheet, ["TIM"])
    shares_column = worksheet_find_column_index(worksheet, ["CHIA SẺ"])
    scan_status_column = worksheet_find_column_index(worksheet, [TTBD_SCAN_STATUS_HEADER])
    resolved_url_column = worksheet_find_column_index(worksheet, [TTBD_RESOLVED_URL_HEADER])
    source_url_column = worksheet_find_column_index(worksheet, [TTBD_SOURCE_URL_HEADER])

    max_row = worksheet.max_row or 0
    max_column = worksheet.max_column or 0
    if max_row < 2 or max_column < 1:
        return 0

    highlight_fill = PatternFill("solid", fgColor=SINGLE_LINK_FILL_COLOR)
    clear_fill = PatternFill(fill_type=None)
    highlighted_count = 0
    for row_index in range(2, max_row + 1):
        link = clean_text(worksheet.cell(row=row_index, column=link_column).value)
        should_highlight = False
        if is_scrapable_tiktok_url(link):
            partners = worksheet_row_partners(worksheet, row_index, partner_columns)
            is_active_video = should_highlight_video_link(
                link,
                likes=worksheet.cell(row=row_index, column=likes_column).value if likes_column else 0,
                shares=worksheet.cell(row=row_index, column=shares_column).value if shares_column else 0,
                resolved_url=worksheet.cell(row=row_index, column=resolved_url_column).value if resolved_url_column else "",
                resolved_source_url=worksheet.cell(row=row_index, column=source_url_column).value if source_url_column else "",
                scan_status=worksheet.cell(row=row_index, column=scan_status_column).value if scan_status_column else "",
            )
            should_highlight = len(partners) == 1 and not is_active_video
        if should_highlight:
            highlighted_count += 1
        for column_index in range(1, max_column + 1):
            cell = worksheet.cell(row=row_index, column=column_index)
            if should_highlight:
                cell.fill = highlight_fill
            elif _cell_has_fill(cell, SINGLE_LINK_FILL_COLOR):
                cell.fill = clear_fill

    return highlighted_count


def highlight_video_link_rows(workbook, data_sheet_name):
    """Bôi xanh video có TIM hoặc CHIA SẺ lớn hơn 0."""
    source_sheet = clean_text(data_sheet_name)
    if not source_sheet or source_sheet not in workbook.sheetnames:
        return 0

    worksheet = workbook[source_sheet]
    link_column = worksheet_find_link_column_index(worksheet)
    if not link_column:
        return 0
    likes_column = worksheet_find_column_index(worksheet, ["TIM"])
    shares_column = worksheet_find_column_index(worksheet, ["CHIA SẺ"])
    scan_status_column = worksheet_find_column_index(worksheet, [TTBD_SCAN_STATUS_HEADER])
    resolved_url_column = worksheet_find_column_index(worksheet, [TTBD_RESOLVED_URL_HEADER])
    source_url_column = worksheet_find_column_index(worksheet, [TTBD_SOURCE_URL_HEADER])

    max_row = worksheet.max_row or 0
    max_column = worksheet.max_column or 0
    if max_row < 2 or max_column < 1:
        return 0

    video_fill = PatternFill("solid", fgColor=VIDEO_LINK_FILL_COLOR)
    clear_fill = PatternFill(fill_type=None)
    video_count = 0

    for row_index in range(2, max_row + 1):
        link = clean_text(worksheet.cell(row=row_index, column=link_column).value)
        is_video = bool(
            link
            and should_highlight_video_link(
                link,
                likes=worksheet.cell(row=row_index, column=likes_column).value if likes_column else 0,
                shares=worksheet.cell(row=row_index, column=shares_column).value if shares_column else 0,
                resolved_url=worksheet.cell(row=row_index, column=resolved_url_column).value if resolved_url_column else "",
                resolved_source_url=worksheet.cell(row=row_index, column=source_url_column).value if source_url_column else "",
                scan_status=worksheet.cell(row=row_index, column=scan_status_column).value if scan_status_column else "",
            )
        )

        if is_video:
            video_count += 1

        for column_index in range(1, max_column + 1):
            cell = worksheet.cell(row=row_index, column=column_index)
            if is_video:
                cell.fill = video_fill
            elif _cell_has_fill(cell, VIDEO_LINK_FILL_COLOR) or _cell_has_fill(cell, LEGACY_PHOTO_LINK_FILL_COLOR):
                cell.fill = clear_fill

    return video_count


def workbook_file_entries(base_dir):
    data_dir = ensure_data_dir(base_dir)
    entries = []

    for filename in os.listdir(base_dir):
        if is_internal_workbook_filename(filename):
            continue
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            entries.append({
                "id": filename.replace("\\", "/"),
                "label": filename,
                "source": "local",
            })

    registry = load_google_sheet_registry(base_dir)
    for filename in os.listdir(data_dir):
        if is_internal_workbook_filename(filename):
            continue
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            relative_id = f"{DATA_DIR_NAME}/{filename}".replace("\\", "/")
            source = registry.get(relative_id, {})
            if isinstance(source, dict) and clean_text(source.get("title")):
                label = clean_text(source["title"])
            else:
                label = google_sheet_filename_to_label(filename)
            if relative_id == LEGACY_GOOGLE_SHEET_FILE_ID:
                label = GOOGLE_SHEET_LABEL
            entries.append({
                "id": relative_id,
                "label": label,
                "source": "google",
            })

    return sorted(entries, key=lambda entry: (entry["source"] != "google", entry["label"].casefold()))
