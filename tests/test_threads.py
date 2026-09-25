import io
import asyncio
import os

import openpyxl
import pytest
from playwright.async_api import async_playwright

from app import build_export_payload, build_google_push_rows, build_partner_report
from google_sheets_sync import create_result_sheet_title

from threads_scraper import (
    parse_action_count,
    parse_threads_http,
    fetch_threads_browser,
    fetch_threads_http,
    result_status,
    run_threads_scraper,
    same_threads_post,
    write_threads_result,
)
from workbook_utils import build_workbook_rows, is_threads_link, list_workbook_partners_with_link_counts


URL = "https://www.threads.com/@miri_viu/post/DdRIHGWCURV?xmt=sample"
SHARE_URL = "https://www.threads.com/share/GlQTw_txU/"


def test_threads_url_requires_real_post_host_and_identity():
    assert is_threads_link(URL)
    assert is_threads_link("https://www.threads.net/@miri_viu/post/DdRIHGWCURV")
    assert is_threads_link(SHARE_URL)
    assert not is_threads_link("https://threads.com.evil.test/@miri_viu/post/DdRIHGWCURV")
    assert not is_threads_link("https://www.tiktok.com/@miri_viu/video/1")
    assert same_threads_post(URL, "https://www.threads.com/@miri_viu/post/DdRIHGWCURV")
    assert not same_threads_post(URL, "https://www.threads.com/?error=invalid_post")
    assert same_threads_post(SHARE_URL, "https://www.threads.com/@trangxinh718/post/DdQ8PEekxig")
    assert not same_threads_post(SHARE_URL, "https://www.threads.com/?error=invalid_post")


def test_http_parser_keeps_missing_counts_unknown():
    html = '<title>Example</title><script type="application/json">{"require":[["BarcelonaLoggedOutExpansionGating",[],{"view_counts":372},7623]]}</script>'
    result = parse_threads_http(html, URL, URL)
    assert result["channel"] == "miri_viu"
    assert result["metrics"] == {"views": 372, "likes": None, "comments": None, "reposts": None, "shares": None}
    assert parse_threads_http(html, URL, "https://www.threads.com/?error=invalid_post")["error"]
    share = parse_threads_http(html, SHARE_URL, "https://www.threads.com/@trangxinh718/post/DdQ8PEekxig")
    assert share["channel"] == "trangxinh718"


def test_action_count_does_not_convert_blank_to_zero():
    assert parse_action_count("4") == 4
    assert parse_action_count("1.2K") == 1200
    assert parse_action_count("") is None
    assert result_status({"metrics": {"views": 372, "likes": 4, "comments": 1, "reposts": None, "shares": 2}, "error": ""}).startswith("Partial:")


def test_workbook_filters_platform_and_preserves_missing_metrics(tmp_path):
    path = tmp_path / "mixed.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Data"
    sheet.append(["Ngày", "Link", "Tên Kênh", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ", "Đối tác"])
    sheet.append(["14/9/2026", URL, "", 100, 2, "", "", "", "Cafe A"])
    sheet.append(["14/9/2026", "https://www.tiktok.com/@demo/video/123", "", 300, 5, 1, 2, 3, "Cafe A"])
    book.save(path)

    threads_rows = build_workbook_rows(path, platform="threads")
    assert len(threads_rows) == 1
    assert threads_rows[0]["REPOST"] == ""
    assert len(build_workbook_rows(path)) == 1

    write_threads_result(sheet, 2, {"channel": "miri_viu", "metrics": {"views": 372, "likes": None, "comments": 1, "reposts": None, "shares": 2}, "error": ""})
    assert sheet["D2"].value == 372
    assert sheet["E2"].value == 2
    assert sheet["F2"].value == 1
    assert sheet["G2"].value == ""
    assert sheet["H2"].value == 2
    assert sheet["C2"].value == "miri_viu"
    assert sheet["G3"].value == 2
    assert any(cell.value == "REPOST" for cell in sheet[1])


def test_threads_exports_use_repost_and_keep_unknown_blank():
    rows = [{
        "NGÀY AIR": "14/9/2026", "TÊN KÊNH": "miri_viu", "LINK AIR": URL,
        "LƯỢT XEM": 372, "TIM": 4, "BÌNH LUẬN": 1, "REPOST": "",
        "CHIA SẺ": 2, "TRẠNG THÁI": "Partial: thiếu REPOST", "partners": ["Cafe A"],
    }]
    pushed = build_google_push_rows(rows, platform="threads")
    assert pushed[0][7] == "REPOST"
    assert pushed[1][7] == ""
    assert pushed[0][-1] == "Trạng thái"
    assert pushed[2][7] == ""

    report = openpyxl.load_workbook(io.BytesIO(build_partner_report("Cafe A", rows, platform="threads")))
    sheet = report.active
    assert sheet["G3"].value == "REPOST"
    assert sheet["G4"].value in (None, "")
    assert sheet["I4"].value == "Partial: thiếu REPOST"
    assert sheet["G5"].value is None


def test_threads_google_result_tab_has_distinct_name():
    assert create_result_sheet_title("Tháng 9", platform="threads").startswith("Report Seeding Threads ")


def test_threads_partner_export_excludes_tiktok_and_does_not_filter_views(tmp_path):
    path = tmp_path / "mixed.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Data"
    sheet.append(["Ngày", "Link", "Tên Kênh", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ", "Đối tác"])
    sheet.append(["14/9/2026", URL, "miri_viu", 80, 4, 1, "", 2, "Cafe A"])
    sheet.append(["14/9/2026", "https://www.tiktok.com/@demo/video/123", "TikTok", 500, 5, 2, 3, 4, "Cafe A"])
    sheet.append(["14/9/2026", "https://www.tiktok.com/@other/video/234", "TikTok 2", 300, 2, 1, 0, 0, "TikTok Only"])
    book.save(path)

    partners = list_workbook_partners_with_link_counts(path, sheet_name="Data", platform="threads", min_views=100)
    assert [partner["name"] for partner in partners] == ["Cafe A"]
    assert partners[0]["linkCount"] == 1
    payload = build_export_payload(path, ["Cafe A"], True, 100, "Data", "threads")
    report = openpyxl.load_workbook(io.BytesIO(payload["content"]))
    assert "Threads" in payload["filename"]
    assert report.active["C4"].value == URL
    assert report.active["C5"].value is None
    assert report.active["G3"].value == "REPOST"
    assert report.active["I4"].value == "Chưa quét"


def test_threads_runner_saves_before_done_and_ignores_tiktok(tmp_path, monkeypatch):
    path = tmp_path / "mixed.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Data"
    sheet.append(["Link", "Tên Kênh", "LƯỢT XEM", "TIM", "Đối tác"])
    sheet.append([URL, "", "", "", "Cafe A"])
    sheet.append(["https://www.tiktok.com/@demo/video/123", "TikTok", 12, 5, "Cafe A"])
    book.save(path)

    monkeypatch.setattr("threads_scraper.fetch_threads_http", lambda _url: {
        "channel": "miri_viu", "metrics": {"views": 372, "likes": 4, "comments": 1, "reposts": None, "shares": 2}, "error": "",
    })

    class Manager:
        async def broadcast_log(self, _message):
            pass

        async def broadcast_duplicates(self, _data):
            pass

        async def broadcast_data(self, _data):
            pass

        async def broadcast_status(self, data):
            if data.get("done"):
                saved = openpyxl.load_workbook(path)
                assert saved.active["C2"].value == 372
                assert saved.active["C3"].value == 12
                saved.close()

    asyncio.run(run_threads_scraper(path, Manager(), sheet_name="Data", mode="request"))


@pytest.mark.skipif(not os.environ.get("THREADS_LIVE_URL"), reason="live Threads check is opt-in")
def test_live_threads_http_and_browser_agree_on_post(tmp_path):
    url = os.environ["THREADS_LIVE_URL"]
    request = fetch_threads_http(url)

    async def browser_check():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                return await fetch_threads_browser(browser, url)
            finally:
                await browser.close()

    rendered = asyncio.run(browser_check())
    assert not request["error"], request
    assert not rendered["error"], rendered
    assert rendered["channel"] == request["channel"]
    assert rendered["metrics"]["views"] is not None
    assert rendered["metrics"]["likes"] is not None
    assert rendered["metrics"]["comments"] is not None
    assert rendered["metrics"]["shares"] is not None

    path = tmp_path / "live_threads.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(["Link", "Tên Kênh", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ", "Đối tác"])
    sheet.append([url, "", "", "", "", "", "", "Cafe A"])
    workbook.save(path)
    asyncio.run(run_threads_scraper(path, sheet_name="Data", mode="hybrid"))
    saved = openpyxl.load_workbook(path)
    assert saved.active["B2"].value == rendered["channel"]
    assert isinstance(saved.active["C2"].value, int)
    assert saved.active["F2"].value is None
    assert saved.active["G2"].value is not None
    saved.close()


@pytest.mark.skipif(not os.environ.get("THREADS_SHARE_URL"), reason="live Threads share check is opt-in")
def test_live_threads_share_redirect_in_http_and_browser():
    url = os.environ["THREADS_SHARE_URL"]
    request = fetch_threads_http(url)

    async def browser_check():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                return await fetch_threads_browser(browser, url)
            finally:
                await browser.close()

    rendered = asyncio.run(browser_check())
    assert not request["error"], request
    assert not rendered["error"], rendered
    assert request["channel"] == rendered["channel"]
    assert request["metrics"]["views"] is not None


@pytest.mark.skipif(not os.environ.get("RIVIU_TEST_URL"), reason="local UI check is opt-in")
def test_threads_ui_uses_repost_column_not_tiktok_saves():
    from playwright.sync_api import sync_playwright

    preview = {
        "sheets": ["Data"], "currentSheet": "Data",
        "columns": ["Link", "Tên Kênh", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "REPOST", "CHIA SẺ"],
        "data": [{"Link": URL, "Tên Kênh": "miri_viu", "LƯỢT XEM": 372, "TIM": 4,
                  "BÌNH LUẬN": 1, "LƯỢT LƯU": 99, "REPOST": 3, "CHIA SẺ": 2}],
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for width, height in [(1440, 900), (390, 844)]:
                page = browser.new_page(viewport={"width": width, "height": height})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/preview-excel*", lambda route: route.fulfill(json=preview))
                page.goto(os.environ["RIVIU_TEST_URL"], wait_until="domcontentloaded")
                page.wait_for_function("websocketSessionReady")
                page.locator("#platformThreads").click()
                page.get_by_role("columnheader", name="REPOST").wait_for()
                headers = page.locator("#previewHeader th").all_inner_texts()
                cells = page.locator("#previewBody tr td").all_inner_texts()
                assert "LƯỢT LƯU" not in headers
                assert cells[headers.index("REPOST")] == "3"
                assert page.locator("#liveSavedHeader").inner_text() == "REPOST"
                page.evaluate("""() => {
                    startBtn.disabled = true;
                    cancelBtn.disabled = false;
                    updateProgress({phase: 'running', total: 1, processed: 1, success: 0, hidden: 1, error: 0, done: false});
                }""")
                assert page.locator("#startBtn").is_disabled()
                assert page.locator("#progressStatus").inner_text() == ""
                page.evaluate("updateProgress({phase: 'completed', total: 1, processed: 1, success: 0, hidden: 1, error: 0, done: true})")
                assert not page.locator("#startBtn").is_disabled()
                assert "thiếu số" in page.locator("#progressStatus").inner_text()
                assert not errors, errors
                page.close()
        finally:
            browser.close()
