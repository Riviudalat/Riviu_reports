"""Report rows per partner, read once per sheet version and shared through the snapshot cache."""

from dataclasses import dataclass

from riviu.workbook.channels import is_failed_channel_name
from riviu.workbook.columns import dataframe_partner_columns, find_column_name, find_link_column_name
from riviu.workbook.constants import (
    ALL_METRIC_COLUMNS,
    COLUMN_ALIASES,
    LAST_UPDATE_COLUMN,
    SCAN_STATUS_ALIASES,
    TTBD_RESOLVED_URL_HEADER,
    TTBD_SOURCE_URL_HEADER,
)
from riviu.workbook.links import is_threads_link, is_tiktok_link
from riviu.workbook.partners import partner_dedup_key, row_partners_from_values
from riviu.workbook.platforms import get_platform
from riviu.workbook.snapshot import open_workbook_snapshot
from riviu.workbook.text import clean_text, is_total_label, metric_number


def fill_missing_dates_from_previous(frame, date_column, link_column):
    if not date_column or date_column not in frame.columns:
        return frame

    date_position = frame.columns.get_loc(date_column)
    links = frame[link_column].tolist() if link_column else [""] * len(frame.index)
    last_date = ""
    for position, (raw_date, raw_link) in enumerate(zip(frame[date_column].tolist(), links)):
        cleaned_date = clean_text(raw_date)
        link = clean_text(raw_link)

        if not (is_tiktok_link(link) or is_threads_link(link)):
            # Totals, section headings and empty separators delimit date groups.
            last_date = ""
            continue
        if cleaned_date:
            last_date = raw_date
            continue
        if clean_text(last_date):
            frame.iat[position, date_position] = last_date
    return frame


@dataclass(frozen=True)
class SheetReportRows:
    """One sheet read for every partner at once (cached; treat as read-only)."""

    rows: tuple  # report rows holding a link of the platform, in sheet order
    partners: tuple  # every partner named on the sheet, first spelling seen, in sheet order


def sheet_report_rows(snapshot, sheet_name, platform="tiktok"):
    spec = get_platform(platform)
    return snapshot.cached(
        ("rows", sheet_name, spec.key),
        lambda: _build_sheet_report_rows(snapshot.frame(sheet_name), sheet_name, spec),
    )


def _build_sheet_report_rows(frame, sheet_name, spec):
    partner_columns = dataframe_partner_columns(frame)
    date_column = find_column_name(frame, COLUMN_ALIASES["date"])
    channel_column = find_column_name(frame, COLUMN_ALIASES["TÊN KÊNH"])
    link_column = find_link_column_name(frame)
    last_update_column = find_column_name(frame, COLUMN_ALIASES[LAST_UPDATE_COLUMN])
    metric_columns = {metric: find_column_name(frame, [metric]) for metric in ALL_METRIC_COLUMNS}
    internal_columns = {
        "_scanStatus": find_column_name(frame, SCAN_STATUS_ALIASES),
        "_resolvedUrl": find_column_name(frame, [TTBD_RESOLVED_URL_HEADER]),
        "_resolvedSourceUrl": find_column_name(frame, [TTBD_SOURCE_URL_HEADER]),
    }
    if link_column:
        frame = fill_missing_dates_from_previous(frame, date_column, link_column)
    positions = {column: index for index, column in enumerate(frame.columns)}
    partner_positions = [positions[column] for column in partner_columns]
    link_position = positions[link_column] if link_column else None

    def position(column):
        return positions[column] if column else None

    date_position = position(date_column)
    channel_position = position(channel_column)
    last_update_position = position(last_update_column)
    metric_positions = {metric: position(column) for metric, column in metric_columns.items()}
    internal_positions = {key: position(column) for key, column in internal_columns.items()}

    partner_names = {}
    rows = []
    # frame.values holds the same cell objects iterrows() yields, without building a Series per row.
    for record in frame.values:
        def cell(index):
            return record[index] if index is not None else ""

        partners = row_partners_from_values(record[index] for index in partner_positions) if partner_positions else []
        for partner in partners:
            partner_names.setdefault(partner_dedup_key(partner), partner)
        if link_position is None:
            continue
        link = spec.normalize_url(record[link_position])
        if not link or is_total_label(link) or not spec.is_link(link):
            continue
        rows.append({
            "sheet_name": sheet_name,
            "NGÀY AIR": cell(date_position),
            "TÊN KÊNH": spec.display_channel(link, clean_text(cell(channel_position))),
            "LINK AIR": link,
            **{metric: cell(index) for metric, index in metric_positions.items()},
            LAST_UPDATE_COLUMN: clean_text(cell(last_update_position)),
            "partners": partners,
            **{key: clean_text(cell(index)) for key, index in internal_positions.items()},
        })
    return SheetReportRows(rows=tuple(rows), partners=tuple(partner_names.values()))


def _shared_report_rows(snapshot, sheet_name, spec):
    """Cached rows of the requested sheet (any sheet) or of every data sheet; do not modify."""
    requested_sheet = clean_text(sheet_name)
    if requested_sheet:
        sheets = [requested_sheet] if requested_sheet in snapshot.sheet_names else []
    else:
        sheets = snapshot.data_sheet_names
    rows = []
    for current_sheet in sheets:
        rows.extend(sheet_report_rows(snapshot, current_sheet, spec.key).rows)
    return rows


def copy_report_row(row):
    return {**row, "partners": list(row["partners"])}


def build_workbook_rows(file_path, selected_partner=None, sheet_name=None, *, platform="tiktok"):
    spec = get_platform(platform)
    with open_workbook_snapshot(file_path) as snapshot:
        rows = _shared_report_rows(snapshot, sheet_name, spec)
    if selected_partner:
        selected_key = selected_partner.casefold()
        rows = [row for row in rows if selected_key in {partner.casefold() for partner in row["partners"]}]
    return [copy_report_row(row) for row in rows]


def build_partner_report_rows(file_path, sheet_name=None, *, platform="tiktok"):
    """{partner_dedup_key: rows} for every partner, from one read of the sheet.

    Same rows, in the same order, as build_workbook_rows(selected_partner=...) per partner.
    """
    spec = get_platform(platform)
    groups = {}
    with open_workbook_snapshot(file_path) as snapshot:
        rows = _shared_report_rows(snapshot, sheet_name, spec)
    for row in rows:
        for partner in row["partners"]:
            groups.setdefault(partner_dedup_key(partner), []).append(row)
    return {key: [copy_report_row(row) for row in group] for key, group in groups.items()}


def is_exportable_report_row(row, *, apply_min_views=True, min_views=100):
    """Every platform reports the same rows: a readable channel and, when enabled, enough views."""
    if is_failed_channel_name(row.get("TÊN KÊNH", "")):
        return False
    if apply_min_views:
        threshold = max(int(min_views or 0), 0)
        if metric_number(row.get("LƯỢT XEM", 0)) < threshold:
            return False
    return True


def list_workbook_partners_with_link_counts(
    file_path,
    sheet_name=None,
    *,
    apply_min_views=True,
    min_views=100,
    platform="tiktok",
):
    spec = get_platform(platform)
    partner_stats = {}
    with open_workbook_snapshot(file_path) as snapshot:
        data_sheets = snapshot.data_sheet_names
        requested_sheet = clean_text(sheet_name)
        if requested_sheet:
            data_sheets = [requested_sheet] if requested_sheet in data_sheets else []
        for current_sheet in data_sheets:
            for partner in sheet_report_rows(snapshot, current_sheet, spec.key).partners:
                key = partner_dedup_key(partner)
                if key not in partner_stats:
                    partner_stats[key] = {"name": partner, "linkCount": 0, "rawLinkCount": 0}
        if not partner_stats:
            return []
        rows = _shared_report_rows(snapshot, sheet_name, spec)

    for row in rows:
        row_partners = row.get("partners") or []
        exportable = bool(row_partners) and is_exportable_report_row(row, apply_min_views=apply_min_views, min_views=min_views)
        for partner in row_partners:
            key = partner_dedup_key(partner)
            if key not in partner_stats:
                continue
            partner_stats[key]["rawLinkCount"] += 1
            if exportable:
                partner_stats[key]["linkCount"] += 1

    values = partner_stats.values()
    if not spec.lists_partners_without_links:
        values = [item for item in values if item["rawLinkCount"] > 0]
    return sorted(values, key=lambda item: item["name"].casefold())


def list_workbook_partners(file_path, sheet_name=None, *, platform="tiktok"):
    return [item["name"] for item in list_workbook_partners_with_link_counts(file_path, sheet_name=sheet_name, platform=platform)]
