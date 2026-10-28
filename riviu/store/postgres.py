"""The Postgres backend (Riviu Data): rows to scan, buffered result writes, scan_run, reports.

Connects only as `riviu_scanner` (riviu_scanner.sql / migration 0011). Contract: Riviu Data
`docs/riviu-reports-schema.md`.

Since Riviu Data migration 0011 the post list lives in `public.<t>` (read through the
soft-delete views `v_tiktok` / `v_threads`) and the channel, metrics, scan status, resolved
links and lock live in Riviu Reports' schema `bao_cao` (`bao_cao.<t>_chi_so`, one row per
post id, absent = nothing known yet). Reads are `v_<t> LEFT JOIN bao_cao.<t>_chi_so USING (id)`
and select a month by `thang_nam` (the row's month sheet, review H-05), never by text. Rows
with `khoa_chi_so = true` are not scanned at all (DESIGN 5.4, review H-07).

Writes are buffered in memory (no network I/O while a result is recorded) and flushed in
one short transaction per checkpoint: upserts into `bao_cao.<t>_chi_so` grouped by the set
of columns they change, each guarded by `EXISTS (live post with id and link_air = <value
read at open>)` and, on conflict, `NOT khoa_chi_so`. A row edited, deleted or locked during
the scan therefore gets no write; it is counted, logged ("dòng đã bị sửa trong lúc quét, bỏ
qua") and recorded in scan_run_item. `bao_cao` is neither audited nor broadcast by Riviu
Data, so flushes set no `app.user` / `app.bulk` and send no NOTIFY.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

from riviu.store import rules
from riviu.store.config import SCANNER_ROLE
from riviu.workbook.partners import partners_from_doi_tac
from riviu.workbook.report_rows import SheetReportRows
from riviu.workbook.text import clean_text

log = logging.getLogger("riviu.store.postgres")

APPLICATION_NAME = "riviu-reports-scanner"
TABLES = {"tiktok": "tiktok", "threads": "threads"}
# Fixed per-table statements (identifiers come from TABLES only, never from input).
LOCKED_COUNT_SQL = {
    table: f"SELECT count(*) FROM public.v_{table} v JOIN bao_cao.{table}_chi_so c USING (id) "
    "WHERE v.thang_nam >= $1 AND v.thang_nam < $2 AND c.khoa_chi_so"
    for table in TABLES.values()
}
# public.<t> (not the view): a row soft-deleted during the scan must be told apart from a gone one.
SKIP_REASON_SQL = {
    table: f"SELECT t.id, t.link_air, t.da_xoa_luc IS NOT NULL AS deleted, coalesce(c.khoa_chi_so, false) AS khoa_chi_so "
    f"FROM public.{table} t LEFT JOIN bao_cao.{table}_chi_so c USING (id) WHERE t.id = ANY($1::bigint[])"
    for table in TABLES.values()
}
UPDATE_CHUNK = 500
SKIP_LOG_LIMIT = 20

PHASE_RUNNING = "dang_quet"
PHASE_DONE = "hoan_tat"
PHASE_STOPPED = "dung_som"
PHASE_FAILED = "loi"
PHASE_CANCELLED = "huy"


class StoreError(RuntimeError):
    pass


class ScanRunNotFound(StoreError):
    pass


def _table(platform):
    table = TABLES.get(platform)
    if table is None:
        raise StoreError("Nền tảng không hợp lệ")
    return table


async def connect(dsn):
    """One asyncpg connection as riviu_scanner (refused under any other role)."""
    import asyncpg

    conn = await asyncpg.connect(dsn, server_settings={"application_name": APPLICATION_NAME})
    try:
        user = await conn.fetchval("SELECT current_user")
        if user != SCANNER_ROLE:
            raise StoreError(f"Kết nối Postgres phải dùng role {SCANNER_ROLE} (đang là {user}).")
    except BaseException:
        await conn.close()
        raise
    return conn


# ---- Advisory lock (one scan per platform across processes) -------------------------

async def try_scan_lock(conn, platform):
    return bool(await conn.fetchval("SELECT pg_try_advisory_lock(hashtext('scan:' || $1::text))", platform))


async def release_scan_lock(conn, platform):
    return bool(await conn.fetchval("SELECT pg_advisory_unlock(hashtext('scan:' || $1::text))", platform))


# ---- scan_run -------------------------------------------------------------------------

async def begin_scan_run(conn, *, platform, month_start, trigger, started_by, scan_run_id=None):
    """The scan_run id for this scan: the requested one (phase 'yeu_cau' from Riviu Data) or a new row.

    UPDATE on scan_run is granted (migration 0011, riviu_scanner.sql); a missing grant is a
    deployment error and fails the start instead of silently recording a second run.
    """
    if scan_run_id is not None:
        run_id = await conn.fetchval(
            "UPDATE public.scan_run SET phase = $3, thang_nam = $4, mode = coalesce(mode, $5) "
            "WHERE id = $1 AND platform = $2 AND finished_at IS NULL RETURNING id",
            scan_run_id, platform, PHASE_RUNNING, month_start, trigger,
        )
        if run_id is None:
            raise ScanRunNotFound(f"scan_run {scan_run_id} không tồn tại, khác nền tảng hoặc đã kết thúc")
        return run_id
    return await conn.fetchval(
        "INSERT INTO public.scan_run (platform, thang_nam, phase, mode, started_by) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id",
        platform, month_start, PHASE_RUNNING, trigger, started_by,
    )


async def update_scan_run(conn, run_id, *, totals=None, phase=None, stop_reason=None, diagnostic=None, finished=False):
    """Best effort: progress and the final state. A failure is logged, never fails the scan."""
    try:
        await conn.execute(
            "UPDATE public.scan_run SET "
            "totals = coalesce($2::jsonb, totals), phase = coalesce($3, phase), "
            "stop_reason = coalesce($4, stop_reason), diagnostic = coalesce(diagnostic, '{}'::jsonb) || coalesce($5::jsonb, '{}'::jsonb), "
            "finished_at = CASE WHEN $6 THEN now() ELSE finished_at END "
            "WHERE id = $1",
            run_id,
            json.dumps(totals, ensure_ascii=False, default=str) if totals is not None else None,
            phase, stop_reason,
            json.dumps(diagnostic, ensure_ascii=False, default=str) if diagnostic is not None else None,
            finished,
        )
        return True
    except Exception as error:  # noqa: BLE001 - bookkeeping must not fail a scan
        log.warning("scan_run %s update failed: %s", run_id, type(error).__name__)
        return False


# ---- Reads ------------------------------------------------------------------------------

# The post list joined to its bao_cao row (NULLs when the post has none yet, schema doc §3).
_MONTH_FROM = (
    "FROM public.v_{t} v LEFT JOIN bao_cao.{t}_chi_so c USING (id) "
    "WHERE v.thang_nam >= $1 AND v.thang_nam < $2"
)


def _scan_query(platform):
    table = _table(platform)
    links = ", c.link_phan_giai, c.link_nguon_phan_giai" if platform == "tiktok" else ""
    return (
        f"SELECT v.id, v.link_air, v.doi_tac, c.ten_kenh{links} "
        + _MONTH_FROM.format(t=table)
        + " AND NOT coalesce(c.khoa_chi_so, false) ORDER BY v.id"
    )


def _report_query(platform):
    table = _table(platform)
    if platform == "tiktok":
        metrics = "c.luot_xem, c.tim, c.binh_luan, c.luot_luu, c.chia_se"
        links = ", c.link_phan_giai, c.link_nguon_phan_giai"
    else:
        metrics, links = "c.luot_xem, c.tim, c.binh_luan, c.repost, c.chia_se", ""
    return (
        f"SELECT v.id, v.ngay_air, c.ten_kenh, v.link_air, v.doi_tac, {metrics}, "
        f"c.cap_nhat_lan_cuoi, c.trang_thai_quet{links} "
        + _MONTH_FROM.format(t=table)
        + " ORDER BY v.id"
    )


def tiktok_scan_rows(records, label, selected_keys=None):
    """DB rows -> the runner's row dicts, through the workbook's own row rules."""
    from riviu.platforms.tiktok.workbook_io import scan_row_fields

    rows = []
    for record in records:
        fields = scan_row_fields(
            record["link_air"], record["link_nguon_phan_giai"], record["link_phan_giai"],
            partners_from_doi_tac(record["doi_tac"]), selected_keys or set(),
        )
        if fields is None:
            continue
        rows.append({
            "sequence": len(rows) + 1, "sheet_name": label, "row": record["id"], **fields,
            "channel": clean_text(record["ten_kenh"]), "link_seen": record["link_air"],
        })
    return rows


def threads_scan_rows(records, label, selected_keys=None):
    """DB rows -> (rows, invalid_links) through the workbook's own Threads row rules."""
    from riviu.platforms.threads import classify_threads_row

    rows, invalid_links = [], []
    for record in records:
        partners = partners_from_doi_tac(record["doi_tac"])
        outcome = classify_threads_row(record["link_air"], partners, selected_keys or set())
        if outcome is None:
            continue
        if outcome[0] == "invalid":
            info = outcome[1]
            invalid_links.append({"sheet_name": label, "row": record["id"], "kind": info["kind"], "display": info["display"]})
            continue
        rows.append({
            "sheet_name": label, "row": record["id"], "url": outcome[1], "partners": partners,
            "channel": clean_text(record["ten_kenh"]), "link_seen": record["link_air"],
        })
    return rows, invalid_links


class PostgresStore:
    """One platform's rows in Riviu Data."""

    backend = "postgres"

    def __init__(self, platform, dsn, *, connector=connect):
        self.platform = platform
        self.table = _table(platform)
        self.dsn = dsn
        self._connect = connector

    async def connect(self):
        return await self._connect(self.dsn)

    async def channel_overrides(self, conn):
        records = await conn.fetch(
            "SELECT link_or_username, ten_kenh FROM public.channel_override WHERE platform = $1", self.platform,
        )
        return {
            str(record["link_or_username"]).casefold(): clean_text(record["ten_kenh"])
            for record in records if clean_text(record["ten_kenh"])
        }

    async def open_scan(self, thang, *, selected_partners=None, run_id=None, events=None):
        """Read the month's rows in one consistent snapshot; the session keeps its connection for flushes."""
        month_start, month_end = rules.parse_thang(thang)
        selected_keys = {clean_text(name).casefold() for name in selected_partners or [] if clean_text(name)}
        conn = await self.connect()
        try:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                records = await conn.fetch(_scan_query(self.platform), month_start, month_end)
                locked = await conn.fetchval(LOCKED_COUNT_SQL[self.platform], month_start, month_end)
                overrides = await self.channel_overrides(conn) if self.platform == "tiktok" else {}
        except BaseException:
            await conn.close()
            raise
        if self.platform == "tiktok":
            rows, invalid = tiktok_scan_rows(records, thang, selected_keys), []
        else:
            rows, invalid = threads_scan_rows(records, thang, selected_keys)
        return PostgresScanSession(
            self, conn, thang, rows, invalid_links=invalid, channel_overrides=overrides,
            locked_count=int(locked or 0), run_id=run_id, events=events,
        )

    async def months(self):
        """The month sheets that hold live rows, newest first: [("YYYY-MM", row count), ...].

        Taken from the rows themselves (staff create and move rows between months), never derived
        from ngay_air; rows with thang_nam NULL belong to no month and are left out.
        """
        conn = await self.connect()
        try:
            records = await conn.fetch(
                f"SELECT thang_nam, count(*) AS rows FROM public.v_{self.table} "
                "WHERE thang_nam IS NOT NULL GROUP BY thang_nam ORDER BY thang_nam DESC"
            )
        finally:
            await conn.close()
        return [(f"{record['thang_nam'].year:04d}-{record['thang_nam'].month:02d}", int(record["rows"])) for record in records]

    async def report_rows(self, thang, *, conn=None, with_ids=False):
        """The month's report rows (same dict keys as the workbook path), from v_<platform>;
        `with_ids` adds each post's id as `_id` (the web app's table)."""
        from riviu.workbook.platforms import get_platform

        spec = get_platform(self.platform)
        month_start, month_end = rules.parse_thang(thang)
        own = conn is None
        conn = conn or await self.connect()
        try:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                records = await conn.fetch(_report_query(self.platform), month_start, month_end)
        finally:
            if own:
                await conn.close()
        rows = []
        for record in records:
            row = rules.report_row(spec, record, thang)
            if row is not None:
                rows.append({**row, "_id": record["id"]} if with_ids else row)
        return SheetReportRows(rows=tuple(rows), partners=rules.report_partner_names(records))


class PostgresScanSession:
    """Rows of one scan and its buffered writes. Used from the event loop only."""

    def __init__(self, store, conn, label, rows, *, invalid_links=(), channel_overrides=None,
                 locked_count=0, run_id=None, events=None):
        self.store = store
        self.platform = store.platform
        self.table = store.table
        self.conn = conn
        self.label = label
        self.rows = rows
        self.invalid_links = list(invalid_links)
        self.channel_overrides = channel_overrides or {}
        self.locked_count = locked_count
        self.run_id = run_id
        self.events = events
        self._rows = {row["row"]: row for row in rows}
        self._pending = {}
        self.written = 0
        self.skipped = []  # {"id", "reason"}
        self.totals = {}
        self.diagnostic = {}
        self.flushes = 0

    # -- buffering (no I/O) ---------------------------------------------------------------

    def _buffer(self, row_id, columns, status):
        bad = set(columns) - rules.WRITABLE_COLUMNS[self.platform]
        if bad:
            raise StoreError(f"cột không được phép ghi: {sorted(bad)}")
        row = self._rows.get(row_id)
        if row is None:
            raise StoreError(f"dòng {row_id} không thuộc phiên quét này")
        previous = self._pending.get(row_id)
        merged = {**previous["set"], **columns} if previous else dict(columns)
        self._pending[row_id] = {"set": merged, "link_seen": row["link_seen"], "status": status}
        if "ten_kenh" in columns:
            row["channel"] = clean_text(columns["ten_kenh"])

    def write_tiktok_result(self, row_id, url, data, channel_name, status, *, resolved_url="",
                            channel_cache=None, channel_overrides=None, cache_lock=None):
        row = self._rows.get(row_id) or {}
        columns = rules.tiktok_update_columns(
            row.get("channel", ""), url, data, channel_name, status, resolved_url=resolved_url,
            channel_cache=channel_cache, channel_overrides=channel_overrides, cache_lock=cache_lock,
        )
        self._buffer(row_id, columns, clean_text(status))

    def write_threads_result(self, row_id, result):
        columns = rules.threads_update_columns(result)
        self._buffer(row_id, columns, columns["trang_thai_quet"])
        return columns["trang_thai_quet"]

    @property
    def pending_count(self):
        return len(self._pending)

    # -- flushing -------------------------------------------------------------------------

    async def flush(self):
        """Write the buffer in one transaction. (True, None) or (False, reason); a failed batch stays buffered."""
        if not self._pending:
            return True, None
        batch, self._pending = self._pending, {}
        try:
            updated, skipped = await self._apply(batch)
        except Exception as error:  # noqa: BLE001 - reported like a failed workbook save
            for row_id, write in batch.items():
                newer = self._pending.get(row_id)
                if newer is not None:
                    write = {**newer, "set": {**write["set"], **newer["set"]}}
                self._pending[row_id] = write
            log.warning("Postgres flush failed: %s: %s", type(error).__name__, error)
            await self._log(f"CẢNH BÁO: Lỗi ghi Postgres ({type(error).__name__}); sẽ thử lại ở lần lưu sau.")
            return False, "other"
        self.flushes += 1
        self.written += len(updated)
        self.skipped.extend(skipped)
        for item in skipped[:SKIP_LOG_LIMIT]:
            await self._log(f"Bỏ qua dòng id={item['id']}: dòng đã bị sửa trong lúc quét ({item['reason']}), không ghi đè.")
        if len(skipped) > SKIP_LOG_LIMIT:
            await self._log(f"... và {len(skipped) - SKIP_LOG_LIMIT} dòng khác bị bỏ qua vì đã bị sửa trong lúc quét.")
        return True, None

    async def _apply(self, batch):
        conn = self.conn
        groups = defaultdict(list)
        for row_id, write in batch.items():
            groups[tuple(sorted(write["set"]))].append((row_id, write))
        updated = set()
        async with conn.transaction():
            for columns, items in groups.items():
                sql = self._upsert_sql(columns)
                for start in range(0, len(items), UPDATE_CHUNK):
                    chunk = items[start:start + UPDATE_CHUNK]
                    args = [[row_id for row_id, _ in chunk], [write["link_seen"] for _, write in chunk]]
                    args.extend([write["set"][column] for _, write in chunk] for column in columns)
                    updated.update(record["id"] for record in await conn.fetch(sql, *args))
            skipped_ids = [row_id for row_id in batch if row_id not in updated]
            skipped = await self._skip_reasons(conn, skipped_ids, batch) if skipped_ids else []
            if self.run_id is not None:
                await conn.execute(
                    "INSERT INTO public.scan_run_item (run_id, platform, row_id, status) "
                    "SELECT $1, $2, u.row_id, u.status FROM unnest($3::bigint[], $4::text[]) AS u(row_id, status) "
                    "ON CONFLICT (run_id, row_id) DO NOTHING",
                    self.run_id, self.platform,
                    [*updated, *(item["id"] for item in skipped)],
                    [*(batch[row_id]["status"] or "" for row_id in updated),
                     *(f"Bỏ qua: {item['reason']}" for item in skipped)],
                )
        return updated, skipped

    def _upsert_sql(self, columns):
        """One upsert into bao_cao.<t>_chi_so naming only `columns` (schema doc §4).

        A post without a bao_cao row gets one (the other columns stay NULL = unknown); an
        existing row changes only `columns`, so a value the result does not touch is kept.
        """
        if not columns:
            raise StoreError("không có cột nào để ghi")
        for column in columns:
            if column not in rules.WRITABLE_COLUMNS[self.platform]:
                raise StoreError(f"cột không được phép ghi: {column}")
        arrays = ", ".join(["$1::bigint[]", "$2::text[]", *(
            f"${index}::{rules.COLUMN_TYPES[column]}[]" for index, column in enumerate(columns, start=3)
        )])
        names = ", ".join(["id", "link_seen", *columns])
        targets = ", ".join(["id", *columns])
        values = ", ".join(f"u.{column}" for column in ("id", *columns))
        assignments = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns)
        return (
            f"INSERT INTO bao_cao.{self.table}_chi_so AS c ({targets}) "
            f"SELECT {values} FROM unnest({arrays}) AS u({names}) "
            f"WHERE EXISTS (SELECT 1 FROM public.v_{self.table} v WHERE v.id = u.id AND v.link_air = u.link_seen) "
            f"ON CONFLICT (id) DO UPDATE SET {assignments} "
            "WHERE NOT c.khoa_chi_so "
            "RETURNING c.id"
        )

    async def _skip_reasons(self, conn, row_ids, batch):
        records = await conn.fetch(SKIP_REASON_SQL[self.table], row_ids)
        found = {record["id"]: record for record in records}
        skipped = []
        for row_id in row_ids:
            record = found.get(row_id)
            if record is None:
                reason = "dòng không còn"
            elif record["deleted"]:
                reason = "dòng đã bị xoá"
            elif record["khoa_chi_so"]:
                reason = "dòng đã khoá chỉ số"
            elif record["link_air"] != batch[row_id]["link_seen"]:
                reason = "link đã đổi"
            else:
                reason = "không cập nhật được"
            skipped.append({"id": row_id, "reason": reason})
            log.info("scan write skipped: %s id=%s (%s)", self.platform, row_id, reason)
        return skipped

    async def _log(self, message):
        if self.events is not None:
            try:
                await self.events.broadcast_log(message)
            except Exception as error:  # noqa: BLE001 - a closed websocket must not fail a flush
                log.debug("scan log not delivered: %s", type(error).__name__)

    async def close(self):
        if self.conn is not None and not self.conn.is_closed():
            await self.conn.close()
