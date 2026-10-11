"""The workbook picker: local workbooks in the data root plus synced Google Sheets in data/."""

import os

from riviu.workbook.constants import DATA_DIR_NAME, GOOGLE_SHEET_LABEL, LEGACY_GOOGLE_SHEET_FILE_ID
from riviu.workbook.files import ensure_data_dir, is_internal_workbook_filename
from riviu.workbook.google_registry import google_sheet_filename_to_label, load_google_sheet_registry
from riviu.workbook.text import clean_text


def _is_listed_workbook(filename):
    return not is_internal_workbook_filename(filename) and (filename.endswith(".xlsx") or filename.endswith(".xls"))


def workbook_file_entries(base_dir):
    data_dir = ensure_data_dir(base_dir)
    entries = []

    for filename in os.listdir(base_dir):
        if _is_listed_workbook(filename):
            entries.append({
                "id": filename.replace("\\", "/"),
                "label": filename,
                "source": "local",
            })

    registry = load_google_sheet_registry(base_dir)
    for filename in os.listdir(data_dir):
        if not _is_listed_workbook(filename):
            continue
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
