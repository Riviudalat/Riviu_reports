import io
from datetime import datetime

from openpyxl import load_workbook

from app import build_google_push_rows, build_partner_report
from workbook_utils import is_failed_channel_name, metric_number


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
