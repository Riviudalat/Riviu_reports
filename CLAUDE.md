# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

The contributor rules above (style, commit format, testing and data-handling policy) are shared with other coding agents. This file adds commands and architecture.

## Commands (PowerShell, repo root)

```powershell
.venv\Scripts\python.exe app.py                                   # web app on http://127.0.0.1:1231
.venv\Scripts\python.exe -m pytest -q tests                        # full suite; always pass `tests` so scratch copies under output/ are not collected
.venv\Scripts\python.exe -m pytest -q tests/test_threads.py::test_name   # single test
.venv\Scripts\python.exe -m pytest -q tests -k "proxy and threads"       # by keyword
node --check static/app.js                                         # JS syntax check (also run in CI)
npm run desktop:dev                                                # build PyInstaller sidecar, then `tauri dev`
npm run desktop:build                                              # sidecar + installers (needs Rust toolchain)
git diff --check
```

Opt-in tests are skipped unless you set env vars: `THREADS_LIVE_URL`, `THREADS_SHARE_URL` (live Threads scans), and `RIVIU_TEST_URL` (UI against a running server). Frontend regression tests in `tests/test_ui_review_fixes.py` and related files run `static/app.js` inside a Node `vm` harness with stubbed DOM, `fetch`, and `WebSocket`. The harness turns the source-row markup that app.js renders into the stub elements, and it fails if any `PLATFORMS[*].dom` id was not rendered. They skip if `node` is not on PATH. The DPAPI tests run only on Windows.

The CI release workflow (`.github/workflows/desktop-release.yml`) runs pytest and `node --check`, then builds and publishes installers on **every push to `main`** (version `0.1.<run number>`). Installed apps auto-update from that release, so a push to `main` ships to users.

## Architecture

**One local FastAPI process, two ways to run it.** `app.py` is the whole HTTP/WebSocket server: every route and the global scan state. Partner Excel reports and Google push rows are built in `reports.py`. In source mode you run it directly. In desktop mode, Tauri (`src-tauri/src/lib.rs`) launches the PyInstaller-built sidecar `riviu-server` (`desktop_server.py` → `desktop/build_sidecar.py`). Tauri passes `RIVIU_PORT`, `RIVIU_SHUTDOWN_TOKEN`, and `RIVIU_DATA_DIR` (the OS app-data dir), then loads the UI from loopback. `desktop_server.py` adds token-protected `/_desktop/prepare-update|cancel-update|shutdown` routes. The updater refuses to install while a scan or source operation is running. Chromium is bundled into the sidecar (`PLAYWRIGHT_BROWSERS_PATH=0`, packaged process only).

**Paths.** `APP_RESOURCE_DIR` is the repo, or `sys._MEIPASS` when frozen, and holds templates, static files, and the logo. `EXCEL_DIR` comes from `RIVIU_DATA_DIR`, falling back to the repo, and holds the user data: `data/` workbooks, `google_sheet_sources.json`, proxy list, scrape history, `threads_scan_history.json`, and the encrypted Threads cookie store. Tests isolate state by pointing these at `tmp_path` or monkeypatching them.

**Local API protection.** `protect_local_api` middleware rejects non-loopback Host/Origin headers and cross-site fetches. Every route except `GET /`, static assets, and `/_desktop/*` requires a per-process session cookie that is set when `/` is loaded. Ad-hoc HTTP calls (curl, test clients) must load `/` first or they get 403.

**Scan flow.** The UI (`templates/index.html` + the single file `static/app.js`) starts a scan over the `/ws` WebSocket. `prepare_scan_start` validates the payload against the workbook named by its `file_id` (not the last `/select-file`) and builds a `scan_options(...)` dict. `app.py` holds one global `SCRAPE_TASK`, so only one scan runs at a time, and `run_scraper_safely(target_path, options)` dispatches through `SCAN_RUNNERS[platform]`:
- `tiktok` → `scraper.run_scraper` (Request / Browser / Hybrid modes, worker pool, retries, optional result sheet, sheet total rows).
- `threads` → `threads_scraper.run_threads_scraper`. When a cookie session is enabled, `threads_session.ThreadsSession` supplies verified cookies plus a fixed proxy route. Only a `SessionError` invalidates the session; other failures leave it valid.

A failed Google push after a saved scan is logged as a warning, not a failed scan.

**Adding a platform.** Platform differences live in tables, not `if threads` branches: `workbook_utils.PLATFORMS` (`PlatformSpec`: report columns, link normalizer/matcher, channel display, video-link highlighting, whether partners without links of that platform are listed, file and Google-tab naming), `app.SCAN_RUNNERS` and `app.PROXY_VALIDATORS` (a test asserts these cover the same keys), and the `PLATFORMS` object at the top of `static/app.js` (plus the icon in `static/platform-icons/`). `templates/index.html` needs no edits: the platform bar and the source-drawer rows (file label, Google link, sync, sheet select, push) are rendered from `PLATFORMS` by `renderPlatformBar()` / `renderPlatformSourceRows()` into `#platformBar` and `#platformSourceRows`. Each entry's `dom` map names the element ids those rows get, and `sourceControls(platform)` looks them up by these ids. TikTok keeps its legacy ids (`googleSheetUrlInput`, `pushGoogleBtn`, ...), so the ids must stay unique across entries.

Scrapers report progress through the `ConnectionManager` (`manager`) via `broadcast_log/status/data/duplicates`. Every message carries the run context (`runId`, `platform`, `fileId`, `sheetName`), and the frontend uses it to drop stale events. Results are written into the workbook in place using atomic saves (temp file + replace). After a scan, results can optionally be pushed to a new tab in the source Google Sheet.

**Module ownership.** Keep platform extraction separate from workbook and transport code:
- `workbook_utils.py`: the platform registry, column lookup, partner parsing, preview/summary dashboards, result/summary sheet naming (timestamp regex), Google-sheet file registry, and the hidden internal columns (`__TTBD_*`, `__THREADS_SCAN_STATUS`). Both scanners and the reports find columns through `worksheet_find_column_index` / `worksheet_ensure_column` / `COLUMN_ALIASES` (matching is case- and diacritic-insensitive). A scanner with its own header matcher appends duplicate columns that the reports never read. Write workbooks and JSON state through `save_workbook_atomic` / `write_json_atomic`. Their hidden `.riviu-*.tmp` temp files never appear in the workbook list.
- `reports.py`: partner Excel reports (`build_partner_report`: logo/banner, platform icon, totals, row fills), the export payload (`build_export_payload`: snapshot copy, single `.xlsx` or `.zip` of partner reports), and the Google push rows (`build_google_push_rows`). Pure workbook logic: it never imports `app` and holds no FastAPI, scan state, locks or WebSocket code. Tests patch report internals (`build_workbook_rows`, `format_filename_datetime`, `ExcelImage`) on `reports`, not `app`.
- `proxy_utils.py`: proxy list parsing and storage, plus HTTP/SOCKS transports. Both platforms use it.
- `google_sheets_sync.py`: OAuth client upload and login, authenticated download, and pushing rows to a new sheet. Calls go through `google_io` / `GOOGLE_IO_LOCK`. The exception is the interactive `authorize_google` browser login, which has a timeout and runs only from `/google-oauth-login` outside that lock. Background pushes and syncs raise `GoogleLoginRequired` instead of opening a browser.
- `threads_session.py`: cookie normalization (Cookie-Editor JSON), OS secure store (DPAPI on Windows, keyring elsewhere, no plaintext fallback), and session verification with a redirect-guarded navigation.

**Platform sources.** TikTok and Threads each keep their own source: file, sheet, and Google link, persisted through `/source-preferences`. API calls pass an explicit `file_id` + `platform`, and code must never fall back to the other platform's workbook.

## Domain invariants (easy to break)

- Missing or unavailable metrics stay **blank/None** on both platforms, in the workbook, previews, partner reports and Google pushes. Write `0` only for a confirmed zero. When scraping fails, keep the existing cell values, with two deliberate exceptions where the old numbers are cleared to blank:
  - TikTok hidden stats, HTTP 404/410 and unavailable pages (`scraper.should_clear_stale_metrics`). The status column explains these.
  - Threads unconfirmed views, per `docs/threads-scanning.md`.

  Totals (partner reports, Google pushes, previews, the "Tổng kết" sheet and dashboard) sum the known values and stay blank only when nothing in that column is known.
- Threads partner reports and Google pushes use the TikTok layout and rules: the same 8 columns with REPOST in the LƯỢT LƯU slot, no status column (the scan status stays in the hidden `__THREADS_SCAN_STATUS` workbook column), the same failed-channel and min-views filters, and a blank TÊN KÊNH shown as `@username` from the post URL.
- Threads never uses numbers from captions, suggested posts, or redirected Home pages. It never rotates proxies to get around 403/429/geoblocks, and never falls back to direct/anonymous connections when the chosen route fails. Logs must not contain proxy credentials or cookies.
- `docs/threads-scanning.md` (Vietnamese) is the authoritative spec for Threads modes, statuses, cookies, and proxy routing. `docs/platform-branding.md` specifies the Excel export layout (logo A1, header row 3, platform icon at H2 on both platforms). `docs/desktop-release.md` covers release secrets and the updater.
- User-facing strings are Vietnamese. Keep UTF-8.
