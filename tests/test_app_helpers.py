import io
from datetime import datetime
from unittest.mock import patch

from openpyxl import load_workbook

from app import build_google_push_rows, build_partner_report
from workbook_utils import SINGLE_LINK_FILL_COLOR, VIDEO_LINK_FILL_COLOR, is_failed_channel_name, metric_number


def test_validate_proxy_start_blocks_empty(tmp_path):
    from app import validate_proxy_start

    msg = validate_proxy_start(True, "", str(tmp_path))
    assert msg is not None
    assert "proxy" in msg.lower()


def test_validate_proxy_start_allows_disabled(tmp_path):
    from app import validate_proxy_start

    assert validate_proxy_start(False, "", str(tmp_path)) is None


def test_build_google_push_rows_includes_total_row():
    rows = [
        {
            "NGÀY AIR": datetime(2026, 6, 2),
            "TÊN KÊNH": "Channel A",
            "LINK AIR": "https://www.tiktok.com/@a/video/1",
            "LƯỢT XEM": 200,
            "TIM": 10,
            "BÌNH LUẬN": 2,
            "LƯỢT LƯU": 3,
            "CHIA SẺ": 4,
            "partners": ["Partner A"],
        }
    ]
    values = build_google_push_rows(rows)
    assert values[0][0] == "Stt"
    assert values[-1][2] == "TỔNG"
    assert values[-1][4].startswith("=SUM(")


def test_build_partner_report_filters_by_min_views():
    rows = [
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "Good",
            "LINK AIR": "https://www.tiktok.com/@a/video/1",
            "LƯỢT XEM": 200,
            "TIM": 1,
            "BÌNH LUẬN": 1,
            "LƯỢT LƯU": 1,
            "CHIA SẺ": 1,
        },
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "Low",
            "LINK AIR": "https://www.tiktok.com/@b/video/2",
            "LƯỢT XEM": 10,
            "TIM": 1,
            "BÌNH LUẬN": 1,
            "LƯỢT LƯU": 1,
            "CHIA SẺ": 1,
        },
    ]
    report_bytes = build_partner_report("Partner", rows, apply_min_views=True, min_views=100)
    assert isinstance(report_bytes, bytes)
    assert len(report_bytes) > 0
    assert metric_number(200) >= 100
    assert is_failed_channel_name("Good") is False


def test_build_partner_report_total_row_has_numeric_sums():
    rows = [
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "A",
            "LINK AIR": "https://www.tiktok.com/@a/video/1",
            "LƯỢT XEM": 100,
            "TIM": 10,
            "BÌNH LUẬN": 2,
            "LƯỢT LƯU": 3,
            "CHIA SẺ": 4,
        },
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "B",
            "LINK AIR": "https://www.tiktok.com/@b/video/2",
            "LƯỢT XEM": 50,
            "TIM": 5,
            "BÌNH LUẬN": 1,
            "LƯỢT LƯU": 2,
            "CHIA SẺ": 1,
        },
    ]
    report_bytes = build_partner_report("Partner", rows, apply_min_views=False)
    workbook = load_workbook(io.BytesIO(report_bytes), data_only=True)
    worksheet = workbook.active
    total_row = None
    for row_index in range(1, worksheet.max_row + 1):
        if worksheet.cell(row=row_index, column=1).value == "TỔNG":
            total_row = row_index
            break
    assert total_row is not None
    assert worksheet.cell(row=total_row, column=4).value == 150
    assert worksheet.cell(row=total_row, column=5).value == 15
    assert worksheet.cell(row=total_row, column=6).value == 3
    assert worksheet.cell(row=total_row, column=7).value == 5
    assert worksheet.cell(row=total_row, column=8).value == 5


def test_build_partner_report_highlights_single_partner_video_as_video_color():
    rows = [
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "Solo video",
            "LINK AIR": "https://www.tiktok.com/@a/video/1",
            "LƯỢT XEM": 200,
            "TIM": 1,
            "BÌNH LUẬN": 1,
            "LƯỢT LƯU": 1,
            "CHIA SẺ": 1,
            "partners": ["Partner A"],
        },
    ]
    report_bytes = build_partner_report("Partner A", rows, apply_min_views=False)
    workbook = load_workbook(io.BytesIO(report_bytes))
    worksheet = workbook.active
    fill = worksheet.cell(row=7, column=1).fill
    assert str(fill.fgColor.rgb).upper().endswith(VIDEO_LINK_FILL_COLOR)
    assert not str(fill.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)


def test_build_partner_report_highlights_row_with_single_partner_unknown_link():
    rows = [
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "Solo",
            "LINK AIR": "https://vt.tiktok.com/ZSabc123/",
            "LƯỢT XEM": 200,
            "TIM": 1,
            "BÌNH LUẬN": 1,
            "LƯỢT LƯU": 1,
            "CHIA SẺ": 1,
            "partners": ["Partner A"],
        },
    ]
    report_bytes = build_partner_report("Partner A", rows, apply_min_views=False)
    workbook = load_workbook(io.BytesIO(report_bytes))
    worksheet = workbook.active
    data_row = 7  # table_header_row (6) + 1
    fill = worksheet.cell(row=data_row, column=1).fill
    assert str(fill.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)


def test_build_partner_report_only_highlights_single_partner_rows():
    rows = [
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "A",
            "LINK AIR": "https://www.tiktok.com/@a/video/1",
            "LƯỢT XEM": 200,
            "TIM": 1,
            "BÌNH LUẬN": 1,
            "LƯỢT LƯU": 1,
            "CHIA SẺ": 1,
            "partners": ["Partner A"],
        },
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "B",
            "LINK AIR": "https://www.tiktok.com/@b/video/2",
            "LƯỢT XEM": 300,
            "TIM": 1,
            "BÌNH LUẬN": 1,
            "LƯỢT LƯU": 1,
            "CHIA SẺ": 1,
            "partners": ["Partner A", "Partner B"],
        },
    ]
    report_bytes = build_partner_report("Partner A", rows, apply_min_views=False)
    workbook = load_workbook(io.BytesIO(report_bytes))
    worksheet = workbook.active
    single_partner_fill = worksheet.cell(row=7, column=1).fill
    multi_partner_fill = worksheet.cell(row=8, column=1).fill
    assert str(single_partner_fill.fgColor.rgb).upper().endswith(VIDEO_LINK_FILL_COLOR)
    assert str(multi_partner_fill.fgColor.rgb).upper().endswith(VIDEO_LINK_FILL_COLOR)


def test_build_partner_report_no_highlight_for_photo_link():
    rows = [
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "Photo",
            "LINK AIR": "https://www.tiktok.com/@baoquyen.dalat/photo/7635505950807346453",
            "LƯỢT XEM": 200,
            "TIM": 1,
            "BÌNH LUẬN": 1,
            "LƯỢT LƯU": 1,
            "CHIA SẺ": 1,
            "partners": ["Partner A", "Partner B"],
        },
    ]
    report_bytes = build_partner_report("Partner", rows, apply_min_views=False)
    workbook = load_workbook(io.BytesIO(report_bytes))
    worksheet = workbook.active
    fill = worksheet.cell(row=7, column=1).fill
    assert fill.fill_type is None or not str(fill.fgColor.rgb or "").upper().endswith(VIDEO_LINK_FILL_COLOR)
    assert not str(fill.fgColor.rgb or "").upper().endswith(SINGLE_LINK_FILL_COLOR)


def test_build_partner_report_highlights_single_partner_photo_as_orange():
    rows = [
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "Photo solo",
            "LINK AIR": "https://www.tiktok.com/@baoquyen.dalat/photo/7635505950807346453",
            "LƯỢT XEM": 300,
            "TIM": 1,
            "BÌNH LUẬN": 1,
            "LƯỢT LƯU": 1,
            "CHIA SẺ": 1,
            "partners": ["Partner A"],
        },
    ]
    report_bytes = build_partner_report("Partner", rows, apply_min_views=False)
    workbook = load_workbook(io.BytesIO(report_bytes))
    worksheet = workbook.active
    fill = worksheet.cell(row=7, column=1).fill
    assert str(fill.fgColor.rgb).upper().endswith(SINGLE_LINK_FILL_COLOR)


def test_build_partner_report_highlights_video_link_without_single_partner():
    rows = [
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "Video",
            "LINK AIR": "https://www.tiktok.com/@ngkhangg.008/video/7635258581851360520",
            "LƯỢT XEM": 200,
            "TIM": 1,
            "BÌNH LUẬN": 1,
            "LƯỢT LƯU": 1,
            "CHIA SẺ": 1,
            "partners": ["Partner A", "Partner B"],
        },
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "Photo",
            "LINK AIR": "https://www.tiktok.com/@baoquyen.dalat/photo/7635505950807346453",
            "LƯỢT XEM": 300,
            "TIM": 1,
            "BÌNH LUẬN": 1,
            "LƯỢT LƯU": 1,
            "CHIA SẺ": 1,
            "partners": ["Partner A", "Partner B"],
        },
    ]
    report_bytes = build_partner_report("Partner", rows, apply_min_views=False)
    workbook = load_workbook(io.BytesIO(report_bytes))
    worksheet = workbook.active
    video_fill = worksheet.cell(row=7, column=1).fill
    photo_fill = worksheet.cell(row=8, column=1).fill
    assert str(video_fill.fgColor.rgb).upper().endswith(VIDEO_LINK_FILL_COLOR)
    assert photo_fill.fill_type is None or not str(photo_fill.fgColor.rgb or "").upper().endswith(VIDEO_LINK_FILL_COLOR)


def test_build_partner_report_works_when_logo_image_unavailable():
    rows = [
        {
            "NGÀY AIR": "",
            "TÊN KÊNH": "A",
            "LINK AIR": "https://www.tiktok.com/@a/video/1",
            "LƯỢT XEM": 100,
            "TIM": 10,
            "BÌNH LUẬN": 2,
            "LƯỢT LƯU": 3,
            "CHIA SẺ": 4,
        }
    ]
    with patch("app.ExcelImage", side_effect=ImportError("You must install Pillow to fetch image objects")):
        report_bytes = build_partner_report("Partner", rows, apply_min_views=False)
    assert isinstance(report_bytes, bytes)
    assert len(report_bytes) > 0


def test_build_export_payload_filename_includes_sheet_and_timestamp(tmp_path):
    import openpyxl
    from app import build_export_payload

    file_path = tmp_path / "report.xlsx"
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Tháng 7"
    sheet.append(["LINK AIR", "Tên Kênh", "Đối tác", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"])
    sheet.append(["https://www.tiktok.com/@a/video/1", "Kenh A", "1/2 Circle Coffee", 200, 1, 0, 0, 0])
    wb.save(file_path)
    wb.close()

    with patch("app.format_filename_datetime", return_value="09-07-2026-13-47"):
        payload = build_export_payload(
            str(file_path),
            ["1/2 Circle Coffee"],
            apply_min_views=False,
            min_views=0,
            requested_sheet_name="Tháng 7",
        )

    assert payload["filename"] == "1-2 Circle Coffee Tháng 7 09-07-2026-13-47.xlsx"