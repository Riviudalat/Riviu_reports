"""Pure rules of the Postgres backend: months, scan writes and report rows (no I/O).

The write functions return the columns one result changes, as {column: value}. A column
that must keep its old value is left out (the UPDATE never names it), exactly like a
workbook cell the scanner does not touch:

TikTok (`riviu.platforms.tiktok.workbook_io.write_result`):
    Success                     metrics = metric_or_none(value) (unknown -> NULL, 0 stays 0),
                                status, resolved/source link, best channel, cap_nhat_lan_cuoi
    should_clear_stale_metrics  metrics = NULL, status, channel kept when usable
    any other failure           status only (+ channel when the old one is unusable)
Threads (`riviu.platforms.threads.write_threads_result`):
    a known value replaces the old one, a missing one keeps it; views become NULL only on
    the explicit `view_query_unknown` invalidation; cap_nhat_lan_cuoi only when the run
    read a channel or a metric.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from riviu.workbook.constants import DISPLAY_DATETIME_FORMAT, LAST_UPDATE_COLUMN
from riviu.workbook.partners import partner_dedup_key, partners_from_doi_tac
from riviu.workbook.text import clean_text, is_total_label

# Vietnam has no daylight saving time; a fixed offset needs no tz database (Windows).
LOCAL_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")

TIKTOK_METRIC_COLUMNS = {
    "views": "luot_xem", "likes": "tim", "comments": "binh_luan", "saves": "luot_luu", "shares": "chia_se",
}
THREADS_METRIC_COLUMNS = {
    "views": "luot_xem", "likes": "tim", "comments": "binh_luan", "reposts": "repost", "shares": "chia_se",
}
# Report header -> column, per platform (headers absent here stay "" like a missing sheet column).
REPORT_METRIC_COLUMNS = {
    "tiktok": {"LƯỢT XEM": "luot_xem", "TIM": "tim", "BÌNH LUẬN": "binh_luan", "LƯỢT LƯU": "luot_luu", "CHIA SẺ": "chia_se"},
    "threads": {"LƯỢT XEM": "luot_xem", "TIM": "tim", "BÌNH LUẬN": "binh_luan", "REPOST": "repost", "CHIA SẺ": "chia_se"},
}
ALL_REPORT_METRICS = ("LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "REPOST", "CHIA SẺ")
# Columns riviu_scanner may write (riviu_scanner.sql / migration 0009). Anything else is a bug.
WRITABLE_COLUMNS = {
    "tiktok": frozenset({*TIKTOK_METRIC_COLUMNS.values(), "ten_kenh", "trang_thai_quet", "cap_nhat_lan_cuoi",
                         "link_phan_giai", "link_nguon_phan_giai"}),
    "threads": frozenset({*THREADS_METRIC_COLUMNS.values(), "ten_kenh", "trang_thai_quet", "cap_nhat_lan_cuoi"}),
}
COLUMN_TYPES = {
    **dict.fromkeys(("luot_xem", "tim", "binh_luan", "luot_luu", "repost", "chia_se"), "bigint"),
    "ten_kenh": "text", "trang_thai_quet": "text", "link_phan_giai": "text", "link_nguon_phan_giai": "text",
    "cap_nhat_lan_cuoi": "timestamptz",
}


class MonthError(ValueError):
    pass


def parse_thang(value) -> tuple[date, date]:
    """'YYYY-MM' -> [first day, first day of the next month). Never the year-less 'Tháng 10' (H-05)."""
    text = clean_text(value)
    match = re.fullmatch(r"(\d{4})-(\d{2})", text)
    if not match:
        raise MonthError("thang phải có dạng YYYY-MM")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise MonthError("thang ngoài khoảng hợp lệ")
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return start, end


def current_thang(now: datetime | None = None) -> str:
    moment = (now or datetime.now(UTC)).astimezone(LOCAL_TZ)
    return f"{moment.year:04d}-{moment.month:02d}"


def utc_now() -> datetime:
    return datetime.now(UTC)


def display_timestamp(value) -> str:
    """timestamptz -> the workbook's 'dd/mm/YYYY-HH:MM' text in Vietnam time ('' when NULL)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(LOCAL_TZ).strftime(DISPLAY_DATETIME_FORMAT)
    return clean_text(value)


def _count(value):
    """A confirmed count for a bigint column (Threads metrics are already ints)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, Decimal)) or (isinstance(value, float) and value.is_integer()):
        return int(value)
    return None


def tiktok_update_columns(existing_channel, url, data, channel_name, status, *, resolved_url="", now=None,
                          channel_cache=None, channel_overrides=None, cache_lock=None):
    """Columns one TikTok result writes; mirrors workbook_io.write_result cell by cell."""
    from riviu.platforms.tiktok import channels
    from riviu.platforms.tiktok.constants import METRIC_KEYS
    from riviu.platforms.tiktok.parsing import metric_or_none
    from riviu.platforms.tiktok.statuses import should_clear_stale_metrics
    from riviu.workbook.links import normalize_tiktok_url

    columns = {}
    success = status == "Success"
    if success or should_clear_stale_metrics(status):
        for metric_key, data_key in METRIC_KEYS.items():
            columns[TIKTOK_METRIC_COLUMNS[metric_key]] = metric_or_none((data or {}).get(data_key)) if success else None
    columns["trang_thai_quet"] = clean_text(status) or None
    if success and resolved_url:
        columns["link_phan_giai"] = normalize_tiktok_url(resolved_url) or None
    if success:
        columns["link_nguon_phan_giai"] = normalize_tiktok_url(url) or None

    channel_value = channels.channel_name_for_sheet(
        url, channel_name, resolved_url=resolved_url if success else "", status=status,
        channel_cache=channel_cache, channel_overrides=channel_overrides, cache_lock=cache_lock,
    )
    existing = clean_text(existing_channel)
    # Same two rules as write_result: a failure keeps any usable channel, and a better
    # existing channel is never downgraded.
    existing_quality = channels.channel_name_quality(existing, url)
    if (not success and existing_quality > 0) or existing_quality > channels.channel_name_quality(channel_value, url):
        channel_value = existing
    # Writing the value already there is a no-op in the workbook; leaving it out here
    # also keeps a staff edit made during the scan.
    if clean_text(channel_value) != existing:
        columns["ten_kenh"] = clean_text(channel_value) or None

    if success:
        columns["cap_nhat_lan_cuoi"] = now or utc_now()
    return columns


def threads_update_columns(result, *, now=None):
    """Columns one Threads result writes; mirrors threads.write_threads_result."""
    from riviu.platforms.threads import result_status

    columns = {}
    channel = result.get("channel")
    if channel:
        columns["ten_kenh"] = channel
    metrics = result.get("metrics") or {}
    for key, column in THREADS_METRIC_COLUMNS.items():
        value = metrics.get(key)
        if value is not None:
            columns[column] = _count(value)
        elif key == "views" and result.get("metricSources", {}).get("views") == "view_query_unknown":
            # Explicit exact-null/conflict invalidates stale display-derived views.
            columns[column] = None
    columns["trang_thai_quet"] = result_status(result)
    if channel or any(value is not None for value in metrics.values()):
        columns["cap_nhat_lan_cuoi"] = now or utc_now()
    return columns


def report_row(spec, record, sheet_name):
    """One DB row as the workbook report dict (report_rows._build_sheet_report_rows), or None.

    record: mapping with ngay_air, ten_kenh, link_air, doi_tac, the metric columns,
    cap_nhat_lan_cuoi, trang_thai_quet and (TikTok) link_phan_giai / link_nguon_phan_giai.
    """
    link = spec.normalize_url(record["link_air"])
    if not link or is_total_label(link) or not spec.is_link(link):
        return None
    ngay_air = record["ngay_air"]
    metric_columns = REPORT_METRIC_COLUMNS[spec.key]
    return {
        "sheet_name": sheet_name,
        # openpyxl hands the workbook path datetimes for date cells.
        "NGÀY AIR": datetime(ngay_air.year, ngay_air.month, ngay_air.day) if ngay_air else "",
        "TÊN KÊNH": spec.display_channel(link, clean_text(record["ten_kenh"])),
        "LINK AIR": link,
        **{metric: (record[metric_columns[metric]] if metric in metric_columns else "") for metric in ALL_REPORT_METRICS},
        LAST_UPDATE_COLUMN: display_timestamp(record["cap_nhat_lan_cuoi"]),
        "partners": partners_from_doi_tac(record["doi_tac"]),
        "_scanStatus": clean_text(record["trang_thai_quet"]),
        "_resolvedUrl": clean_text(_get(record, "link_phan_giai")),
        "_resolvedSourceUrl": clean_text(_get(record, "link_nguon_phan_giai")),
    }


def _get(record, key):
    try:
        return record[key]
    except (KeyError, IndexError):
        return None


def report_partner_names(records):
    """Every partner named on the rows (links or not), first spelling, in row order."""
    names = {}
    for record in records:
        for partner in partners_from_doi_tac(record["doi_tac"]):
            names.setdefault(partner_dedup_key(partner), partner)
    return tuple(names.values())
