# Per-sheet summary (Tổng kết theo tháng)

## Goal

When the user scans a data sheet (e.g. `Tháng 6`), rebuild only that sheet’s partner totals into `Tổng kết tháng 6`, placed immediately after the data sheet in the local Excel workbook.

## Naming

- Formula: `Tổng kết ` + `data_sheet_name.casefold()` (max 31 Excel chars).
- Example: `Tháng 6` → `Tổng kết tháng 6`.

## Behavior

- `build_partner_summary_rows` aggregates **one** data sheet only.
- `rebuild_summary_sheet(workbook, data_sheet_name=...)` creates/updates the paired summary sheet and inserts it at `index(data_sheet) + 1`.
- Legacy single sheet `Tổng kết` is no longer updated; still excluded from data-sheet lists.
- `/summary-dashboard?sheet_name=` accepts the **data** sheet name (e.g. `Tháng 6`).
- Web UI: summary view when the active tab is a `Tổng kết …` sheet; API uses mapped data sheet name.

## Out of scope

- Partner cell parsing (multi-name in one cell).
- Migrating/deleting old global `Tổng kết` automatically.
