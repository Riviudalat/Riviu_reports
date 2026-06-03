import pandas as pd

from workbook_utils import (
    dataframe_partner_columns,
    is_exportable_report_row,
    is_failed_channel_name,
    is_generated_username_channel,
    is_scrapable_tiktok_url,
    metric_number,
    normalize_tiktok_url,
    safe_join,
    split_partner_value,
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


def test_is_generated_username_channel():
    assert is_generated_username_channel("user1234567890") is True
    assert is_generated_username_channel("Nice Cafe") is False


def test_is_exportable_report_row_respects_min_views():
    row = {"TÊN KÊNH": "Cafe", "LƯỢT XEM": 50}
    assert is_exportable_report_row(row, apply_min_views=True, min_views=100) is False
    assert is_exportable_report_row(row, apply_min_views=False, min_views=100) is True


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
