"""Local APIs, scan snapshots, and authenticated transport: synthetic credentials only."""
import asyncio
import json
import time
import urllib.request

import openpyxl
import pytest
from fastapi.testclient import TestClient

import app as backend
import proxy_utils
import threads_scraper as threads
import threads_session as sessions
from test_backend_review_fixes import isolated_backend, run_fixture_websocket_start
from test_threads_transport import Events, NoBrowser, URL, html_fixture, metrics, workbook
from test_ui_review_fixes import run_js

COOKIE = {"name": "sessionid", "value": "synthetic-cookie-secret", "domain": ".threads.com", "path": "/", "secure": True, "httpOnly": True}


def auth_html(valid=True):
    shared = ["BarcelonaSharedData", [], {"viewer": {"id": "123"} if valid else None}, 42]
    return '<script type="application/json">' + json.dumps({"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"define": [shared]}}]]]}) + '</script>'


class Store:
    def __init__(self): self.cookies = None
    def load(self): return self.cookies
    def save(self, cookies): self.cookies = cookies
    def delete(self): self.cookies = None


@pytest.fixture
def session_backend(isolated_backend, monkeypatch):
    manager = sessions.ThreadsSession(isolated_backend, store=Store())
    monkeypatch.setattr(backend, "threads_session_manager", lambda: manager)
    async def verified(cookies, proxy_config=None):
        return {"state": "valid", "checkedAt": "fixture", "message": "Verified", "route": "direct"}
    monkeypatch.setattr(sessions, "verify_cookies", verified)
    return manager


def test_session_api_local_auth_no_store_and_secrets(session_backend):
    with TestClient(backend.app, base_url="http://127.0.0.1:1231") as client:
        assert client.get("/threads-session/status").status_code == 403
        client.get("/")
        assert client.post("/threads-session/import", headers={"Origin": "https://foreign.example"}, json=[COOKIE]).status_code == 403
        response = client.post("/threads-session/import", json=[COOKIE])
        assert response.status_code == 200 and response.json()["success"]
        assert response.headers["cache-control"] == "no-store"
        assert COOKIE["value"] not in response.text
        status = client.get("/threads-session/status")
        assert status.json()["configured"] and status.json()["state"] == "valid"
        assert COOKIE["value"] not in status.text
        assert client.post("/threads-session/verify").json()["success"]
        deleted = client.delete("/threads-session")
        assert deleted.headers["cache-control"] == "no-store" and not deleted.json()["configured"]


def test_import_size_and_busy_gate_leave_store_unchanged(session_backend, monkeypatch):
    with TestClient(backend.app, base_url="http://127.0.0.1:1231") as client:
        client.get("/")
        assert client.post("/threads-session/import", content=b"x" * (sessions.MAX_IMPORT_BYTES + 1)).status_code == 400
        monkeypatch.setattr(backend, "SOURCE_BUSY", True)
        for method, url in [("post", "/threads-session/import"), ("post", "/threads-session/verify"), ("delete", "/threads-session")]:
            response = getattr(client, method)(url)
            assert response.status_code == 409 and response.headers["cache-control"] == "no-store"
        assert not session_backend.status()["configured"]


def test_session_backend_errors_do_not_echo_request(session_backend, monkeypatch):
    async def failed(_payload): raise RuntimeError(COOKIE["value"])
    monkeypatch.setattr(session_backend, "import_cookie", failed)
    with TestClient(backend.app, base_url="http://127.0.0.1:1231") as client:
        client.get("/")
        response = client.post("/threads-session/import", json=[COOKIE])
        assert response.status_code == 503 and COOKIE["value"] not in response.text
    assert not backend.SOURCE_BUSY


def test_http_session_uses_uncached_cookiejar(monkeypatch):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def read(self): return (auth_html() + html_fixture()).encode()
        def geturl(self): return URL
    def transport(request, config, timeout, cookiejar):
        cookiejar.add_cookie_header(request)
        assert COOKIE["value"] in request.get_header("Cookie")
        calls.append(cookiejar)
        return Response()
    monkeypatch.setattr(proxy_utils, "urlopen_with_config", transport)
    first = threads.fetch_threads_http(URL, session_cookies=[COOKIE])
    second = threads.fetch_threads_http(URL, session_cookies=[COOKIE])
    assert first["metrics"]["likes"] == second["metrics"]["likes"] == 0
    assert calls[0] is not calls[1]


def test_authenticated_browser_uses_synthetic_cookie_and_verified_target(monkeypatch):
    from playwright.async_api import APIRequestContext, async_playwright
    html = auth_html() + html_fixture()
    seen = []
    class Response:
        status = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        async def body(self): return html.encode()
        async def dispose(self): pass
    async def get(self, url, **kwargs):
        assert url == URL and kwargs["max_redirects"] == 0
        seen.append(url)
        return Response()
    monkeypatch.setattr(APIRequestContext, "get", get)
    async def check():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                result = await threads.fetch_threads_browser(browser, URL, session_cookies=[COOKIE])
                assert browser.contexts == []
                return result
            finally: await browser.close()
    result = asyncio.run(check())
    assert result["metrics"] == {"views": 15, "likes": 0, "comments": 0, "reposts": 0, "shares": 2}
    assert seen == [URL]
    assert COOKIE["value"] not in json.dumps(result)


def test_authenticated_share_redirect_extracts_header_at_real_permalink(monkeypatch):
    from playwright.async_api import APIRequestContext, async_playwright
    from test_threads_header_views import header
    share = "https://www.threads.com/share/Fixture/"
    payload = html_fixture().replace('"view_counts": 15', '"view_counts": null')
    html = auth_html() + payload + header("314 views")
    seen = []
    class Response:
        def __init__(self, redirect=False):
            self.status = 302 if redirect else 200
            self.headers = {"location": URL} if redirect else {"content-type": "text/html"}
        async def body(self): return html.encode()
        async def dispose(self): pass
    async def get(self, url, **kwargs):
        assert kwargs["max_redirects"] == 0 and kwargs["headers"]["Sec-Fetch-Mode"] == "navigate"
        seen.append(url)
        return Response(redirect=url == share)
    monkeypatch.setattr(APIRequestContext, "get", get)
    async def check():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try: return await threads.fetch_threads_browser(browser, share, session_cookies=[COOKIE])
            finally: await browser.close()
    result = asyncio.run(check())
    assert result["error"] == "" and result["channel"] == "demo"
    assert result["metrics"]["views"] == 314
    assert seen == [share, URL, URL]


def test_cookie_redirect_guard_rejects_other_hosts_and_http():
    guard = proxy_utils.SessionRedirectHandler()
    request = urllib.request.Request(URL)
    for target in ["https://example.com/", URL.replace("https:", "http:"), "https://threads.com.evil.example/"]:
        with pytest.raises(sessions.SessionError): guard.redirect_request(request, None, 302, "Found", {}, target)
    assert guard.redirect_request(request, None, 302, "Found", {}, URL + "?next=1").full_url.startswith(URL)


def test_auth_preflight_fails_before_workbook_changes(tmp_path, monkeypatch):
    path = workbook(tmp_path)
    original = path.read_bytes()
    async def rejected(cookies, proxy_config=None): return {"state": "unknown"}
    monkeypatch.setattr(sessions, "verify_cookies", rejected)
    with pytest.raises(sessions.SessionError): asyncio.run(threads.run_threads_scraper(path, session_cookies=[COOKIE]))
    assert path.read_bytes() == original
    history = json.loads((tmp_path / "data/threads_scan_history.json").read_text())["history"][0]
    assert history["authSelected"] and history["authState"] == "unknown"
    assert COOKIE["value"] not in json.dumps(history)


def test_authenticated_run_pins_first_proxy(tmp_path, monkeypatch):
    path = workbook(tmp_path, count=3)
    configs = [proxy_utils.normalize_proxy_config({"host": name, "port": 8080}) for name in ["a.example", "b.example"]]
    routes = []
    async def verified(cookies, proxy_config=None):
        assert proxy_config == configs[0]
        return {"state": "valid"}
    def request(url, proxy_config, session_cookies):
        assert session_cookies == (COOKIE,)
        routes.append(proxy_config)
        return metrics(views=15, likes=0, comments=0, reposts=0, shares=2)
    monkeypatch.setattr(sessions, "verify_cookies", verified)
    monkeypatch.setattr(threads, "fetch_threads_http", request)
    monkeypatch.setattr(threads, "async_playwright", NoBrowser)
    asyncio.run(threads.run_threads_scraper(path, mode="request", use_proxy=True, session_cookies=(COOKIE,), proxy_configs=configs))
    assert routes == [configs[0]] * 3


def test_midrun_cookie_rejection_stops_without_anonymous_fallback(tmp_path, monkeypatch):
    path = workbook(tmp_path)
    async def verified(*_args): return {"state": "valid"}
    monkeypatch.setattr(sessions, "verify_cookies", verified)
    monkeypatch.setattr(threads, "async_playwright", NoBrowser)
    calls = []
    def rejected(url, session_cookies):
        calls.append(url)
        return threads.failure("auth rejected", "auth_invalid")
    monkeypatch.setattr(threads, "fetch_threads_http", rejected)
    with pytest.raises(sessions.SessionError): asyncio.run(threads.run_threads_scraper(path, mode="hybrid", session_cookies=(COOKIE,)))
    assert calls == [URL]


@pytest.mark.parametrize("consent", [False, True])
def test_websocket_auth_snapshot_follows_proxy_without_extra_consent(session_backend, monkeypatch, consent):
    async def check():
        imported = await session_backend.import_cookie([COOKIE])
        generation = imported["status"]["generation"]
        calls = []
        async def runner(path, workers, **kwargs): calls.append(kwargs)
        monkeypatch.setattr(backend, "run_scraper_safely", runner)
        messages = await run_fixture_websocket_start(monkeypatch, {
            "platform": "threads", "use_threads_session": True, "threads_session_generation": generation,
            "use_proxy": True, "proxy_text": "proxy.example:8080", "threads_session_proxy_consent": consent,
        })
        assert len(calls) == 1 and calls[0]["threads_cookies"][0]["value"] == COOKIE["value"]
        assert calls[0]["threads_proxies"][0]["host"] == "proxy.example"
        assert calls[0]["threads_generation"] == generation
        assert COOKIE["value"] not in json.dumps(messages)
    asyncio.run(check())


def test_scan_failure_invalidates_green_session_without_leaking(session_backend, isolated_backend, monkeypatch):
    async def check():
        imported = await session_backend.import_cookie([COOKIE])
        async def failed(*_args, **_kwargs): raise RuntimeError(COOKIE["value"])
        monkeypatch.setattr(backend, "run_threads_scraper", failed)
        await backend.run_scraper_safely(str(isolated_backend / "A.xlsx"), 1, platform="threads", sheet_name="Data",
                                         threads_cookies=session_backend.snapshot(), threads_generation=imported["status"]["generation"])
        assert session_backend.status()["state"] == "unknown"
        assert COOKIE["value"] not in json.dumps(backend.manager.snapshot())
    asyncio.run(check())


def test_http_auth_unknown_does_not_publish_anonymous_metrics(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def read(self): return html_fixture().encode()
        def geturl(self): return URL
    monkeypatch.setattr(proxy_utils, "urlopen_with_config", lambda *_args, **_kwargs: Response())
    result = threads.fetch_threads_http(URL, session_cookies=[COOKIE])
    assert result["error_kind"] == "auth_unknown"
    assert all(value is None for value in result["metrics"].values())


def test_ui_session_follows_proxy_toggle_without_extra_consent():
    run_js("""
      ws=new WebSocket('fixture'); activePlatform='threads';
      threadsCookieState={configured:true,generation:'opaque',state:'valid'};
      el('threadsSessionUse').checked=true; el('proxyUseCheckbox').checked=true;
      proxyListText='proxy.example:8080'; el('proxyTextInput').value=proxyListText;
      openThreadsCookieModal=()=>{};
      startScraping([], 'Data'); assert.equal(sent.length,1);
      assert.equal(sent[0].use_threads_session,true);
      assert.equal(sent[0].threads_session_generation,'opaque');
      assert.equal(sent[0].use_proxy,true);
      assert.equal('threads_session_proxy_consent' in sent[0],false);
      assert.equal('cookies' in sent[0],false);
    """)


def test_ui_cookie_busy_blocks_start_and_close():
    run_js("""
      ws=new WebSocket('fixture'); threadsCookieBusy=true;
      el('threadsCookieText').value='synthetic-secret';
      startScraping([], 'Data'); assert.equal(sent.length,0);
      closeThreadsCookieModal(); assert.equal(el('threadsCookieText').value,'synthetic-secret');
      threadsCookieBusy=false; closeThreadsCookieModal(); assert.equal(el('threadsCookieText').value,'');
    """)
