"""Partner report workbooks, export payloads and Google push rows.

Pure workbook/report building: no FastAPI, scan state, locks or WebSocket code.
`riviu/api/export.py` and `riviu/api/google.py` own the routes that call into this module; this module must not import `riviu.app`.
"""
import io
import os
import re
import zipfile
from copy import copy
from datetime import datetime
from functools import lru_cache

import pandas as pd
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string
from openpyxl.utils.units import pixels_to_EMU

from riviu import paths
from riviu.workbook_utils import (
    default_data_sheet,
    LAST_UPDATE_COLUMN,
    TIKTOK_MEDIA_PHOTO,
    TIKTOK_MEDIA_VIDEO,
    WorkbookSnapshot,
    build_partner_report_rows,
    clean_text,
    detect_tiktok_media_type,
    find_data_sheet_names,
    format_display_datetime,
    format_filename_datetime,
    format_metric,
    get_platform,
    group_rows_by_partner,
    is_exportable_report_row,
    is_failed_channel_name,
    list_workbook_partners,
    metric_total,
    partner_dedup_key,
    partner_link_counts,
    set_cell_literal,
)


LOGO_PATH = paths.LOGO_PATH
# Distinct date texts one export or push remembers; a sheet repeats a few dates over many rows.
REPORT_DATE_MEMO_SIZE = 4096


def safe_report_name(name):
    filename = re.sub(r'[\\/:*?"<>|]+', "-", clean_text(name))
    filename = re.sub(r"\s+", " ", filename).strip(" .")
    return filename[:90] if filename else "doi_tac"


def spreadsheet_text(value):
    return clean_text(value)


def spreadsheet_date_text(value, parse_date=None):
    if pd.isna(value):
        return ""
    if hasattr(value, "to_pydatetime"):
        return spreadsheet_text(value.to_pydatetime().strftime("%d/%m/%Y"))
    if isinstance(value, datetime):
        return spreadsheet_text(value.strftime("%d/%m/%Y"))
    parsed = (parse_date or parse_report_date)(value)
    if not pd.isna(parsed):
        return spreadsheet_text(parsed.to_pydatetime().strftime("%d/%m/%Y"))
    text = clean_text(value)
    if text.endswith(" 00:00:00"):
        text = text[:10]
    return spreadsheet_text(text)


def parse_report_date(value):
    """Parse report dates as Vietnamese day/month/year values."""
    if pd.isna(value):
        return pd.NaT
    if hasattr(value, "to_pydatetime") or isinstance(value, datetime):
        return value
    text = clean_text(value)
    if not text:
        return pd.NaT
    # Keep ISO timestamps unambiguous; all other slash/dash dates are D/M/Y.
    if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", text):
        return pd.to_datetime(text, errors="coerce")
    return pd.to_datetime(text, errors="coerce", dayfirst=True)


def report_date_parser(maxsize=REPORT_DATE_MEMO_SIZE):
    """`parse_report_date` for one export or push, parsing each distinct date text once.

    The memo is bounded and lives only as long as the operation that made it.
    """
    parse_text = lru_cache(maxsize=maxsize)(parse_report_date)

    def parse(value):
        return parse_text(value) if isinstance(value, str) else parse_report_date(value)

    return parse


def spreadsheet_hyperlink(value):
    return clean_text(value)


def excel_column_name(index):
    name = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def column_width_to_pixels(width):
    if not width:
        width = 8.43
    return int(width * 7 + 5)


def row_height_to_pixels(height):
    if not height:
        height = 15
    return int(height * 96 / 72)


def add_centered_image_to_cell(ws, cell_address, image_path, max_height_px=36):
    """Anchor an image in the middle of one cell, scaled down (aspect kept) to fit it.

    The cell's final column width and row height must already be set: the offsets are
    computed from them, and a width set afterwards leaves the image off centre.
    """
    if not image_path or not os.path.exists(image_path):
        return False
    try:
        img = ExcelImage(image_path)
    except Exception:
        return False

    col_letter, row_number = coordinate_from_string(cell_address)
    col_idx = column_index_from_string(col_letter) - 1
    row_idx = row_number - 1

    source_width, source_height = img.width, img.height
    cell_width_px = column_width_to_pixels(ws.column_dimensions[col_letter].width)
    cell_height_px = row_height_to_pixels(ws.row_dimensions[row_number].height)
    max_width_px = max(cell_width_px - 8, 16)
    scale = min(1, max_height_px / source_height, max_width_px / source_width)
    img.width = max(round(source_width * scale), 1)
    img.height = max(round(source_height * scale), 1)

    col_off = pixels_to_EMU(max((cell_width_px - img.width) // 2, 0))
    row_off = pixels_to_EMU(max((cell_height_px - img.height) // 2, 0))
    img.anchor = OneCellAnchor(
        _from=AnchorMarker(col=col_idx, colOff=col_off, row=row_idx, rowOff=row_off),
        ext=XDRPositiveSize2D(pixels_to_EMU(img.width), pixels_to_EMU(img.height)),
    )
    ws.add_image(img)
    return True


# Banner images: Riviu logo in A1 and the platform icon in the last column, both in row 1
# (42pt = 56px). The icon PNGs are 3x renditions of REPORT_ICON_PX (tools/make_report_icons.py).
REPORT_LOGO_MAX_HEIGHT_PX = 40
REPORT_ICON_PX = 24
POST_TYPE_COLUMN = "LOẠI BÀI"
# Poster reports (one file per Người đăng) also say which partners each link is for.
PARTNERS_COLUMN = "ĐỐI TÁC"
NO_POSTER = "Chưa rõ người đăng"
SUBJECT_PARTNER = "partner"
SUBJECT_POSTER = "poster"
SUBJECT_TITLES = {SUBJECT_PARTNER: "ĐỐI TÁC", SUBJECT_POSTER: "NGƯỜI ĐĂNG"}
# Header text shown in the report for a source column (the rows keep their workbook names).
REPORT_HEADER_LABELS = {"NGÀY AIR": "NGÀY ĐĂNG"}
REPORT_COLUMN_WIDTHS = {
    "NGÀY AIR": 14, "TÊN KÊNH": 24, "LINK AIR": 60, "LƯỢT XEM": 14, "TIM": 12,
    "BÌNH LUẬN": 14, "LƯỢT LƯU": 14, "REPOST": 14, "CHIA SẺ": 12, POST_TYPE_COLUMN: 18, PARTNERS_COLUMN: 24,
}
# Text columns grow to their longest value (up to this width) instead of wrapping onto two lines.
REPORT_MAX_TEXT_WIDTH = {"TÊN KÊNH": 40, "LINK AIR": 90, PARTNERS_COLUMN: 48}
# Room for Excel's filter button at the right of each header cell, so it never covers the label.
REPORT_FILTER_BUTTON_WIDTH = 7
REPORT_TEXT_COLUMNS = {"LINK AIR", "TÊN KÊNH", POST_TYPE_COLUMN, PARTNERS_COLUMN}
REPORT_ACCENT = "FF6B00"
REPORT_ACCENT_TEXT = "9A3412"
REPORT_TOTAL_FILL = "FFF7ED"
MEDIA_LABELS = {TIKTOK_MEDIA_PHOTO: "Ảnh", TIKTOK_MEDIA_VIDEO: "Video"}


def report_columns(spec, subject=SUBJECT_PARTNER):
    extra = [PARTNERS_COLUMN] if subject == SUBJECT_POSTER else []
    return [*spec.report_columns, *extra, POST_TYPE_COLUMN]


def poster_of(row):
    """A row's Người đăng (Riviu Data's nguoi_air), or NO_POSTER when the sheet names nobody."""
    return clean_text(row.get("_nguoiAir", "")) or NO_POSTER


def poster_sort_key(name):
    return (name == NO_POSTER, name.casefold())


def poster_link_counts(rows, *, apply_min_views=True, min_views=100):
    """[{name, linkCount, rawLinkCount}] per Người đăng, A → Z with NO_POSTER last.

    Like partner_link_counts: rawLinkCount is every link of the poster, linkCount the ones the
    report keeps (readable channel, enough views when the filter is on). Each link counts once.
    """
    stats = {}
    for row in rows:
        item = stats.setdefault(poster_of(row), {"name": poster_of(row), "linkCount": 0, "rawLinkCount": 0})
        item["rawLinkCount"] += 1
        if is_exportable_report_row(row, apply_min_views=apply_min_views, min_views=min_views):
            item["linkCount"] += 1
    return sorted(stats.values(), key=lambda item: poster_sort_key(item["name"]))


def group_rows_by_poster(rows):
    groups = {}
    for row in rows:
        groups.setdefault(poster_of(row), []).append(row)
    return groups


def report_header_label(header):
    return REPORT_HEADER_LABELS.get(header, header)


def report_cell_value(header, value, parse_date=None):
    if header == "NGÀY AIR":
        if pd.isna(value):
            return ""
        if hasattr(value, "to_pydatetime"):
            return value.to_pydatetime()
        # A column mixing real date cells with date text stays `object`, so plain datetimes arrive here.
        if isinstance(value, datetime):
            return value
        parsed_date = (parse_date or parse_report_date)(value)
        return parsed_date.to_pydatetime() if not pd.isna(parsed_date) else clean_text(value)
    if header in REPORT_TEXT_COLUMNS:
        return clean_text(value)
    return format_metric(value)


def report_post_type(spec, source_row):
    """Ảnh/Video (Bài viết on Threads) + độc quyền (one partner) or lịch trình (several).

    A TikTok short link that no scan has resolved yet has no known media type; it shows the
    partner part alone.
    """
    partners = source_row.get("partners")
    partners = partners if isinstance(partners, (list, tuple)) else []
    if not partners:
        return ""
    scope = "độc quyền" if len(partners) == 1 else "lịch trình"
    if spec.key == "threads":
        media = "Bài viết"
    else:
        media = MEDIA_LABELS.get(detect_tiktok_media_type(
            clean_text(source_row.get("LINK AIR", "")), resolved_url=clean_text(source_row.get("_resolvedUrl", "")),
        ), "")
    return f"{media} {scope}" if media else scope.capitalize()


def report_partners_text(source_row):
    partners = source_row.get("partners")
    return ", ".join(clean_text(name) for name in partners if clean_text(name)) if isinstance(partners, (list, tuple)) else ""


def report_platform_icon_path(spec):
    return os.path.join(paths.REPORT_ICONS_DIR, f"{spec.key}.png")


def report_column_widths(columns, values_by_column):
    widths = []
    for header in columns:
        width = REPORT_COLUMN_WIDTHS.get(header, 14)
        longest = max((len(str(value)) for value in values_by_column.get(header, ()) if value), default=0)
        if header in REPORT_MAX_TEXT_WIDTH:
            width = max(width, min(longest + 2, REPORT_MAX_TEXT_WIDTH[header]))
        # The label is bold, so it gets a little more than its character count.
        width = max(width, round(len(report_header_label(header)) * 1.2) + REPORT_FILTER_BUTTON_WIDTH)
        widths.append(width)
    return widths


def write_report_banner(ws, spec, partner, link_count, columns, widths, subject=SUBJECT_PARTNER):
    ws.row_dimensions[1].height = 42
    ws.row_dimensions[2].height = 26
    # Final widths first: the images below are centred from them.
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    add_centered_image_to_cell(ws, "A1", LOGO_PATH, max_height_px=REPORT_LOGO_MAX_HEIGHT_PX)

    # Row 1: logo, the title across the middle, the platform icon in the outermost column.
    last_column = get_column_letter(len(columns))
    ws.merge_cells(start_row=1, start_column=2, end_row=1, end_column=len(columns) - 1)
    title_cell = ws["B1"]
    title_cell.value = f"BÁO CÁO {spec.label.upper()} - {SUBJECT_TITLES[subject]}: {partner}"
    title_cell.fill = PatternFill("solid", fgColor=REPORT_ACCENT)
    title_cell.font = Font(color="FFFFFF", bold=True, size=14)
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    add_centered_image_to_cell(ws, f"{last_column}1", report_platform_icon_path(spec), max_height_px=REPORT_ICON_PX)

    # Row 2: only the metadata line, across the whole width.
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(columns))
    ws["A2"] = f"Tổng link: {link_count} • Ngày cập nhật: {format_display_datetime()}"
    ws["A2"].font = Font(color=REPORT_ACCENT_TEXT, italic=True, bold=True)
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center")


def write_report_total(ws, spec, frame, columns, total_row, border):
    total_fill = PatternFill("solid", fgColor=REPORT_TOTAL_FILL)
    ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=3)
    for col_index in range(1, len(columns) + 1):
        cell = ws.cell(row=total_row, column=col_index)
        cell.fill = total_fill
        cell.border = border
    total_label = ws.cell(row=total_row, column=1, value="TỔNG")
    total_label.font = Font(bold=True, color=REPORT_ACCENT_TEXT)
    total_label.alignment = Alignment(horizontal="center", vertical="center")
    for header in spec.metric_columns:
        if header not in frame.columns:
            continue
        total_value = metric_total(frame[header])
        if total_value == "":
            continue
        cell = ws.cell(row=total_row, column=columns.index(header) + 1, value=total_value)
        cell.font = Font(bold=True, color=REPORT_ACCENT_TEXT)
        cell.number_format = "#,##0"
        cell.alignment = Alignment(horizontal="right")


def build_partner_report(
    partner, rows, *, apply_min_views=True, min_views=100, platform="tiktok", parse_date=None, subject=SUBJECT_PARTNER,
):
    """One partner's report; with subject=SUBJECT_POSTER, one Người đăng's (titled so, plus ĐỐI TÁC)."""
    spec = get_platform(platform)
    parse_date = parse_date or report_date_parser()
    columns = report_columns(spec, subject)
    valid_rows = [
        row for row in rows
        if is_exportable_report_row(row, apply_min_views=apply_min_views, min_views=min_views)
    ]
    frame = pd.DataFrame(valid_rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "Báo cáo"
    table_header_row = 3
    data_start_row = table_header_row + 1
    thin = Side(style="thin", color="D8DEE9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Cell values first: the text columns are sized from them before the banner centres its images.
    table = []
    for _, source_row in frame.iterrows():
        table.append([
            report_post_type(spec, source_row) if header == POST_TYPE_COLUMN
            else report_partners_text(source_row) if header == PARTNERS_COLUMN
            else report_cell_value(header, source_row.get(header, ""), parse_date)
            for header in columns
        ])
    values_by_column = {header: [row[index] for row in table] for index, header in enumerate(columns)}
    write_report_banner(ws, spec, partner, len(frame), columns, report_column_widths(columns, values_by_column), subject)
    for col_index, header in enumerate(columns, start=1):
        cell = ws.cell(row=table_header_row, column=col_index, value=report_header_label(header))
        cell.fill = PatternFill("solid", fgColor=REPORT_ACCENT)
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    # A data cell's finished style depends only on (column, hyperlink). openpyxl registers each
    # style object on assignment, which dominated large exports, so the first cell of each kind
    # is styled in full and the others copy its style ids. Rows are not coloured: LOẠI BÀI says
    # what the colours used to.
    style_templates = {}
    for row_index, values in enumerate(table, start=data_start_row):
        for col_index, (header, value) in enumerate(zip(columns, values), start=1):
            cell = set_cell_literal(ws.cell(row=row_index, column=col_index), value)
            is_hyperlink = header == "LINK AIR" and isinstance(value, str) and value.startswith("http")
            if is_hyperlink:
                cell.hyperlink = value
            style_key = (header, is_hyperlink)
            template = style_templates.get(style_key)
            if template is not None:
                cell._style = copy(template)
                continue
            cell.border = border
            # One line per link: long names are cut at the column edge, never wrapped.
            cell.alignment = Alignment(vertical="top", wrap_text=False)
            if is_hyperlink:
                cell.style = "Hyperlink"
                cell.alignment = Alignment(vertical="top", wrap_text=False)
            elif header == "NGÀY AIR":
                cell.number_format = "dd/mm/yyyy"
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif header == POST_TYPE_COLUMN:
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif header not in REPORT_TEXT_COLUMNS:
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="right", vertical="top")
            style_templates[style_key] = copy(cell._style)

    total_row = len(frame) + data_start_row if len(frame) else None
    if total_row:
        write_report_total(ws, spec, frame, columns, total_row, border)

    ws.freeze_panes = f"A{data_start_row}"
    filter_last_row = (total_row - 1) if total_row else max(data_start_row, table_header_row)
    ws.auto_filter.ref = f"A{table_header_row}:{get_column_letter(len(columns))}{filter_last_row}"

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def build_google_push_rows(rows, *, platform="tiktok"):
    spec = get_platform(platform)
    parse_date = report_date_parser()
    normalized_rows = []
    max_partner_columns = 1
    for row in rows:
        partner_names = row.get("partners", [])
        if not isinstance(partner_names, list):
            partner_names = []
        partner_names = [clean_text(name) for name in partner_names if clean_text(name)]
        max_partner_columns = max(max_partner_columns, len(partner_names))
        normalized_rows.append((row, partner_names))

    headers = ["Stt", "Ngày", "Link", "Tên Kênh", *spec.metric_columns]
    headers.extend(["Đối tác" if index == 0 else f"Đối tác {index + 1}" for index in range(max_partner_columns)])
    headers.append(LAST_UPDATE_COLUMN)

    def metric_cell(row, metric):
        # Unknown metrics stay blank in Google too; only confirmed numbers are written.
        value = format_metric(row.get(metric, ""))
        return "" if value is None else value

    values = [headers]
    for index, (row, partner_names) in enumerate(normalized_rows, start=1):
        padded_partners = partner_names + [""] * (max_partner_columns - len(partner_names))
        channel_name = clean_text(row.get("TÊN KÊNH", ""))
        if is_failed_channel_name(channel_name):
            channel_name = "Lỗi"
        values.append([
            index,
            spreadsheet_date_text(row.get("NGÀY AIR", ""), parse_date),
            spreadsheet_hyperlink(row.get("LINK AIR", "")),
            channel_name,
            *[metric_cell(row, metric) for metric in spec.metric_columns],
            *padded_partners,
            clean_text(row.get(LAST_UPDATE_COLUMN, "")),
        ])
    if len(values) > 1:
        data_last_row = len(values)
        total_row = ["", "", "TỔNG", ""]
        for offset in range(len(spec.metric_columns)):
            column_index = 5 + offset
            column_name = excel_column_name(column_index)
            metric_values = [row[column_index - 1] for row in values[1:]]
            # Same rule as the Excel report: sum the known values, blank only when none is known.
            known = metric_total(metric_values) != ""
            total_row.append(f"=SUM({column_name}2:{column_name}{data_last_row})" if known else "")
        total_row.extend([""] * (len(headers) - len(total_row)))
        values.append(total_row)
    return values


def build_export_payload(target_path, selected_partners, apply_min_views, min_views, requested_sheet_name="", platform="tiktok"):
    # One in-memory snapshot feeds discovery and every partner report in this export.
    # Atomic scan saves may replace the source while this function is running.
    with WorkbookSnapshot(target_path) as snapshot:
        return _build_export_payload(snapshot, selected_partners, apply_min_views, min_views, requested_sheet_name, platform)


def _build_export_payload(snapshot, selected_partners, apply_min_views, min_views, requested_sheet_name="", platform="tiktok"):
    spec = get_platform(platform)
    data_sheets = find_data_sheet_names(snapshot)
    report_sheet = clean_text(requested_sheet_name) or default_data_sheet(data_sheets)
    if report_sheet and report_sheet not in data_sheets:
        raise ValueError(f"Sheet {report_sheet} không tồn tại trong file.")
    partners = select_export_partners(
        list_workbook_partners(snapshot, sheet_name=report_sheet, platform=spec.key), selected_partners,
    )
    export_timestamp = format_filename_datetime()
    # The sheet is read once for all partners, not once per partner.
    rows_by_partner = build_partner_report_rows(snapshot, sheet_name=report_sheet, platform=spec.key)
    return render_export_payload(spec, partners, rows_by_partner, report_sheet, apply_min_views, min_views, export_timestamp)


def select_export_partners(available_names, selected_partners):
    """The selected partners that exist, in their available spelling, without duplicates."""
    available_partners = {partner_dedup_key(name): name for name in available_names}
    partners = list(dict.fromkeys(
        available_partners[partner_dedup_key(name)]
        for name in selected_partners
        if clean_text(name) and partner_dedup_key(name) in available_partners
    ))
    if not partners:
        raise ValueError("Không tìm thấy đối tác đã chọn trong file")
    return partners


def build_export_payload_from_rows(sheet_rows, selected_partners, apply_min_views, min_views, report_sheet, platform="tiktok"):
    """build_export_payload for rows that are already read (riviu.store, e.g. one Postgres month).

    sheet_rows: a SheetReportRows; report_sheet labels the file names like a sheet name.
    """
    spec = get_platform(platform)
    available = [item["name"] for item in partner_link_counts(sheet_rows.partners, sheet_rows.rows, platform=spec.key)]
    partners = select_export_partners(available, selected_partners)
    export_timestamp = format_filename_datetime()
    rows_by_partner = group_rows_by_partner(sheet_rows.rows)
    return render_export_payload(spec, partners, rows_by_partner, report_sheet, apply_min_views, min_views, export_timestamp)


def build_poster_export_payload_from_rows(sheet_rows, selected_posters, apply_min_views, min_views, report_sheet, platform="tiktok"):
    """Like build_export_payload_from_rows, one report per Người đăng (rows need `_nguoiAir`)."""
    spec = get_platform(platform)
    groups = group_rows_by_poster(sheet_rows.rows)
    wanted = {clean_text(name) for name in selected_posters if clean_text(name)}
    posters = sorted((name for name in groups if name in wanted), key=poster_sort_key)
    if not posters:
        raise ValueError("Không tìm thấy người đăng đã chọn trong tháng này")
    return render_export_payload(
        spec, posters, groups, report_sheet, apply_min_views, min_views, format_filename_datetime(), subject=SUBJECT_POSTER,
    )


def render_export_payload(
    spec, partners, rows_by_partner, report_sheet, apply_min_views, min_views, export_timestamp, subject=SUBJECT_PARTNER,
):
    """One .xlsx (one partner) or a .zip of partner reports, from rows grouped by partner key.

    With subject=SUBJECT_POSTER the names are Người đăng and `rows_by_partner` is keyed by them.
    """
    sheet_tag = safe_report_name(report_sheet) if report_sheet else ""
    name_tags = [tag for tag in (spec.file_tag, sheet_tag, export_timestamp) if tag]
    # Partners share rows and air dates, so every report in this export shares one date memo.
    parse_date = report_date_parser()

    def report_bytes(partner):
        key = partner if subject == SUBJECT_POSTER else partner_dedup_key(partner)
        return build_partner_report(
            partner, rows_by_partner.get(key, []), apply_min_views=apply_min_views, min_views=min_views,
            platform=spec.key, parse_date=parse_date, subject=subject,
        )

    if len(partners) == 1:
        partner = partners[0]
        return {
            "content": report_bytes(partner),
            "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": f"{' '.join([safe_report_name(partner), *name_tags])}.xlsx",
        }

    archive = io.BytesIO()
    used_names = set()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for partner in partners:
            base_name = " ".join([safe_report_name(partner), *name_tags])
            filename = f"{base_name}.xlsx"
            counter = 2
            while filename in used_names:
                filename = f"{base_name}_{counter}.xlsx"
                counter += 1
            used_names.add(filename)
            zip_file.writestr(filename, report_bytes(partner))
    return {
        "content": archive.getvalue(),
        "media_type": "application/zip",
        "filename": f"{' '.join(['bao_cao_nguoi_dang' if subject == SUBJECT_POSTER else 'bao_cao_doi_tac', *name_tags])}.zip",
    }
