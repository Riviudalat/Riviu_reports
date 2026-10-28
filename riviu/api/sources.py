"""Workbook sources: listing, selection, upload, previews, download and saved per-platform preferences."""

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, urlsplit, urlunsplit

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from riviu import scan_state
from riviu import server_context as context
from riviu import source_files
from riviu.api import security
from riviu.google_sheets_sync import oauth_status
from riviu.source_files import (
    SourceRequestError,
    content_disposition,
    default_sheet_for_file,
    publish_new_workbook,
    resolve_source_file,
    source_sheet_default,
    validate_platform,
    validate_workbook,
)
from riviu.workbook_utils import (
    PLATFORMS,
    clean_text,
    data_sheet_name_for_summary_title,
    default_data_sheet,
    find_data_sheet_listing,
    find_data_sheet_names,
    google_sheet_source_for_file,
    parse_google_spreadsheet_id,
    read_sheet_preview,
    read_summary_dashboard,
    safe_join,
    workbook_sheet_names,
    write_json_atomic,
)


router = APIRouter()


@router.get("/list-files")
async def list_files(request: Request = None, file_id: str | None = None, platform: str = "tiktok"):
    try:
        platform = validate_platform(platform)
        # One listing per request: validation, the label and the response all read it.
        entries = source_files.file_entries()
        selected_id, target_path, file_label, platform = resolve_source_file(file_id, platform, allow_empty=True, entries=entries)
    except SourceRequestError as error:
        return error.response()
    google_source = dict(google_sheet_source_for_file(context.EXCEL_DIR, selected_id)) if selected_id else {}
    target_sheet_url = google_source.get("url", "")
    target_spreadsheet_id = ""
    if target_sheet_url:
        try:
            target_spreadsheet_id = parse_google_spreadsheet_id(target_sheet_url)
        except Exception:
            target_spreadsheet_id = ""
    try:
        sheets, hidden_sheets = await asyncio.to_thread(find_data_sheet_listing, target_path) if target_path else ([], [])
    except Exception:
        sheets, hidden_sheets = [], []
    default_sheet = default_data_sheet(sheets)
    selected_sheet = context.CURRENT_SELECTED_SHEET if file_id is None else default_sheet
    scan_sheet = (context.CURRENT_SCAN_SHEET or selected_sheet) if file_id is None else selected_sheet
    state = {
        "files": entries,
        "current": selected_id,
        "file_id": selected_id,
        "currentLabel": file_label,
        "currentSheet": selected_sheet,
        "sheets": sheets,
        "hiddenSheets": hidden_sheets,
        "scanSheet": scan_sheet,
        # What the page falls back to when a saved sheet is gone: the newest month sheet.
        "defaultSheet": default_sheet,
        "googleSheetUrl": target_sheet_url,
        "platform": platform,
    }
    if request is not None and security.request_role(request) == "viewer":
        # The public viewer never pushes: skip the Google status (and its lock and network calls).
        oauth = {"configured": False, "ready": False}
    else:
        oauth = await asyncio.to_thread(context.google_io, oauth_status, context.EXCEL_DIR)
    state.update({
        "googlePushReady": bool(target_spreadsheet_id) and oauth.get("ready", oauth.get("valid")),
        "googleOAuthConfigured": oauth.get("configured"),
        "googleOAuthAuthorized": oauth.get("ready", oauth.get("valid")),
    })
    return state


@router.post("/select-file")
async def select_file(data: dict):
    try:
        platform = validate_platform(data.get("platform", "tiktok"))
    except SourceRequestError as error:
        return error.response()
    if scan_state.source_mutation_blocked():
        return JSONResponse({"error": "Đang xử lý dữ liệu, hãy chờ hoàn tất trước khi đổi file"}, status_code=409)
    try:
        file_id, target_path, _, platform = resolve_source_file(data.get("file_id", data.get("filename", "")), platform)
    except SourceRequestError as error:
        return error.response()
    with scan_state.source_operation():
        names = await asyncio.to_thread(find_data_sheet_names, target_path)
        all_names = await asyncio.to_thread(workbook_sheet_names, target_path)
        selected_sheet = clean_text(data.get("display_sheet", data.get("sheet_name", ""))) or default_data_sheet(names)
        scan_sheet = clean_text(data.get("scan_sheet", "")) or (selected_sheet if selected_sheet in names else default_data_sheet(names))
        if (selected_sheet and selected_sheet not in all_names) or (scan_sheet and scan_sheet not in names):
            return JSONResponse({"error": "Sheet không tồn tại"}, status_code=400)
        source_files.select_source(file_id, selected_sheet, scan_sheet)
        return {"success": True, "selected": file_id, "file_id": file_id, "sheet": selected_sheet, "scanSheet": scan_sheet, "platform": platform}


@router.get("/preview-excel")
async def preview_excel(sheet_name: str = "", file_id: str | None = None, platform: str = "tiktok"):
    explicit = file_id is not None
    try:
        file_id, target_path, file_label, platform = resolve_source_file(file_id, platform)
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return {"sheets": [], "currentSheet": "", "columns": [], "data": [], "message": f"Không tìm thấy file {context.CURRENT_SELECTED_FILE}."}
    try:
        requested_sheet = sheet_name or source_sheet_default(file_id, explicit=explicit) or default_sheet_for_file(file_id)

        def _load_preview():
            return read_sheet_preview(target_path, sheet_name=requested_sheet, platform=platform)

        preview = await asyncio.to_thread(_load_preview)
        if not explicit and context.CURRENT_SELECTED_FILE == file_id:
            context.CURRENT_SELECTED_SHEET = preview.get("currentSheet", context.CURRENT_SELECTED_SHEET)
        preview["file"] = file_id
        preview["file_id"] = file_id
        preview["fileLabel"] = file_label
        preview["platform"] = platform
        preview["summarySource"] = data_sheet_name_for_summary_title(preview.get("sheets", []), preview.get("currentSheet", ""))
        return preview
    except Exception as error:
        return {"file": file_id, "sheets": [], "currentSheet": "", "columns": [], "data": [], "message": f"File đang bận hoặc lỗi: {str(error)}"}


@router.get("/summary-dashboard")
async def summary_dashboard(sheet_name: str = "", file_id: str | None = None, platform: str = "tiktok"):
    explicit = file_id is not None
    try:
        file_id, target_path, file_label, platform = resolve_source_file(file_id, platform)
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)
    try:
        requested_sheet = clean_text(sheet_name) or source_sheet_default(file_id, scan=True, explicit=explicit)
        summary = await asyncio.to_thread(read_summary_dashboard, target_path, requested_sheet or None, platform=platform)
        summary["file"] = file_id
        summary["file_id"] = file_id
        summary["fileLabel"] = file_label
        summary["platform"] = platform
        return summary
    except Exception as error:
        return JSONResponse(content={"error": f"Không đọc được sheet Tổng kết: {str(error)}"}, status_code=500)


@router.get("/download-excel")
async def download_excel(file_id: str | None = None, platform: str = "tiktok"):
    try:
        file_id, target_path, _, platform = resolve_source_file(file_id, platform)
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)
    content = await asyncio.to_thread(Path(target_path).read_bytes)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(os.path.basename(file_id))},
    )


@router.post("/upload-excel")
async def upload_excel(
    file: UploadFile = File(...),
    platform: Annotated[str, Form()] = "tiktok",
    activate: Annotated[bool, Form()] = True,
):
    try:
        platform = validate_platform(platform)
    except SourceRequestError as error:
        return error.response()
    if scan_state.source_mutation_blocked():
        return {"success": False, "error": "Đang xử lý dữ liệu, hãy chờ hoàn tất trước khi nạp file"}
    # Set and cleared by hand (not source_operation()) to keep the original finally order:
    # the staged file is removed before the gate reopens.
    scan_state.SOURCE_BUSY = True
    staged_path = None
    try:
        original_name = os.path.basename(file.filename or "")
        if not original_name.lower().endswith(".xlsx"):
            return {"success": False, "error": "Chỉ hỗ trợ .xlsx. Hãy lưu file .xls thành .xlsx trước khi nạp."}

        desired_path = safe_join(context.EXCEL_DIR, original_name)
        if not desired_path:
            return {"success": False, "error": "Tên file không hợp lệ"}
        with tempfile.NamedTemporaryFile(prefix="_upload-", suffix=".xlsx", dir=context.EXCEL_DIR, delete=False) as staged:
            staged_path = staged.name
        def validate_and_publish():
            with open(staged_path, "wb") as destination:
                file.file.seek(0)
                shutil.copyfileobj(file.file, destination)
            preview = validate_workbook(staged_path)
            sheets = find_data_sheet_names(staged_path)
            published = publish_new_workbook(staged_path, desired_path)
            return published, preview, sheets
        save_path, preview, sheets = await context.complete_blocking(validate_and_publish)
        file_id = source_files.file_id_for_path(save_path)
        selected_sheet = default_data_sheet(sheets)
        if activate:
            source_files.select_source(file_id, selected_sheet, selected_sheet)
        return {"success": True, "filename": file_id, "file_id": file_id, "sheet": selected_sheet, "scanSheet": selected_sheet, "sheets": sheets, "platform": platform, "activate": activate}
    except Exception as error:
        return {"success": False, "error": str(error)}
    finally:
        if staged_path and os.path.exists(staged_path):
            os.unlink(staged_path)
        scan_state.SOURCE_BUSY = False


def normalize_source_preferences(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), dict):
        raise ValueError("Cấu hình nguồn không hợp lệ.")
    sources = {}
    for platform in PLATFORMS:
        item = payload["sources"].get(platform, {})
        if not isinstance(item, dict):
            raise ValueError("Cấu hình nguồn không hợp lệ.")
        result = {}
        for key in ("fileId", "displaySheet", "scanSheet", "pushSheet", "url"):
            value = item.get(key, "")
            if not isinstance(value, str) or len(value) > (4096 if key == "url" else 1024) or any(ord(char) < 32 for char in value):
                raise ValueError("Cấu hình nguồn không hợp lệ.")
            if key == "fileId" and value:
                if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value) or any(part in {"", ".", ".."} for part in value.replace("\\", "/").split("/")):
                    raise ValueError("Cấu hình nguồn không hợp lệ.")
                value = value.replace("\\", "/")
            elif key in {"displaySheet", "scanSheet", "pushSheet"}:
                if len(value) > 31 or re.search(r"[\\[\\]:*?/\\\\]", value):
                    raise ValueError("Cấu hình sheet không hợp lệ.")
            elif key == "url" and value.strip():
                parsed = urlsplit(value.strip())
                match = re.fullmatch(r"/spreadsheets/d/[A-Za-z0-9_-]+(?:/[^?#]*)?", parsed.path)
                if parsed.scheme != "https" or parsed.hostname != "docs.google.com" or parsed.username or parsed.password or parsed.port not in (None, 443) or not match:
                    raise ValueError("Link Google Sheet không hợp lệ.")
                gid = parse_qs(parsed.query).get("gid", parse_qs(parsed.fragment).get("gid", [""]))[0]
                value = urlunsplit(("https", "docs.google.com", parsed.path, "gid=" + gid if gid.isdigit() else "", ""))
            result[key] = value
        sources[platform] = result
    return {"sources": sources}


def source_preferences_path():
    return os.path.join(context.EXCEL_DIR, "data", "source_preferences.json")


def load_source_preferences():
    try:
        with open(source_preferences_path(), encoding="utf-8") as stream:
            payload = normalize_source_preferences(json.load(stream))
        return payload
    except (OSError, ValueError, TypeError):
        return {"sources": {}}


def save_source_preferences(payload):
    path = source_preferences_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json_atomic(path, payload)


@router.get("/source-preferences")
async def source_preferences():
    return JSONResponse(await asyncio.to_thread(load_source_preferences), headers={"Cache-Control": "no-store"})


@router.post("/source-preferences")
async def update_source_preferences(request: Request):
    if scan_state.source_mutation_blocked():
        return JSONResponse({"error": "Đang xử lý phiên; lưu cấu hình nguồn sau."}, status_code=409, headers={"Cache-Control": "no-store"})
    with scan_state.source_operation():
        try:
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 32768:
                    return JSONResponse({"error": "Cấu hình nguồn quá lớn."}, status_code=413)
            payload = normalize_source_preferences(json.loads(data))
            await context.complete_blocking(save_source_preferences, payload)
            return JSONResponse({"success": True}, headers={"Cache-Control": "no-store"})
        except (ValueError, TypeError):
            return JSONResponse({"error": "Cấu hình nguồn không hợp lệ."}, status_code=400)
        except OSError:
            return JSONResponse({"error": "Không lưu được cấu hình nguồn."}, status_code=503)
