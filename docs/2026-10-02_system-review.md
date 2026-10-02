# Riviu Reports — system review 2026-10-02

## Scope and method
Requested `/orchestration` was unavailable (Unknown skill). Used an explicitly authorized Workflow with two read-only review scopes and one combined adversarial verifier: 3 agents total, at most 2 concurrent. Reviewed current working tree, including untracked source/tests, not only committed baseline.

Runtime scope: local API/WebSocket, scan lifecycle, TikTok/Threads extraction, sessions, cookies, proxy, cancellation and security. Data/UI/desktop scope: workbook/provenance, Google import/export, report builder, platform source state, UI counters, sidecar/Tauri/updater/release source. Historical review documents were not treated as present-day proof.

Findings below were independently rechecked using source and isolated synthetic/in-memory probes. No credentials, live account/proxy/Google requests, production workbooks, or user app processes were used. The review does not establish a bug-free system.

## Verified findings — historical reproduction, fixed in follow-up

| Priority | Finding | Reproduction / impact | Anchor (repo-relative) |
|---|---|---|---|
| P1 | TikTok total-row generation overwrites a following Threads row | TikTok row2 + Threads row3: total helper writes TỔNG at row3 and final save persists lost permalink/metrics. Also runs outside intended scanned sheet. | `scraper.py:660` |
| P1 | Untrusted text becomes exported spreadsheet formulas | Channel `=1+1` becomes Excel formula; Google USER_ENTERED accepts formula-prefixed channel/partner text, including external-fetch formulas. Preserve intentional total formulas but force input text literal. | `app.py:652`, `google_sheets_sync.py:418` |
| P2 | TikTok cancellation does not join active HTTP executor | Awaiting coroutine cancellation finishes while HTTP thread continues; cleanup clears shared proxy pool and later run may replace its routing. | `scraper.py:2398`, `scraper.py:3068` |
| P2 | TikTok substring URL filter allows non-TikTok hosts | Arbitrary-host/loopback/lookalike URL containing tiktok.com passes scan filter before navigation. Validate scheme/host and redirects. | `workbook_utils.py:599`, `scraper.py:559` |
| P2 | TikTok proxy-on can become direct with all-disabled list | Validation accepts disabled config, session pool filters it out, empty pool routes direct. Reject enabled-pool-empty. | `app.py:738`, `proxy_utils.py:364` |
| P2 | Later exact-unknown Threads views keep earlier rounded value | header_display15100 then view_query_unknown merge retains15100 contrary to exact-null/conflict contract. Reverse order already tested; transition needs correction. | `threads_scraper.py:388` |
| P2 | Truncated summary names collide | Two long source names map to same31-character summary; rebuilding second clears first summary. Stable collision-safe mapping required. | `workbook_utils.py:501`, `workbook_utils.py:1450` |
| P2 | Multi-partner ZIP export reads different workbook generations | Scan atomic save between independent per-partner loads produces mixed generations in one export. Reproduced controlled reads; live scheduling not exercised. | `app.py:970`, `app.py:1413` |
| P2 | Report modal keeps previous file's sheet choice | NewfileB requests Old A Sheet from retained reportSheetSelect.value, receives sheet-not-found. | `static/app.js:2190` |
| P2 | TikTok preview total includes hidden Threads rows | Mixed sheet TIM10 TikTok+TIM20 Threads displays only TikTok but total30. Need platform-specific totals. | `workbook_utils.py:674`, `static/app.js:1291` |
| P2 | Schemeless Threads links excluded by preview | Backend accepts threads.com/@demo/post/abc; frontend requires explicit HTTP prefix so hides/counts0. | `static/app.js:1288`, `workbook_utils.py:565` |
| P2 | Empty platform retains previous summary view | Switch from summary to unconfigured Threads writes empty message into hidden preview without clearing/showing correct view. | `static/app.js:1243` |
| P2 | Updater catch throws ReferenceError | Updater failure calls undefined isCurrent(), masking original rejected IPC and producing rejected background promise. | `static/app.js:1957` |
| P2 | Desktop preferences depend on changing origin port | Tauri gets new port per launch; origin localStorage cannot read old source selections. Source-only/high confidence; actual Tauri restart not tested. | `src-tauri/src/lib.rs:20`, `src-tauri/src/lib.rs:183`, `static/app.js:27` |

Line numbers above reflect the original review snapshot and may shift. A follow-up authorized fix pass addressed all14, with an independent verifier checking integration and focused regression tests. See `2026-10-02_review-fixes.md` for corrections, verification and remaining acceptance gates. The original reproduction descriptions remain as audit history, not current open defects.

## Icons implemented separately
Local Simple Icons CC0 SVGs and256px transparent PNG counterparts in `static/platform-icons`, source/trademark notice included. UI platform buttons/source labels/report modal carry text+icon, no external icon font dependency. Partner Excel keeps Riviu logoA1/table row3/data4; platform icon is embedded at final column row2. Both platform report titles explicitly identify TikTok/Threads. Google Sheets are not modified with external IMAGE formulas; platform tab labels remain.

## Verification limits
Review agents did not run full pytest, live scans, actual Google writes, Tauri/WebView restart, installer build/update, or icon tests. Main runs icon/UI/export regression gates separately and reports results. Passing fixture tests is not live-device/account/proxy/installer acceptance. Missing root AGENTS/README/CLAUDE/agent-runbook was checked; current desktop and Threads documentation used where present.
