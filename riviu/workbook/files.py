"""User-data directory layout: the data/ folder, safe paths and small JSON state files."""

import json
import os

from riviu.workbook.constants import CHANNEL_OVERRIDE_FILENAME, DATA_DIR_NAME
from riviu.workbook.text import clean_text


def ensure_data_dir(base_dir):
    data_dir = os.path.join(base_dir, DATA_DIR_NAME)
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def is_internal_workbook_filename(filename):
    """Skip Excel/atomic-save temp files and underscore-prefixed internal/test workbooks."""
    name = os.path.basename(str(filename or ""))
    return not name or name.startswith(("~$", "_", "."))


def base_dir_for_file(file_path):
    absolute_path = os.path.abspath(file_path)
    parent = os.path.dirname(absolute_path)
    if os.path.basename(parent).casefold() == DATA_DIR_NAME:
        return os.path.dirname(parent)
    return parent


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


def read_json_object(path):
    """The JSON object stored at path; {} when it is missing, unreadable or not an object."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def channel_override_path(file_path):
    return os.path.join(ensure_data_dir(base_dir_for_file(file_path)), CHANNEL_OVERRIDE_FILENAME)


def load_channel_overrides(file_path):
    data = read_json_object(channel_override_path(file_path))
    return {str(key).casefold(): clean_text(value) for key, value in data.items() if clean_text(value)}
