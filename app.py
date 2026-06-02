from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
import asyncio
import io
import json
import os
import re
import zipfile
from datetime import datetime
from urllib.parse import quote

import pandas as pd
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from scraper import run_scraper, read_scrape_history
from google_sheets_sync import authorize_google, oauth_status, push_rows_to_new_sheet, save_oauth_client
from workbook_utils import (
    GOOGLE_SHEET_LABEL,
    LEGACY_GOOGLE_SHEET_FILE_ID,
    REPORT_COLUMNS,
    build_workbook_rows,
    clean_text,
    download_google_sheet,
    ensure_data_dir,
    find_data_sheet_names,
    google_sheet_source_for_file,
    is_failed_channel_name,
    list_workbook_partners,
    normalize_header,
    parse_google_spreadsheet_id,
    read_summary_dashboard,
    read_sheet_preview,
    register_google_sheet_source,
    timestamped_google_sheet_file_id,
    workbook_file_entries,
    workbook_sheet_names,
)


app = FastAPI()
templates = Jinja2Templates(directory="templates")


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_log(self, message: str):
        for conn in self.active_connections:
            try:
                await conn.send_json({"type": "log", "message": message})
            except Exception:
                pass

    async def broadcast_status(self, status: dict):
        for conn in self.active_connections:
            try:
                await conn.send_json({"type": "status", "data": status})
            except Exception:
                pass

    async def broadcast_data(self, row: dict):
        for conn in self.active_connections:
            try:
                await conn.send_json({"type": "data", "row": row})
            except Exception:
                pass


manager = ConnectionManager()
EXCEL_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = ensure_data_dir(EXCEL_DIR)
SCRAPE_TASK = None
CURRENT_SELECTED_FILE = ""
CURRENT_SELECTED_SHEET = ""
CURRENT_SCAN_SHEET = ""
GOOGLE_SHEET_SOURCE_URL = ""
RIVIU_LOGO_PATH = r"C:\Users\cattfan\.cursor\projects\c-Users-cattfan-Desktop-TTBD\assets\c__Users_cattfan_AppData_Roaming_Cursor_User_workspaceStorage_e0f8bf31141cddbc037deae49819011e_images_image-8d49c87a-8bce-42b8-b5c7-f5ef7715efa5.png"


def file_entries():
    return workbook_file_entries(EXCEL_DIR)


def resolve_file_path(file_id):
    if not file_id:
        return ""
    return os.path.join(EXCEL_DIR, file_id.replace("/", os.sep))


def ensure_selected_file():
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET, CURRENT_SCAN_SHEET
    current_path = resolve_file_path(CURRENT_SELECTED_FILE)
    if CURRENT_SELECTED_FILE and os.path.exists(current_path):
        return CURRENT_SELECTED_FILE

    entries = file_entries()
    if not entries:
        CURRENT_SELECTED_FILE = ""
        CURRENT_SELECTED_SHEET = ""
        CURRENT_SCAN_SHEET = ""
        return ""

    google_ids = [
        entry["id"]
        for entry in entries
        if entry.get("source") == "google" and entry["id"] != LEGACY_GOOGLE_SHEET_FILE_ID
    ]
    google_ids = sorted(
        google_ids,
        key=lambda file_id: os.path.getmtime(resolve_file_path(file_id)) if os.path.exists(resolve_file_path(file_id)) else 0,
        reverse=True,
    )
    preferred_ids = google_ids + [LEGACY_GOOGLE_SHEET_FILE_ID, "Report_v1.xlsx", "Report_v1_with_partners.xlsx"]
    available_ids = {entry["id"] for entry in entries}
    CURRENT_SELECTED_FILE = next((file_id for file_id in preferred_ids if file_id in available_ids), entries[0]["id"])
    return CURRENT_SELECTED_FILE


def current_excel_path():
    selected = ensure_selected_file()
    return resolve_file_path(selected) if selected else ""


def current_google_sheet_source():
    current_id = ensure_selected_file()
    return google_sheet_source_for_file(EXCEL_DIR, current_id)


def current_display_label():
    current_id = ensure_selected_file()
    for entry in file_entries():
        if entry["id"] == current_id:
            return entry["label"]
    return current_id


def default_sheet_for_file(file_id):
    if not file_id:
        return ""
    try:
        sheets = find_data_sheet_names(resolve_file_path(file_id))
        return sheets[0] if sheets else ""
    except Exception:
        return ""


def sheets_for_current_file():
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return []
    try:
        return find_data_sheet_names(target_path)
    except Exception:
        return []


def resolve_scan_sheet(requested_sheet=""):
    global CURRENT_SCAN_SHEET
    sheets = sheets_for_current_file()
    if not sheets:
        return "", []
    sheet_name = clean_text(requested_sheet) or CURRENT_SCAN_SHEET or CURRENT_SELECTED_SHEET or sheets[0]
    if sheet_name not in sheets:
        raise ValueError("Sheet không tồn tại")
    CURRENT_SCAN_SHEET = sheet_name
    return sheet_name, sheets


def safe_report_name(name):
    filename = re.sub(r'[\\/:*?"<>|]+', "-", clean_text(name))
    filename = re.sub(r"\s+", " ", filename).strip(" .")
    return filename[:90] if filename else "doi_tac"


def filename_timestamp():
    return datetime.now().strftime("%d-%m-%Y-%H-%M")


def format_metric(value):
    if pd.isna(value) or value == "":
        return 0
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return value


def metric_number(value):
    if pd.isna(value) or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(float(value))
    raw_text = str(value).strip()
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", raw_text):
        return int(re.sub(r"[.,]", "", raw_text))
    if re.fullmatch(r"\d+\.0+", raw_text):
        return int(float(raw_text))
    text = str(value).replace(",", "").replace(".", "").strip()
    try:
        return int(float(text))
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def spreadsheet_text(value):
    text = clean_text(value)
    return f"'{text}" if text else ""


def spreadsheet_date_text(value):
    if pd.isna(value):
        return ""
    if hasattr(value, "to_pydatetime"):
        return spreadsheet_text(value.to_pydatetime().strftime("%d/%m/%Y"))
    if isinstance(value, datetime):
        return spreadsheet_text(value.strftime("%d/%m/%Y"))
    parsed = pd.to_datetime(value, errors="coerce")
    if not pd.isna(parsed):
        return spreadsheet_text(parsed.to_pydatetime().strftime("%d/%m/%Y"))
    text = clean_text(value)
    if text.endswith(" 00:00:00"):
        text = text[:10]
    return spreadsheet_text(text)


def spreadsheet_formula_text(value):
    return clean_text(value).replace('"', '""')


def spreadsheet_hyperlink(value):
    return clean_text(value)


def excel_column_name(index):
    name = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def find_report_column(frame, header):
    target = normalize_header(header)
    for column in frame.columns:
        if normalize_header(column) == target:
            return column
    if header == "LINK AIR":
        for column in frame.columns:
            name = normalize_header(column)
            if "link" in name or "url" in name:
                return column
    if header == "TÊN KÊNH":
        for column in frame.columns:
            name = normalize_header(column)
            if "tên kênh" in name or "ten kenh" in name:
                return column
    if header == "NGÀY AIR":
        for column in frame.columns:
            name = normalize_header(column)
            if "ngày" in name or "ngay" in name:
                return column
    return None


def build_partner_report(partner, rows, *, apply_min_views=True, min_views=100):
    threshold = max(int(min_views or 0), 0) if apply_min_views else 0
    total_rows = len(rows)
    failed_rows = sum(1 for row in rows if is_failed_channel_name(row.get("TÊN KÊNH", "")))
    if apply_min_views:
        under_threshold_rows = sum(
            1
            for row in rows
            if not is_failed_channel_name(row.get("TÊN KÊNH", ""))
            and metric_number(row.get("LƯỢT XEM", 0)) < threshold
        )
        valid_rows = [
            row
            for row in rows
            if not is_failed_channel_name(row.get("TÊN KÊNH", ""))
            and metric_number(row.get("LƯỢT XEM", 0)) >= threshold
        ]
    else:
        under_threshold_rows = 0
        valid_rows = [
            row
            for row in rows
            if not is_failed_channel_name(row.get("TÊN KÊNH", ""))
        ]
    frame = pd.DataFrame(valid_rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "Báo cáo"
    updated_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    table_header_row = 6
    data_start_row = table_header_row + 1
    last_column = get_column_letter(len(REPORT_COLUMNS))

    title_fill = PatternFill("solid", fgColor="FF6B00")
    header_fill = PatternFill("solid", fgColor="FF6B00")
    total_fill = PatternFill("solid", fgColor="FFF7ED")
    thin = Side(style="thin", color="D8DEE9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=2)
    if os.path.exists(RIVIU_LOGO_PATH):
        logo = ExcelImage(RIVIU_LOGO_PATH)
        logo.height = 54
        logo.width = 150
        ws.add_image(logo, "A1")
    ws.merge_cells(start_row=1, start_column=3, end_row=1, end_column=len(REPORT_COLUMNS))
    ws["C1"] = f"BÁO CÁO ĐỐI TÁC: {partner}"
    ws["C1"].fill = title_fill
    ws["C1"].font = Font(color="FFFFFF", bold=True, size=14)
    ws["C1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 34
    ws.row_dimensions[2].height = 24

    ws.merge_cells(start_row=2, start_column=3, end_row=2, end_column=len(REPORT_COLUMNS))
    ws["C2"] = f"Tổng link: {len(frame)} • Ngày cập nhật: {updated_at}"
    ws["C2"].font = Font(color="9A3412", italic=True, bold=True)
    ws["C2"].alignment = Alignment(horizontal="center")

    for col_index, header in enumerate(REPORT_COLUMNS, start=1):
        cell = ws.cell(row=table_header_row, column=col_index, value=header)
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    for row_index, (_, source_row) in enumerate(frame.iterrows(), start=data_start_row):
        for col_index, header in enumerate(REPORT_COLUMNS, start=1):
            value = source_row.get(header, "")
            if header == "LINK AIR":
                value = clean_text(value)
            elif header == "TÊN KÊNH":
                value = clean_text(value)
            elif header == "NGÀY AIR":
                if pd.isna(value):
                    value = ""
                elif hasattr(value, "to_pydatetime"):
                    value = value.to_pydatetime()
                else:
                    parsed_date = pd.to_datetime(value, errors="coerce")
                    value = parsed_date.to_pydatetime() if not pd.isna(parsed_date) else clean_text(value)
            else:
                value = format_metric(value)

            cell = ws.cell(row=row_index, column=col_index, value=value)
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=(header in {"LINK AIR", "TÊN KÊNH"}))
            if header == "LINK AIR" and isinstance(value, str) and value.startswith("http"):
                cell.hyperlink = value
                cell.style = "Hyperlink"
            elif header == "NGÀY AIR":
                cell.number_format = "dd/mm/yyyy"
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif header not in {"LINK AIR", "TÊN KÊNH"}:
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="right", vertical="top")

    total_row = len(frame) + data_start_row
    for col_index in range(1, len(REPORT_COLUMNS) + 1):
        cell = ws.cell(row=total_row, column=col_index)
        cell.fill = total_fill
        cell.border = border
    ws.cell(row=total_row, column=1, value="TỔNG")
    ws.cell(row=total_row, column=1).font = Font(bold=True)
    if len(frame) > 0:
        for col_index in range(4, len(REPORT_COLUMNS) + 1):
            letter = get_column_letter(col_index)
            cell = ws.cell(row=total_row, column=col_index, value=f"=SUM({letter}{data_start_row}:{letter}{total_row - 1})")
            cell.fill = total_fill
            cell.font = Font(bold=True)
            cell.border = border
            cell.number_format = "#,##0"
            cell.alignment = Alignment(horizontal="right")

    ws.freeze_panes = f"A{data_start_row}"
    ws.auto_filter.ref = f"A{table_header_row}:{last_column}{max(total_row, table_header_row)}"
    widths = [14, 24, 72, 14, 12, 14, 14, 12]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def content_disposition(filename):
    return f"attachment; filename*=UTF-8''{quote(filename)}"


async def run_scraper_safely(target_path, worker_count, partner=None, partners=None, create_result_sheet=False, push_to_google=False, sheet_name=""):
    try:
        scan_sheet, _ = resolve_scan_sheet(sheet_name)
        await run_scraper(
            target_path,
            manager,
            worker_count=worker_count,
            selected_partner=partner,
            selected_partners=partners,
            create_result_sheet=create_result_sheet,
            base_dir=EXCEL_DIR,
            file_label=current_display_label(),
            sheet_name=scan_sheet,
        )
        if push_to_google:
            source = current_google_sheet_source()
            spreadsheet_id = source.get("spreadsheetId", "")
            if spreadsheet_id:
                rows = build_workbook_rows(target_path)
                values = build_google_push_rows(rows)
                sheet_title = push_rows_to_new_sheet(EXCEL_DIR, spreadsheet_id, values)
                await manager.broadcast_log(f"Đã đẩy kết quả lên Google Sheet: {sheet_title}.")
            else:
                await manager.broadcast_log("BỎ QUA đẩy Google: file hiện tại chưa gắn với Google Sheet gốc.")
    except asyncio.CancelledError:
        await manager.broadcast_log("Đã hủy phiên quét.")
        await manager.broadcast_status({"total": 0, "processed": 0, "success": 0, "error": 0, "done": True, "cancelled": True})
        raise
    except Exception as error:
        await manager.broadcast_log(f"Lỗi quét: {str(error)}")
        await manager.broadcast_status({"total": 0, "processed": 0, "success": 0, "error": 1, "done": True})


def build_google_push_rows(rows):
    normalized_rows = []
    max_partner_columns = 1
    for row in rows:
        partner_names = row.get("partners", [])
        if not isinstance(partner_names, list):
            partner_names = []
        partner_names = [clean_text(name) for name in partner_names if clean_text(name)]
        max_partner_columns = max(max_partner_columns, len(partner_names))
        normalized_rows.append((row, partner_names))

    headers = ["Stt", "Ngày", "Link", "Tên Kênh", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"]
    headers.extend(["Đối tác" if index == 0 else f"Đối tác {index + 1}" for index in range(max_partner_columns)])
    headers.append("Cập nhật lần cuối")

    values = [headers]
    for index, (row, partner_names) in enumerate(normalized_rows, start=1):
        padded_partners = partner_names + [""] * (max_partner_columns - len(partner_names))
        channel_name = clean_text(row.get("TÊN KÊNH", ""))
        if is_failed_channel_name(channel_name):
            channel_name = "Lỗi"
        values.append([
            index,
            spreadsheet_date_text(row.get("NGÀY AIR", "")),
            spreadsheet_hyperlink(row.get("LINK AIR", "")),
            channel_name,
            metric_number(row.get("LƯỢT XEM", 0)),
            metric_number(row.get("TIM", 0)),
            metric_number(row.get("BÌNH LUẬN", 0)),
            metric_number(row.get("LƯỢT LƯU", 0)),
            metric_number(row.get("CHIA SẺ", 0)),
            *padded_partners,
            datetime.now().strftime("%d/%m/%Y %H:%M"),
        ])
    if len(values) > 1:
        total_row_number = len(values) + 1
        data_last_row = total_row_number - 1
        total_row = ["", "", "TỔNG", ""]
        for column_index in range(5, 10):
            column_name = excel_column_name(column_index)
            total_row.append(f"=SUM({column_name}2:{column_name}{data_last_row})")
        total_row.extend([""] * (max_partner_columns + 1))
        values.append(total_row)
    return values


def has_scraped_google_push_data(rows):
    for row in rows:
        if not is_failed_channel_name(row.get("TÊN KÊNH", "")):
            return True
        if any(metric_number(row.get(column, 0)) > 0 for column in ["LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"]):
            return True
    return False


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@app.get("/riviu-logo.png", include_in_schema=False)
async def riviu_logo():
    if os.path.exists(RIVIU_LOGO_PATH):
        return FileResponse(RIVIU_LOGO_PATH, media_type="image/png")
    return Response(status_code=404)


@app.get("/list-files")
async def list_files():
    ensure_selected_file()
    oauth = oauth_status(EXCEL_DIR)
    google_source = current_google_sheet_source()
    target_sheet_url = GOOGLE_SHEET_SOURCE_URL or google_source.get("url", "")
    target_spreadsheet_id = ""
    if target_sheet_url:
        try:
            target_spreadsheet_id = parse_google_spreadsheet_id(target_sheet_url)
        except Exception:
            target_spreadsheet_id = ""
    return {
        "files": file_entries(),
        "current": CURRENT_SELECTED_FILE,
        "currentLabel": current_display_label(),
        "currentSheet": CURRENT_SELECTED_SHEET,
        "sheets": sheets_for_current_file(),
        "scanSheet": CURRENT_SCAN_SHEET or CURRENT_SELECTED_SHEET,
        "googleSheetUrl": target_sheet_url,
        "googlePushReady": bool(target_spreadsheet_id) and oauth.get("authorized"),
        "googleOAuthConfigured": oauth.get("configured"),
        "googleOAuthAuthorized": oauth.get("authorized"),
    }


@app.post("/select-file")
async def select_file(data: dict):
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET, CURRENT_SCAN_SHEET
    file_id = data.get("filename")
    if file_id and os.path.exists(resolve_file_path(file_id)):
        CURRENT_SELECTED_FILE = file_id
        CURRENT_SELECTED_SHEET = default_sheet_for_file(file_id)
        CURRENT_SCAN_SHEET = CURRENT_SELECTED_SHEET
        return {"success": True, "selected": CURRENT_SELECTED_FILE, "sheet": CURRENT_SELECTED_SHEET, "scanSheet": CURRENT_SCAN_SHEET}
    return {"success": False, "error": "File không tồn tại"}


@app.post("/sync-google-sheet")
async def sync_google_sheet(data: dict):
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET, CURRENT_SCAN_SHEET, GOOGLE_SHEET_SOURCE_URL
    source_url = (data.get("url") or "").strip()
    if not source_url:
        return JSONResponse(content={"error": "Vui lòng nhập URL Google Sheet"}, status_code=400)

    try:
        spreadsheet_id = parse_google_spreadsheet_id(source_url)
        timestamp = filename_timestamp()
        file_id = timestamped_google_sheet_file_id(timestamp)
        target_path = resolve_file_path(file_id)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        download_google_sheet(source_url, target_path)
        preview = read_sheet_preview(target_path)
        GOOGLE_SHEET_SOURCE_URL = source_url
        CURRENT_SELECTED_FILE = file_id
        CURRENT_SELECTED_SHEET = preview.get("currentSheet", "")
        CURRENT_SCAN_SHEET = CURRENT_SELECTED_SHEET
        register_google_sheet_source(EXCEL_DIR, file_id, source_url)
        await manager.broadcast_log(f"Đã nạp Google Sheet thành file mới: {os.path.basename(file_id)}.")
        return {
            "success": True,
            "file": file_id,
            "label": current_display_label(),
            "sheets": preview.get("sheets", []),
            "currentSheet": CURRENT_SELECTED_SHEET,
            "scanSheet": CURRENT_SCAN_SHEET,
            "spreadsheetId": spreadsheet_id,
        }
    except Exception as error:
        return JSONResponse(content={"error": f"Lỗi đồng bộ Google Sheet: {str(error)}"}, status_code=500)


@app.get("/google-oauth-status")
async def google_oauth_status():
    source = current_google_sheet_source()
    target_url = GOOGLE_SHEET_SOURCE_URL or source.get("url", "")
    target_id = ""
    if target_url:
        try:
            target_id = parse_google_spreadsheet_id(target_url)
        except Exception:
            target_id = ""
    status = oauth_status(EXCEL_DIR)
    return {
        **status,
        "connectedSheetUrl": target_url,
        "connectedSpreadsheetId": target_id,
    }


@app.post("/google-oauth-client")
async def upload_google_oauth_client(file: UploadFile = File(...)):
    try:
        if not file.filename.lower().endswith(".json"):
            return JSONResponse(content={"error": "Chỉ nhận file JSON OAuth client."}, status_code=400)
        payload = json.loads((await file.read()).decode("utf-8"))
        save_oauth_client(EXCEL_DIR, payload)
        return {"success": True}
    except Exception as error:
        return JSONResponse(content={"error": f"Không lưu được OAuth client: {str(error)}"}, status_code=500)


@app.post("/google-oauth-login")
async def google_oauth_login():
    try:
        authorize_google(EXCEL_DIR)
        return {"success": True}
    except Exception as error:
        return JSONResponse(content={"error": f"Đăng nhập Google thất bại: {str(error)}"}, status_code=500)


@app.post("/push-google-sheet")
async def push_google_sheet(data: dict | None = None):
    global GOOGLE_SHEET_SOURCE_URL
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)

    request_url = clean_text((data or {}).get("url", ""))
    source_url = request_url or GOOGLE_SHEET_SOURCE_URL or current_google_sheet_source().get("url", "")
    spreadsheet_id = ""
    try:
        spreadsheet_id = parse_google_spreadsheet_id(source_url)
    except Exception:
        spreadsheet_id = ""
    if not spreadsheet_id:
        return JSONResponse(content={"error": "Vui lòng nhập đúng URL Google Sheet đích trước khi tạo sheet."}, status_code=400)

    try:
        GOOGLE_SHEET_SOURCE_URL = source_url
        register_google_sheet_source(EXCEL_DIR, ensure_selected_file(), source_url)
        upload_sheet = clean_text((data or {}).get("sourceSheet", "")) or CURRENT_SCAN_SHEET or CURRENT_SELECTED_SHEET
        rows = build_workbook_rows(target_path, sheet_name=upload_sheet)
        if not rows:
            return JSONResponse(content={"error": f"Sheet {upload_sheet or 'đang chọn'} không có link TikTok để tạo sheet."}, status_code=400)
        if not has_scraped_google_push_data(rows):
            return JSONResponse(content={"error": "File đang chọn mới nạp nhưng chưa quét dữ liệu. Hãy bấm BẮT ĐẦU QUÉT trước rồi tạo sheet."}, status_code=400)
        values = build_google_push_rows(rows)
        sheet_title = push_rows_to_new_sheet(EXCEL_DIR, spreadsheet_id, values)
        return {"success": True, "sheetTitle": sheet_title, "sourceSheet": upload_sheet}
    except Exception as error:
        return JSONResponse(content={"error": f"Đẩy dữ liệu lên Google Sheet thất bại: {str(error)}"}, status_code=500)


@app.get("/preview-excel")
async def preview_excel(sheet_name: str = Query(default="")):
    global CURRENT_SELECTED_SHEET
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return {"sheets": [], "currentSheet": "", "columns": [], "data": [], "message": f"Không tìm thấy file {CURRENT_SELECTED_FILE}."}
    try:
        requested_sheet = sheet_name or CURRENT_SELECTED_SHEET or default_sheet_for_file(CURRENT_SELECTED_FILE)
        preview = read_sheet_preview(target_path, sheet_name=requested_sheet)
        CURRENT_SELECTED_SHEET = preview.get("currentSheet", CURRENT_SELECTED_SHEET)
        preview["file"] = CURRENT_SELECTED_FILE
        preview["fileLabel"] = current_display_label()
        return preview
    except Exception as error:
        return {"sheets": [], "currentSheet": "", "columns": [], "data": [], "message": f"File đang bận hoặc lỗi: {str(error)}"}


@app.get("/summary-dashboard")
async def summary_dashboard():
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)
    try:
        summary = read_summary_dashboard(target_path)
        summary["file"] = CURRENT_SELECTED_FILE
        summary["fileLabel"] = current_display_label()
        return summary
    except Exception as error:
        return JSONResponse(content={"error": f"Không đọc được sheet Tổng kết: {str(error)}"}, status_code=500)


@app.get("/download-excel")
async def download_excel():
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)
    download_name = os.path.basename(CURRENT_SELECTED_FILE)
    return FileResponse(
        path=target_path,
        filename=download_name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/scrape-history")
async def scrape_history(limit: int = Query(default=50)):
    safe_limit = max(1, min(int(limit or 50), 200))
    return {"history": read_scrape_history(EXCEL_DIR, limit=safe_limit)}


@app.get("/report-partners")
async def report_partners():
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)

    try:
        partners = list_workbook_partners(target_path)
        data_sheets = find_data_sheet_names(target_path)
        all_sheets = workbook_sheet_names(target_path)
        return {
            "partners": partners,
            "total": len(partners),
            "file": CURRENT_SELECTED_FILE,
            "fileLabel": current_display_label(),
            "dataSheet": data_sheets[0] if data_sheets else "",
            "allSheets": all_sheets,
        }
    except Exception as error:
        return JSONResponse(content={"error": f"Không đọc được danh sách đối tác: {str(error)}"}, status_code=500)


@app.post("/export-report")
async def export_report(data: dict):
    target_path = current_excel_path()
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
        available_partners = set(list_workbook_partners(target_path))
        partners = [clean_text(name) for name in selected_partners if clean_text(name) in available_partners]
        if not partners:
            return JSONResponse(content={"error": "Không tìm thấy đối tác đã chọn trong file"}, status_code=404)
        export_timestamp = filename_timestamp()

        if len(partners) == 1:
            partner = partners[0]
            report_rows = build_workbook_rows(target_path, selected_partner=partner)
            report_bytes = build_partner_report(
                partner,
                report_rows,
                apply_min_views=apply_min_views,
                min_views=min_views,
            )
            filename = f"{safe_report_name(partner)}-{export_timestamp}.xlsx"
            return Response(
                content=report_bytes,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": content_disposition(filename)},
            )

        archive = io.BytesIO()
        used_names = set()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for partner in partners:
                report_rows = build_workbook_rows(target_path, selected_partner=partner)
                report_bytes = build_partner_report(
                    partner,
                    report_rows,
                    apply_min_views=apply_min_views,
                    min_views=min_views,
                )
                base_name = f"{safe_report_name(partner)}-{export_timestamp}"
                filename = f"{base_name}.xlsx"
                counter = 2
                while filename in used_names:
                    filename = f"{base_name}_{counter}.xlsx"
                    counter += 1
                used_names.add(filename)
                zip_file.writestr(filename, report_bytes)

        archive.seek(0)
        return Response(
            content=archive.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": content_disposition(f"bao_cao_doi_tac-{export_timestamp}.zip")},
        )
    except Exception as error:
        return JSONResponse(content={"error": f"Lỗi xuất báo cáo: {str(error)}"}, status_code=500)


@app.post("/upload-excel")
async def upload_excel(file: UploadFile = File(...)):
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET, CURRENT_SCAN_SHEET
    try:
        if not (file.filename.endswith(".xlsx") or file.filename.endswith(".xls")):
            return {"success": False, "error": "Chỉ chấp nhận file Excel (.xlsx, .xls)"}

        save_path = os.path.join(EXCEL_DIR, file.filename)
        with open(save_path, "wb") as file_obj:
            content = await file.read()
            file_obj.write(content)

        CURRENT_SELECTED_FILE = file.filename
        CURRENT_SELECTED_SHEET = default_sheet_for_file(file.filename)
        CURRENT_SCAN_SHEET = CURRENT_SELECTED_SHEET
        return {"success": True, "filename": file.filename, "sheet": CURRENT_SELECTED_SHEET, "scanSheet": CURRENT_SCAN_SHEET}
    except Exception as error:
        return {"success": False, "error": str(error)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global SCRAPE_TASK
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {"action": data}

            action = payload.get("action")
            if action == "start":
                if SCRAPE_TASK and not SCRAPE_TASK.done():
                    await manager.broadcast_log("Đang có phiên quét chạy. Vui lòng chờ hoàn tất trước khi chạy phiên mới.")
                    continue

                target_path = current_excel_path()
                worker_count = payload.get("workers", 20)
                create_result_sheet = bool(payload.get("create_result_sheet", False))
                push_to_google = bool(payload.get("push_to_google", False))
                partner = clean_text(payload.get("partner", ""))
                sheet_name = clean_text(payload.get("sheet_name", ""))
                try:
                    sheet_name, _ = resolve_scan_sheet(sheet_name)
                except ValueError as error:
                    await manager.broadcast_log(str(error))
                    await manager.broadcast_status({"total": 0, "processed": 0, "success": 0, "error": 1, "done": True})
                    continue
                partners_payload = payload.get("partners", [])
                partners = []
                if isinstance(partners_payload, list):
                    partners = [clean_text(name) for name in partners_payload if clean_text(name)]
                SCRAPE_TASK = asyncio.create_task(
                    run_scraper_safely(
                        target_path,
                        worker_count,
                        partner=partner or None,
                        partners=partners,
                        create_result_sheet=create_result_sheet,
                        push_to_google=push_to_google,
                        sheet_name=sheet_name,
                    )
                )
            elif action == "cancel":
                if SCRAPE_TASK and not SCRAPE_TASK.done():
                    SCRAPE_TASK.cancel()
                    await manager.broadcast_log("Đang hủy phiên quét hiện tại...")
                else:
                    await manager.broadcast_log("Không có phiên quét nào đang chạy.")
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=1231)
