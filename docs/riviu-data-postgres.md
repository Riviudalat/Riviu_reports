# Riviu Reports on the Riviu Data Postgres

Riviu Data's Postgres holds the seeding post list (`public.tiktok`, `public.threads`). Since Riviu
Data migration `0011_month_sheets` the channel name, metrics, scan status, resolved links and the
metric lock of each post live in Riviu Reports' own schema **`bao_cao`** in the same database
(`bao_cao.tiktok_chi_so`, `bao_cao.threads_chi_so`, one row per post id, `ON DELETE CASCADE`).
With `RIVIU_STORAGE=postgres`, a Riviu Reports **server** scans the posts, writes `bao_cao` and builds
partner reports from both. Desktop and source mode never use Postgres: they keep the workbooks, unchanged.

The contract is Riviu Data `docs/riviu-reports-schema.md` (tables, reads, writes, grants). Design
background: Riviu Data `docs/integration-and-ops.md` §1 and §4.6, `docs/review-v1.md`
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

- **Reads.** `public.v_<t> v LEFT JOIN bao_cao.<t>_chi_so c USING (id)`: the post fields (`id`,
  `link_air`, `ngay_air`, `doi_tac`) from the view, `ten_kenh`, metrics, `trang_thai_quet`,
  `cap_nhat_lan_cuoi`, `link_phan_giai`/`link_nguon_phan_giai` and `khoa_chi_so` from `bao_cao`
  (all NULL, lock false, when the post has no `bao_cao` row yet).
- **Month selection.** `thang` is `YYYY-MM`; rows are selected with
  `thang_nam >= first day AND thang_nam < first day of next month` (review H-05). Since 0011
  `thang_nam` is the row's **month sheet**: set when the row is created (from `ngay_air` if not
  given) and moved by staff, never re-derived from `ngay_air` on update. Rows with `thang_nam`
  NULL belong to no month and are never scanned.
- **Soft delete.** Reads go through `v_tiktok` / `v_threads` (live rows only); every write requires
  the post to still be live in `v_<t>`.
- **Locked metrics.** Rows with `bao_cao.<t>_chi_so.khoa_chi_so = true` are not scanned at all
  (DESIGN §5.4/§7), and the upsert's conflict branch requires `NOT khoa_chi_so`, so a row locked
  during the scan gets no write either. Since 0011 nobody sets the lock from Riviu Data (it has no
  lock UI any more); the lock is Riviu Reports' to manage. Riviu Reports has no lock toggle yet
  (out of scope); until it does, a lock can only be set in SQL
  (`INSERT INTO bao_cao.<t>_chi_so (id, khoa_chi_so) VALUES (…, true) ON CONFLICT (id) DO UPDATE SET khoa_chi_so = true`).
- **Write rules** (same as the workbook, `riviu/store/rules.py`):

  | TikTok outcome | metrics | `trang_thai_quet` | `link_phan_giai` / `link_nguon_phan_giai` | `ten_kenh` | `cap_nhat_lan_cuoi` |
  |---|---|---|---|---|---|
  | `Success` | `metric_or_none` (unknown → NULL, `0` stays 0) | set | set | best quality | `now()` |
  | `should_clear_stale_metrics` (hidden stats, gone confirmed by oEmbed) | NULL | set | — | kept if usable | — |
  | any other failure | — (not in the upsert) | set | — | kept if usable | — |

  A column marked — is not named in the upsert: an existing `bao_cao` row keeps its value, a new
  one gets NULL (unknown, never 0).
  Threads: a known value replaces the old one, a missing one is left out of the upsert, views become
  NULL only on the explicit `view_query_unknown` invalidation, `cap_nhat_lan_cuoi` only when a channel
  or a metric was read. A channel equal to the stored one is not written, so a staff edit made during
  the scan survives.
- **Concurrency.** Results are buffered (no I/O in the result loop) and flushed every `save_every`
  results (TikTok) or 30 s (Threads), and at the end, each flush one short transaction: one upsert
  per set of changed columns (≤ 500 rows per statement), then the `scan_run_item` rows:

  ```sql
  INSERT INTO bao_cao.tiktok_chi_so AS c (id, <changed columns>)
  SELECT u.id, u.<changed columns>
  FROM unnest($1::bigint[], $2::text[], <one array per column>) AS u(id, link_seen, <changed columns>)
  WHERE EXISTS (SELECT 1 FROM public.v_tiktok v WHERE v.id = u.id AND v.link_air = u.link_seen)
  ON CONFLICT (id) DO UPDATE SET <col> = EXCLUDED.<col>, …   -- only the changed columns
  WHERE NOT c.khoa_chi_so
  RETURNING c.id
  ```

  (Threads: `bao_cao.threads_chi_so` and `public.v_threads`.) `link_seen` is the `link_air` read at
  open. `bao_cao` is not audited by Riviu Data (`lich_su` holds post edits only) and its grid does not
  show metrics, so flushes no longer set `app.user` / `app.bulk` nor send the `rda_changes` resync
  NOTIFY. `riviu_scanner` has no INSERT/UPDATE/DELETE on `public.tiktok`/`threads` at all.
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
  `dang_quet`, `thang_nam`; `mode` kept if set), otherwise a new row (`mode` = trigger). This needs
  the column-limited UPDATE on `scan_run` (granted since 0011); without it the scan fails to start
  (the former fallback that recorded a second `scan_run` row was removed). At the end:
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
read one month from `v_<platform>` LEFT JOIN `bao_cao.<platform>_chi_so` in one `REPEATABLE READ` transaction.

## The web app on Riviu Data

`GET /api/version` reports `"storage": "postgres"`, and the web app then works by month instead of by
workbook file and sheet:

- `GET /riviu-data/months?platform=` lists the month sheets that hold live rows, newest first
  (`SELECT thang_nam, count(*) FROM v_<t> ... GROUP BY thang_nam`; rows with `thang_nam` NULL are left
  out). `defaultThang` is the newest month not in the future.
- `GET /riviu-data/rows?platform=&thang=` returns the month's posts for the table: `id`, `ngayAir`,
  `channel`, `link`, `partners`, the metrics (`views`, `likes`, `comments`, `saves` or `reposts`,
  `shares`; `null` = unknown, never 0), `updatedAt`, `status`.
- These two and the report routes above are public reads, like the workbook viewer routes
  (`security.VIEWER_GET_PATHS` / `VIEWER_POST_PATHS`).
- A scan started over `/ws`, by the scheduler or by a resume goes through `scan_service` as on
  workbooks, with `file_id` `"riviu-data"` and the month (`YYYY-MM`) as `sheet_name` (empty: the default
  month). `scan_service.StoreRun` takes the platform's advisory lock and one `scan_run` row (`mode`
  `thu_cong`, or `timer` for the scheduler; `started_by` `riviu-reports:<username>` or
  `riviu-reports:lich`) for the whole run. Each pass (main, retry) opens a fresh store session, so the
  retry pass reads current rows and the scan journal resumes by `(month, id, url)`. There is no result tab
  and no Google push. The scheduler does not download a Google Sheet; it scans the default month.
- `POST /internal/scan` keeps its own path (`riviu/internal_scan.py`, options from the environment). Both
  paths share the advisory lock and the in-process scan gate, so they never overlap.
- `deploy/server/compose.v2.yaml` runs this next to V1 (own checkout, data dir `/srv/riviu-v2/data`, env
  and secrets in `/etc/riviu-v2`, port 18111, Docker network `riviu-data` to reach `riviu-data-db:5432`).

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

They cover: `thang_nam` selection across a year boundary and for a row moved to another month sheet,
locked and soft-deleted rows (at read and mid-scan), the `link_air` guard (row edited mid-scan →
skipped, logged, `scan_run_item`), NULL vs 0 on every TikTok/Threads outcome, the first `bao_cao` row
of a never-scanned post, no `lich_su` entry and no UPDATE grant on `public.tiktok`, the advisory-lock 409,
timer 200 vs manual 202 (reusing a `scan_run` created by `riviu_app`), a real Threads scan through
the endpoint, the partner export with an untrusted channel, and Python vs SQL `rda_split_doi_tac`
parity. The unit file covers config, write rules, workbook/Postgres parity of rows, partners and
links, the runners on a session, and auth 401/403, 404, 409, 422.

## Deviations and open points

1. **`dual` is not built.** `RIVIU_STORAGE=dual` refuses to start. Phase 2 of the migration plan
   (shadow writes) would need it.
2. **Locked rows are not scanned at all**, not "scanned without metrics": DESIGN §7 says the scanner
   skips them. Their status and channel are therefore not refreshed either. The lock is in `bao_cao`
   and has no UI yet (see Data rules).
3. **Resolved by 0011: resolution links.** `link_phan_giai` / `link_nguon_phan_giai` now live in
   `bao_cao.tiktok_chi_so`; the former `v_tiktok` JOIN `tiktok` workaround is gone.
4. **Changed by 0011: no report views.** Reports read `v_<t>` LEFT JOIN `bao_cao.<t>_chi_so` and build
   the report rows in Python (the same functions as the workbook path), which keeps the failed-channel
   and min-views filters and the `@username` display rule. The `v_<t>_report` / partner-summary views
   proposed earlier are no longer asked of Riviu Data: `bao_cao` is Riviu Reports' own schema, so such
   views, if ever needed, would be Riviu Reports' (schema doc §6 shows the join).
5. **Resolved by 0011: `scan_run` UPDATE.** Migration 0011 and `deploy/postgres/riviu_scanner.sql` grant
   `UPDATE (phase, thang_nam, mode, totals, diagnostic, stop_reason, finished_at)`; the fallback that
   recorded a second `scan_run` row (`diagnostic.scanRunYeuCau`) was removed, so a missing grant is a
   visible deployment error instead of a silently split run.
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
