"""Atomic replacement of workbooks and JSON state files."""

import json
import os
import tempfile

from riviu.workbook.snapshot import WORKBOOK_READ_CACHE


def _replace_atomically(path, write):
    # Hidden temp name: an interrupted save must never show up as a selectable workbook.
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temp_path = tempfile.mkstemp(prefix=".riviu-", suffix=".tmp", dir=directory)
    os.close(descriptor)
    try:
        write(temp_path)
        os.replace(temp_path, path)
        # Reads are keyed by content, so this is memory hygiene: the old version is gone.
        WORKBOOK_READ_CACHE.invalidate(path)
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
