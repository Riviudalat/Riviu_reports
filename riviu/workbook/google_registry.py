"""Google Sheet sources: synced file names and labels, sheet-title map, and the source registry."""

import json
import os
import re
from urllib.parse import urlparse

from openpyxl.packaging.custom import StringProperty

from riviu.workbook.atomic import write_json_atomic
from riviu.workbook.constants import DATA_DIR_NAME, GOOGLE_SHEET_REGISTRY_FILENAME, GOOGLE_SHEET_TITLE_MAP_PROPERTY
from riviu.workbook.dates import format_display_datetime, parse_filename_datetime_stamp
from riviu.workbook.files import ensure_data_dir, read_json_object
from riviu.workbook.naming import safe_excel_sheet_title, safe_workbook_filename
from riviu.workbook.text import clean_text


def parse_google_spreadsheet_id(source_url):
    parsed = urlparse(source_url.strip())
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", parsed.path)
    if not match:
        raise ValueError("URL Google Sheet không hợp lệ")
    return match.group(1)


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
    return read_json_object(google_sheet_registry_path(base_dir))


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
