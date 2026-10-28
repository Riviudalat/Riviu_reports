"""Partner reports read from Riviu Data (RIVIU_STORAGE=postgres only; 404 otherwise).

GET  /riviu-data/report-partners?platform=tiktok&thang=2026-10&apply_min_views=true&min_views=100
POST /riviu-data/export-report {"platform", "thang", "partners": [...], "applyMinViews", "minViews"}

Same rows, partner list, filters and Excel files as /report-partners and /export-report on a
workbook, read from the soft-delete view v_<platform> for one `thang_nam` month. The routes
sit behind the normal login (server mode session cookie).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from riviu import internal_scan
from riviu.reports import build_export_payload_from_rows
from riviu.source_files import content_disposition
from riviu.store import config as store_config
from riviu.store.rules import MonthError, parse_thang
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


@router.get("/riviu-data/report-partners")
async def riviu_data_report_partners(platform: str = "tiktok", thang: str = "", apply_min_views: bool = True, min_views: int = 100):
    denied = _check(platform, thang)
    if denied is not None:
        return denied
    try:
        sheet_rows = await internal_scan.make_store(platform).report_rows(thang)
    except Exception as error:  # noqa: BLE001
        return JSONResponse({"error": f"Không đọc được Riviu Data: {type(error).__name__}"}, status_code=502)
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
        return JSONResponse({"error": f"Không đọc được Riviu Data: {type(error).__name__}"}, status_code=502)
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
