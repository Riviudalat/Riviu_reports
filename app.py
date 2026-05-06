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
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from scraper import run_scraper
from workbook_utils import (
    GOOGLE_SHEET_LABEL,
    LEGACY_GOOGLE_SHEET_FILE_ID,
    REPORT_COLUMNS,
    build_workbook_rows,
    clean_text,
    download_google_sheet,
    ensure_data_dir,
    is_failed_channel_name,
    list_workbook_partners,
    normalize_header,
    parse_google_spreadsheet_id,
    read_summary_dashboard,
    read_sheet_preview,
    timestamped_google_sheet_file_id,
    workbook_file_entries,
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
GOOGLE_SHEET_SOURCE_URL = ""


def file_entries():
    return workbook_file_entries(EXCEL_DIR)


def resolve_file_path(file_id):
    if not file_id:
        return ""
    return os.path.join(EXCEL_DIR, file_id.replace("/", os.sep))


def ensure_selected_file():
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET
    current_path = resolve_file_path(CURRENT_SELECTED_FILE)
    if CURRENT_SELECTED_FILE and os.path.exists(current_path):
        return CURRENT_SELECTED_FILE

    entries = file_entries()
    if not entries:
        CURRENT_SELECTED_FILE = ""
        CURRENT_SELECTED_SHEET = ""
        return ""

    google_ids = [
        entry["id"]
        for entry in entries
        if entry.get("source") == "google" and entry["id"] != LEGACY_GOOGLE_SHEET_FILE_ID
    ]
    preferred_ids = google_ids + [LEGACY_GOOGLE_SHEET_FILE_ID, "Report_v1.xlsx", "Report_v1_with_partners.xlsx"]
    available_ids = {entry["id"] for entry in entries}
    CURRENT_SELECTED_FILE = next((file_id for file_id in preferred_ids if file_id in available_ids), entries[0]["id"])
    return CURRENT_SELECTED_FILE


def current_excel_path():
    selected = ensure_selected_file()
    return resolve_file_path(selected) if selected else ""


def current_display_label():
    current_id = ensure_selected_file()
    for entry in file_entries():
        if entry["id"] == current_id:
            return entry["label"]
    return current_id


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


def build_partner_report(partner, rows):
    valid_rows = [row for row in rows if not is_failed_channel_name(row.get("TÊN KÊNH", ""))]
    frame = pd.DataFrame(valid_rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "Báo cáo"
    updated_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    table_header_row = 5
    data_start_row = table_header_row + 1
    last_column = get_column_letter(len(REPORT_COLUMNS))

    title_fill = PatternFill("solid", fgColor="123A63")
    header_fill = PatternFill("solid", fgColor="0B5ED7")
    total_fill = PatternFill("solid", fgColor="EAF3FF")
    thin = Side(style="thin", color="D8DEE9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(REPORT_COLUMNS))
    ws["A1"] = f"BÁO CÁO ĐỐI TÁC: {partner}"
    ws["A1"].fill = title_fill
    ws["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(REPORT_COLUMNS))
    ws["A2"] = f"Tổng link: {len(frame)}"
    ws["A2"].font = Font(color="374151", italic=True)
    ws["A2"].alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=len(REPORT_COLUMNS))
    ws["A3"] = f"Ngày cập nhật: {updated_at}"
    ws["A3"].font = Font(color="374151", italic=True)
    ws["A3"].alignment = Alignment(horizontal="center")

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


async def run_scraper_safely(target_path, worker_count, partner=None, partners=None):
    try:
        await run_scraper(target_path, manager, worker_count=worker_count, selected_partner=partner, selected_partners=partners)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await manager.broadcast_log(f"Lỗi quét: {str(error)}")
        await manager.broadcast_status({"total": 0, "processed": 0, "success": 0, "error": 1, "done": True})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@app.get("/list-files")
async def list_files():
    ensure_selected_file()
    return {
        "files": file_entries(),
        "current": CURRENT_SELECTED_FILE,
        "currentLabel": current_display_label(),
        "currentSheet": CURRENT_SELECTED_SHEET,
        "googleSheetUrl": GOOGLE_SHEET_SOURCE_URL,
    }


@app.post("/select-file")
async def select_file(data: dict):
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET
    file_id = data.get("filename")
    sheet_name = data.get("sheet_name", "")
    if file_id and os.path.exists(resolve_file_path(file_id)):
        CURRENT_SELECTED_FILE = file_id
        CURRENT_SELECTED_SHEET = sheet_name or ""
        return {"success": True, "selected": CURRENT_SELECTED_FILE, "sheet": CURRENT_SELECTED_SHEET}
    return {"success": False, "error": "File không tồn tại"}


@app.post("/sync-google-sheet")
async def sync_google_sheet(data: dict):
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET, GOOGLE_SHEET_SOURCE_URL
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
        await manager.broadcast_log(f"Đã nạp Google Sheet thành file mới: {os.path.basename(file_id)}.")
        return {
            "success": True,
            "file": file_id,
            "label": current_display_label(),
            "sheets": preview.get("sheets", []),
            "currentSheet": CURRENT_SELECTED_SHEET,
            "spreadsheetId": spreadsheet_id,
        }
    except Exception as error:
        return JSONResponse(content={"error": f"Lỗi đồng bộ Google Sheet: {str(error)}"}, status_code=500)


@app.get("/preview-excel")
async def preview_excel(sheet_name: str = Query(default="")):
    global CURRENT_SELECTED_SHEET
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return {"sheets": [], "currentSheet": "", "columns": [], "data": [], "message": f"Không tìm thấy file {CURRENT_SELECTED_FILE}."}
    try:
        preview = read_sheet_preview(target_path, sheet_name=sheet_name or CURRENT_SELECTED_SHEET)
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


@app.get("/report-partners")
async def report_partners():
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)

    try:
        partners = list_workbook_partners(target_path)
        return {
            "partners": partners,
            "total": len(partners),
            "file": CURRENT_SELECTED_FILE,
            "fileLabel": current_display_label(),
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

    try:
        available_partners = set(list_workbook_partners(target_path))
        partners = [clean_text(name) for name in selected_partners if clean_text(name) in available_partners]
        if not partners:
            return JSONResponse(content={"error": "Không tìm thấy đối tác đã chọn trong file"}, status_code=404)
        export_timestamp = filename_timestamp()

        if len(partners) == 1:
            partner = partners[0]
            report_rows = build_workbook_rows(target_path, selected_partner=partner)
            report_bytes = build_partner_report(partner, report_rows)
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
                report_bytes = build_partner_report(partner, report_rows)
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
    global CURRENT_SELECTED_FILE, CURRENT_SELECTED_SHEET
    try:
        if not (file.filename.endswith(".xlsx") or file.filename.endswith(".xls")):
            return {"success": False, "error": "Chỉ chấp nhận file Excel (.xlsx, .xls)"}

        save_path = os.path.join(EXCEL_DIR, file.filename)
        with open(save_path, "wb") as file_obj:
            content = await file.read()
            file_obj.write(content)

        CURRENT_SELECTED_FILE = file.filename
        CURRENT_SELECTED_SHEET = ""
        return {"success": True, "filename": file.filename}
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
                worker_count = payload.get("workers", 5)
                partner = clean_text(payload.get("partner", ""))
                partners_payload = payload.get("partners", [])
                partners = []
                if isinstance(partners_payload, list):
                    partners = [clean_text(name) for name in partners_payload if clean_text(name)]
                SCRAPE_TASK = asyncio.create_task(run_scraper_safely(target_path, worker_count, partner=partner or None, partners=partners))
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
