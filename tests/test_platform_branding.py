"""Local brand assets in UI and embedded partner reports, without live data."""
import io
from pathlib import Path
import zipfile

import openpyxl
from PIL import Image
import pytest

from riviu import reports
from test_ui_review_fixes import run_js

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("platform", ["tiktok", "threads"])
def test_partner_report_embeds_platform_icon_and_keeps_table(platform):
    url = "https://www.threads.com/@demo/post/Fixture" if platform == "threads" else "https://www.tiktok.com/@demo/video/123"
    rows = [{"NGÀY AIR": "01/10/2026", "TÊN KÊNH": "demo", "LINK AIR": url, "LƯỢT XEM": 314,
             "TIM": 2, "BÌNH LUẬN": 6, "REPOST": 0, "LƯỢT LƯU": 0, "CHIA SẺ": "", "partners": ["Fixture"]}]
    payload = reports.build_partner_report("Fixture", rows, platform=platform, apply_min_views=False)
    expected_image = (ROOT / "riviu/web/static/platform-icons" / f"{platform}.png").read_bytes()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        media = [archive.read(name) for name in archive.namelist() if name.startswith("xl/media/")]
        assert expected_image in media and len(media) == 2
    book = openpyxl.load_workbook(io.BytesIO(payload))
    try:
        sheet = book.active
        assert platform.upper() in sheet["B1"].value
        # Both platforms share the 8-column layout: metadata A2:G2, platform icon at H2.
        assert "A2:G2" in {str(merge) for merge in sheet.merged_cells.ranges}
        assert sheet["C3"].value == "LINK AIR" and sheet["C4"].value == url
        assert sheet.freeze_panes == "A4"
        assert len(sheet._images) == 2
        platform_image = sheet._images[1]
        assert platform_image.anchor._from.row == 1
        assert platform_image.anchor._from.col == 7
        assert sheet["H3"].value == "CHIA SẺ" and sheet["H4"].value is None and sheet["I3"].value is None
        assert sheet["G3"].value == ("REPOST" if platform == "threads" else "LƯỢT LƯU")
    finally: book.close()


@pytest.mark.parametrize("platform", ["tiktok", "threads"])
def test_local_icon_assets_are_transparent_and_no_remote_refs(platform):
    svg = (ROOT / "riviu/web/static/platform-icons" / f"{platform}.svg").read_text(encoding="utf-8")
    assert 'viewBox="0 0 24 24"' in svg and "<script" not in svg and "href=" not in svg
    with Image.open(ROOT / "riviu/web/static/platform-icons" / f"{platform}.png") as image:
        assert image.size == (256, 256) and image.mode == "RGBA"
        assert image.getextrema()[3][0] == 0


def test_report_modal_brand_follows_platform():
    run_js("""
      applyPlatformUI('threads');
      assert.equal(el('reportPlatformIcon').src,'/static/platform-icons/threads.svg');
      assert.match(el('reportPlatformLabel').textContent,/Threads/);
      applyPlatformUI('tiktok');
      assert.equal(el('reportPlatformIcon').src,'/static/platform-icons/tiktok.svg');
      assert.match(el('reportPlatformLabel').textContent,/TikTok/);
    """)
