"""Partner reports read from Riviu Data (RIVIU_STORAGE=postgres only; 404 otherwise).

GET  /riviu-data/months?platform=tiktok
GET  /riviu-data/rows?platform=tiktok&thang=2026-10
GET  /riviu-data/report-partners?platform=tiktok&thang=2026-10&apply_min_views=true&min_views=100
POST /riviu-data/export-report {"platform", "thang", "partners": [...], "applyMinViews", "minViews"}

`months` lists the month sheets (newest first) and `rows` the posts of one month with their
metrics and scan status, for the web app's table. The report routes give the same rows,
partner list, filters and Excel files as /report-partners and /export-report on a workbook,
read from the soft-delete view v_<platform> for one `thang_nam` month. All four are reads the
public viewer makes (`security.VIEWER_GET_PATHS` / `VIEWER_POST_PATHS`).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from riviu import internal_scan
from riviu.reports import build_export_payload_from_rows
from riviu.source_files import content_disposition
from riviu.store import config as store_config
from riviu.store.rules import ALL_REPORT_METRICS, REPORT_METRIC_COLUMNS, MonthError, current_thang, parse_thang
from riviu.workbook.constants import LAST_UPDATE_COLUMN
from riviu.workbook.report_rows import partner_link_counts

router = APIRouter()


def _check(platform, thang):
    if not store_config.ACTIVE.postgres:
        return JSONResponse({"error": "Not Found"}, status_code=404)
    if platform not in internal_scan.PLATFORMS:
        return JSONResponse({"error": "Nền tảng không hợp lệ"}, status_code=400)
    try:
        parse_thang(thang)
    except MonthError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return None


def _unavailable(error):
    return JSONResponse({"error": f"Không đọc được Riviu Data: {type(error).__name__}"}, status_code=502)


def month_label(thang):
    year, month = thang.split("-")
    return f"Tháng {int(month)}/{year}"


def default_month(months, today=None):
    """The newest month sheet that is not in the future (staff may create next month early); else the newest."""
    today = today or current_thang()
    return next((thang for thang in months if thang <= today), months[0] if months else "")


@router.get("/riviu-data/months")
async def riviu_data_months(platform: str = "tiktok"):
    denied = _check(platform, current_thang())
    if denied is not None:
        return denied
    try:
        months = await internal_scan.make_store(platform).months()
    except Exception as error:  # noqa: BLE001
        return _unavailable(error)
    return {
        "platform": platform,
        "months": [{"thang": thang, "label": month_label(thang), "rows": rows} for thang, rows in months],
        "defaultThang": default_month([thang for thang, _ in months]),
    }


# Row keys of the table; metric keys are the platform's own (REPOST in place of LƯỢT LƯU on Threads).
METRIC_KEYS = {"LƯỢT XEM": "views", "TIM": "likes", "BÌNH LUẬN": "comments", "LƯỢT LƯU": "saves", "REPOST": "reposts", "CHIA SẺ": "shares"}


def table_row(row, platform):
    ngay_air = row["NGÀY AIR"]
    return {
        "id": row["_id"],
        "ngayAir": ngay_air.strftime("%d/%m/%Y") if ngay_air else "",
        "nguoiAir": row.get("_nguoiAir", ""),
        "channel": row["TÊN KÊNH"],
        "link": row["LINK AIR"],
        "partners": list(row["partners"]),
        # None = unknown (never scanned or failed), 0 = a confirmed zero.
        **{METRIC_KEYS[metric]: row[metric] for metric in ALL_REPORT_METRICS if metric in REPORT_METRIC_COLUMNS[platform]},
        "updatedAt": row[LAST_UPDATE_COLUMN],
        "status": row["_scanStatus"],
    }


@router.get("/riviu-data/rows")
async def riviu_data_rows(platform: str = "tiktok", thang: str = ""):
    denied = _check(platform, thang)
    if denied is not None:
        return denied
    try:
        sheet_rows = await internal_scan.make_store(platform).report_rows(thang, with_ids=True)
    except Exception as error:  # noqa: BLE001
        return _unavailable(error)
    return {
        "platform": platform, "thang": thang, "partners": list(sheet_rows.partners),
        "rows": [table_row(row, platform) for row in sheet_rows.rows],
    }


@router.get("/riviu-data/report-partners")
async def riviu_data_report_partners(platform: str = "tiktok", thang: str = "", apply_min_views: bool = True, min_views: int = 100):
    denied = _check(platform, thang)
    if denied is not None:
        return denied
    try:
        sheet_rows = await internal_scan.make_store(platform).report_rows(thang)
    except Exception as error:  # noqa: BLE001
        return _unavailable(error)
    partners = partner_link_counts(
        sheet_rows.partners, sheet_rows.rows, apply_min_views=apply_min_views, min_views=max(int(min_views or 0), 0),
        platform=platform,
    )
    return {"partners": partners, "total": len(partners), "platform": platform, "thang": thang, "rows": len(sheet_rows.rows)}


@router.post("/riviu-data/export-report")
async def riviu_data_export_report(data: dict):
    platform = data.get("platform", "tiktok")
    thang = data.get("thang", "")
    denied = _check(platform, thang if isinstance(thang, str) else "")
    if denied is not None:
        return denied
    selected = data.get("partners") or []
    if not isinstance(selected, list) or not selected:
        return JSONResponse({"error": "Vui lòng chọn ít nhất một đối tác"}, status_code=400)
    try:
        min_views = max(int(data.get("minViews", 100)), 0)
    except (TypeError, ValueError):
        min_views = 100
    try:
        sheet_rows = await internal_scan.make_store(platform).report_rows(thang)
    except Exception as error:  # noqa: BLE001
        return _unavailable(error)
    try:
        payload = await asyncio.to_thread(
            build_export_payload_from_rows, sheet_rows, selected, bool(data.get("applyMinViews", True)), min_views,
            thang, platform,
        )
    except ValueError as error:
        return JSONResponse({"error": str(error)}, status_code=404 if "Không tìm thấy" in str(error) else 400)
    return Response(
        content=payload["content"], media_type=payload["media_type"],
        headers={"Content-Disposition": content_disposition(payload["filename"])},
    )
