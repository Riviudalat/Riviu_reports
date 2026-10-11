"""Column lookup shared by pandas frames and openpyxl worksheets.

Scanners, previews and reports must find the same column for the same header, so both kinds of
sheet go through the same header-list helpers here.
"""

from openpyxl.utils import get_column_letter

from riviu.workbook.constants import COLUMN_ALIASES, LAST_UPDATE_COLUMN, TTBD_INTERNAL_HEADERS
from riviu.workbook.links import is_post_url
from riviu.workbook.text import normalize_header, normalize_key


LINK_COLUMN_SAMPLE_ROWS = 200
# "Link kênh"/"Profile URL" point at an account, never at the post a scan or report needs.
NON_POST_LINK_HEADER_MARKERS = ("kenh", "channel", "profile", "tai khoan")


def set_cell_literal(cell, value):
    """Write untrusted input literally, without altering its displayed text.

    Generated formulas must use normal cell assignment instead of this helper.
    """
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"
    return cell


def find_header_position(headers, aliases):
    """0-based position of the first header matching an alias (case/diacritic-insensitive)."""
    alias_set = {normalize_key(alias) for alias in aliases}
    for position, header in enumerate(headers):
        if normalize_key(header) in alias_set:
            return position
    return None


def partner_column_positions(headers):
    """0-based positions of the "Đối tác" column and the blank/"Unnamed" columns right after it."""
    start = None
    for position, header in enumerate(headers):
        if normalize_key(header).startswith("doi tac"):
            start = position
            break
    if start is None:
        return []
    positions = [start]
    for position in range(start + 1, len(headers)):
        header = headers[position]
        raw_key = normalize_header(header)
        if normalize_key(header).startswith("doi tac") or not raw_key or raw_key.startswith("unnamed:"):
            positions.append(position)
        else:
            break
    return positions


def pick_link_column(headers, sample_values):
    """Index of the post-link column among headers matched by substring ('link'/'url').

    `sample_values(index)` yields cell values of that column. Account/profile link columns are
    skipped unless they are the only ones holding post URLs; a column with post URLs wins.
    """
    internal_keys = {normalize_key(header) for header in TTBD_INTERNAL_HEADERS}
    post_candidates = []
    account_candidates = []
    for index, header in enumerate(headers):
        key = normalize_key(header)
        if key in internal_keys or not ("link" in key or "url" in key):
            continue
        if any(marker in key for marker in NON_POST_LINK_HEADER_MARKERS):
            account_candidates.append(index)
        else:
            post_candidates.append(index)
    for index in post_candidates + account_candidates:
        if any(is_post_url(value) for value in sample_values(index)):
            return index
    return post_candidates[0] if post_candidates else None


# pandas frames: columns are returned by name.

def find_column_name(frame, aliases):
    columns = list(frame.columns)
    position = find_header_position(columns, aliases)
    return None if position is None else columns[position]


def find_link_column_name(frame):
    column = find_column_name(frame, COLUMN_ALIASES["link"])
    if column:
        return column
    columns = list(frame.columns)
    index = pick_link_column(
        columns,
        lambda position: frame.iloc[:LINK_COLUMN_SAMPLE_ROWS, position].tolist(),
    )
    return None if index is None else columns[index]


def dataframe_partner_columns(frame):
    columns = list(frame.columns)
    return [columns[position] for position in partner_column_positions(columns)]


# openpyxl worksheets: columns are returned as 1-based indexes.

def worksheet_headers(worksheet):
    max_column = worksheet.max_column or 0
    if max_column < 1:
        return []
    return [worksheet.cell(row=1, column=index).value for index in range(1, max_column + 1)]


def worksheet_find_column_index(worksheet, aliases):
    position = find_header_position(worksheet_headers(worksheet), aliases)
    return None if position is None else position + 1


def worksheet_ensure_column(worksheet, header, aliases=None, *, hidden=False):
    """Find a column the same way previews/reports do, appending it only when truly absent."""
    index = worksheet_find_column_index(worksheet, aliases or COLUMN_ALIASES.get(header) or (header,))
    if index is None:
        index = (worksheet.max_column or 0) + 1
        worksheet.cell(row=1, column=index).value = header
    if hidden:
        worksheet.column_dimensions[get_column_letter(index)].hidden = True
    return index


def worksheet_find_link_column_index(worksheet):
    column_index = worksheet_find_column_index(worksheet, COLUMN_ALIASES["link"])
    if column_index:
        return column_index

    last_row = min(worksheet.max_row or 0, LINK_COLUMN_SAMPLE_ROWS + 1)
    index = pick_link_column(
        worksheet_headers(worksheet),
        lambda position: (worksheet.cell(row=row, column=position + 1).value for row in range(2, last_row + 1)),
    )
    return None if index is None else index + 1


def worksheet_find_last_update_column_index(worksheet):
    return worksheet_find_column_index(worksheet, COLUMN_ALIASES[LAST_UPDATE_COLUMN])


def worksheet_partner_column_indexes(worksheet):
    return [position + 1 for position in partner_column_positions(worksheet_headers(worksheet))]
