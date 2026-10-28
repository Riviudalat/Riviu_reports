# Repository Guidelines

## Project Structure & Module Organization

The Python code is the `riviu` package. It runs two ways: source mode, `python -m riviu` on a local machine (the root `app.py` is a shim that does the same), and server mode, the same app in Docker with `RIVIU_SERVER_MODE=1` (see below). There is no desktop app any more.

- `riviu/app.py` builds the FastAPI app from the routers in `riviu/api/`; `riviu/scan_state.py` and `riviu/scan_service.py` own the single-scan state and dispatch.
- the `riviu/platforms/tiktok/` package handles TikTok; `riviu/platforms/threads.py` and `riviu/platforms/threads_session.py` handle Threads metrics and authenticated sessions.
- the `riviu/workbook/` package (re-exported by the `riviu/workbook_utils.py` facade), `riviu/google_sheets_sync.py`, and `riviu/proxy_utils.py` own workbook operations, Google integration, and proxy transport.
- `riviu/reports.py` builds partner Excel reports, export payloads, and Google push rows; it must not import `riviu.app`.
- `riviu/paths.py` resolves web resources and the user-data directory.
- `riviu/web/templates/index.html`, `riviu/web/static/` and `riviu/web/logo.png` contain the HTML, JavaScript, CSS, icons and logo.
- Tests live in `tests/`. The current architecture and specs live in `docs/`, and dated reviews, plans and specs in `docs/archive/`.
- `Khoidong.bat`, `setup.bat` and `capnhat.bat` are the Windows source-mode launchers (start, install, update).
- `riviu/store/` (selected by `RIVIU_STORAGE`, default `workbook`), `riviu/internal_scan.py`, `riviu/api/internal.py` and `riviu/api/riviu_data.py` add the opt-in Riviu Data Postgres backend for server mode; see `docs/riviu-data-postgres.md`. Never commit a DSN or scan token.
- `riviu/server_mode.py` and `riviu/api/auth.py` add the opt-in server mode (`RIVIU_SERVER_MODE=1`) and `riviu/server_users.py` its admin/viewer accounts; `deploy/server/` holds its Docker, compose and nginx files. Never commit a real password hash, secret key or env file.

## Build, Test, and Development Commands

Run from the repository root in PowerShell:

```powershell
uv sync
uv run playwright install chromium
uv run python -m riviu
```

Dependencies live in `pyproject.toml` and `uv.lock`. `requirements.txt` is generated from the lock (`uv export --format requirements.txt --no-dev --locked -o requirements.txt`) as the pip fallback for source-mode machines without uv; never edit it by hand.

The web app listens on `http://127.0.0.1:1231`.

- `uv run pytest -q tests`: run repository tests, excluding archived copies under `output/` (`pyproject.toml` also limits plain `pytest` to `tests/`).
- `node --check riviu/web/static/app.js`: check JavaScript syntax.
- `uv lock --check`: check that `uv.lock` matches `pyproject.toml`.
- `uvx pip-audit -r requirements.txt --disable-pip --no-deps`: check the locked runtime dependencies for known vulnerabilities.

## Coding Style & Naming Conventions

Follow surrounding code: four-space indentation, Python `snake_case`, JavaScript `camelCase`, and descriptive module names. Keep platform-specific extraction separate from workbook and transport logic. Preserve Vietnamese interface text and UTF-8 encoding. Run `git diff --check` before submitting changes.

## Testing Guidelines

Use pytest with `test_*.py` files and `test_*` functions; browser regressions use Playwright and frontend tests also use Node harnesses. Use the `test-audit` skill before writing, changing, reviewing, or sweeping tests. Give each observable contract one owner test, preferably extending existing coverage. Bug regressions must fail against pre-fix code for the intended reason. Avoid tests that merely mirror implementation. Use synthetic cookies and temporary workbooks; live scans remain opt-in.

## Commit & Pull Request Guidelines

Recent commits use concise imperative summaries, commonly `fix:` or `feat:`. Keep changes focused. Describe the problem, resulting behavior, validation commands, and remaining limitations. Link relevant issues and include screenshots for visual changes. Distinguish source tests from verification on the deployed server.

## Security & Data Handling

Never commit cookies, proxy credentials, OAuth tokens, or signing secrets. Preserve atomic workbook saves and distinguish confirmed zero counts from unavailable values. Use workbook copies for exploratory scans and keep each platform bound to its intended file and sheet.
