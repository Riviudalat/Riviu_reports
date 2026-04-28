from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
import asyncio
import io
import json
import os
import re
import shutil
import zipfile
from urllib.parse import quote
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from scraper import run_scraper

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
            try: await conn.send_json({"type": "log", "message": message})
            except: pass

    async def broadcast_status(self, status: dict):
        for conn in self.active_connections:
            try: await conn.send_json({"type": "status", "data": status})
            except: pass

    async def broadcast_data(self, row: dict):
        for conn in self.active_connections:
            try: await conn.send_json({"type": "data", "row": row})
            except: pass

manager = ConnectionManager()
EXCEL_DIR = os.path.dirname(os.path.abspath(__file__))
SCRAPE_TASK = None

# Biến toàn cục lưu file đang được chọn
CURRENT_SELECTED_FILE = "Report_v1.xlsx"
REPORT_COLUMNS = ["NGÀY AIR", "LINK AIR", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"]


def excel_files():
    return sorted([
        f for f in os.listdir(EXCEL_DIR)
        if (f.endswith('.xlsx') or f.endswith('.xls')) and not f.startswith('~$')
    ])


def ensure_selected_file():
    global CURRENT_SELECTED_FILE
    current_path = os.path.join(EXCEL_DIR, CURRENT_SELECTED_FILE)
    if CURRENT_SELECTED_FILE and os.path.exists(current_path):
        return CURRENT_SELECTED_FILE

    files = excel_files()
    if not files:
        CURRENT_SELECTED_FILE = ""
        return ""

    CURRENT_SELECTED_FILE = "Report_v1.xlsx" if "Report_v1.xlsx" in files else files[0]
    return CURRENT_SELECTED_FILE


def current_excel_path():
    selected = ensure_selected_file()
    return os.path.join(EXCEL_DIR, selected) if selected else ""


def normalize_header(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def clean_text(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def find_report_column(df, header):
    target = normalize_header(header)
    for col in df.columns:
        if normalize_header(col) == target:
            return col
    if header == "LINK AIR":
        for col in df.columns:
            name = normalize_header(col)
            if "link" in name or "url" in name:
                return col
    return None


def partner_columns(df):
    columns = list(df.columns)
    start_index = None
    for index, col in enumerate(columns):
        if normalize_header(col) == normalize_header("Đối tác"):
            start_index = index
            break

    if start_index is None:
        return [col for col in columns if "đối tác" in normalize_header(col)]

    return columns[start_index:]


def list_partners_from_df(df):
    partners = set()
    for col in partner_columns(df):
        for value in df[col].dropna().tolist():
            name = clean_text(value)
            if name:
                partners.add(name)
    return sorted(partners, key=lambda x: x.casefold())


def rows_for_partner(df, partner):
    cols = partner_columns(df)
    if not cols:
        return df.iloc[0:0]

    def has_partner(row):
        return any(clean_text(row[col]) == partner for col in cols)

    return df[df.apply(has_partner, axis=1)]


def safe_report_name(name):
    filename = re.sub(r'[\\/:*?"<>|]+', "-", clean_text(name))
    filename = re.sub(r"\s+", " ", filename).strip(" .")
    return filename[:90] if filename else "doi_tac"


def format_metric(value):
    if pd.isna(value) or value == "":
        return 0
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return value


def build_partner_report(partner, df):
    wb = Workbook()
    ws = wb.active
    ws.title = "Báo cáo"

    title_fill = PatternFill("solid", fgColor="123A63")
    header_fill = PatternFill("solid", fgColor="0B5ED7")
    total_fill = PatternFill("solid", fgColor="EAF3FF")
    thin = Side(style="thin", color="D8DEE9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.merge_cells("A1:G1")
    ws["A1"] = f"BÁO CÁO ĐỐI TÁC: {partner}"
    ws["A1"].fill = title_fill
    ws["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:G2")
    ws["A2"] = f"Tổng link: {len(df)}"
    ws["A2"].font = Font(color="374151", italic=True)
    ws["A2"].alignment = Alignment(horizontal="center")

    for col_index, header in enumerate(REPORT_COLUMNS, start=1):
        cell = ws.cell(row=4, column=col_index, value=header)
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    column_map = {header: find_report_column(df, header) for header in REPORT_COLUMNS}
    for row_index, (_, source_row) in enumerate(df.iterrows(), start=5):
        for col_index, header in enumerate(REPORT_COLUMNS, start=1):
            source_col = column_map[header]
            value = source_row[source_col] if source_col else ""
            if header == "LINK AIR":
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
            cell.alignment = Alignment(vertical="top", wrap_text=(header == "LINK AIR"))
            if header == "LINK AIR" and value.startswith("http"):
                cell.hyperlink = value
                cell.style = "Hyperlink"
            elif header == "NGÀY AIR":
                cell.number_format = "dd/mm/yyyy"
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif header != "LINK AIR":
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="right", vertical="top")

    total_row = len(df) + 5
    for col_index in range(1, len(REPORT_COLUMNS) + 1):
        cell = ws.cell(row=total_row, column=col_index)
        cell.fill = total_fill
        cell.border = border
    ws.cell(row=total_row, column=1, value="TỔNG")
    ws.cell(row=total_row, column=1).font = Font(bold=True)
    if len(df) > 0:
        for col_index in range(3, len(REPORT_COLUMNS) + 1):
            letter = get_column_letter(col_index)
            cell = ws.cell(row=total_row, column=col_index, value=f"=SUM({letter}5:{letter}{total_row - 1})")
            cell.fill = total_fill
            cell.font = Font(bold=True)
            cell.border = border
            cell.number_format = "#,##0"
            cell.alignment = Alignment(horizontal="right")

    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:G{max(total_row, 4)}"
    widths = [14, 72, 14, 12, 14, 14, 12]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def content_disposition(filename):
    return f"attachment; filename*=UTF-8''{quote(filename)}"


async def run_scraper_safely(target_path, worker_count):
    try:
        await run_scraper(target_path, manager, worker_count=worker_count)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        await manager.broadcast_log(f"Lỗi quét: {str(e)}")
        await manager.broadcast_status({"total": 0, "processed": 0, "success": 0, "error": 1, "done": True})

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)

@app.get("/list-files")
async def list_files():
    files = excel_files()
    ensure_selected_file()
    return {"files": files, "current": CURRENT_SELECTED_FILE}

@app.post("/select-file")
async def select_file(data: dict):
    global CURRENT_SELECTED_FILE
    filename = data.get("filename")
    if filename and os.path.exists(os.path.join(EXCEL_DIR, filename)):
        CURRENT_SELECTED_FILE = filename
        return {"success": True, "selected": CURRENT_SELECTED_FILE}
    return {"success": False, "error": "File không tồn tại"}

@app.get("/preview-excel")
async def preview_excel():
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return {"columns": [], "data": [], "message": f"Không tìm thấy file {CURRENT_SELECTED_FILE}. Vui lòng chọn hoặc tải file lên."}
    try:
        # Đọc file với engine openpyxl để ổn định hơn trên Windows
        df = pd.read_excel(target_path, engine='openpyxl')
        # Chuyển đổi các cột ngày tháng sang chuỗi để tránh lỗi JSON
        for col in df.select_dtypes(include=['datetime']).columns:
            df[col] = df[col].dt.strftime('%Y-%m-%d %H:%M:%S')
            
        data = df.head(100).fillna("").to_dict(orient="records")
        columns = df.columns.tolist()
        return {"columns": columns, "data": data}
    except Exception as e:
        return {"columns": [], "data": [], "message": f"File đang bận hoặc lỗi: {str(e)}"}

@app.get("/download-excel")
async def download_excel():
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)
    return FileResponse(
        path=target_path,
        filename=CURRENT_SELECTED_FILE,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.get("/report-partners")
async def report_partners():
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)

    try:
        df = pd.read_excel(target_path, engine='openpyxl')
        partners = list_partners_from_df(df)
        return {"partners": partners, "total": len(partners), "file": CURRENT_SELECTED_FILE}
    except Exception as e:
        return JSONResponse(content={"error": f"Không đọc được danh sách đối tác: {str(e)}"}, status_code=500)

@app.post("/export-report")
async def export_report(data: dict):
    target_path = current_excel_path()
    if not os.path.exists(target_path):
        return JSONResponse(content={"error": "File không tồn tại"}, status_code=404)

    selected_partners = data.get("partners") or []
    if not isinstance(selected_partners, list) or not selected_partners:
        return JSONResponse(content={"error": "Vui lòng chọn ít nhất một đối tác"}, status_code=400)

    try:
        df = pd.read_excel(target_path, engine='openpyxl')
        available_partners = set(list_partners_from_df(df))
        partners = [clean_text(name) for name in selected_partners if clean_text(name) in available_partners]
        if not partners:
            return JSONResponse(content={"error": "Không tìm thấy đối tác đã chọn trong file"}, status_code=404)

        if len(partners) == 1:
            partner = partners[0]
            report_df = rows_for_partner(df, partner)
            report_bytes = build_partner_report(partner, report_df)
            filename = f"{safe_report_name(partner)}.xlsx"
            return Response(
                content=report_bytes,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": content_disposition(filename)}
            )

        archive = io.BytesIO()
        used_names = set()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for partner in partners:
                report_df = rows_for_partner(df, partner)
                report_bytes = build_partner_report(partner, report_df)
                base_name = safe_report_name(partner)
                filename = f"{base_name}.xlsx"
                counter = 2
                while filename in used_names:
                    filename = f"{base_name}_{counter}.xlsx"
                    counter += 1
                used_names.add(filename)
                zf.writestr(filename, report_bytes)

        archive.seek(0)
        return Response(
            content=archive.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": content_disposition("bao_cao_doi_tac.zip")}
        )
    except Exception as e:
        return JSONResponse(content={"error": f"Lỗi xuất báo cáo: {str(e)}"}, status_code=500)

@app.post("/upload-excel")
async def upload_excel(file: UploadFile = File(...)):
    global CURRENT_SELECTED_FILE
    try:
        if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
            return {"success": False, "error": "Chỉ chấp nhận file Excel (.xlsx, .xls)"}
            
        save_path = os.path.join(EXCEL_DIR, file.filename)
        with open(save_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        CURRENT_SELECTED_FILE = file.filename # Tự động chọn file vừa upload
        return {"success": True, "filename": file.filename}
    except Exception as e:
        return {"success": False, "error": str(e)}

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
                SCRAPE_TASK = asyncio.create_task(run_scraper_safely(target_path, worker_count))
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
