# Riviu Reports — review fixes 2026-10-02

User authorized fixing the14 findings, test scans on copies, then commit/push to the existing feature branch. `/orchestration` unavailable; used Workflow with two isolated implementation scopes plus one independent verifier (3 agents total, no grandchildren). Unexpected task-aligned workspace edits were detected; writes paused and user explicitly chose preserve/review. Compatible changes retained, duplicate helper/import additions reconciled, no unknown changes blindly reverted.

## Corrections

| # | Review issue | Fix / regression |
|---|---|---|
| 1 | TikTok totals overwrite Threads/other rows | Totals placed after occupied rows; SUM references only TikTok rows; only scanned sheets touched. Copied mixed-sheet scan regression preserves source bytes, Threads row, footer and unselected sheet. |
| 2 | Spreadsheet formula injection | `set_cell_literal` enforces Excel string cell type for input text; app report cells and Threads channel use it. Google inputs RAW; only exact final-row generated SUM formulas evaluated separately. |
| 3 | Cancelled HTTP threads outlive proxy state | TikTok executor shielded/joined before cleanup; failure/cancellation propagation tested; proxy pool released only after owned HTTP completes. |
| 4 | TikTok URL substring/redirect acceptance | HTTP(S), exact allowed hostnames, URL identity checks before fetch; HTTP redirect validator; browser manual approved hops and final refetch rejects30x. |
| 5 | All-disabled proxy uses direct | Start validation and TikTok runner require nonempty enabled pool when proxy requested. |
| 6 | Display-derived views retained after exact null | Later `view_query_unknown` invalidates header display in merge and saved views cell; generic missing metrics preserve existing values; exact confirmed values remain authoritative. |
| 7 | Summary-name collisions | Stable hash suffix for long names; lower-case identity matching Excel distinguishes Straße/STRASSE; reverse mapping used by backend preview `summarySource`, consumed by UI. |
| 8 | Export ZIP mixes generations | One temporary immutable workbook snapshot feeds partner discovery and every report; synthetic atomic-source change test yields same generation for both partners. |
| 9 | Old report sheet in new source | Native report sheet select cleared/repopulated before request. |
| 10 | Mixed preview totals incorrect | Preview receives explicit platform and filters before totals; accepted mobile TikTok host visible too. |
| 11 | Schemeless Threads preview missing | UI normalizes/validates exact hosts + post/share paths before filtering/counts. |
| 12 | Empty platform retains old summary | Clear dashboard/counters and show empty preview; stale success/failure responses ignored. |
| 13 | Updater catch masks failure | Removed undefined isCurrent guard; synthetic failed IPC recovers controls and reports failure. |
| 14 | Port-scoped desktop preferences lost | Protected atomic `/source-preferences` GET/POST under persistent app data; validated file/sheet/Google URL fields only, no cookie/proxy secrets; serialized frontend writes and startup restore. Query/fragment gid round-trips. |

## Verification
- Focused new suites: `test_review_scan_data.py`, `test_review_frontend.py`, `test_review_main_fixes.py`; independent verification rechecked5 residual integration findings after correction.
- Full offline suite is run with `python -m pytest -q tests` (not archival output copies), alongside JavaScript syntax, pip dependency consistency and diff checks. Final count: **765 passed, 3 skipped**; JavaScript syntax, pip dependency check and diff whitespace gates passed. Dependency deprecation warnings remain.
- Live Threads copy scan:90 eligible rows/89 unique URLs,2 profiles skipped,82URLsuccess/83savedrows,7unavailable.79views all `view_query_exact`;82likes/comments/reposts;34shares;3exactviewqueriesunknown. Original workbook hash unchanged. Output `output/threads-excel-retest/run-8yf624ub`.
- TikTok scan verified through synthetic copied-workbook request runner and controlled browser/redirect/cancellation fixtures. No live TikTok account/proxy or Google write.

## Acceptance limits and git
No production report overwrite, Google write, real proxy, user app restart, actual Tauri restart, installer/build/update release. Persistence tested across distinct local HTTP ports, not Tauri/WebView installation. Mac/Linux secure vault behavior remains mocked on Windows. The default main-branch release workflow is not intentionally triggered: commit/push is to `fix/threads-hydrated-counts` only, no merge/main push. Cookie/secret/data/output/build artifacts are excluded from staging; prior committed OAuth config is not modified or reproduced in this change.
