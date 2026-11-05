"""What the report pages show on Riviu Data: each month as its last finished scan left it.

Staff keep adding links to Riviu Data all day, and a scan writes its numbers row by row. The
owner wants the report pages (link list, Người đăng, Tổng kết, Excel exports) to change only
when a scan of the month has finished (2026-11): links added after that scan started stay out,
and the numbers a running scan is writing stay out, until the next scan of the month ends.

`report_rows(store, thang)` gives that month's snapshot:
- no finished scan of the month (old months nobody scans): the rows as they are now;
- otherwise the rows added before the last finished scan started, read once and kept for that
  scan_run, so a later scan's partial writes never reach the page. While a scan of the month runs
  and nothing is kept yet (after a restart), the rows are read now, still without newer links.

`refresh(platform, thang)` is called when a scan of the month ends, so the snapshot is taken at
once rather than on the first page view (by then a new scan may already be writing).
The snapshot lives in memory; after a restart the first read takes it again.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("riviu.store.published")

# (platform, thang) -> (scan_run id, SheetReportRows with ids)
_SNAPSHOTS: dict[tuple[str, str], tuple[int, object]] = {}
_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _lock(key):
    lock = _LOCKS.get(key)
    if lock is None:
        lock = _LOCKS[key] = asyncio.Lock()
    return lock


async def report_rows(store, thang):
    """The month's published report rows, with `_id` and `_nguoiAir` (see the module doc)."""
    key = (store.platform, thang)
    last = await store.last_finished_scan(thang)
    if last is None:
        return await store.report_rows(thang, with_ids=True)
    run_id, started_at, running = last
    kept = _SNAPSHOTS.get(key)
    if kept is not None and kept[0] == run_id:
        return kept[1]
    async with _lock(key):
        kept = _SNAPSHOTS.get(key)
        if kept is not None and kept[0] == run_id:
            return kept[1]
        rows = await store.report_rows(thang, with_ids=True, created_before=started_at)
        # A running scan of this month is writing numbers: show them, but do not keep them.
        if not running:
            _SNAPSHOTS[key] = (run_id, rows)
        return rows


async def refresh(store, thang):
    """Take the month's snapshot now (a scan of it just ended). Best effort: a failure is logged."""
    try:
        await report_rows(store, thang)
    except Exception as error:  # noqa: BLE001 - the page takes it on its next read instead
        log.warning("published snapshot %s/%s failed: %s", store.platform, thang, type(error).__name__)


def kept_count(platform, thang):
    """Rows in the month's kept snapshot, or None when none is kept (the month list's link count)."""
    kept = _SNAPSHOTS.get((platform, thang))
    return len(kept[1].rows) if kept is not None else None


def clear():
    """Test seam."""
    _SNAPSHOTS.clear()
    _LOCKS.clear()
