"""Timestamp formats used in sheet titles, file names, "Cập nhật lần cuối" cells and previews."""

import re
from datetime import datetime

from riviu.workbook.constants import (
    DISPLAY_DATETIME_FORMAT,
    FILENAME_DATETIME_FORMAT,
    GOOGLE_SHEET_DATETIME_FORMAT,
    SHEET_DATETIME_FORMAT,
)
from riviu.workbook.text import clean_text


def _format_moment(moment, pattern):
    value = moment or datetime.now()
    return value.strftime(pattern)


def format_display_datetime(moment=None):
    return _format_moment(moment, DISPLAY_DATETIME_FORMAT)


def format_filename_datetime(moment=None):
    return _format_moment(moment, FILENAME_DATETIME_FORMAT)


def format_excel_sheet_datetime(moment=None):
    return _format_moment(moment, SHEET_DATETIME_FORMAT)


def format_google_sheet_datetime(moment=None):
    return _format_moment(moment, GOOGLE_SHEET_DATETIME_FORMAT)


def display_datetime_sort_key(value):
    """Order "dd/mm/YYYY-HH:MM" stamps chronologically; unparseable text sorts first."""
    text = clean_text(value)
    try:
        return (1, datetime.strptime(text, DISPLAY_DATETIME_FORMAT), "")
    except ValueError:
        return (0, datetime.min, text)


def parse_filename_datetime_stamp(stamp):
    text = clean_text(stamp)
    match = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return text
    day, month, year, hour, minute = match.groups()
    return f"{day}/{month}/{year}-{hour}:{minute}"


def preview_datetime_text(value):
    """A date-only cell (midnight) shows as dd/mm/yyyy; a time is shown only when there is one."""
    if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
        return value.strftime("%d/%m/%Y")
    return value.strftime(DISPLAY_DATETIME_FORMAT)
