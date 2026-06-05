import pandas as pd

from workbook_utils import (
    dataframe_partner_columns,
    fetch_google_spreadsheet_title,
    google_sheet_file_id_from_title,
    format_display_datetime,
    format_filename_datetime,
    google_sheet_filename_to_label,
    google_sheet_sync_label,
    parse_filename_datetime_stamp,
    result_sheet_display_name,
    is_exportable_report_row,
    display_channel_name_from_file,
    is_failed_channel_name,
    is_generated_username_channel,
    is_scrapable_tiktok_url,
    is_summary_sheet_name,
    metric_number,
    normalize_tiktok_url,
    build_summary_totals_row,
    rebuild_summary_sheet,
    safe_join,
    safe_workbook_filename,
    split_partner_value,
    summary_sheet_title_for_data_sheet,
    to_number,
)


def test_metric_number_handles_thousand_separators():
    assert metric_number("1.234.567") == 1234567
    assert metric_number("1,234,567") == 1234567
    assert metric_number("150") == 150
    assert metric_number("") == 0
    assert metric_number(None) == 0


def test_to_number_delegates_to_metric_number():
    assert to_number("2.500") == 2500


def test_split_partner_value_multiline():
    raw = "Partner A\nPartner B"
    assert split_partner_value(raw) == ["Partner A", "Partner B"]


def test_dataframe_partner_columns_detects_partner_block():
    frame = pd.DataFrame(
        {
            "Đối tác": ["A"],
            "Đối tác 2": ["B"],
            "LINK AIR": ["https://www.tiktok.com/@x/video/1"],
        }
    )
    columns = dataframe_partner_columns(frame)
    assert columns == ["Đối tác", "Đối tác 2"]


def test_is_failed_channel_name():
    assert is_failed_channel_name("") is True
    assert is_failed_channel_name("Lỗi") is True
    assert is_failed_channel_name("Error: timeout") is True
    assert is_failed_channel_name("Nice Cafe") is False
    assert is_failed_channel_name("Screen time breaks") is True


def test_display_channel_name_maps_tiktok_ui_garbage_to_loi():
    assert display_channel_name_from_file("https://www.tiktok.com/@a/video/1", "Screen time breaks") == "Lỗi"


def test_is_generated_username_channel():
    assert is_generated_username_channel("user1234567890") is True
    assert is_generated_username_channel("Nice Cafe") is False


def test_is_exportable_report_row_respects_min_views():
    row = {"TÊN KÊNH": "Cafe", "LƯỢT XEM": 50}
    assert is_exportable_report_row(row, apply_min_views=True, min_views=100) is False
    assert is_exportable_report_row(row, apply_min_views=False, min_views=100) is True


def test_google_sheet_file_id_uses_spreadsheet_title(monkeypatch):
    monkeypatch.setattr(
        "workbook_utils.fetch_google_spreadsheet_title",
        lambda _url: "Report Seeding Tiktok 2026",
    )
    file_id = google_sheet_file_id_from_title("Report Seeding Tiktok 2026", "05-06-2026-10-42")
    assert file_id == "data/Report Seeding Tiktok 2026 05-06-2026-10-42.xlsx"


def test_google_sheet_sync_label_includes_display_timestamp():
    label = google_sheet_sync_label("Report Seeding Tiktok 2026", "03/06/2026-14:30")
    assert label == "Report Seeding Tiktok 2026 03/06/2026-14:30"


def test_datetime_formats_are_consistent():
    moment = __import__("datetime").datetime(2026, 6, 3, 14, 30)
    assert format_display_datetime(moment) == "03/06/2026-14:30"
    assert format_filename_datetime(moment) == "03-06-2026-14-30"
    assert parse_filename_datetime_stamp("03-06-2026-14-30") == "03/06/2026-14:30"


def test_result_sheet_display_name_uses_excel_safe_timestamp():
    name = result_sheet_display_name("03-06-2026-14-30")
    assert name.startswith("Report Seeding Tiktok 03-06-")
    assert len(name) <= 31


def test_google_sheet_filename_to_label():
    assert (
        google_sheet_filename_to_label("Report Seeding Tiktok 2026 05-06-2026-10-42.xlsx")
        == "Report Seeding Tiktok 2026 05/06/2026-10:42"
    )
    assert (
        google_sheet_filename_to_label("Report Seeding Tiktok 2026-05-06-2026-10-42.xlsx")
        == "Report Seeding Tiktok 2026 05/06/2026-10:42"
    )


def test_fetch_google_spreadsheet_title_from_html(monkeypatch):
    class FakeResponse:
        def read(self):
            return b"<html><title>Report Seeding Tiktok 2026 - Google Sheets</title></html>"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse())
    title = fetch_google_spreadsheet_title("https://docs.google.com/spreadsheets/d/abc123/edit")
    assert title == "Report Seeding Tiktok 2026"


def test_safe_workbook_filename():
    assert safe_workbook_filename('File:name?') == "File-name-"


def test_summary_sheet_title_for_data_sheet():
    assert summary_sheet_title_for_data_sheet("Tháng 6") == "Tổng kết tháng 6"
    assert summary_sheet_title_for_data_sheet("Tháng 5") == "Tổng kết tháng 5"
    assert is_summary_sheet_name("Tổng kết tháng 6") is True
    assert is_summary_sheet_name("Tháng 6") is False


def test_rebuild_summary_sheet_only_one_data_sheet(tmp_path):
    import openpyxl

    file_path = tmp_path / "multi.xlsx"
    wb = openpyxl.Workbook()
    may = wb.active
    may.title = "Tháng 5"
    may.append(["LINK AIR", "Đối tác", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"])
    may.append(["https://www.tiktok.com/@a/video/1", "Partner May", 10, 1, 0, 0, 0])
    june = wb.create_sheet("Tháng 6")
    june.append(["LINK AIR", "Đối tác", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"])
    june.append(["https://www.tiktok.com/@b/video/2", "Partner June", 100, 2, 0, 0, 0])
    wb.save(file_path)
    wb.close()

    wb = openpyxl.load_workbook(file_path)
    count = rebuild_summary_sheet(wb, data_sheet_name="Tháng 6")
    assert count == 1
    assert "Tổng kết tháng 6" in wb.sheetnames
    summary = wb["Tổng kết tháng 6"]
    partner_names = [summary.cell(row=r, column=2).value for r in range(2, summary.max_row + 1)]
    assert partner_names == ["Partner June"]
    assert wb.sheetnames.index("Tổng kết tháng 6") == wb.sheetnames.index("Tháng 6") + 1
    wb.close()


def test_build_summary_totals_row_sums_partner_metrics():
    rows = [
        {
            "TỔNG LINK": 2,
            "TỔNG LƯỢT XEM": 100,
            "TỔNG TIM": 10,
            "TỔNG BÌNH LUẬN": 3,
            "TỔNG LƯỢT LƯU": 4,
            "TỔNG CHIA SẺ": 5,
        },
        {
            "TỔNG LINK": 1,
            "TỔNG LƯỢT XEM": 50,
            "TỔNG TIM": 5,
            "TỔNG BÌNH LUẬN": 1,
            "TỔNG LƯỢT LƯU": 2,
            "TỔNG CHIA SẺ": 1,
        },
    ]
    totals = build_summary_totals_row(rows)
    assert totals["ĐỐI TÁC"] == "TỔNG"
    assert totals["TỔNG LINK"] == 3
    assert totals["TỔNG LƯỢT XEM"] == 150
    assert totals["TỔNG TIM"] == 15
    assert totals["TỔNG CHIA SẺ"] == 6


def test_build_summary_totals_row_aligned_uses_actual_column_names():
    from workbook_utils import build_summary_totals_row_aligned

    columns = ["Stt", "Đối tác", "Tổng link", "Tổng lượt xem", "Tổng tim", "Cập nhật lần cuối"]
    rows = [
        {"Stt": 1, "Đối tác": "A", "Tổng link": 2, "Tổng lượt xem": 100, "Tổng tim": 10, "Cập nhật lần cuối": ""},
        {"Stt": 2, "Đối tác": "B", "Tổng link": 1, "Tổng lượt xem": 50, "Tổng tim": 5, "Cập nhật lần cuối": ""},
    ]
    aligned = build_summary_totals_row_aligned(columns, rows)
    assert aligned["Đối tác"] == "TỔNG"
    assert aligned["Tổng link"] == 3
    assert aligned["Tổng lượt xem"] == 150
    assert aligned["Tổng tim"] == 15


def test_is_scrapable_tiktok_url():
    assert is_scrapable_tiktok_url("tiktok.com/@a/video/1") is True
    assert is_scrapable_tiktok_url("https://www.tiktok.com/@a/video/1") is True
    assert is_scrapable_tiktok_url("TỔNG") is False
    assert is_scrapable_tiktok_url("") is False


def test_normalize_tiktok_url_adds_https():
    raw = "tiktok.com/@ngc.ngc.i.chill/photo/7646786750978854152"
    assert normalize_tiktok_url(raw) == "https://tiktok.com/@ngc.ngc.i.chill/photo/7646786750978854152"
    assert normalize_tiktok_url("https://www.tiktok.com/@a/video/1") == "https://www.tiktok.com/@a/video/1"
    assert normalize_tiktok_url("//www.tiktok.com/@a/video/1") == "https://www.tiktok.com/@a/video/1"


def test_safe_join_blocks_traversal(tmp_path):
    base = tmp_path / "root"
    base.mkdir()
    allowed = safe_join(str(base), "data/file.xlsx")
    assert allowed.endswith("file.xlsx")
    assert safe_join(str(base), "../outside.xlsx") == ""
    assert safe_join(str(base), "../../etc/passwd") == ""
