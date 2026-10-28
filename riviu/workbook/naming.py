"""Sheet and file naming: data vs summary vs result sheets, per-platform summary titles (pure)."""

import hashlib
import re
from datetime import date

from riviu.workbook.constants import (
    RESULT_SHEET_PREFIX,
    SUMMARY_SHEET_NAME,
    SUMMARY_SHEET_TITLE_PREFIX,
    THREADS_RESULT_SHEET_PREFIX,
)
from riviu.workbook.dates import format_excel_sheet_datetime
from riviu.workbook.platforms import PLATFORMS, get_platform
from riviu.workbook.text import clean_text, normalize_key


RESULT_SHEET_TIMESTAMP_RE = re.compile(
    r"^(?:T\d{1,2}\s+)?\d{2}-\d{2}-\d{4}-\d{2}[:-]?\d{2}(?:-\d+)?$"
)


def safe_workbook_filename(name, *, max_length=80):
    filename = re.sub(r'[\\/:*?"<>|]+', "-", clean_text(name))
    filename = re.sub(r"\s+", " ", filename).strip(" .")
    return filename[:max_length] if filename else "google-sheet"


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


def result_sheet_display_name(timestamp_text=None):
    stamp = clean_text(timestamp_text) or format_excel_sheet_datetime()
    return safe_excel_sheet_title(stamp)


def summary_sheet_title_for_data_sheet(data_sheet_name, platform="tiktok"):
    """Build a stable per-source, per-platform summary title without truncation collisions.

    Each platform owns its own tab ("Tổng kết <sheet>" for TikTok, the legacy title;
    "Tổng kết Threads <sheet>" for Threads), so scanning one platform of a sheet that holds
    both kinds of links never overwrites the other platform's summary.
    """
    tag = get_platform(platform).summary_tag
    prefix = f"{SUMMARY_SHEET_TITLE_PREFIX}{tag} " if tag else SUMMARY_SHEET_TITLE_PREFIX
    label = clean_text(data_sheet_name).lower()
    if not label:
        return prefix.strip() if tag else SUMMARY_SHEET_NAME
    title = f"{prefix}{label}"
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


# "Tháng 10", "tháng 10", "Thg 10", "Tháng 10/2026", "Tháng 10 2026", "Báo cáo tháng 10-26" (after
# normalize_key: lower case, no diacritics). A two-digit year needs a / . - _ separator so that a
# trailing day ("Tháng 10 12") is never read as a year.
_MONTH_WORD_RE = re.compile(
    r"(?<![a-z0-9])(?:thang|thg)\s*0*(\d{1,2})(?!\d)(?:\s*[/._-]\s*(\d{4}|\d{2})(?!\d)|\s+(\d{4})(?!\d))?"
)
# "T10", "T 10", "T10/2026", "T10-26", "T10 2026", optionally followed by more words ("T10 seeding").
_MONTH_SHORT_RE = re.compile(
    r"^t\s?0*(\d{1,2})(?:\s*[/._-]\s*(\d{4}|\d{2})(?!\d)|\s+(\d{4})(?!\d))?(?=$|[\s/._-])"
)


def _today():
    """Today's date; the month picker's clock (tests replace it)."""
    return date.today()


def month_sheet_key(sheet_name, today=None):
    """`(year, month)` of a month data sheet ("Tháng 10", "T10/2026", ...), else None.

    A name without a year means the latest such month not after `today` (default: now), so in
    January "Tháng 12" is last December and sorts before "Tháng 1". Summary and generated
    result/push tabs ("Tổng kết tháng 10", "T10 12-10-2026-10-31") are never month sheets.
    """
    if not clean_text(sheet_name) or not is_data_sheet_name(sheet_name):
        return None
    key = normalize_key(sheet_name)
    match = _MONTH_WORD_RE.search(key) or _MONTH_SHORT_RE.match(key)
    if not match:
        return None
    month = int(match.group(1))
    if not 1 <= month <= 12:
        return None
    year_text = match.group(2) or match.group(3)
    today = today or _today()
    if year_text:
        year = int(year_text) + (2000 if len(year_text) == 2 else 0)
        if not 2000 <= year <= 2099:
            return None
    else:
        year = today.year if month <= today.month else today.year - 1
    return year, month


def newest_month_sheet(sheet_names, today=None):
    """The data sheet of the newest month, or "" when no month sheet is recognised.
    Ties ("Tháng 10" and "tháng 10") go to the first in the given order (visible sheets first)."""
    best_name, best_key = "", None
    for name in sheet_names or ():
        key = month_sheet_key(name, today)
        if key is not None and (best_key is None or key > best_key):
            best_name, best_key = name, key
    return best_name


def default_data_sheet(sheet_names, today=None):
    """The sheet a freshly loaded or synced workbook opens on: the newest month sheet, else the
    first data sheet (the old behaviour)."""
    names = [name for name in sheet_names or () if is_data_sheet_name(name)]
    return newest_month_sheet(names, today) or (names[0] if names else "")


def keep_or_newest_month_sheet(sheet_names, saved="", today=None):
    """After a re-sync: the newest month sheet, unless the saved sheet still exists and no newer
    month is present (same month, or no month sheet recognised at all)."""
    names = [name for name in sheet_names or () if is_data_sheet_name(name)]
    if saved and saved in names:
        newest = newest_month_sheet(names, today)
        saved_key = month_sheet_key(saved, today)
        if not newest or (saved_key is not None and saved_key >= month_sheet_key(newest, today)):
            return saved
    return default_data_sheet(names, today)


def resolve_summary_sheet(workbook_sheet_names, summary_sheet_name):
    """(data sheet, platform key) owning a per-sheet summary tab.

    The legacy bare "Tổng kết" tab and tabs whose data sheet is gone resolve to ("", "tiktok"):
    every summary written before platform-specific tabs existed was a TikTok summary.
    """
    text = clean_text(summary_sheet_name)
    if normalize_key(text) == normalize_key(SUMMARY_SHEET_NAME):
        return "", "tiktok"
    prefix_folded = SUMMARY_SHEET_TITLE_PREFIX.casefold()
    if not text.casefold().startswith(prefix_folded) or not text[len(SUMMARY_SHEET_TITLE_PREFIX) :].strip():
        return "", "tiktok"
    for candidate in workbook_sheet_names:
        if is_summary_sheet_name(candidate) or is_result_sheet_name(candidate):
            continue
        for key in PLATFORMS:
            if summary_sheet_title_for_data_sheet(candidate, key).lower() == text.lower():
                return candidate, key
    return "", "tiktok"


def data_sheet_name_for_summary_title(workbook_sheet_names, summary_sheet_name):
    """Resolve the source data sheet name from a per-sheet summary tab title."""
    return resolve_summary_sheet(workbook_sheet_names, summary_sheet_name)[0]


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


def is_data_sheet_name(sheet_name):
    return not is_summary_sheet_name(sheet_name) and not is_result_sheet_name(sheet_name)


def workbook_data_sheet_names(workbook):
    """Data sheets of an openpyxl workbook, in workbook order."""
    return [sheet for sheet in workbook.sheetnames if is_data_sheet_name(sheet)]


def hidden_sheet_names_in_workbook(workbook):
    """Titles of the worksheets Excel hides (hidden or veryHidden), in workbook order."""
    try:
        return [ws.title for ws in workbook.book.worksheets if ws.sheet_state != "visible"]
    except AttributeError:
        return []


def find_data_sheet_names_in_workbook(workbook):
    """Data sheets with visible ones first: callers default to [0], which must not be a hidden old month."""
    data_sheets = [sheet for sheet in workbook.sheet_names if is_data_sheet_name(sheet)]
    hidden = set(hidden_sheet_names_in_workbook(workbook))
    return [sheet for sheet in data_sheets if sheet not in hidden] + [sheet for sheet in data_sheets if sheet in hidden]
