"""Partner listing and partner Excel report export (built by `riviu.reports`)."""

import asyncio
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from riviu.reports import build_export_payload
from riviu.source_files import SourceRequestError, content_disposition, resolve_source_file
from riviu.workbook_utils import (
    default_data_sheet,
    clean_text,
    find_data_sheet_listing,
    list_workbook_partners_with_link_counts,
    workbook_sheet_names,
)


router = APIRouter()


@router.get("/report-partners")
async def report_partners(
    sheet_name: str = "",
    apply_min_views: bool = True,
    min_views: int = 100,
    platform: str = "tiktok",
    file_id: str | None = None,
):
    try:
        file_id, target_path, file_label, platform = resolve_source_file(file_id, platform)
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)

    try:
        data_sheets, hidden_sheets = await asyncio.to_thread(find_data_sheet_listing, target_path)
        requested_sheet = clean_text(sheet_name) or default_data_sheet(data_sheets)
        if requested_sheet and requested_sheet not in data_sheets:
            return JSONResponse(content={"error": f"Sheet {requested_sheet} không tồn tại trong file."}, status_code=400)
        safe_min_views = max(int(min_views or 0), 0)

        def _load_partners():
            return list_workbook_partners_with_link_counts(
                target_path,
                sheet_name=requested_sheet,
                apply_min_views=apply_min_views,
                min_views=safe_min_views,
                platform=platform,
            )

        partners = await asyncio.to_thread(_load_partners)
        all_sheets = await asyncio.to_thread(workbook_sheet_names, target_path)
        return {
            "partners": partners,
            "total": len(partners),
            "file": file_id,
            "file_id": file_id,
            "fileLabel": file_label,
            "sheets": data_sheets,
            "hiddenSheets": hidden_sheets,
            "currentSheet": requested_sheet,
            "dataSheet": requested_sheet,
            "allSheets": all_sheets,
            "platform": platform,
        }
    except Exception as error:
        return JSONResponse(content={"error": f"Không đọc được danh sách đối tác: {str(error)}"}, status_code=500)


@router.post("/export-report")
async def export_report(data: dict):
    try:
        _, target_path, _, platform = resolve_source_file(data.get("file_id"), data.get("platform", "tiktok"))
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)

    selected_partners = data.get("partners") or []
    if not isinstance(selected_partners, list) or not selected_partners:
        return JSONResponse(content={"error": "Vui lòng chọn ít nhất một đối tác"}, status_code=400)

    apply_min_views = bool(data.get("applyMinViews", True))
    try:
        min_views = int(data.get("minViews", 100))
    except (TypeError, ValueError):
        min_views = 100
    min_views = max(min_views, 0)

    try:
        payload = await asyncio.to_thread(
            build_export_payload,
            target_path,
            selected_partners,
            apply_min_views,
            min_views,
            data.get("sheetName", ""),
            platform,
        )
        return Response(
            content=payload["content"],
            media_type=payload["media_type"],
            headers={"Content-Disposition": content_disposition(payload["filename"])},
        )
    except ValueError as error:
        status_code = 404 if "Không tìm thấy" in str(error) else 400
        return JSONResponse(content={"error": str(error)}, status_code=status_code)
    except Exception as error:
        return JSONResponse(content={"error": f"Lỗi xuất báo cáo: {str(error)}"}, status_code=500)
