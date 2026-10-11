"""Immutable in-memory workbook versions and the process-wide cache of values parsed from them."""

import hashlib
import io
import os
import threading
from collections import OrderedDict
from contextlib import contextmanager

import pandas as pd

from riviu.workbook.naming import find_data_sheet_names_in_workbook, hidden_sheet_names_in_workbook
from riviu.workbook.xlsx_formulas import worksheet_column_formulas


def load_excel_file(file_path):
    return pd.ExcelFile(file_path, engine="openpyxl")


# Per sheet: its frame, preview and report rows (~1-5 MB each for a 1,600-row month sheet).
WORKBOOK_READ_CACHE_MAX_ENTRIES = 12


def workbook_path_key(file_path):
    return os.path.normcase(os.path.abspath(file_path))


class WorkbookReadCache:
    """Small thread-safe LRU of values parsed from one exact workbook version.

    Every key starts with the version the value was parsed from: (absolute path, mtime_ns,
    size, content digest). The value is computed from those very bytes, so a cached value
    can never be served for different file content, whoever writes the file. Atomic saves
    also drop the path's entries so a replaced version stops holding memory. Concurrent
    misses for the same key compute once; the others wait for that result.
    """

    def __init__(self, max_entries=WORKBOOK_READ_CACHE_MAX_ENTRIES):
        self.max_entries = max_entries
        self._entries = OrderedDict()
        self._loading = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key, compute):
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return self._entries[key]
            loading = self._loading.setdefault(key, threading.Lock())
        with loading:
            with self._lock:
                if key in self._entries:
                    self._entries.move_to_end(key)
                    return self._entries[key]
            try:
                value = compute()
                with self._lock:
                    self._entries[key] = value
                    self._entries.move_to_end(key)
                    while len(self._entries) > self.max_entries:
                        self._entries.popitem(last=False)
                return value
            finally:
                with self._lock:
                    if self._loading.get(key) is loading:
                        del self._loading[key]

    def invalidate(self, file_path):
        path_key = workbook_path_key(file_path)
        with self._lock:
            for key in [key for key in self._entries if key[0][0] == path_key]:
                del self._entries[key]

    def clear(self):
        with self._lock:
            self._entries.clear()

    def __len__(self):
        with self._lock:
            return len(self._entries)


WORKBOOK_READ_CACHE = WorkbookReadCache()


class WorkbookSnapshot:
    """One immutable version of a workbook file, read into memory once.

    All reads go through the bytes captured here, so one request (an export of many partners,
    a partner list) sees one consistent version even if a scan replaces the file meanwhile,
    and the source file is not held open while it is parsed. Parsed pieces are shared through
    WORKBOOK_READ_CACHE; values handed out are copies the caller may modify.
    """

    def __init__(self, file_path):
        self.path = os.path.abspath(file_path)
        modified_ns = os.stat(self.path).st_mtime_ns
        with open(self.path, "rb") as stream:
            self.content = stream.read()
        digest = hashlib.blake2b(self.content, digest_size=16).hexdigest()
        self.version = (workbook_path_key(self.path), modified_ns, len(self.content), digest)
        self._excel = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def close(self):
        if self._excel is not None:
            self._excel.close()
            self._excel = None

    def excel(self):
        if self._excel is None:
            self._excel = load_excel_file(io.BytesIO(self.content))
        return self._excel

    def cached(self, item, compute):
        return WORKBOOK_READ_CACHE.get_or_compute((self.version, item), compute)

    def _sheet_lists(self):
        def compute():
            excel = self.excel()
            return (
                tuple(excel.sheet_names),
                tuple(find_data_sheet_names_in_workbook(excel)),
                tuple(hidden_sheet_names_in_workbook(excel)),
            )

        return self.cached(("sheets",), compute)

    @property
    def sheet_names(self):
        return list(self._sheet_lists()[0])

    @property
    def data_sheet_names(self):
        return list(self._sheet_lists()[1])

    @property
    def hidden_sheet_names(self):
        return list(self._sheet_lists()[2])

    def frame(self, sheet_name):
        """The sheet parsed by pandas (header on row 1), as a copy the caller may modify."""
        return self.cached(("frame", sheet_name), lambda: self.excel().parse(sheet_name)).copy()

    # Only the cached preview needs these two, so they are not cached on their own.
    def header_row(self, sheet_name):
        """Raw header cells, without pandas' "Unnamed: N" / "X.1" renaming (reads one row)."""
        header_frame = self.excel().parse(sheet_name, header=None, nrows=1)
        return header_frame.iloc[0].tolist() if len(header_frame.index) else []

    def column_formulas(self, sheet_name, column_letter):
        """{sheet row: formula text} for one column; data_only parsing hides formulas."""
        return worksheet_column_formulas(self.content, sheet_name, column_letter)


@contextmanager
def open_workbook_snapshot(source):
    if isinstance(source, WorkbookSnapshot):
        yield source
        return
    with WorkbookSnapshot(source) as snapshot:
        yield snapshot


def workbook_sheet_names(file_path):
    with open_workbook_snapshot(file_path) as snapshot:
        return snapshot.sheet_names


def find_data_sheet_names(file_path):
    with open_workbook_snapshot(file_path) as snapshot:
        return snapshot.data_sheet_names


def find_data_sheet_listing(file_path):
    """(data sheets, hidden sheet titles) from one workbook read."""
    with open_workbook_snapshot(file_path) as snapshot:
        return snapshot.data_sheet_names, snapshot.hidden_sheet_names
