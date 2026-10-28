# Riviu Reports on the Riviu Data Postgres

Riviu Data's Postgres is the single source of truth for the seeding rows (`tiktok`, `threads`).
With `RIVIU_STORAGE=postgres`, a Riviu Reports **server** scans those tables and builds partner
reports from them. Desktop and source mode never use Postgres: they keep the workbooks, unchanged.

Design background: Riviu Data `docs/integration-and-ops.md` §1 and §4.6, `docs/review-v1.md`
(H-04, H-05, H-06, H-07, M-06, M-09, M-10, M-13, M-19, M-24, M-25).

## Code map

| Module | Role |
|---|---|
| `riviu/store/config.py` | `RIVIU_STORAGE` and the Postgres settings; refuses to start on a bad setting |
| `riviu/store/rules.py` | Pure rules: months (`YYYY-MM` → `thang_nam` range), the TikTok/Threads write rules as "columns to set", DB row → report row |
| `riviu/store/postgres.py` | `PostgresStore` (reads, `open_scan`, `report_rows`), `PostgresScanSession` (buffered writes, `flush`), advisory lock and `scan_run` helpers |
| `riviu/internal_scan.py` | The scan behind `POST /internal/scan`: lock, `scan_run`, run, close |
| `riviu/api/internal.py` | `POST /internal/scan` (bearer token) |
| `riviu/api/riviu_data.py` | `GET /riviu-data/report-partners`, `POST /riviu-data/export-report` |

The scanners were not forked. `run_scraper(..., store_session=...)` and
`run_threads_scraper(..., store_session=...)` take rows from the session and send each result to
`session.write_*_result` instead of a worksheet; saves become `session.flush()`. Without
`store_session` both behave exactly as before. Row selection is shared with the workbook path
(`workbook_io.scan_row_fields`, `threads.classify_threads_row`), and partner reports use the same
pure functions (`partner_link_counts`, `group_rows_by_partner`, `render_export_payload`).

## Configuration (server mode only)

| Variable | Meaning |
|---|---|
| `RIVIU_STORAGE` | `workbook` (default) or `postgres`. `postgres` needs `RIVIU_SERVER_MODE=1` and never runs frozen (desktop). |
| `RIVIU_DATABASE_URL_FILE` | File with the DSN `postgresql://riviu_scanner:<pw>@<host>:<port>/riviu_data` (preferred). |
| `RIVIU_DATABASE_URL` | The DSN itself, if no file. The user **must** be `riviu_scanner`; the store also checks `current_user` at every connect. |
| `RIVIU_SCAN_TOKEN_FILE` | Bearer tokens for `/internal/scan`, one per line: `<token> [scope,scope]` (default scope `scan:run`, token ≥ 32 chars) or `sha256:<hex of the token> [scopes]`. `#` starts a comment. Only SHA-256 digests are kept in memory. |
| `RIVIU_SCAN_TIKTOK_MODE` / `_WORKERS` | `request`/`browser`/`hybrid` (default `hybrid`) / 1–50 (default 10) |
| `RIVIU_SCAN_THREADS_MODE` / `_WORKERS` | default `hybrid` / 1–10 (default 3) |
| `RIVIU_SCAN_USE_PROXY` | `1` uses the saved proxy list in the data dir (TikTok round-robin; Threads per `docs/threads-scanning.md`). Default off. |

Generate a token with `python -c "import secrets; print(secrets.token_urlsafe(32))"`; give the same
value to Riviu Data (`RDA_SCAN_SECRET`) and to the timer (`RIVIU_SCAN_TOKEN_FILE` of
`rda-daily-scan`). Never commit the token or the DSN.

## Data rules

- **Month selection.** `thang` is `YYYY-MM`; rows are selected with
  `thang_nam >= first day AND thang_nam < first day of next month` (review H-05), never by the
  year-less `thang` text. Rows whose `ngay_air` is NULL have no `thang_nam` and are never scanned.
- **Soft delete.** Reads go through `v_tiktok` / `v_threads` (`da_xoa_luc IS NULL`). Every UPDATE
  also requires `da_xoa_luc IS NULL`.
- **Locked metrics.** Rows with `khoa_chi_so = true` are not scanned at all (DESIGN §5.4/§7), and every
  UPDATE requires `NOT khoa_chi_so`, so a row locked during the scan gets no write either.
- **Write rules** (same as the workbook, `riviu/store/rules.py`):

  | TikTok outcome | metrics | `trang_thai_quet` | `link_phan_giai` / `link_nguon_phan_giai` | `ten_kenh` | `cap_nhat_lan_cuoi` |
  |---|---|---|---|---|---|
  | `Success` | `metric_or_none` (unknown → NULL, `0` stays 0) | set | set | best quality | `now()` |
  | `should_clear_stale_metrics` (hidden stats, gone confirmed by oEmbed) | NULL | set | — | kept if usable | — |
  | any other failure | — (not in the UPDATE) | set | — | kept if usable | — |

  Threads: a known value replaces the old one, a missing one is left out of the UPDATE, views become
  NULL only on the explicit `view_query_unknown` invalidation, `cap_nhat_lan_cuoi` only when a channel
  or a metric was read. A channel equal to the stored one is not written, so a staff edit made during
  the scan survives.
- **Concurrency.** Results are buffered (no I/O in the result loop) and flushed every `save_every`
  results (TikTok) or 30 s (Threads), and at the end, each flush one short transaction:
  `set_config('app.user','app:riviu-reports-scanner',true)`, `set_config('app.bulk','on',true)`
  (no per-row NOTIFY, review M-06), then one `UPDATE … FROM unnest(…) … RETURNING id` per set of
  changed columns (≤ 500 rows per statement), guarded by
  `id = … AND link_air = <value read at open> AND da_xoa_luc IS NULL AND NOT khoa_chi_so`, the
  `scan_run_item` rows, and one `pg_notify('rda_changes', {"resync": true, "bang": …, "ly_do": "quet"})`.
  A row edited (link changed), deleted or locked during the scan is skipped, logged in the scan log
  ("dòng đã bị sửa trong lúc quét, bỏ qua") and recorded as `Bỏ qua: <reason>` in `scan_run_item`.
  A failed flush keeps its writes buffered and retries at the next checkpoint; a failed final
  flush fails the scan.
- **Partners.** `doi_tac` is split exactly like Riviu Data's `rda_split_doi_tac` (comma, NFC,
  zero-width removed, whitespace collapsed, first spelling wins): `partners_from_doi_tac`. For partner
  names without a comma it returns the workbook's own list (`row_partners_from_values`) after the
  importer's `", ".join(...)`; `doi_tac_from_partners` refuses names with a comma (review H-04).
- **Links.** `link_air` goes through the workbook's normalizers (`normalize_tiktok_url`,
  `normalize_threads_url`, `trusted_resolved_media_id`, Threads profile/malformed detection).

## `POST /internal/scan`

```
Authorization: Bearer <token with scope scan:run>
{"platform": "tiktok"|"threads", "thang": "YYYY-MM", "trigger": "timer"|"thu_cong",
 "started_by"?: str, "scan_run_id"?: int}
```

| Status | When |
|---|---|
| 200 | `trigger == "timer"`: the scan finished, body = summary (`scan_run_id`, `phase` `hoan_tat` or `dung_som` after a Threads HTTP 429, `stop_reason`, `totals`) |
| 202 | any other trigger (default `thu_cong`): accepted, `{"scan_run_id", "phase": "dang_quet"}`; the scan runs in the background |
| 401 | missing, malformed or unknown bearer token |
| 403 | the token lacks `scan:run`, or the request carries `Origin` / `Sec-Fetch-Site` (browsers) |
| 404 | `RIVIU_STORAGE` is not `postgres`, or `scan_run_id` is unknown, of another platform or already finished |
| 409 | another scan of the platform holds `pg_try_advisory_lock(hashtext('scan:'||platform))`, or any scan runs in this process (UI scan, other platform) |
| 413 / 422 | body over 4 KB / invalid body (`thang` must be `YYYY-MM`; omitted = current month in Vietnam) |
| 500 / 503 | timer scan failed (`phase` `loi`) / was cancelled (`huy`) |

- The route is exempt from the session cookie and Host checks (machine-to-machine); the token is
  compared in constant time against SHA-256 digests (`hmac.compare_digest`, every entry checked).
- The advisory lock is taken on a dedicated connection held for the whole scan and released
  explicitly before it closes (never pooled, review M-25).
- `scan_run`: a given `scan_run_id` (Riviu Data's `phase='yeu_cau'` row) is reused (`phase`
  `dang_quet`, `thang_nam`; `mode` kept if set), otherwise a new row (`mode` = trigger). At the end:
  `phase` `hoan_tat`/`dung_som`/`loi`/`huy`, `stop_reason`, `finished_at`, `totals`
  (written, skipped, lockedRows, invalidLinks, success/error/hidden…) and `diagnostic` (Threads run
  diagnostic, the first 200 skipped rows, the error text).
- A timer request waits for the scan (the 07:00 script allows 3 h). The scan is shielded from the
  HTTP connection: if the caller disconnects, the scan still finishes and records its run. Call the
  service directly (loopback or the Docker network). If nginx sits in between, raise
  `proxy_read_timeout` above 3 h.
- The UI shows the scan's log and progress like a UI scan (same `/ws` events), and its stop button
  cancels it (`phase` `huy`).
- Scans requested here are anonymous: no TikTok/Threads cookie session is used.

## Reports

`GET /riviu-data/report-partners?platform=&thang=&apply_min_views=&min_views=` and
`POST /riviu-data/export-report {"platform","thang","partners","applyMinViews","minViews"}` return the
same partner list and the same Excel files as `/report-partners` / `/export-report` on a workbook
(same filters, totals, fills, and the formula-injection guard `set_cell_literal`, review M-13). They
read one month from `v_<platform>` in one `REPEATABLE READ` transaction and sit behind the normal
server-mode login. The existing UI still drives the workbook routes; wiring a month picker to
these routes is not part of this change.

## Tests

```powershell
uv run pytest -q tests                                  # full suite; the Postgres integration tests skip
uv run pytest -q tests/test_store_postgres.py           # store faked at the connection: rules, parity, runners, endpoint
```

Integration tests against PostgreSQL 17 built from Riviu Data's own migrations
(`tests/test_store_postgres_integration.py`):

```powershell
# 1. A throwaway Postgres 17 with Riviu Data's test roles, on a free port (here 55447):
$env:RDA_PG_PORT = "55447"
docker compose -p rr-pgtest -f C:\Users\cattfan\Desktop\Riviu_Data_App\backend\docker-compose.test.yml up -d --wait
# 2. Run (the fixture creates riviu_reports_it and runs `alembic upgrade head` from the checkout
#    as riviu_owner, with a uv environment in %TEMP%, so nothing is written into the checkout):
$env:RIVIU_PG_TEST_ADMIN_URL = "postgresql://postgres:test-super@127.0.0.1:55447/postgres"
$env:RIVIU_DATA_APP_DIR = "C:\Users\cattfan\Desktop\Riviu_Data_App"
uv run pytest -q tests/test_store_postgres_integration.py
# 3. Clean up:
docker compose -p rr-pgtest -f C:\Users\cattfan\Desktop\Riviu_Data_App\backend\docker-compose.test.yml down -v
```

They cover: `thang_nam` selection across a year boundary, locked and soft-deleted rows (at read and
mid-scan), the `link_air` guard (row edited mid-scan → skipped, logged, `scan_run_item`), NULL vs 0
on every TikTok/Threads outcome, audit actor `app:riviu-reports-scanner`, the advisory-lock 409,
timer 200 vs manual 202 (reusing a `scan_run` created by `riviu_app`), a real Threads scan through
the endpoint, the partner export with an untrusted channel, and Python vs SQL `rda_split_doi_tac`
parity. The unit file covers config, write rules, workbook/Postgres parity of rows, partners and
links, the runners on a session, and auth 401/403, 404, 409, 422.

## Deviations and open points

1. **`dual` is not built.** `RIVIU_STORAGE=dual` refuses to start. Phase 2 of the migration plan
   (shadow writes) would need it.
2. **Locked rows are not scanned at all**, not "scanned without metrics": DESIGN §7 says the scanner
   skips them. Their status and channel are therefore not refreshed either.
3. **`v_tiktok` lacks `link_phan_giai` / `link_nguon_phan_giai`.** Migration 0003 creates it as
   `SELECT *` before 0009 adds those columns, so Postgres froze the old column list. The TikTok queries
   read `v_tiktok` joined to `tiktok` on `id` for the two columns. Riviu Data should recreate the view
   (or add `v_tiktok_report`).
4. **No `v_<platform>_report` / `v_<platform>_partner_summary` views exist** in the migrations. Reports
   read `v_<platform>` and build the report rows in Python (the same functions as the workbook path).
   Proposed views for Riviu Data (security_invoker, review L-03):

   ```sql
   CREATE VIEW public.v_tiktok_report WITH (security_invoker = true) AS
   SELECT t.id, t.thang_nam, t.ngay_air, t.ten_kenh, t.link_air, t.doi_tac, t.luot_xem, t.tim, t.binh_luan,
          t.luot_luu, t.chia_se, t.cap_nhat_lan_cuoi, t.trang_thai_quet, t.link_phan_giai, t.link_nguon_phan_giai,
          t.khoa_chi_so, public.rda_split_doi_tac(t.doi_tac) AS doi_tac_ds
   FROM public.tiktok t WHERE t.da_xoa_luc IS NULL;
   -- v_threads_report: the same with repost instead of luot_luu and without the two link columns.

   CREATE VIEW public.v_tiktok_partner_summary WITH (security_invoker = true) AS
   SELECT r.thang_nam, d.ten AS doi_tac, count(*) AS tong_link,
          sum(r.luot_xem) AS tong_luot_xem, sum(r.tim) AS tong_tim, sum(r.binh_luan) AS tong_binh_luan,
          sum(r.luot_luu) AS tong_luot_luu, sum(r.chia_se) AS tong_chia_se, max(r.cap_nhat_lan_cuoi) AS cap_nhat_lan_cuoi
   FROM public.v_tiktok_report r JOIN public.tiktok_doi_tac l ON l.tiktok_id = r.id JOIN public.doi_tac d ON d.id = l.doi_tac_id
   GROUP BY r.thang_nam, d.ten;
   -- SUM ignores NULLs and is NULL when nothing is known: the workbook's metric_total rule.
   ```

   The Python side keeps the failed-channel and min-views filters and the `@username` display rule.
5. **`deploy/postgres/riviu_scanner.sql` grants only `SELECT, INSERT` on `scan_run`**, while migration
   0009 grants `SELECT, INSERT, UPDATE`. Re-running the roles script would remove the `UPDATE` that
   progress and the final phase need. Without it the store logs a warning, records the scan in a new
   `scan_run` row pointing at the requested one (`diagnostic.scanRunYeuCau`), and cannot set
   `finished_at`. Riviu Data should grant `UPDATE (phase, thang_nam, mode, totals, diagnostic,
   stop_reason, finished_at)` on `scan_run` in the script.
6. **`scan_run_item` has no UPDATE grant**, so its insert is `ON CONFLICT DO NOTHING`: if a row were
   written twice in one run, the first flushed status stays.
7. **Advisory-lock key.** The spec's single-key `hashtext('scan:'||platform)` is used. Riviu Data's
   other locks use the two-key form with a class id (review M-25); a scan class (e.g. 1) would rule
   out collisions with other `hashtext` keys.
8. **The workbook UI is unchanged.** In postgres mode the `/ws` scan and the existing report routes
   still operate on workbooks; only `/internal/scan` and `/riviu-data/*` use Postgres.
9. **No Google push, result tab, summary tab or row highlights** in Postgres scans: Riviu Data computes
   those from the rows (views); the JSON scan histories are replaced by `scan_run`.
10. **Driver.** `asyncpg` (Apache-2.0, review M-23) directly, without SQLAlchemy; it is imported only
    when the Postgres backend runs.
