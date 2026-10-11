"""Scrape history (data/scrape_history.json) and the metric totals recorded with each run."""
import json
import os

from riviu.workbook_utils import (
    clean_text,
    is_scrapable_tiktok_url,
    metric_number,
    workbook_data_sheet_names,
    write_json_atomic,
)
from riviu.platforms.tiktok.workbook_io import detect_columns


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
