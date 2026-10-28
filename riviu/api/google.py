"""Google Sheets: sync a sheet into a new workbook, OAuth client and login, and push results."""

import asyncio
import json
import os
import tempfile
import urllib.error

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from riviu import scan_state, server_mode
from riviu import server_context as context
from riviu import source_files
from riviu.google_sheets_sync import (
    SERVICE_ACCOUNT_MAX_BYTES,
    ServiceAccountAccessError,
    ServiceAccountError,
    authorize_google,
    delete_service_account,
    download_google_sheet_authenticated,
    finish_paste_back_login,
    google_credentials,
    oauth_status,
    push_rows_to_new_sheet,
    save_oauth_client,
    save_service_account,
    start_paste_back_login,
)
from riviu.reports import build_google_push_rows
from riviu.source_files import (
    SourceRequestError,
    publish_new_workbook,
    resolve_source_file,
    source_sheet_default,
    validate_platform,
    validate_workbook,
)
from riviu.workbook_utils import (
    default_data_sheet,
    build_workbook_rows,
    clean_text,
    download_google_sheet,
    fetch_google_spreadsheet_title,
    find_data_sheet_names,
    format_filename_datetime,
    google_sheet_file_id_from_title,
    google_sheet_source_for_file,
    google_sheet_sync_label,
    parse_google_spreadsheet_id,
    register_google_sheet_source,
    safe_join,
)


router = APIRouter()
# Server mode: the one pending paste-back login (state + PKCE verifier stay server side).
PENDING_GOOGLE_LOGIN = None


def sync_download_google_sheet(base_dir, source_url, spreadsheet_id, destination_path):
    auth_error = None
    try:
        creds = google_credentials(base_dir)
    except ServiceAccountError as error:
        creds, auth_error = None, error
    if creds is not None:
        try:
            download_google_sheet_authenticated(base_dir, spreadsheet_id, destination_path)
            return
        except Exception as error:
            auth_error = error
    try:
        download_google_sheet(source_url, destination_path)
    except Exception as public_error:
        # A sheet the service account cannot open: say which email to share it with.
        if isinstance(auth_error, (ServiceAccountAccessError, ServiceAccountError)):
            raise auth_error from public_error
        if creds is not None:
            raise public_error
        message = str(public_error)
        if isinstance(public_error, urllib.error.HTTPError) and public_error.code in (401, 403):
            raise ValueError("Sheet riêng tư — cần tài khoản dịch vụ được chia sẻ sheet hoặc đăng nhập Google hợp lệ.") from public_error
        if "401" in message or "403" in message or "private" in message.lower():
            raise ValueError("Sheet riêng tư — cần tài khoản dịch vụ được chia sẻ sheet hoặc đăng nhập Google hợp lệ.") from public_error
        raise


@router.post("/sync-google-sheet")
async def sync_google_sheet(data: dict):
    try:
        platform = validate_platform(data.get("platform", "tiktok"))
    except SourceRequestError as error:
        return error.response()
    activate = data.get("activate", True)
    if not isinstance(activate, bool):
        return JSONResponse({"error": "activate phải là boolean"}, status_code=400)
    if scan_state.source_mutation_blocked():
        return JSONResponse({"error": "Đang xử lý dữ liệu, hãy chờ hoàn tất trước khi đồng bộ"}, status_code=409)
    source_url = (data.get("url") or "").strip()
    if not source_url:
        return JSONResponse(content={"error": "Vui lòng nhập URL Google Sheet"}, status_code=400)

    with scan_state.source_operation():
        try:
            return await sync_google_sheet_source(source_url, platform, activate=activate)
        except Exception as error:
            return JSONResponse(content={"error": f"Lỗi đồng bộ Google Sheet: {str(error)}"}, status_code=500)


async def sync_google_sheet_source(source_url, platform, *, activate):
    """Download a Google Sheet into a new workbook, register its source and (optionally) make it
    the legacy current selection. Shared by "Nạp sheet" and the scheduler; the caller validates
    the platform and holds the source gate. Raises on any failure."""
    spreadsheet_id = parse_google_spreadsheet_id(source_url)
    base_dir = context.EXCEL_DIR
    def download_and_publish():
        spreadsheet_title = fetch_google_spreadsheet_title(source_url)
        timestamp_filename = format_filename_datetime()
        file_label = google_sheet_sync_label(spreadsheet_title)
        desired_id = google_sheet_file_id_from_title(spreadsheet_title, timestamp_filename)
        desired_path = safe_join(base_dir, desired_id)
        os.makedirs(os.path.dirname(desired_path), exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="_import-", suffix=".xlsx", dir=os.path.dirname(desired_path), delete=False) as staged:
            staged_path = staged.name
        try:
            context.google_io(sync_download_google_sheet, base_dir, source_url, spreadsheet_id, staged_path)
            preview = validate_workbook(staged_path)
            data_sheets = find_data_sheet_names(staged_path)
            # The newest month sheet ("Tháng 10"), not the Google Sheet's first tab.
            preview["currentSheet"] = default_data_sheet(data_sheets)
            preview["dataSheets"] = data_sheets
            target_path = publish_new_workbook(staged_path, desired_path)
        finally:
            os.unlink(staged_path)
        file_id = os.path.relpath(target_path, base_dir).replace("\\", "/")
        register_google_sheet_source(base_dir, file_id, source_url, title=file_label)
        return file_id, file_label, preview
    file_id, file_label, preview = await context.complete_blocking(download_and_publish)
    selected_sheet = preview.get("currentSheet", "")
    if activate:
        source_files.select_source(file_id, selected_sheet, selected_sheet)
    await scan_state.manager.broadcast_log(
        f"Đã nạp Google Sheet thành file mới: {file_label} → {os.path.basename(file_id)} • sheet={selected_sheet} • url={source_url}",
        level="OK",
    )
    return {
        "success": True,
        "file": file_id,
        "file_id": file_id,
        "label": file_label,
        "sheets": preview.get("sheets", []),
        "dataSheets": preview.get("dataSheets", []),
        "currentSheet": selected_sheet,
        "scanSheet": selected_sheet,
        "defaultSheet": selected_sheet,
        "spreadsheetId": spreadsheet_id,
        "platform": platform,
        "activate": activate,
    }


@router.get("/google-oauth-status")
async def google_oauth_status():
    source = source_files.current_google_sheet_source()
    target_url = source.get("url", "")
    target_id = ""
    if target_url:
        try:
            target_id = parse_google_spreadsheet_id(target_url)
        except Exception:
            target_id = ""
    status = await asyncio.to_thread(context.google_io, oauth_status, context.EXCEL_DIR)
    return {
        **status,
        "connectedSheetUrl": target_url,
        "connectedSpreadsheetId": target_id,
    }


@router.post("/google-oauth-client")
async def upload_google_oauth_client(file: UploadFile = File(...)):
    try:
        if not file.filename.lower().endswith(".json"):
            return JSONResponse(content={"error": "Chỉ nhận file JSON OAuth client."}, status_code=400)
        payload = json.loads((await file.read()).decode("utf-8"))
        await asyncio.to_thread(context.google_io, save_oauth_client, context.EXCEL_DIR, payload)
        return {"success": True}
    except Exception as error:
        return JSONResponse(content={"error": f"Không lưu được OAuth client: {str(error)}"}, status_code=500)


@router.post("/google-service-account")
async def upload_google_service_account(file: UploadFile = File(...)):
    """Admin upload of a Google Cloud service-account key (JSON). The key is never logged
    or echoed; only its client_email comes back."""
    try:
        if not (file.filename or "").lower().endswith(".json"):
            return JSONResponse(content={"error": "Chỉ nhận file JSON khoá tài khoản dịch vụ."}, status_code=400)
        raw = await file.read(SERVICE_ACCOUNT_MAX_BYTES + 1)
        if len(raw) > SERVICE_ACCOUNT_MAX_BYTES:
            return JSONResponse(content={"error": "File khoá tài khoản dịch vụ quá lớn."}, status_code=400)
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, ValueError):
            return JSONResponse(content={"error": "File không phải JSON hợp lệ."}, status_code=400)
        email = await asyncio.to_thread(context.google_io, save_service_account, context.EXCEL_DIR, payload)
        return {"success": True, "email": email}
    except ServiceAccountError as error:
        return JSONResponse(content={"error": str(error)}, status_code=400)
    except Exception:
        return JSONResponse(content={"error": "Không lưu được tài khoản dịch vụ."}, status_code=500)


@router.delete("/google-service-account")
async def delete_google_service_account():
    try:
        await asyncio.to_thread(context.google_io, delete_service_account, context.EXCEL_DIR)
    except Exception:
        return JSONResponse(content={"error": "Không gỡ được tài khoản dịch vụ."}, status_code=500)
    return {"success": True}


@router.post("/google-oauth-login")
async def google_oauth_login():
    if server_mode.ACTIVE is not None:
        return await start_server_google_login()
    # Waits on the user's browser (bounded by a timeout); holding GOOGLE_IO_LOCK here would freeze
    # previews, pushes and scans until the user finished or abandoned the login tab.
    if not context.GOOGLE_LOGIN_LOCK.acquire(blocking=False):
        return JSONResponse(content={"error": "Đang chờ đăng nhập Google ở cửa sổ trình duyệt khác."}, status_code=409)
    try:
        await asyncio.to_thread(authorize_google, context.EXCEL_DIR)
        return {"success": True}
    except Exception as error:
        return JSONResponse(content={"error": f"Đăng nhập Google thất bại: {str(error)}"}, status_code=500)
    finally:
        context.GOOGLE_LOGIN_LOCK.release()


@router.post("/push-google-sheet")
async def push_google_sheet(data: dict | None = None):
    data = data or {}
    try:
        platform = validate_platform(data.get("platform", "tiktok"))
    except SourceRequestError as error:
        return error.response()
    if scan_state.source_mutation_blocked():
        return JSONResponse({"error": "Đang xử lý dữ liệu, hãy chờ lưu xong trước khi xuất"}, status_code=409)
    try:
        file_id, target_path, _, platform = resolve_source_file(data.get("file_id"), platform)
    except SourceRequestError as error:
        return error.response()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)

    source = dict(google_sheet_source_for_file(context.EXCEL_DIR, file_id))
    request_url = clean_text(data.get("url", ""))
    source_url = request_url or source.get("url", "")
    upload_sheet = clean_text(data.get("sourceSheet", "")) or source_sheet_default(file_id, scan=True, explicit=data.get("file_id") is not None)
    spreadsheet_id = ""
    try:
        spreadsheet_id = parse_google_spreadsheet_id(source_url)
    except Exception:
        spreadsheet_id = ""
    if not spreadsheet_id:
        return JSONResponse(content={"error": "Vui lòng nhập đúng URL Google Sheet đích trước khi tạo sheet."}, status_code=400)

    with scan_state.source_operation():
        try:
            available_sheets = await asyncio.to_thread(find_data_sheet_names, target_path)
            if upload_sheet and upload_sheet not in available_sheets:
                return JSONResponse(content={"error": f"Sheet {upload_sheet} không tồn tại trong file."}, status_code=400)
            rows = await asyncio.to_thread(build_workbook_rows, target_path, sheet_name=upload_sheet, platform=platform)
            if not rows:
                return JSONResponse(content={"error": f"Sheet \"{upload_sheet or 'đang chọn'}\" không có link {platform} để tạo sheet."}, status_code=400)
            # Off the loop on the fresh row copies; a cancel still waits for it inside the source gate.
            values = await context.complete_blocking(build_google_push_rows, rows, platform=platform)
            sheet_title = await context.complete_blocking(context.google_io, push_rows_to_new_sheet, context.EXCEL_DIR, spreadsheet_id, values, source_sheet_name=upload_sheet, platform=platform)
            return {"success": True, "sheetTitle": sheet_title, "sourceSheet": upload_sheet, "file": file_id, "file_id": file_id, "platform": platform}
        except Exception as error:
            return JSONResponse(content={"error": f"Đẩy dữ liệu lên Google Sheet thất bại: {str(error)}"}, status_code=500)


async def start_server_google_login():
    global PENDING_GOOGLE_LOGIN
    try:
        PENDING_GOOGLE_LOGIN = await asyncio.to_thread(context.google_io, start_paste_back_login, context.EXCEL_DIR)
    except Exception as error:
        return JSONResponse(content={"error": f"Đăng nhập Google thất bại: {str(error)}"}, status_code=500)
    return {"pasteBack": True, "authUrl": PENDING_GOOGLE_LOGIN["authUrl"]}


@router.post("/google-oauth-complete")
async def google_oauth_complete(data: dict | None = None):
    global PENDING_GOOGLE_LOGIN
    if server_mode.ACTIVE is None:
        return JSONResponse({"error": "Not Found"}, status_code=404)
    pending = PENDING_GOOGLE_LOGIN
    pasted = (data or {}).get("url", "")
    if not context.GOOGLE_LOGIN_LOCK.acquire(blocking=False):
        return JSONResponse(content={"error": "Đang xác nhận đăng nhập Google, hãy chờ."}, status_code=409)
    try:
        await asyncio.to_thread(context.google_io, finish_paste_back_login, context.EXCEL_DIR, pending, pasted)
    except ValueError as error:
        return JSONResponse(content={"error": str(error)}, status_code=400)
    except Exception as error:
        return JSONResponse(content={"error": f"Đăng nhập Google thất bại: {str(error)}"}, status_code=500)
    finally:
        context.GOOGLE_LOGIN_LOCK.release()
    # One code per consent; a second paste must start over.
    PENDING_GOOGLE_LOGIN = None
    return {"success": True}
