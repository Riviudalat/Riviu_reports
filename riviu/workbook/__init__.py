"""Workbook reading and writing, split by concern; `riviu.workbook_utils` re-exports it all."""

# Every workbook read (scans, previews, reports) goes through openpyxl: read
# Google-export inline strings on the fast path before any of them runs.
from riviu.workbook import inline_strings as _inline_strings  # noqa: F401
