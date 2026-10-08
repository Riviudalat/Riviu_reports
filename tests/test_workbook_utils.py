import pandas as pd

from workbook_utils import (
    dataframe_partner_columns,
    fetch_google_spreadsheet_title,
    find_link_column_name,
    google_sheet_file_id_from_title,
    format_display_datetime,
    format_excel_sheet_datetime,
    format_filename_datetime,
    google_sheet_filename_to_label,
    google_sheet_sync_label,
    highlight_single_partner_link_rows,
    highlight_video_link_rows,
    is_internal_workbook_filename,
    parse_filename_datetime_stamp,
    result_sheet_display_name,
    is_exportable_report_row,
    display_channel_name_from_file,
    is_failed_channel_name,
    is_generated_username_channel,
    is_scrapable_tiktok_url,
    is_result_sheet_name,
    is_result_sheet_timestamp_name,
    is_summary_sheet_name,
    metric_number,
    normalize_tiktok_url,
    build_workbook_rows,
    read_sheet_preview,
    rebuild_summary_sheet,
    safe_join,
    safe_workbook_filename,
    SINGLE_LINK_FILL_COLOR,
    VIDEO_LINK_FILL_COLOR,
    TIKTOK_MEDIA_PHOTO,
    TIKTOK_MEDIA_VIDEO,
    detect_tiktok_media_type,
    is_tiktok_video_link,
    month_label_for_sheet_name,
    should_highlight_video_link,
    split_partner_value,
    summary_sheet_title_for_data_sheet,

    workbook_file_entries,
    worksheet_find_link_column_index,
)


def test_metric_number_handles_thousand_separators():
    assert metric_number("1.234.567") == 1234567
    assert metric_number("1,234,567") == 1234567
    assert metric_number("150") == 150
    assert metric_number("") == 0
    assert metric_number(None) == 0


def test_link_column_fallback_ignores_internal_metadata_headers():
    import openpyxl

    frame = pd.DataFrame({"Nội dung": ["row"], "__TTBD_SOURCE_URL": ["https://vt.tiktok.com/ZSold/"]})
    assert find_link_column_name(frame) is None

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Nội dung", "__TTBD_RESOLVED_URL", "__TTBD_SOURCE_URL"])
    assert worksheet_find_link_column_index(sheet) is None
    workbook.close()


def test_link_column_fallback_skips_channel_links_and_prefers_post_urls():
    import openpyxl

    headers = ["Link kênh", "Link tham khảo", "Link bài đăng"]
    rows = [
        ["https://www.tiktok.com/@shop", "https://example.com/brief", "https://www.tiktok.com/@shop/video/1"],
        ["https://www.threads.com/@cafe", "", "https://www.threads.com/@cafe/post/ABC"],
    ]
    frame = pd.DataFrame(rows, columns=headers)
    assert find_link_column_name(frame) == "Link bài đăng"

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    assert worksheet_find_link_column_index(sheet) == 3

    # No sample post URL anywhere: the first non-account link column still wins over "Link kênh".
    assert find_link_column_name(pd.DataFrame({"Link kênh": [""], "Video URL": [""]})) == "Video URL"
    workbook.close()


def test_metric_number_reads_dot_thousands_separator():
    assert metric_number("2.500") == 2500


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
    assert is_failed_channel_name("1.0") is True
    assert is_failed_channel_name("1") is True
    assert is_failed_channel_name(1.0) is True


def test_display_channel_name_maps_tiktok_ui_garbage_to_loi():
    assert display_channel_name_from_file("https://www.tiktok.com/@a/video/1", "Screen time breaks") == "Lỗi"


def test_display_channel_name_maps_numeric_garbage_to_loi():
    link = "https://www.tiktok.com/@a/video/1"
    assert display_channel_name_from_file(link, "1.0") == "Lỗi"
    assert display_channel_name_from_file(link, 1.0) == "Lỗi"
    assert display_channel_name_from_file(link, "") == ""
    assert display_channel_name_from_file(link, "Nice Cafe") == "Nice Cafe"


def test_is_generated_username_channel():
    assert is_generated_username_channel("user1234567890") is True
    assert is_generated_username_channel("Nice Cafe") is False


def test_is_exportable_report_row_respects_min_views():
    row = {"TÊN KÊNH": "Cafe", "LƯỢT XEM": 50}
    assert is_exportable_report_row(row, apply_min_views=True, min_views=100) is False
    assert is_exportable_report_row(row, apply_min_views=False, min_views=100) is True


def test_threads_rows_use_url_username_for_blank_channel_and_count_like_tiktok(tmp_path):
    import openpyxl
    from workbook_utils import list_workbook_partners_with_link_counts

    path = tmp_path / "threads.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Data"
    sheet.append(["Link", "Tên Kênh", "LƯỢT XEM", "TIM", "Đối tác"])
    sheet.append(["https://www.threads.com/@miri_viu/post/AAA", "", 500, 3, "Cafe A"])
    sheet.append(["https://www.threads.com/@cafe.saigon/post/BBB", "cafe.saigon", 0, 0, "Cafe A"])
    sheet.append(["https://www.threads.com/@an_123/post/CCC", "", "", "", "Cafe A"])
    book.save(path)

    rows = build_workbook_rows(path, platform="threads")
    assert [row["TÊN KÊNH"] for row in rows] == ["@miri_viu", "cafe.saigon", "@an_123"]
    assert all("TRẠNG THÁI" not in row for row in rows)
    assert not any(is_failed_channel_name(row["TÊN KÊNH"]) for row in rows)

    def counts(**options):
        [partner] = list_workbook_partners_with_link_counts(path, platform="threads", **options)
        return partner["linkCount"], partner["rawLinkCount"]

    # Same rule as TikTok: 0/unknown views fall under the min-views threshold unless it is off.
    assert counts(apply_min_views=True, min_views=100) == (1, 3)
    assert counts(apply_min_views=False) == (3, 3)


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
    assert format_excel_sheet_datetime(moment) == "03-06-2026-14-30"
    assert parse_filename_datetime_stamp("03-06-2026-14-30") == "03/06/2026-14:30"


def test_result_sheet_display_name_uses_excel_safe_timestamp():
    name = result_sheet_display_name("17-06-2026-14:44")
    assert name == "17-06-2026-14-44"
    assert len(name) <= 31


def test_is_result_sheet_name_recognizes_timestamp_and_legacy_prefix():
    assert is_result_sheet_timestamp_name("17-06-2026-14:44") is True
    assert is_result_sheet_timestamp_name("17-06-2026-14:44-2") is True
    assert is_result_sheet_timestamp_name("03-06-2026-14-30") is True
    assert is_result_sheet_timestamp_name("03-06-2026-14-30-2") is True
    assert is_result_sheet_timestamp_name("Tháng 6") is False
    assert is_result_sheet_name("03-06-2026-14-30") is True
    assert is_result_sheet_name("Report Seeding Tiktok 03-06-2026-14-30") is True
    assert is_result_sheet_name("Tháng 6") is False


def test_is_result_sheet_name_recognizes_month_prefixed_timestamp():
    # Google Sheets strips ":" from tab titles on xlsx export, so the pushed
    # "T6 <timestamp>" title can come back with the time squished together.
    assert is_result_sheet_name("T6 06-08-2026-14:30") is True
    assert is_result_sheet_name("T6 06-08-2026-1430") is True
    assert is_result_sheet_name("T12 06-08-2026-1430-2") is True
    assert is_result_sheet_name("Tháng 6") is False


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
    assert summary_sheet_title_for_data_sheet("Tháng 6", "threads") == "Tổng kết Threads tháng 6"
    long_name = "Danh sách seeding tháng 6 năm 2026"
    assert len(summary_sheet_title_for_data_sheet(long_name, "threads")) <= 31
    assert summary_sheet_title_for_data_sheet(long_name, "threads") != summary_sheet_title_for_data_sheet(long_name)
    assert is_summary_sheet_name("Tổng kết tháng 6") is True
    assert is_summary_sheet_name("Tổng kết Threads tháng 6") is True
    assert is_summary_sheet_name("Tháng 6") is False


def test_month_label_for_sheet_name():
    assert month_label_for_sheet_name("Tháng 6") == "T6"
    assert month_label_for_sheet_name("Tháng 07") == "T7"
    assert month_label_for_sheet_name("thang12") == "T12"
    assert month_label_for_sheet_name("Tổng kết tháng 6") == "T6"
    assert month_label_for_sheet_name("Tháng 13") == ""
    assert month_label_for_sheet_name("") == ""
    assert month_label_for_sheet_name("Danh sách kênh") == ""


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


def test_summary_groups_partner_case_variants_and_orders_updates_by_date():
    import openpyxl

    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Data"
    sheet.append(["LINK AIR", "Đối tác", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ", "Cập nhật lần cuối"])
    sheet.append(["https://www.tiktok.com/@a/video/1", "Shop A", 10, 1, 0, 0, 0, "31/08/2026-23:00"])
    sheet.append(["https://www.tiktok.com/@b/video/2", "shop a", 5, 1, 0, 0, 0, "01/09/2026-08:00"])

    assert rebuild_summary_sheet(wb, data_sheet_name="Data") == 1
    summary = wb[wb.sheetnames[1]]
    headers = [cell.value for cell in summary[1]]
    row = {header: summary.cell(row=2, column=index).value for index, header in enumerate(headers, start=1)}
    assert row["TỔNG LINK"] == 2 and row["TỔNG LƯỢT XEM"] == 15
    # "01/09" is later than "31/08" even though it sorts first as text.
    assert row["Cập nhật lần cuối"] == "01/09/2026-08:00"


def test_summary_sheet_and_dashboard_keep_unknown_partner_metrics_blank(tmp_path):
    import openpyxl
    from workbook_utils import read_summary_dashboard

    path = tmp_path / "summary.xlsx"
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Data"
    sheet.append(["LINK AIR", "Đối tác", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"])
    sheet.append(["https://www.tiktok.com/@a/video/1", "Known", 10, 0, None, None, None])
    sheet.append(["https://www.tiktok.com/@b/video/2", "Known", None, None, None, None, None])
    sheet.append(["https://www.tiktok.com/@c/video/3", "Unknown", None, None, None, None, None])
    assert rebuild_summary_sheet(wb, data_sheet_name="Data") == 2
    summary = wb[summary_sheet_title_for_data_sheet("Data")]
    headers = [cell.value for cell in summary[1]]
    cells = {
        summary.cell(row=row, column=2).value: {header: summary.cell(row=row, column=index).value for index, header in enumerate(headers, start=1)}
        for row in range(2, summary.max_row + 1)
    }
    assert cells["Known"]["TỔNG LINK"] == 2
    assert (cells["Known"]["TỔNG LƯỢT XEM"], cells["Known"]["TỔNG TIM"], cells["Known"]["TỔNG BÌNH LUẬN"]) == (10, 0, None)
    assert cells["Unknown"]["TỔNG LINK"] == 1
    assert cells["Unknown"]["TỔNG LƯỢT XEM"] is None and cells["Unknown"]["TỔNG TIM"] is None
    wb.save(path)
    wb.close()

    dashboard = read_summary_dashboard(str(path), "Data")
    rows = {row["ĐỐI TÁC"]: row for row in dashboard["rows"]}
    assert rows["Known"]["TỔNG LƯỢT XEM"] == 10 and rows["Known"]["TỔNG TIM"] == 0
    assert rows["Known"]["TỔNG BÌNH LUẬN"] == ""
    assert rows["Unknown"]["TỔNG LƯỢT XEM"] == "" and rows["Unknown"]["TỔNG LINK"] == 1
    assert dashboard["totals"]["views"] == 10 and dashboard["totals"]["likes"] == 0
    assert dashboard["totals"]["comments"] == "" and dashboard["totals"]["links"] == 3


def test_mixed_sheet_keeps_one_summary_per_platform(tmp_path):
    """Each platform counts only its own links into its own tab; neither rebuild touches the other."""
    import openpyxl
    from workbook_utils import SUMMARY_COLUMNS, read_summary_dashboard

    path = tmp_path / "mixed.xlsx"
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Data"
    sheet.append(["LINK AIR", "Đối tác", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "REPOST", "CHIA SẺ"])
    sheet.append(["https://www.tiktok.com/@a/video/1", "Shop A", 100, 5, 1, 2, None, 3])
    sheet.append(["https://www.threads.com/@a/post/AbC1", "Shop A", 40, 4, 0, None, 1, None])
    sheet.append(["https://www.threads.com/@b/post/AbC2", "Shop A", None, 6, None, None, 2, None])
    sheet.append(["https://www.threads.com/@c/post/AbC3", "Shop B", None, None, None, None, None, None])
    wb.create_sheet("Other")

    assert rebuild_summary_sheet(wb, data_sheet_name="Data", platform="threads") == 2
    assert rebuild_summary_sheet(wb, data_sheet_name="Data") == 1
    assert rebuild_summary_sheet(wb, data_sheet_name="Data", platform="threads") == 2
    assert wb.sheetnames == ["Data", "Tổng kết data", "Tổng kết Threads data", "Other"]

    def table(title):
        worksheet = wb[title]
        headers = [cell.value for cell in worksheet[1]]
        rows = {
            worksheet.cell(row=row, column=2).value: dict(zip(headers, (cell.value for cell in worksheet[row])))
            for row in range(2, worksheet.max_row + 1)
        }
        return headers, rows

    tiktok_headers, tiktok = table("Tổng kết data")
    assert tiktok_headers == SUMMARY_COLUMNS and list(tiktok) == ["Shop A"]
    assert (tiktok["Shop A"]["TỔNG LINK"], tiktok["Shop A"]["TỔNG LƯỢT LƯU"]) == (1, 2)
    threads_headers, threads = table("Tổng kết Threads data")
    assert threads_headers == [
        "Stt", "ĐỐI TÁC", "TỔNG LINK", "TỔNG LƯỢT XEM", "TỔNG TIM",
        "TỔNG BÌNH LUẬN", "TỔNG REPOST", "TỔNG CHIA SẺ", "Cập nhật lần cuối",
    ]
    shop_a = threads["Shop A"]
    assert [shop_a[header] for header in threads_headers[2:8]] == [2, 40, 10, 0, 3, None]
    assert threads["Shop B"]["TỔNG LINK"] == 1 and threads["Shop B"]["TỔNG LƯỢT XEM"] is None
    wb.save(path)
    wb.close()

    threads_dashboard = read_summary_dashboard(str(path), "Data", platform="threads")
    assert (threads_dashboard["sheet"], threads_dashboard["dataSheet"]) == ("Tổng kết Threads data", "Data")
    assert threads_dashboard["totals"] == {
        "partners": 2, "links": 3, "views": 40, "likes": 10, "comments": 0, "reposts": 3, "shares": "",
    }
    assert read_summary_dashboard(str(path), "Data")["totals"] == {
        "partners": 1, "links": 1, "views": 100, "likes": 5, "comments": 1, "saves": 2, "shares": 3,
    }
    # A summary tab opened from the TikTok view is read with the platform that owns it.
    opened = read_summary_dashboard(str(path), "Tổng kết Threads data", platform="tiktok")
    assert (opened["dataSheet"], opened["totals"]) == ("Data", threads_dashboard["totals"])


def test_read_sheet_preview_hides_pandas_placeholder_headers(tmp_path):
    import json
    import openpyxl

    path = tmp_path / "headers.xlsx"
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Data"
    sheet.append(["LINK AIR", None, "Ghi chú", "Ghi chú", None])
    sheet.append(["https://www.tiktok.com/@a/video/1", None, "một", "hai", "dữ liệu"])
    wb.save(path)
    wb.close()

    preview = read_sheet_preview(str(path), sheet_name="Data")
    assert preview["columns"] == ["LINK AIR", "Ghi chú", "Ghi chú (2)", "Cột E"]
    assert preview["data"][0]["Ghi chú"] == "một" and preview["data"][0]["Ghi chú (2)"] == "hai"
    assert preview["data"][0]["Cột E"] == "dữ liệu"
    assert "Unnamed" not in json.dumps(preview, ensure_ascii=False)


def test_shared_column_lookup_matches_unaccented_headers_instead_of_appending():
    import openpyxl
    from workbook_utils import worksheet_ensure_column

    sheet = openpyxl.Workbook().active
    sheet.append(["Link", "Luot xem", "Ngày cập nhật"])
    assert worksheet_ensure_column(sheet, "LƯỢT XEM") == 2
    assert worksheet_ensure_column(sheet, "Cập nhật lần cuối") == 3
    assert worksheet_ensure_column(sheet, "TIM") == 4 and sheet.cell(row=1, column=4).value == "TIM"


def test_atomic_save_temp_files_are_never_listed_as_workbooks(tmp_path):
    import openpyxl
    from workbook_utils import save_workbook_atomic, workbook_file_entries

    target = tmp_path / "Report.xlsx"
    save_workbook_atomic(openpyxl.Workbook(), target)
    (tmp_path / ".riviu-interrupted.xlsx").write_bytes(b"partial")
    assert [entry["id"] for entry in workbook_file_entries(str(tmp_path))] == ["Report.xlsx"]
    assert sorted(path.name for path in tmp_path.iterdir() if path.is_file()) == [".riviu-interrupted.xlsx", "Report.xlsx"]


def test_detect_tiktok_media_type_from_video_and_photo_urls():
    video_url = (
        "https://www.tiktok.com/@ngkhangg.008/video/7635258581851360520"
        "?is_from_webapp=1&web_id=7636006415740044807"
    )
    photo_url = (
        "https://www.tiktok.com/@baoquyen.dalat/photo/7635505950807346453"
        "?image_index=2&is_from_webapp=1&web_id=7636006415740044807"
    )
    assert detect_tiktok_media_type(video_url) == TIKTOK_MEDIA_VIDEO
    assert detect_tiktok_media_type(photo_url) == TIKTOK_MEDIA_PHOTO
    assert is_tiktok_video_link(video_url) is True
    assert is_tiktok_video_link(photo_url) is False
    assert detect_tiktok_media_type("https://vt.tiktok.com/ZSabc123/") == ""
    assert (
        detect_tiktok_media_type(
            "https://vt.tiktok.com/ZSabc123/",
            resolved_url=video_url,
        )
        == TIKTOK_MEDIA_VIDEO
    )
    assert detect_tiktok_media_type(photo_url, resolved_url=video_url) == TIKTOK_MEDIA_PHOTO
    assert detect_tiktok_media_type(video_url, resolved_url=photo_url) == TIKTOK_MEDIA_VIDEO


def test_should_highlight_video_link_requires_readable_like_or_share_activity():
    video_url = "https://www.tiktok.com/@demo/video/123"
    photo_url = "https://www.tiktok.com/@demo/photo/123"
    short_url = "https://vt.tiktok.com/ZSdemo/"

    assert should_highlight_video_link(video_url, likes=1, shares=0) is True
    assert should_highlight_video_link(video_url, likes=0, shares=1) is True
    assert should_highlight_video_link(video_url, likes=0, shares=0) is False
    assert should_highlight_video_link(video_url, likes=1, shares=1, metrics_readable=False) is False
    assert should_highlight_video_link(photo_url, likes=1, shares=1) is False
    assert should_highlight_video_link(
        short_url,
        likes=1,
        shares=0,
        resolved_url=video_url,
    ) is True


def test_should_highlight_video_link_uses_latest_scan_status():
    video_url = "https://www.tiktok.com/@demo/video/123"

    assert should_highlight_video_link(
        video_url,
        likes=10,
        shares=4,
        scan_status="Success",
    ) is True
    assert should_highlight_video_link(
        video_url,
        likes=10,
        shares=4,
        scan_status="Error: Không đọc được số liệu",
    ) is False
    assert should_highlight_video_link(video_url, likes=10, shares=4, scan_status="") is True


def test_highlight_video_link_rows_marks_video_not_photo():
    import openpyxl

    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Tháng 6"
    sheet.append(["LINK AIR", "Đối tác", "Đối tác 2", "LƯỢT XEM", "TIM", "CHIA SẺ"])
    sheet.append(
        [
            "https://www.tiktok.com/@ngkhangg.008/video/7635258581851360520",
            "Partner A",
            "Partner B",
            10,
            1,
            0,
        ]
    )
    sheet.append(
        [
            "https://www.tiktok.com/@baoquyen.dalat/photo/7635505950807346453",
            "Partner A",
            "Partner B",
            20,
            1,
            1,
        ]
    )

    count = highlight_video_link_rows(wb, "Tháng 6")

    assert count == 1
    video_fill = sheet.cell(row=2, column=1).fill
    photo_fill = sheet.cell(row=3, column=1).fill
    assert str(video_fill.fgColor.rgb).upper().endswith(VIDEO_LINK_FILL_COLOR)
    assert photo_fill.fill_type is None or not str(photo_fill.fgColor.rgb or "").upper().endswith(VIDEO_LINK_FILL_COLOR)


def test_highlight_video_link_rows_includes_single_partner_video():
    import openpyxl

    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Tháng 6"
    sheet.append(["LINK AIR", "Đối tác", "Đối tác 2", "LƯỢT XEM", "TIM", "CHIA SẺ"])
    sheet.append(["https://www.tiktok.com/@a/video/1", "Partner A", "", 10, 1, 0])

    count = highlight_video_link_rows(wb, "Tháng 6")

    assert count == 1
    assert str(sheet.cell(row=2, column=1).fill.fgColor.rgb).upper().endswith(VIDEO_LINK_FILL_COLOR)


def test_highlight_video_link_rows_clears_stale_blue_from_zero_activity_video():
    import openpyxl
    from openpyxl.styles import PatternFill

    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Tháng 6"
    sheet.append(["LINK AIR", "Đối tác", "LƯỢT XEM", "TIM", "CHIA SẺ"])
    sheet.append(["https://www.tiktok.com/@a/video/1", "Partner A", 10, 1, 0])
    sheet.append(["https://www.tiktok.com/@b/video/2", "Partner B", 20, 0, 1])
    sheet.append(["https://www.tiktok.com/@c/video/3", "Partner C", 30, 0, 0])
    stale_blue = PatternFill("solid", fgColor=VIDEO_LINK_FILL_COLOR)
    for column_index in range(1, sheet.max_column + 1):
        sheet.cell(row=4, column=column_index).fill = stale_blue

    count = highlight_video_link_rows(wb, "Tháng 6")

    assert count == 2
    assert str(sheet.cell(row=2, column=1).fill.fgColor.rgb).upper().endswith(VIDEO_LINK_FILL_COLOR)
    assert str(sheet.cell(row=3, column=1).fill.fgColor.rgb).upper().endswith(VIDEO_LINK_FILL_COLOR)
    assert all(
        sheet.cell(row=4, column=column_index).fill.fill_type is None
        for column_index in range(1, sheet.max_column + 1)
    )


def test_inactive_single_partner_video_keeps_orange_warning():
    import openpyxl

    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Tháng 6"
    sheet.append(["LINK AIR", "Đối tác", "Đối tác 2", "LƯỢT XEM", "TIM", "CHIA SẺ"])
    sheet.append(["https://www.tiktok.com/@a/video/1", "Partner A", "", 10, 0, 0])

    orange_count = highlight_single_partner_link_rows(wb, "Tháng 6")
    blue_count = highlight_video_link_rows(wb, "Tháng 6")

    fill = sheet.cell(row=2, column=1).fill
    assert orange_count == 1
    assert blue_count == 0
    assert str(fill.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)


def test_highlight_single_partner_link_rows_skips_video_not_photo():
    import openpyxl

    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Tháng 6"
    sheet.append(["LINK AIR", "Đối tác", "Đối tác 2", "LƯỢT XEM", "TIM", "CHIA SẺ"])
    sheet.append(["https://www.tiktok.com/@a/video/1", "Partner A", "", 10, 1, 0])
    sheet.append(["https://www.tiktok.com/@b/photo/2", "Partner B", "", 20, 1, 1])
    sheet.append(["https://vt.tiktok.com/ZSabc123/", "Partner C", "", 30, 1, 1])

    count = highlight_single_partner_link_rows(wb, "Tháng 6")

    assert count == 2
    assert not str(sheet.cell(row=2, column=1).fill.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)
    assert str(sheet.cell(row=3, column=1).fill.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)
    assert str(sheet.cell(row=4, column=1).fill.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)


def test_highlight_video_link_rows_wrapper_after_single_partner():
    import openpyxl

    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Tháng 6"
    sheet.append(["LINK AIR", "Đối tác", "Đối tác 2", "LƯỢT XEM", "TIM", "CHIA SẺ"])
    sheet.append(["https://www.tiktok.com/@a/video/1", "Partner A", "", 10, 1, 0])
    sheet.append(["https://www.tiktok.com/@b/photo/2", "Partner B", "", 20, 1, 1])

    highlight_single_partner_link_rows(wb, "Tháng 6")
    count = highlight_video_link_rows(wb, "Tháng 6")

    assert count == 1
    fill = sheet.cell(row=2, column=1).fill
    assert str(fill.fgColor.rgb).upper().endswith(VIDEO_LINK_FILL_COLOR)
    assert not str(fill.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)


def test_read_sheet_preview_flags_video_link_rows(tmp_path):
    import openpyxl

    file_path = tmp_path / "preview-video.xlsx"
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Tháng 6"
    sheet.append(["LINK AIR", "TÊN KÊNH", "Đối tác", "Đối tác 2", "LƯỢT XEM", "TIM", "CHIA SẺ"])
    sheet.append(
        [
            "https://www.tiktok.com/@ngkhangg.008/video/7635258581851360520",
            "Video",
            "Partner A",
            "Partner B",
            10,
            1,
            0,
        ]
    )
    sheet.append(
        [
            "https://www.tiktok.com/@baoquyen.dalat/photo/7635505950807346453",
            "Photo",
            "Partner A",
            "Partner B",
            20,
            1,
            1,
        ]
    )
    sheet.append(
        [
            "https://www.tiktok.com/@inactive/video/3",
            "Inactive video",
            "Partner A",
            "Partner B",
            30,
            0,
            0,
        ]
    )
    wb.save(file_path)
    wb.close()

    preview = read_sheet_preview(str(file_path), sheet_name="Tháng 6")
    rows = preview["data"]
    assert rows[0].get("_videoLink") is True
    assert not rows[1].get("_videoLink")
    assert not rows[2].get("_videoLink")


def test_persisted_scan_metadata_drives_workbook_preview_and_is_hidden(tmp_path):
    import openpyxl

    file_path = tmp_path / "preview-scan-metadata.xlsx"
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Data"
    sheet.append([
        "LINK AIR",
        "TÊN KÊNH",
        "Đối tác",
        "LƯỢT XEM",
        "TIM",
        "CHIA SẺ",
        "__TTBD_SCAN_STATUS",
        "__TTBD_RESOLVED_URL",
        "__TTBD_SOURCE_URL",
    ])
    sheet.append([
        "https://vt.tiktok.com/ZSactive/",
        "Active",
        "Partner A",
        100,
        10,
        4,
        "Success",
        "https://www.tiktok.com/@active/video/123",
        "https://vt.tiktok.com/ZSactive/",
    ])
    sheet.append([
        "https://www.tiktok.com/@stale/video/456",
        "Stale",
        "Partner B",
        200,
        20,
        5,
        "Error: Không đọc được số liệu",
        "https://www.tiktok.com/@stale/video/456",
        "https://www.tiktok.com/@stale/video/456",
    ])
    sheet.append([
        "https://vt.tiktok.com/ZSnew/",
        "Changed",
        "Partner C",
        300,
        30,
        6,
        "Success",
        "https://www.tiktok.com/@old/video/789",
        "https://vt.tiktok.com/ZSold/",
    ])

    blue_count = highlight_video_link_rows(wb, "Data")
    wb.save(file_path)
    wb.close()

    preview = read_sheet_preview(str(file_path), sheet_name="Data")

    assert blue_count == 1
    assert preview["data"][0].get("_videoLink") is True
    assert not preview["data"][1].get("_videoLink")
    assert not preview["data"][2].get("_videoLink")
    assert "__TTBD_SCAN_STATUS" not in preview["columns"]
    assert "__TTBD_RESOLVED_URL" not in preview["columns"]
    assert "__TTBD_SOURCE_URL" not in preview["columns"]


def test_build_workbook_rows_carries_internal_scan_metadata(tmp_path):
    import openpyxl

    file_path = tmp_path / "report-scan-metadata.xlsx"
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Data"
    sheet.append([
        "LINK AIR",
        "TÊN KÊNH",
        "Đối tác",
        "LƯỢT XEM",
        "TIM",
        "BÌNH LUẬN",
        "LƯỢT LƯU",
        "CHIA SẺ",
        "__TTBD_SCAN_STATUS",
        "__TTBD_RESOLVED_URL",
        "__TTBD_SOURCE_URL",
    ])
    sheet.append([
        "https://vt.tiktok.com/ZSactive/",
        "Active",
        "Partner A",
        100,
        10,
        1,
        2,
        4,
        "Success",
        "https://www.tiktok.com/@active/video/123",
        "https://vt.tiktok.com/ZSactive/",
    ])
    wb.save(file_path)
    wb.close()

    rows = build_workbook_rows(str(file_path), sheet_name="Data")

    assert rows[0]["_scanStatus"] == "Success"
    assert rows[0]["_resolvedUrl"] == "https://www.tiktok.com/@active/video/123"
    assert rows[0]["_resolvedSourceUrl"] == "https://vt.tiktok.com/ZSactive/"


def test_highlight_single_partner_link_rows_marks_rows_with_exactly_one_partner():
    import openpyxl

    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Tháng 6"
    sheet.append(["LINK AIR", "Đối tác", "Đối tác 2", "LƯỢT XEM"])
    sheet.append(["https://vt.tiktok.com/ZSaaa111/", "Partner A", "", 10])
    sheet.append(["https://www.tiktok.com/@b/video/2", "Partner A", "Partner B", 20])
    sheet.append(["https://vt.tiktok.com/ZSbbb222/", "Partner B", "", 30])

    count = highlight_single_partner_link_rows(wb, "Tháng 6")

    assert count == 2
    single_fill_row2 = sheet.cell(row=2, column=1).fill
    multi_fill_row3 = sheet.cell(row=3, column=1).fill
    single_fill_row4 = sheet.cell(row=4, column=1).fill
    assert str(single_fill_row2.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)
    assert not str(multi_fill_row3.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)
    assert str(single_fill_row4.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)


def test_highlight_single_partner_link_rows_clears_stale_highlight_once_row_gains_partner():
    import openpyxl

    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Tháng 6"
    sheet.append(["LINK AIR", "Đối tác", "Đối tác 2", "LƯỢT XEM"])
    sheet.append(["https://vt.tiktok.com/ZSaaa111/", "Partner A", "", 10])

    first_count = highlight_single_partner_link_rows(wb, "Tháng 6")
    assert first_count == 1
    assert str(sheet.cell(row=2, column=1).fill.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)

    # Dòng vừa được gắn thêm 1 đối tác nữa -> highlight cũ phải được xóa.
    sheet.cell(row=2, column=3, value="Partner B")
    second_count = highlight_single_partner_link_rows(wb, "Tháng 6")

    assert second_count == 0
    assert not str(sheet.cell(row=2, column=1).fill.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)


def test_highlight_single_partner_link_rows_missing_sheet_returns_zero():
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "Tháng 6"
    assert highlight_single_partner_link_rows(wb, "Tháng 7") == 0


def test_read_sheet_preview_flags_single_partner_rows(tmp_path):
    import openpyxl

    file_path = tmp_path / "preview.xlsx"
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Tháng 6"
    sheet.append(["LINK AIR", "TÊN KÊNH", "Đối tác", "Đối tác 2", "LƯỢT XEM"])
    sheet.append(["https://www.tiktok.com/@a/video/1", "Kenh A", "Partner A", "", 10])
    sheet.append(["https://www.tiktok.com/@b/video/2", "Kenh B", "Partner A", "Partner B", 20])
    sheet.append(["", "Kenh C", "Partner A", "", 0])
    wb.save(file_path)
    wb.close()

    preview = read_sheet_preview(str(file_path), sheet_name="Tháng 6")

    rows = preview["data"]
    assert rows[0].get("_singlePartner") is True
    assert not rows[1].get("_singlePartner")
    assert not rows[2].get("_singlePartner")


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


def test_is_internal_workbook_filename():
    assert is_internal_workbook_filename("_test_export.xlsx") is True
    assert is_internal_workbook_filename("~$Report.xlsx") is True
    assert is_internal_workbook_filename("Report Seeding Tiktok.xlsx") is False


def test_workbook_file_entries_skips_internal_files(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (tmp_path / "Report.xlsx").write_bytes(b"x")
    (data_dir / "_test_export.xlsx").write_bytes(b"x")
    (data_dir / "Report Seeding Tiktok.xlsx").write_bytes(b"x")

    ids = {entry["id"] for entry in workbook_file_entries(str(tmp_path))}
    assert "data/_test_export.xlsx" not in ids
    assert "data/Report Seeding Tiktok.xlsx" in ids
    assert "Report.xlsx" in ids


def test_data_sheet_defaults_skip_hidden_months(tmp_path):
    import openpyxl
    from workbook_utils import find_data_sheet_names, is_threads_link

    workbook = openpyxl.Workbook()
    workbook.active.title = "Tháng 6"
    workbook.active.sheet_state = "hidden"
    workbook.create_sheet("Tháng 8")
    path = tmp_path / "months.xlsx"
    workbook.save(path)
    # Every "first data sheet" default ([0]) must land on the month the user can see.
    assert find_data_sheet_names(path) == ["Tháng 8", "Tháng 6"]
    # A malformed host must be "not a Threads link", never an exception that aborts a scan or export.
    assert is_threads_link("https://[threads.com/@a/post/ABC") is False


def count_sheet_parses(monkeypatch):
    """Record each full pandas parse of a sheet (the 1-row raw-header read is not a parse)."""
    parses = []
    original = pd.ExcelFile.parse

    def parse(self, sheet_name=0, *args, **kwargs):
        if kwargs.get("nrows") is None:
            parses.append(sheet_name)
        return original(self, sheet_name, *args, **kwargs)

    monkeypatch.setattr(pd.ExcelFile, "parse", parse)
    return parses


def test_unchanged_workbook_reads_are_cached_until_an_atomic_save(tmp_path, monkeypatch):
    import openpyxl
    from workbook_utils import list_workbook_partners_with_link_counts, save_workbook_atomic

    path = tmp_path / "cached.xlsx"
    book = openpyxl.Workbook()
    book.active.title = "Data"
    book.active.append(["Link", "Tên Kênh", "LƯỢT XEM", "Đối tác"])
    book.active.append(["https://www.tiktok.com/@a/video/1", "Kênh A", 111, "Partner A"])
    book.save(path)
    parses = count_sheet_parses(monkeypatch)

    assert read_sheet_preview(str(path), "Data", platform="tiktok")["data"][0]["LƯỢT XEM"] == "111"
    list_workbook_partners_with_link_counts(str(path), "Data")
    assert read_sheet_preview(str(path), "Data", platform="tiktok")["data"][0]["LƯỢT XEM"] == "111"
    assert parses == ["Data"]

    # A scan writes its results through save_workbook_atomic; the next read must see them.
    book = openpyxl.load_workbook(path)
    book["Data"]["C2"] = 222
    save_workbook_atomic(book, str(path))
    assert read_sheet_preview(str(path), "Data", platform="tiktok")["data"][0]["LƯỢT XEM"] == "222"
    assert build_workbook_rows(str(path), sheet_name="Data")[0]["LƯỢT XEM"] == 222
    assert parses == ["Data", "Data"]


def test_preview_shows_formula_stt_numbers_without_cached_values(tmp_path):
    import openpyxl

    path = tmp_path / "stt.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Data"
    sheet.append(["   STT", "Link"])
    sheet.append([1, "https://www.tiktok.com/@a/video/1"])
    sheet.append(["=A2+1", "https://www.tiktok.com/@a/video/2"])
    sheet.append(["=A3+1", "https://www.tiktok.com/@a/video/3"])
    sheet.append(["=row()-1", "https://www.tiktok.com/@a/video/4"])
    sheet.append(["=ROW()-3", "https://www.tiktok.com/@a/video/5"])
    sheet.append([None, "https://www.tiktok.com/@a/video/6"])
    # openpyxl stores formulas without cached values, as every scan save does.
    book.save(path)

    preview = read_sheet_preview(str(path), "Data", platform="tiktok")

    assert [row["   STT"] for row in preview["data"]] == ["1", "2", "3", "4", "3", ""]


def test_preview_drops_midnight_time_from_date_only_cells(tmp_path):
    from datetime import datetime
    import openpyxl

    path = tmp_path / "dates.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Data"
    sheet.append(["Ngày", "Link", "Cập nhật"])
    sheet.append([datetime(2026, 8, 1), "https://www.tiktok.com/@a/video/1", datetime(2026, 8, 2, 14, 30)])
    sheet.append([None, "https://www.tiktok.com/@a/video/2", datetime(2026, 8, 3)])
    book.save(path)

    rows = read_sheet_preview(str(path), "Data", platform="tiktok")["data"]

    assert [row["Ngày"] for row in rows] == ["01/08/2026", "01/08/2026"]
    assert [row["Cập nhật"] for row in rows] == ["02/08/2026-14:30", "03/08/2026"]
