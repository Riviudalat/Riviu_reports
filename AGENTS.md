# Repository Guidelines

## Project Structure & Module Organization

The Python code is the `riviu` package; run it with `python -m riviu` (the root `app.py` is a shim that does the same).

- `riviu/app.py` serves the FastAPI API and WebSocket scan events; `riviu/desktop_server.py` integrates the desktop backend.
- `riviu/platforms/tiktok.py` handles TikTok; `riviu/platforms/threads.py` and `riviu/platforms/threads_session.py` handle Threads metrics and authenticated sessions.
- `riviu/workbook_utils.py`, `riviu/google_sheets_sync.py`, and `riviu/proxy_utils.py` own workbook operations, Google integration, and proxy transport.
- `riviu/reports.py` builds partner Excel reports, export payloads, and Google push rows; it must not import `riviu.app`.
- `riviu/paths.py` resolves web resources and the user-data directory for source and frozen runs.
- `riviu/web/templates/index.html`, `riviu/web/static/` and `riviu/web/logo.png` contain the HTML, JavaScript, CSS, icons and logo.
- `src-tauri/` contains the Rust desktop shell; `desktop/` contains sidecar packaging scripts and `desktop-dist/` the Tauri loading page. Tests live in `tests/`. The current architecture and release notes live in `docs/`, and dated reviews, plans and specs in `docs/archive/`.
- `Khoidong.bat`, `setup.bat` and `capnhat.bat` are the Windows source-mode launchers (start, install, update).

## Build, Test, and Development Commands

Run from the repository root in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -m riviu
```

The web app listens on `http://127.0.0.1:1231`.

- `npm run desktop:dev`: build the Python sidecar and start Tauri development.
- `npm run desktop:build`: build the sidecar and desktop application; requires Rust and platform build tools.
- `.venv\Scripts\python.exe -m pytest -q tests`: run repository tests, excluding archived copies under `output/` (`pyproject.toml` also limits plain `pytest` to `tests/`).
- `node --check riviu/web/static/app.js`: check JavaScript syntax.
- `.venv\Scripts\python.exe -m pip check`: check installed dependency compatibility.

## Coding Style & Naming Conventions

Follow surrounding code: four-space indentation, Python `snake_case`, JavaScript `camelCase`, and descriptive module names. Keep platform-specific extraction separate from workbook and transport logic. Preserve Vietnamese interface text and UTF-8 encoding. Run `git diff --check` before submitting changes.

## Testing Guidelines

Use pytest with `test_*.py` files and `test_*` functions; browser regressions use Playwright and frontend tests also use Node harnesses. Use the `test-audit` skill before writing, changing, reviewing, or sweeping tests. Give each observable contract one owner test, preferably extending existing coverage. Bug regressions must fail against pre-fix code for the intended reason. Avoid tests that merely mirror implementation. Use synthetic cookies and temporary workbooks; live scans remain opt-in.

## Commit & Pull Request Guidelines

Recent commits use concise imperative summaries, commonly `fix:` or `feat:`. Keep changes focused. Describe the problem, resulting behavior, validation commands, and remaining limitations. Link relevant issues and include screenshots for visual changes. Distinguish source tests from installer verification. Check release automation before pushing to `main`.

## Security & Data Handling

Never commit cookies, proxy credentials, OAuth tokens, or signing secrets. Preserve atomic workbook saves and distinguish confirmed zero counts from unavailable values. Use workbook copies for exploratory scans and keep each platform bound to its intended file and sheet.
