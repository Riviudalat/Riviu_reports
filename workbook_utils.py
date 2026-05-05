import os
import re
import unicodedata
import urllib.request
import json
from typing import Iterable
from urllib.parse import urlparse

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


DATA_DIR_NAME = "data"
GOOGLE_SHEET_FILENAME_PREFIX = "google_sheet_"
LEGACY_GOOGLE_SHEET_FILE_ID = "data/google_sheet_main.xlsx"
GOOGLE_SHEET_LABEL = "Google Sheet chính"
REPORT_COLUMNS = ["NGÀY AIR", "TÊN KÊNH", "LINK AIR", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"]
SUMMARY_SHEET_NAME = "Tổng kết"
SUMMARY_COLUMNS = ["Stt", "ĐỐI TÁC", "TỔNG LINK", "TỔNG LƯỢT XEM", "TỔNG TIM", "TỔNG BÌNH LUẬN", "TỔNG LƯỢT LƯU", "TỔNG CHIA SẺ"]
METRIC_COLUMNS = ["LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"]
SUMMARY_METRIC_COLUMNS = ["TỔNG LƯỢT XEM", "TỔNG TIM", "TỔNG BÌNH LUẬN", "TỔNG LƯỢT LƯU", "TỔNG CHIA SẺ"]
PREFERRED_DATA_SHEET_KEYS = {"thang 5", "thang5"}
PARTNER_HEADING_MARKERS = ("DANH SÁCH", "DANH SACH", "BỘ ẢNH", "BO ANH")
CHANNEL_OVERRIDE_FILENAME = "channel_name_overrides.json"


def ensure_data_dir(base_dir):
    data_dir = os.path.join(base_dir, DATA_DIR_NAME)
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def normalize_header(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def normalize_key(value):
    text = str(value or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.strip()).casefold()


def clean_text(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def normalize_channel_display(value):
    text = clean_text(value)
    channel_fixes = {
        "B?o Quy?n": "Bảo Quyên",
    }
    return channel_fixes.get(text, text)


def clean_preview_value(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    text = clean_text(value)
    if text.endswith(" 00:00:00"):
        return text[:10]
    return text


def parse_google_spreadsheet_id(source_url):
    parsed = urlparse(source_url.strip())
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", parsed.path)
    if not match:
        raise ValueError("URL Google Sheet không hợp lệ")
    return match.group(1)


def extract_tiktok_username(url):
    match = re.search(r"tiktok\.com/@([^/?]+)", str(url or ""), re.IGNORECASE)
    return match.group(1).strip() if match else ""


def google_sheet_file_id(spreadsheet_id):
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "", spreadsheet_id)
    if not safe_id:
        raise ValueError("Google Sheet ID không hợp lệ")
    return f"{DATA_DIR_NAME}/{GOOGLE_SHEET_FILENAME_PREFIX}{safe_id}.xlsx"


def base_dir_for_file(file_path):
    absolute_path = os.path.abspath(file_path)
    parent = os.path.dirname(absolute_path)
    if os.path.basename(parent).casefold() == DATA_DIR_NAME:
        return os.path.dirname(parent)
    return parent


def channel_override_path(file_path):
    return os.path.join(ensure_data_dir(base_dir_for_file(file_path)), CHANNEL_OVERRIDE_FILENAME)


def load_channel_overrides(file_path):
    path = channel_override_path(file_path)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key).casefold(): clean_text(value) for key, value in data.items() if clean_text(value)}


def google_sheet_export_url(source_url):
    spreadsheet_id = parse_google_spreadsheet_id(source_url)
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=xlsx"


def download_google_sheet(source_url, destination_path):
    export_url = google_sheet_export_url(source_url)
    request = urllib.request.Request(export_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    with open(destination_path, "wb") as file_obj:
        file_obj.write(content)
    return destination_path


def load_excel_file(file_path):
    return pd.ExcelFile(file_path, engine="openpyxl")


def workbook_sheet_names(file_path):
    workbook = load_excel_file(file_path)
    return workbook.sheet_names


def read_sheet_frame(file_path, sheet_name):
    return pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")


def is_summary_sheet_name(sheet_name):
    return normalize_key(sheet_name) == normalize_key(SUMMARY_SHEET_NAME)


def is_preferred_data_sheet_name(sheet_name):
    return normalize_key(sheet_name) in PREFERRED_DATA_SHEET_KEYS


def read_sheet_preview(file_path, sheet_name=None, limit=100):
    workbook = load_excel_file(file_path)
    sheets = workbook.sheet_names
    current_sheet = sheet_name if sheet_name in sheets else (sheets[0] if sheets else "")
    if not current_sheet:
        return {"sheets": [], "currentSheet": "", "columns": [], "data": [], "message": "Workbook không có sheet nào."}

    frame = workbook.parse(current_sheet)
    channel_overrides = load_channel_overrides(file_path)
    link_column = find_link_column_name(frame)
    channel_column = find_column_name(frame, ["TÊN KÊNH", "Tên Kênh"])
    for column in frame.select_dtypes(include=["datetime"]).columns:
        frame[column] = frame[column].dt.strftime("%Y-%m-%d %H:%M:%S")

    data = []
    for record in frame.head(limit).to_dict(orient="records"):
        preview_row = {}
        for column, value in record.items():
            cleaned = clean_preview_value(value)
            if normalize_key(column) == normalize_key("TÊN KÊNH"):
                cleaned = normalize_channel_display(cleaned)
            preview_row[column] = cleaned
        if link_column and channel_column:
            username = extract_tiktok_username(record.get(link_column, ""))
            override_name = channel_overrides.get(username.casefold()) if username else ""
            if override_name:
                preview_row[channel_column] = override_name
        data.append(preview_row)

    return {
        "sheets": sheets,
        "currentSheet": current_sheet,
        "columns": frame.columns.tolist(),
        "data": data,
    }


def read_summary_dashboard(file_path):
    workbook = load_excel_file(file_path)
    summary_sheet = next((sheet for sheet in workbook.sheet_names if is_summary_sheet_name(sheet)), "")
    if not summary_sheet:
        return {
            "sheet": "",
            "columns": SUMMARY_COLUMNS,
            "rows": [],
            "totals": {},
            "message": "Workbook chưa có sheet Tổng kết.",
        }

    frame = workbook.parse(summary_sheet).fillna("")
    columns = frame.columns.tolist()
    rows = []
    partner_column = find_column_name(frame, ["ĐỐI TÁC", "Đối tác"])
    total_link_column = find_column_name(frame, ["TỔNG LINK", "Tổng link"])
    summary_metric_map = {
        "views": find_column_name(frame, ["TỔNG LƯỢT XEM", "LƯỢT XEM"]),
        "likes": find_column_name(frame, ["TỔNG TIM", "TIM"]),
        "comments": find_column_name(frame, ["TỔNG BÌNH LUẬN", "BÌNH LUẬN"]),
        "saves": find_column_name(frame, ["TỔNG LƯỢT LƯU", "LƯỢT LƯU"]),
        "shares": find_column_name(frame, ["TỔNG CHIA SẺ", "CHIA SẺ"]),
    }

    numeric_summary_keys = {normalize_key(item) for item in SUMMARY_COLUMNS if normalize_key(item) != "doi tac"}
    for record in frame.to_dict(orient="records"):
        partner_name = clean_text(record.get(partner_column or "", ""))
        if not partner_name:
            continue
        row = {}
        for column in columns:
            value = record.get(column, "")
            row[column] = to_number(value) if normalize_key(column) in numeric_summary_keys else clean_text(value)
        rows.append(row)

    totals = {
        "partners": len(rows),
        "links": 0,
        "views": 0,
        "likes": 0,
        "comments": 0,
        "saves": 0,
        "shares": 0,
    }
    for row in rows:
        totals["links"] += to_number(row.get(total_link_column, 0)) if total_link_column else 0
        for key, column in summary_metric_map.items():
            if column:
                totals[key] += to_number(row.get(column, 0))

    return {
        "sheet": summary_sheet,
        "columns": columns,
        "rows": rows,
        "totals": totals,
    }


def dataframe_partner_columns(frame):
    columns = list(frame.columns)
    start_index = None
    for index, column in enumerate(columns):
        if normalize_key(column).startswith("doi tac"):
            start_index = index
            break

    if start_index is None:
        return []

    partner_columns = [columns[start_index]]
    for column in columns[start_index + 1:]:
        normalized = normalize_key(column)
        raw_normalized = normalize_header(column)
        if normalized.startswith("doi tac") or raw_normalized.startswith("unnamed:") or raw_normalized == "":
            partner_columns.append(column)
        else:
            break
    return partner_columns


def split_partner_value(value):
    text = clean_text(value)
    if not text:
        return []

    raw_lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
    if len(raw_lines) <= 1 and not any(marker in text.upper() for marker in PARTNER_HEADING_MARKERS) and not text.lstrip().startswith(("-", "*", "•")):
        cleaned = _normalize_partner_token(text)
        return [cleaned] if cleaned else []

    partners = []
    for line in raw_lines:
        cleaned = _normalize_partner_token(line)
        if not cleaned:
            continue
        upper_line = cleaned.upper()
        if any(marker in upper_line for marker in PARTNER_HEADING_MARKERS):
            continue
        partners.append(cleaned)
    return unique_preserve_order(partners)


def _normalize_partner_token(text):
    cleaned = clean_text(text)
    cleaned = re.sub(r"^[\-\*\u2022]+\s*", "", cleaned)
    cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:\t")
    return cleaned


def extract_row_partners(row, partner_columns: Iterable):
    partners = []
    for column in partner_columns:
        for partner in split_partner_value(row.get(column, "")):
            partners.append(partner)
    return unique_preserve_order(partners)


def unique_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def find_column_name(frame, aliases):
    alias_set = {normalize_key(alias) for alias in aliases}
    for column in frame.columns:
        column_key = normalize_key(column)
        if column_key in alias_set:
            return column
    return None


def find_link_column_name(frame):
    column = find_column_name(frame, ["LINK AIR", "Link", "URL"])
    if column:
        return column
    for candidate in frame.columns:
        key = normalize_key(candidate)
        if "link" in key or "url" in key:
            return candidate
    return None


def find_data_sheet_names(file_path):
    workbook = load_excel_file(file_path)
    sheets = workbook.sheet_names
    data_sheets = [sheet for sheet in sheets if not is_summary_sheet_name(sheet)]
    if not data_sheets:
        return []

    for sheet_name in data_sheets:
        if is_preferred_data_sheet_name(sheet_name):
            return [sheet_name]

    matching_sheets = []
    for sheet_name in data_sheets:
        try:
            frame = workbook.parse(sheet_name)
        except Exception:
            continue
        if find_link_column_name(frame):
            matching_sheets.append(sheet_name)

    return matching_sheets or [data_sheets[0]]


def build_workbook_rows(file_path, selected_partner=None):
    workbook = load_excel_file(file_path)
    selected_key = selected_partner.casefold() if selected_partner else None
    channel_overrides = load_channel_overrides(file_path)
    rows = []

    for sheet_name in find_data_sheet_names(file_path):
        frame = workbook.parse(sheet_name)
        partner_columns = dataframe_partner_columns(frame)
        date_column = find_column_name(frame, ["NGÀY AIR", "Ngày"])
        channel_column = find_column_name(frame, ["TÊN KÊNH", "Tên Kênh"])
        link_column = find_link_column_name(frame)
        metric_columns = {
            "LƯỢT XEM": find_column_name(frame, ["LƯỢT XEM"]),
            "TIM": find_column_name(frame, ["TIM"]),
            "BÌNH LUẬN": find_column_name(frame, ["BÌNH LUẬN"]),
            "LƯỢT LƯU": find_column_name(frame, ["LƯỢT LƯU"]),
            "CHIA SẺ": find_column_name(frame, ["CHIA SẺ"]),
        }

        if not link_column:
            continue

        for _, row in frame.iterrows():
            partners = extract_row_partners(row, partner_columns) if partner_columns else []
            if selected_key and selected_key not in {partner.casefold() for partner in partners}:
                continue
            if selected_key and not partners:
                continue

            link = clean_text(row.get(link_column, ""))
            if not link:
                continue
            username = extract_tiktok_username(link)
            override_name = channel_overrides.get(username.casefold()) if username else ""

            rows.append({
                "sheet_name": sheet_name,
                "NGÀY AIR": row.get(date_column, "") if date_column else "",
                "TÊN KÊNH": override_name or (clean_text(row.get(channel_column, "")) if channel_column else ""),
                "LINK AIR": link,
                "LƯỢT XEM": row.get(metric_columns["LƯỢT XEM"], "") if metric_columns["LƯỢT XEM"] else "",
                "TIM": row.get(metric_columns["TIM"], "") if metric_columns["TIM"] else "",
                "BÌNH LUẬN": row.get(metric_columns["BÌNH LUẬN"], "") if metric_columns["BÌNH LUẬN"] else "",
                "LƯỢT LƯU": row.get(metric_columns["LƯỢT LƯU"], "") if metric_columns["LƯỢT LƯU"] else "",
                "CHIA SẺ": row.get(metric_columns["CHIA SẺ"], "") if metric_columns["CHIA SẺ"] else "",
                "partners": partners,
            })

    return rows


def list_workbook_partners(file_path):
    workbook = load_excel_file(file_path)
    partners = []
    for sheet_name in find_data_sheet_names(file_path):
        frame = workbook.parse(sheet_name)
        partner_columns = dataframe_partner_columns(frame)
        if not partner_columns:
            continue
        for _, row in frame.iterrows():
            partners.extend(extract_row_partners(row, partner_columns))
    return sorted(unique_preserve_order(partners), key=lambda value: value.casefold())


def worksheet_headers(worksheet):
    max_column = worksheet.max_column or 0
    if max_column < 1:
        return []
    return [worksheet.cell(row=1, column=index).value for index in range(1, max_column + 1)]


def worksheet_find_column_index(worksheet, aliases):
    alias_set = {normalize_key(alias) for alias in aliases}
    for index, header in enumerate(worksheet_headers(worksheet), start=1):
        if normalize_key(header) in alias_set:
            return index
    return None


def worksheet_find_link_column_index(worksheet):
    column_index = worksheet_find_column_index(worksheet, ["LINK AIR", "Link", "URL"])
    if column_index:
        return column_index

    for index, header in enumerate(worksheet_headers(worksheet), start=1):
        key = normalize_key(header)
        if "link" in key or "url" in key:
            return index
    return None


def worksheet_has_link_column(worksheet):
    if worksheet_find_link_column_index(worksheet):
        return True

    max_row = worksheet.max_row or 0
    max_column = worksheet.max_column or 0
    for row_index in range(2, min(max_row, 25) + 1):
        for column_index in range(1, max_column + 1):
            value = clean_text(worksheet.cell(row=row_index, column=column_index).value)
            if "tiktok.com" in value or "vt.tiktok.com" in value:
                return True
    return False


def workbook_data_sheet_names(workbook):
    sheet_names = [sheet for sheet in workbook.sheetnames if not is_summary_sheet_name(sheet)]
    if not sheet_names:
        return []

    for sheet_name in sheet_names:
        if is_preferred_data_sheet_name(sheet_name):
            return [sheet_name]

    matching_sheets = [sheet_name for sheet_name in sheet_names if worksheet_has_link_column(workbook[sheet_name])]
    return matching_sheets or [sheet_names[0]]


def worksheet_partner_column_indexes(worksheet):
    headers = worksheet_headers(worksheet)
    start_index = None
    for index, header in enumerate(headers, start=1):
        if normalize_key(header).startswith("doi tac"):
            start_index = index
            break

    if start_index is None:
        return []

    indexes = [start_index]
    for index in range(start_index + 1, len(headers) + 1):
        header_key = normalize_key(headers[index - 1])
        raw_header_key = normalize_header(headers[index - 1])
        if header_key.startswith("doi tac") or not raw_header_key or raw_header_key.startswith("unnamed:"):
            indexes.append(index)
        else:
            break
    return indexes


def worksheet_row_partners(worksheet, row_index, partner_columns):
    values = {column_index: worksheet.cell(row=row_index, column=column_index).value for column_index in partner_columns}
    partners = []
    for value in values.values():
        partners.extend(split_partner_value(value))
    return unique_preserve_order(partners)


def to_number(value):
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = clean_text(value)
    if not text:
        return 0
    compact = re.sub(r"[,\s]", "", text)
    try:
        return int(float(compact))
    except ValueError:
        return 0


def build_partner_summary_rows(workbook):
    summary = {}
    for sheet_name in workbook_data_sheet_names(workbook):
        worksheet = workbook[sheet_name]
        link_column = worksheet_find_link_column_index(worksheet)
        if not link_column:
            continue

        partner_columns = worksheet_partner_column_indexes(worksheet)
        if not partner_columns:
            continue

        metric_columns = {
            header: worksheet_find_column_index(worksheet, [header])
            for header in METRIC_COLUMNS
        }

        for row_index in range(2, (worksheet.max_row or 0) + 1):
            link = clean_text(worksheet.cell(row=row_index, column=link_column).value)
            if not link or ("tiktok.com" not in link and "vt.tiktok.com" not in link):
                continue

            partners = worksheet_row_partners(worksheet, row_index, partner_columns)
            if not partners:
                continue

            for partner in partners:
                bucket = summary.setdefault(
                    partner,
                    {
                        "ĐỐI TÁC": partner,
                        "TỔNG LINK": 0,
                        "TỔNG LƯỢT XEM": 0,
                        "TỔNG TIM": 0,
                        "TỔNG BÌNH LUẬN": 0,
                        "TỔNG LƯỢT LƯU": 0,
                        "TỔNG CHIA SẺ": 0,
                    },
                )
                bucket["TỔNG LINK"] += 1
                for metric, column_index in metric_columns.items():
                    if not column_index:
                        continue
                    value = to_number(worksheet.cell(row=row_index, column=column_index).value)
                    if metric == "LƯỢT XEM":
                        bucket["TỔNG LƯỢT XEM"] += value
                    elif metric == "TIM":
                        bucket["TỔNG TIM"] += value
                    elif metric == "BÌNH LUẬN":
                        bucket["TỔNG BÌNH LUẬN"] += value
                    elif metric == "LƯỢT LƯU":
                        bucket["TỔNG LƯỢT LƯU"] += value
                    elif metric == "CHIA SẺ":
                        bucket["TỔNG CHIA SẺ"] += value

    result = []
    for index, name in enumerate(sorted(summary, key=lambda value: value.casefold()), start=1):
        row = summary[name]
        row["Stt"] = index
        result.append(row)
    return result


def rebuild_summary_sheet(workbook):
    summary_sheet = next((name for name in workbook.sheetnames if is_summary_sheet_name(name)), None)
    if summary_sheet:
        worksheet = workbook[summary_sheet]
        worksheet.delete_rows(1, max(worksheet.max_row or 1, 1))
        if worksheet.title != SUMMARY_SHEET_NAME:
            worksheet.title = SUMMARY_SHEET_NAME
    else:
        worksheet = workbook.create_sheet(SUMMARY_SHEET_NAME)

    rows = build_partner_summary_rows(workbook)
    header_fill = PatternFill("solid", fgColor="0B5ED7")
    for column_index, header in enumerate(SUMMARY_COLUMNS, start=1):
        cell = worksheet.cell(row=1, column=column_index, value=header)
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center")

    for row_index, row in enumerate(rows, start=2):
        for column_index, header in enumerate(SUMMARY_COLUMNS, start=1):
            cell = worksheet.cell(row=row_index, column=column_index, value=row.get(header, ""))
            if header == "ĐỐI TÁC":
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            else:
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="right")

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(len(SUMMARY_COLUMNS))}{max(len(rows) + 1, 1)}"
    widths = [8, 38, 12, 16, 12, 16, 16, 14]
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width

    return len(rows)


def workbook_file_entries(base_dir):
    data_dir = ensure_data_dir(base_dir)
    entries = []

    for filename in os.listdir(base_dir):
        if filename.startswith("~$"):
            continue
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            entries.append({
                "id": filename.replace("\\", "/"),
                "label": filename,
                "source": "local",
            })

    for filename in os.listdir(data_dir):
        if filename.startswith("~$"):
            continue
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            relative_id = f"{DATA_DIR_NAME}/{filename}".replace("\\", "/")
            label = f"{GOOGLE_SHEET_LABEL}: {filename.removesuffix('.xlsx').replace(GOOGLE_SHEET_FILENAME_PREFIX, '')[:12]}"
            if relative_id == LEGACY_GOOGLE_SHEET_FILE_ID:
                label = GOOGLE_SHEET_LABEL
            entries.append({
                "id": relative_id,
                "label": label,
                "source": "google",
            })

    return sorted(entries, key=lambda entry: (entry["source"] != "google", entry["label"].casefold()))
