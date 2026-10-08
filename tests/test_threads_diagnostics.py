"""Verified root-route restrictions and credential-free Threads run diagnostics."""
import asyncio
import json

import pytest
from playwright.async_api import async_playwright

from riviu.platforms import threads
from test_threads_transport import Events, NoBrowser, URL, html_fixture, metrics, workbook


def geo_document(route_url="/@demo/post/Fixture", canonical="comet.barcelonawebloggedout.BarcelonaGeoBlockRoute"):
    route = {"url": route_url, "canonicalRouteName": canonical, "tracePolicy": "barcelona.geoBlockPage",
             "rootView": {"resource": {"__dr": "BarcelonaGeoBlockedErrorRoot.react"},
                          "props": {"geoBlockRuleType": None, "title": "This content isn't available to everyone",
                                    "description": "It can't be seen by certain audiences."}}}
    return {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"require": [
        ["CometPlatformRootClient", "initialize", [], [{"initialRouteInfo": {"route": route}}]],
    ]}}]]]}


def script(document):
    return '<script type="application/json">' + json.dumps(document) + '</script>'


def read_history(tmp_path):
    return json.loads((tmp_path / "data" / "threads_scan_history.json").read_text(encoding="utf-8"))["history"]


@pytest.mark.parametrize("other_payload", ["", html_fixture()])
def test_json_root_geoblock_is_authoritative(other_payload):
    result = threads.parse_threads_http(script(geo_document()) + other_payload, URL, URL)
    assert result["error_kind"] == "geo_restricted"
    assert "vị trí" in result["error"]
    assert all(value is None for value in result["metrics"].values())


@pytest.mark.parametrize("doc", [geo_document("/@other/post/Other"), geo_document(canonical="other.route"),
                                {"caption": json.dumps(geo_document())}, {"recommended_post": {"initialRouteInfo": geo_document()}}])
def test_wrong_or_untyped_restriction_is_not_geoblock(doc):
    assert threads.parse_threads_http(script(doc), URL, URL).get("error_kind") != "geo_restricted"


@pytest.mark.parametrize("first_restricted", [False, True])
def test_merge_does_not_publish_metrics_after_geoblock(first_restricted):
    gate = threads.failure("restricted", "geo_restricted")
    valid = metrics(views=15, likes=0)
    result = threads.merge_results(gate, valid) if first_restricted else threads.merge_results(valid, gate)
    assert result["error_kind"] == "geo_restricted"
    assert all(value is None for value in result["metrics"].values())


@pytest.mark.parametrize("rendered", [False, True])
def test_real_browser_recognizes_navigation_root_geoblock(rendered):
    if rendered:
        html = '<html><body><script>setTimeout(() => { const node=document.createElement("script"); node.type="application/json"; node.textContent=' + json.dumps(json.dumps(geo_document())) + '; document.body.append(node); }, 100);</script></body></html>'
    else:
        html = '<html><body>' + script(geo_document()) + '</body></html>'
    async def check():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            new_page = browser.new_page
            async def fixture_page():
                page = await new_page()
                await page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))
                return page
            browser.new_page = fixture_page
            try: return await threads.fetch_threads_browser(browser, URL)
            finally: await browser.close()
    assert asyncio.run(check())["error_kind"] == "geo_restricted"


@pytest.mark.parametrize("on_retry", [False, True])
def test_geoblock_stops_http_retry_and_hybrid(tmp_path, monkeypatch, on_retry):
    path = workbook(tmp_path)
    values = [metrics(views=15)] if on_retry else []
    values += [threads.failure("geo", "geo_restricted")]
    calls = []
    def request(url):
        calls.append(url)
        return values.pop(0)
    monkeypatch.setattr(threads, "async_playwright", NoBrowser)
    monkeypatch.setattr(threads, "fetch_threads_http", request)
    events = Events()
    asyncio.run(threads.run_threads_scraper(path, events, mode="hybrid"))
    assert len(calls) == (2 if on_retry else 1)
    assert events.data[0]["views"] is None
    entry = read_history(tmp_path)[0]
    assert entry["phase"] == "completed" and entry["errorKinds"] == {"geo_restricted": 1}


def test_diagnostic_counts_unique_and_rows_without_proxy_secrets(tmp_path, monkeypatch):
    path = workbook(tmp_path)
    import openpyxl
    book = openpyxl.load_workbook(path)
    book.active.append([URL])
    book.save(path)
    book.close()
    monkeypatch.setattr(threads, "async_playwright", NoBrowser)
    def request(url, proxy_config):
        assert proxy_config["password"] == "private-password"
        return metrics(views=15, likes=0, comments=0, reposts=0, shares=2)
    monkeypatch.setattr(threads, "fetch_threads_http", request)
    asyncio.run(threads.run_threads_scraper(path, mode="request", use_proxy=True,
                                          proxy_text="http://private-user:private-password@private-host.example:8080"))
    entry = read_history(tmp_path)[0]
    assert entry["totalUrls"] == entry["processedUrls"] == entry["success"] == 1
    assert entry["totalRows"] == entry["processedRows"] == 2
    assert entry["proxyEnabled"] and entry["proxyCount"] == 1 and entry["proxyTypes"] == ["http"]
    assert "private" not in json.dumps(entry)
    assert URL not in json.dumps(entry)


def test_failed_scan_history_omits_exception_text(tmp_path, monkeypatch):
    path = workbook(tmp_path)
    monkeypatch.setattr(threads, "async_playwright", NoBrowser)
    monkeypatch.setattr(threads, "fetch_threads_http", lambda url: metrics(views=15, likes=0, comments=0, reposts=0, shares=2))
    def failed(*_args): raise PermissionError("private secret destination")
    monkeypatch.setattr(threads, "save_workbook_atomic", failed)
    with pytest.raises(PermissionError): asyncio.run(threads.run_threads_scraper(path, mode="request"))
    entry = read_history(tmp_path)[0]
    assert entry["phase"] == "failed" and entry["failureType"] == "PermissionError"
    assert "secret" not in json.dumps(entry)


def test_cancelled_scan_finalizes_history(tmp_path, monkeypatch):
    path = workbook(tmp_path)
    monkeypatch.setattr(threads, "async_playwright", NoBrowser)
    monkeypatch.setattr(threads, "fetch_threads_http", lambda url: metrics(views=15, likes=0, comments=0, reposts=0, shares=2))
    class Cancel(Events):
        async def broadcast_data(self, data): raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError): asyncio.run(threads.run_threads_scraper(path, Cancel(), mode="request"))
    assert read_history(tmp_path)[0]["phase"] == "cancelled"


def test_diagnostic_retention_and_atomic_failure(tmp_path, monkeypatch):
    for index in range(55): threads.append_threads_diagnostic(tmp_path, {"index": index})
    assert len(read_history(tmp_path)) == 50 and read_history(tmp_path)[0]["index"] == 54
    before = (tmp_path / "data" / "threads_scan_history.json").read_bytes()
    def rejected(*_args): raise PermissionError("locked")
    monkeypatch.setattr(threads.os, "replace", rejected)
    with pytest.raises(PermissionError): threads.append_threads_diagnostic(tmp_path, {"index": 55})
    assert (tmp_path / "data" / "threads_scan_history.json").read_bytes() == before
    assert list((tmp_path / "data").glob("*.json")) == [tmp_path / "data" / "threads_scan_history.json"]


def test_diagnostic_write_failure_does_not_fail_saved_scan(tmp_path, monkeypatch):
    path = workbook(tmp_path)
    monkeypatch.setattr(threads, "async_playwright", NoBrowser)
    monkeypatch.setattr(threads, "fetch_threads_http", lambda url: metrics(views=15, likes=0, comments=0, reposts=0, shares=2))
    def rejected(*_args): raise PermissionError("private-secret")
    monkeypatch.setattr(threads, "append_threads_diagnostic", rejected)
    events = Events()
    asyncio.run(threads.run_threads_scraper(path, events, mode="request"))
    assert events.data[0]["views"] == 15
    assert any("Không lưu được chẩn đoán" in line for line in events.logs)
    assert "private-secret" not in json.dumps(events.logs)
