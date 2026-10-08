# Audit Fixes B→C→D — Design Spec

**Date:** 2026-06-26  
**Status:** Approved  
**Priority order:** B (Proxy + scrape) → C (Google OAuth/Sheets) → D (Cleanup + UI sync)

## Goal

Harden proxy/scrape behavior, improve Google OAuth/Sheet UX, and remove frontend/backend drift — without touching OAuth client secret rotation (out of scope, group A).

## Non-goals

- Rotating/removing committed `google_oauth_client.json` from Git history
- Changing `capnhat.bat` `git reset --hard` behavior
- Adding authentication to localhost server

---

## Phase B — Proxy + scrape

### B1 Fail-fast when proxy enabled but empty

**Behavior:** If WebSocket `start` has `use_proxy=true` and `resolve_proxy_configs(base_dir, proxy_text)` returns `[]`, reject before spawning scrape task.

**Surfaces:**
- WebSocket error/log message to client
- Frontend already blocks empty text in `startScraping()`; backend must also guard when saved file is empty/invalid

**Files:** `app.py` (WebSocket handler), optional test in `tests/test_app_helpers.py`

### B2 Proxy test UX

**Behavior:**
- One IP check per pasted line, parallel (keep current)
- **Test** does not auto-save (`save: false` default)
- **Lưu** remains explicit save only
- No TikTok probe in default test (IP-only = OK)

**Files:** `static/app.js`, `app.py` `/proxy-test`, `proxy_utils.py`

### B3 SOCKS5 for Request mode

**Behavior:**
- Add `PySocks` to `requirements.txt` and `setup.bat` import check
- Fix `urlopen_with_config` for `type == "socks5"` using SOCKS-aware opener (not HTTP ProxyHandler with http URL)
- Unit test: parse `socks5://host:port` + mock or skip live SOCKS if unavailable

**Files:** `proxy_utils.py`, `requirements.txt`, `setup.bat`, `tests/test_proxy_utils.py`

### B4 Server version visibility

**Behavior:**
- `Khoidong.bat` prints `PROXY_TEST_BUILD` / git short hash on startup (Python one-liner)
- Frontend on `window.onload`: `GET /api/version` — if missing or stale vs last known, show dismissible banner “Restart Khoidong.bat”
- Keep existing legacy detection in `renderProxyTestResult`

**Files:** `Khoidong.bat`, `static/app.js`, `app.py`, `proxy_utils.py`

---

## Phase C — Google Sheet / OAuth

### C1 OAuth status validation

**Behavior:** Extend `oauth_status()`:
- `configured`: client secret file exists
- `authorized`: token file exists
- `valid`: `load_credentials()` returns creds and `creds.valid` (refresh if expired)
- UI labels: Chưa cấu hình / Chưa đăng nhập / Token hết hạn / Đã đăng nhập

**Files:** `google_sheets_sync.py`, `app.py`, `static/app.js`

### C2 Push button state

**Behavior:** Use `googlePushReady` from `/files` (or dedicated status) in `setGooglePushState()` instead of duplicating logic.

**Decision:** Do **not** require scan completion to enable push (keep current flexibility); fix misleading copy if any.

**Files:** `static/app.js`, `app.py`

### C3 Private Google Sheet sync

**Behavior:** `download_google_sheet()` / sync path:
- If valid credentials → use Sheets API export/read
- Else → existing public export URL with clear error for private sheets

**Files:** `workbook_utils.py` or `google_sheets_sync.py`, `app.py` sync endpoint

---

## Phase D — Cleanup + UI/backend sync

### D1 Hybrid scrape mode in UI

**Behavior:** Add `<option value="hybrid">Hybrid</option>` to `#scrapeModeSelect`; map to `scrape_mode: "hybrid"` in WebSocket (already supported in `app.py`).

**Files:** `templates/index.html`, `static/app.js`

### D2 Cache bust both JS and CSS

**Behavior:** `asset_version = max(mtime(app.js), mtime(styles.css))` in index route.

**Files:** `app.py`, `templates/index.html`

### D3 Dead code removal

**Remove or wire:**
- `GET /proxy-status` — remove if unused after confirming no callers
- WebSocket `create_result_sheet`, `push_to_google` if never sent from UI — remove from handler or document as reserved
- `push_rows_to_sheet` in `google_sheets_sync.py` if unused — delete

**Files:** `app.py`, `google_sheets_sync.py`, grep verification

### D4 Small fixes

- `loadProxyList()`: notify on fetch failure
- `load_proxy_list_text()`: use `with open`
- Remove phantom `samples` field usage in frontend

**Files:** `proxy_utils.py`, `static/app.js`

---

## Testing strategy

| Phase | Verification |
|-------|----------------|
| B | `pytest tests/test_proxy_utils.py`; manual 2-line VN/SG test; start scrape with proxy on + empty list → blocked |
| C | Mock expired token; push button states; sync private sheet with valid OAuth |
| D | Select Hybrid → hybrid log line in scraper; CSS change reflects after reload; no references to removed endpoints |

## Rollout

Three commits (one per phase), push after each phase passes tests.
