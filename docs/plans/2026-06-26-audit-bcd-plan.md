# Audit Fixes B→C→D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix proxy/scrape safety and UX, validate Google OAuth properly, and align UI with backend (hybrid mode, cache bust, dead code removal).

**Architecture:** Three sequential commits (Phase B → C → D). Each phase is independently testable via pytest + manual smoke. Minimal new abstractions; extend existing modules.

**Tech Stack:** Python 3.11+, FastAPI, urllib/Playwright scraper, vanilla JS, PySocks (new)

**Spec:** `docs/specs/2026-06-26-audit-bcd-design.md`

---

## File map

| File | Phase | Change |
|------|-------|--------|
| `proxy_utils.py` | B,D | SOCKS5 opener, `with open`, build constant |
| `app.py` | B,C,D | proxy fail-fast WS, oauth fields, asset_version, remove dead routes |
| `scraper.py` | B | (read-only unless fail-fast message) |
| `static/app.js` | B,C,D | test/save split, version banner, hybrid, googlePushReady |
| `templates/index.html` | D | hybrid option |
| `google_sheets_sync.py` | C,D | oauth_status valid, optional API sync, delete dead fn |
| `workbook_utils.py` | C | authenticated sheet download |
| `requirements.txt`, `setup.bat` | B | PySocks |
| `Khoidong.bat` | B | print version |
| `tests/test_proxy_utils.py` | B | SOCKS5 parse test |
| `tests/test_app_helpers.py` | B | proxy fail-fast helper if extracted |

---

## Phase B — Proxy + scrape

### Task B1: Backend fail-fast when use_proxy with empty configs

**Files:**
- Modify: `app.py` (~WebSocket `start` handler before `create_task`)
- Test: `tests/test_app_helpers.py`

- [ ] **Step 1: Add helper**

```python
def validate_proxy_start(use_proxy: bool, proxy_text: str, base_dir: str) -> str | None:
    if not use_proxy:
        return None
    from proxy_utils import resolve_proxy_configs
    if resolve_proxy_configs(base_dir, proxy_text):
        return None
    return "Bật proxy nhưng chưa có proxy hợp lệ. Mở Cấu hình và dán proxy."
```

- [ ] **Step 2: Write failing test**

```python
def test_validate_proxy_start_blocks_empty():
    from app import validate_proxy_start
    msg = validate_proxy_start(True, "", str(tmp_path))
    assert msg is not None
```

- [ ] **Step 3: Wire WebSocket** — if message returned, `send_json` error and `continue` without starting task

- [ ] **Step 4: Run** `pytest tests/test_app_helpers.py -q`

- [ ] **Step 5: Commit** `fix: block scrape start when proxy enabled but empty`

---

### Task B2: Split Test vs Save for proxy list

**Files:**
- Modify: `static/app.js` (`testProxyList`, `saveProxyList`)
- Modify: `app.py` `/proxy-test` — `save` defaults `False`

- [ ] **Step 1:** Change `testProxyList` body to `{ text, save: false }`
- [ ] **Step 2:** After successful test, optionally prompt or rely on user clicking Lưu
- [ ] **Step 3:** Manual: Test does not overwrite `proxy_list.txt` until Lưu
- [ ] **Step 4: Commit** `fix: proxy test no longer auto-saves`

---

### Task B3: SOCKS5 support in Request mode

**Files:**
- Modify: `requirements.txt` — add `PySocks>=1.7.1`
- Modify: `setup.bat` verify line — include `socks`
- Modify: `proxy_utils.py` `urlopen_with_config`
- Test: `tests/test_proxy_utils.py`

- [ ] **Step 1: Test parse**

```python
def test_parse_socks5_url():
    cfg = parse_proxy_line("socks5://1.2.3.4:1080")
    assert cfg["type"] == "socks5"
```

- [ ] **Step 2: Fix opener** — for socks5 use `socks.socksocket` or `urllib.request` with ProxyHandler using `socks5h://user:pass@host:port` via PySocks monkeypatch pattern documented in PySocks README

- [ ] **Step 3:** Run full `pytest tests/ -q`

- [ ] **Step 4: Commit** `fix: SOCKS5 proxy support for HTTP request scraping`

---

### Task B4: Version banner + Khoidong startup line

**Files:**
- Modify: `Khoidong.bat`, `static/app.js`, `app.py`

- [ ] **Step 1:** Khoidong after venv activate:

```bat
"%VENV_PY%" -c "from proxy_utils import PROXY_TEST_BUILD; print('[OK] Proxy test build:', PROXY_TEST_BUILD)"
```

- [ ] **Step 2:** `checkServerVersion()` on load — fetch `/api/version`, compare `proxyTestBuild` to `localStorage.serverProxyBuild`; mismatch → show top banner

- [ ] **Step 3:** On successful `/proxy-test`, store build in localStorage

- [ ] **Step 4: Commit** `feat: server version banner after code update`

---

## Phase C — Google OAuth / Sheets

### Task C1: Valid OAuth status

**Files:**
- Modify: `google_sheets_sync.py` `oauth_status`
- Modify: `static/app.js` `refreshGoogleOauthStatus`

- [ ] **Step 1:** Return `{ configured, authorized, valid, accountEmail }` where `valid = load_credentials() is not None and creds.valid`

- [ ] **Step 2:** UI button text: valid → Đã đăng nhập; authorized but not valid → Token hết hạn; else Chưa đăng nhập

- [ ] **Step 3: Commit** `fix: oauth status reflects token validity`

---

### Task C2: Use googlePushReady from API

**Files:**
- Modify: `static/app.js` `updateFileList` / `setGooglePushState`

- [ ] **Step 1:** When `/files` returns `googlePushReady`, assign to module variable and use in `setGooglePushState()`

- [ ] **Step 2: Commit** `fix: wire googlePushReady from backend`

---

### Task C3: Authenticated sheet download

**Files:**
- Modify: `google_sheets_sync.py` or `workbook_utils.py`
- Modify: `app.py` sync-google-sheet handler

- [ ] **Step 1:** Add `download_sheet_with_credentials(spreadsheet_id, sheet_name, creds)` using Sheets API v4 `spreadsheets.values.get`

- [ ] **Step 2:** In sync endpoint, try credentials first; fallback public export; return explicit error JSON for private sheet without login

- [ ] **Step 3: Commit** `feat: sync private google sheets when oauth valid`

---

## Phase D — Cleanup + UI sync

### Task D1: Hybrid mode in UI

**Files:**
- Modify: `templates/index.html`, `static/app.js`

- [ ] **Step 1:** Add option `Hybrid (Request + trình duyệt fallback)`
- [ ] **Step 2:** `startScraping` sends `scrape_mode: 'hybrid'` when selected
- [ ] **Step 3: Commit** `feat: expose hybrid scrape mode in UI`

---

### Task D2: Cache bust CSS + JS

**Files:**
- Modify: `app.py` index route

```python
def static_asset_version(base_dir: str) -> str:
    paths = [
        os.path.join(base_dir, "static", "app.js"),
        os.path.join(base_dir, "static", "styles.css"),
    ]
    mtimes = [int(os.path.getmtime(p)) for p in paths if os.path.exists(p)]
    return str(max(mtimes)) if mtimes else "1"
```

- [ ] **Commit** `fix: cache bust css and js together`

---

### Task D3: Remove dead endpoints/code

**Files:**
- Modify: `app.py`, `google_sheets_sync.py`

- [ ] **Step 1:** Grep confirm no references to `/proxy-status`, `push_rows_to_sheet`
- [ ] **Step 2:** Remove unused functions/routes
- [ ] **Step 3:** Remove unused WebSocket flags if frontend never sends them
- [ ] **Step 4: Commit** `chore: remove unused proxy-status and dead sheet helpers`

---

### Task D4: Small cleanups

**Files:**
- Modify: `proxy_utils.py`, `static/app.js`

- [ ] **Step 1:** `load_proxy_list_text` use context manager
- [ ] **Step 2:** `loadProxyList` catch → `notify(..., 'error')`
- [ ] **Step 3: Commit** `chore: proxy list loader and fetch error handling`

---

## Final verification

- [ ] `pytest tests/ -q` — all pass
- [ ] Manual: 2 proxy lines → Test → `2/2 proxy OK` with VN/SG
- [ ] Manual: proxy on, empty list → cannot start scrape
- [ ] Manual: Hybrid mode → log mentions hybrid workers
- [ ] Restart Khoidong after pull → no stale-server banner after test

---

## Execution handoff

Plan saved to `docs/plans/2026-06-26-audit-bcd-plan.md`.

**Options:**
1. **Subagent-Driven** — one subagent per task, review between tasks
2. **Inline** — implement B→C→D in this session with checkpoints after each phase

Which approach?
