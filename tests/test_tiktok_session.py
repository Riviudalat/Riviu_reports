"""Opt-in "Cookie TikTok": normalization, storage without plaintext, the one-request login
check, and cookies reaching requests and browser contexts only when the toggle is on.

Synthetic cookies and workbooks only; every network seam is patched.
"""

import asyncio
import json
import sys
import time

import openpyxl
import pytest

from riviu import scan_service, server_context
from riviu.platforms import threads_session
from riviu.platforms import tiktok as scraper
from riviu.platforms import tiktok_session as session
from riviu.platforms.tiktok import cookies, fetch_browser, fetch_html


SECRET = "synthetic-tiktok-session-value"
LINK = "https://www.tiktok.com/@demo/photo/7400000000000000001"


def cookie(name="sessionid", value=SECRET, domain=".tiktok.com", **extra):
    entry = {"domain": domain, "name": name, "value": value, "path": "/", "secure": True,
             "httpOnly": True, "hostOnly": not domain.startswith("."), "sameSite": "no_restriction",
             "expirationDate": time.time() + 86400}
    entry.update(extra)
    return entry


def export(*entries):
    return json.dumps(list(entries))


@pytest.fixture(autouse=True)
def no_active_cookies():
    cookies.deactivate()
    yield
    cookies.deactivate()


def test_normalization_keeps_only_tiktok_cookies_and_needs_a_session_cookie():
    normalized = session.normalize_tiktok_cookies(export(
        cookie(), cookie("tt_csrf_token", "csrf", "www.tiktok.com"),
        cookie("SID", "google", ".google.com"), cookie("sessionid", "threads", ".threads.com"),
        cookie("sessionid", "evil", ".tiktok.com.evil.example"), cookie("sessionid", "lookalike", "nottiktok.com"),
    ))
    assert {(item["name"], item["domain"]) for item in normalized} == {("sessionid", ".tiktok.com"), ("tt_csrf_token", "www.tiktok.com")}
    for name in ("sessionid_ss", "sid_tt"):
        assert session.normalize_tiktok_cookies(export(cookie(name)))[0]["name"] == name
    with pytest.raises(session.TikTokSessionError) as caught:
        session.normalize_tiktok_cookies(export(cookie("tt_csrf_token"), cookie("sessionid", domain=".google.com")))
    assert caught.value.state == "missing_session"
    with pytest.raises(session.TikTokSessionError) as caught:
        session.normalize_tiktok_cookies(export(cookie(expirationDate=time.time() - 60)))
    assert caught.value.state == "expired"
    assert SECRET not in str(caught.value)


def test_encrypted_server_store_never_writes_plaintext_and_is_separate_from_threads(tmp_path):
    store = threads_session.EncryptedFileStore(tmp_path, b"k" * 32, spec=session.TIKTOK_STORE)
    normalized = session.normalize_tiktok_cookies(export(cookie()))
    store.save(normalized)
    blob = store.path.read_bytes()
    assert store.path.name == "tiktok_session.aesgcm"
    assert SECRET.encode() not in blob and b"sessionid" not in blob and blob.startswith(b"RVTK1")
    assert store.load() == normalized
    threads_store = threads_session.EncryptedFileStore(tmp_path, b"k" * 32)
    assert threads_store.path != store.path and threads_store.load() is None
    threads_store.path = store.path  # Another vault's binding must not decrypt this file.
    with pytest.raises(threads_session.SessionError):
        threads_store.load()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_windows_dpapi_store_never_writes_plaintext(tmp_path):
    store = session.default_store(tmp_path)
    normalized = session.normalize_tiktok_cookies(export(cookie()))
    store.save(normalized)
    assert store.path.name == "tiktok_session.enc"
    assert SECRET.encode() not in store.path.read_bytes()
    assert store.load() == normalized
    store.delete()
    assert store.load() is None


@pytest.mark.parametrize("payload, state", [
    ({"message": "success", "data": {"user_id": 7012345678901234567, "username": "demo"}}, "valid"),
    ({"message": "success", "data": {"user_id_str": "7012345678901234567"}}, "valid"),
    ({"data": {"description": "session expired, please sign in again", "error_code": 13, "name": "session_expired"}, "message": "error"}, "invalid"),
    ({"message": "success", "data": {"user_id": 0}}, "unknown"),
    ({"message": "success", "data": {}}, "unknown"),
    ("<html>200 OK</html>", "unknown"),
])
def test_account_info_classification(payload, state):
    assert session.account_state(payload) == state


def test_verification_is_one_direct_request_carrying_only_tiktok_cookies(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return json.dumps({"message": "success", "data": {"user_id_str": "123"}}).encode()

    def fake_urlopen(request, config, timeout, redirect_validator):
        calls.append((request.full_url, request.get_header("Cookie"), config))
        return Response()

    monkeypatch.setattr("riviu.proxy_utils.urlopen_with_config", fake_urlopen)
    assert session.verify_cookies(export(cookie(), cookie("SID", "google", ".google.com"))) == "valid"
    [(url, header, config)] = calls
    assert url == session.ACCOUNT_INFO_URL and config is None
    assert header == f"sessionid={SECRET}"


class MemoryStore:
    def __init__(self):
        self.saved = None

    def load(self):
        return self.saved

    def save(self, cookies):
        self.saved = cookies

    def delete(self):
        self.saved = None


def test_import_saves_only_a_verified_session_and_status_never_shows_values():
    store = MemoryStore()
    manager = session.TikTokSession("unused", store=store, verifier=lambda _cookies: "invalid")
    result = manager.import_cookie(export(cookie()))
    assert result["success"] is False and result["check"]["state"] == "invalid" and store.saved is None

    manager.verifier = lambda _cookies: "valid"
    result = manager.import_cookie(export(cookie()))
    assert result["success"] is True and store.saved[0]["value"] == SECRET
    status = manager.status()
    assert status["configured"] and status["state"] == "valid" and status["count"] == 1
    assert SECRET not in json.dumps(result) and SECRET not in json.dumps(status)
    assert manager.snapshot()[0]["value"] == SECRET
    manager.clear()
    with pytest.raises(session.TikTokSessionError):
        manager.snapshot()


def captured_fetch(monkeypatch):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def geturl(self):
            return LINK

        def read(self):
            return b"<html></html>"

    def fake_urlopen(request, timeout, redirect_validator):
        seen.append(request.get_header("Cookie"))
        return Response()

    monkeypatch.setattr(fetch_html, "urlopen_request", fake_urlopen)
    return seen


def test_request_fetches_send_cookies_only_while_the_toggle_activated_them(monkeypatch):
    seen = captured_fetch(monkeypatch)
    fetch_html.fetch_tiktok_html(LINK)
    cookies.activate(session.normalize_tiktok_cookies(export(cookie(), cookie("SID", "google", ".google.com"))))
    fetch_html.fetch_tiktok_html(LINK)
    cookies.deactivate()
    fetch_html.fetch_tiktok_html(LINK)
    assert seen == [None, f"sessionid={SECRET}", None]


class FakeContext:
    def __init__(self):
        self.added = []

    async def add_cookies(self, items):
        self.added.append(items)

    async def route(self, *_args):
        return None

    async def close(self):
        return None


class FakeBrowser:
    async def new_context(self, **_kwargs):
        return FakeContext()


def test_browser_contexts_get_cookies_only_while_the_toggle_activated_them():
    without = asyncio.run(fetch_browser.make_browser_context(FakeBrowser()))
    cookies.activate(session.normalize_tiktok_cookies(export(cookie(), cookie("tt_csrf_token", "csrf", "www.tiktok.com"))))
    with_cookies = asyncio.run(fetch_browser.make_browser_context(FakeBrowser()))
    assert without.added == []
    [added] = with_cookies.added
    assert {item["name"] for item in added} == {"sessionid", "tt_csrf_token"}
    assert all(item["secure"] for item in added)
    assert {"name": "sessionid", "value": SECRET, "domain": ".tiktok.com", "path": "/"}.items() <= added[0].items()


def scan_workbook(path):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Data"
    sheet.append(["Link", "Tên Kênh", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ", "Đối tác"])
    sheet.append([LINK, "Demo", 100, 10, 1, 2, 3, "Partner"])
    book.save(path)


@pytest.mark.parametrize("session_cookies", [None, "toggle-on"])
def test_scan_uses_cookies_only_when_started_with_them_and_clears_them_after(tmp_path, monkeypatch, session_cookies):
    path = tmp_path / "scan.xlsx"
    scan_workbook(path)
    headers = []

    class NoBrowser:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    def fake_scrape(_worker_index, url, *_args, **_kwargs):
        headers.append(cookies.cookie_header_for(url))
        return {"Views": "500", "Likes": "50", "Comments": "4", "Saves": "3", "Shares": "2"}, "Demo", "Success", 1, url

    monkeypatch.setattr(scraper.runner, "async_playwright", NoBrowser)
    monkeypatch.setattr(scraper.workers, "_run_request_scrape", fake_scrape)
    saved = session.normalize_tiktok_cookies(export(cookie())) if session_cookies else None
    kwargs = {"session_cookies": saved} if saved else {}
    asyncio.run(scraper.run_scraper(str(path), sheet_name="Data", worker_count=1, retries=0, base_dir=tmp_path, **kwargs))
    assert headers == ([f"sessionid={SECRET}"] if saved else [""])
    assert cookies.active() == ()


def test_scan_start_passes_cookies_only_with_the_toggle(monkeypatch, tmp_path):
    received = []

    async def fake_run_scraper(*_args, **kwargs):
        received.append(kwargs)

    monkeypatch.setattr(scan_service, "run_scraper", fake_run_scraper)
    options = {**scan_service.scan_options(sheet_name="Data"), "file_label": ""}
    asyncio.run(scan_service.run_tiktok_scan("book.xlsx", None, options))
    asyncio.run(scan_service.run_tiktok_scan("book.xlsx", None, {**options, "tiktok_cookies": ({"name": "sessionid"},)}))
    assert "session_cookies" not in received[0]
    assert received[1]["session_cookies"] == ({"name": "sessionid"},)

    path = tmp_path / "book.xlsx"
    scan_workbook(path)
    monkeypatch.setattr(scan_service, "resolve_source_file", lambda *_args: ("book.xlsx", str(path), None, "tiktok"))
    store = MemoryStore()
    store.saved = session.normalize_tiktok_cookies(export(cookie()))
    monkeypatch.setattr(server_context, "tiktok_session_manager", lambda: session.TikTokSession("unused", store=store))
    _, default = asyncio.run(scan_service.prepare_scan_start({"platform": "tiktok", "file_id": "book.xlsx"}))
    _, toggled = asyncio.run(scan_service.prepare_scan_start({"platform": "tiktok", "file_id": "book.xlsx", "use_tiktok_session": True}))
    assert "tiktok_cookies" not in default
    assert toggled["tiktok_cookies"][0]["value"] == SECRET
    store.saved = None
    monkeypatch.setattr(server_context, "tiktok_session_manager", lambda: session.TikTokSession("unused", store=store))
    with pytest.raises(ValueError, match="Chưa lưu cookie TikTok"):
        asyncio.run(scan_service.prepare_scan_start({"platform": "tiktok", "file_id": "book.xlsx", "use_tiktok_session": True}))


def test_ui_sends_the_tiktok_session_flag_only_when_switched_on():
    from test_ui_review_fixes import run_js

    run_js("""
      ws=new WebSocket('test'); activePlatform='tiktok'; currentFileId='Fixture.xlsx'; currentSheetName='Data';
      const warnings=[]; notify=(...args)=>warnings.push(args); let opened=false; openTikTokCookieModal=()=>{opened=true;};
      startScraping([],'Data');
      assert.equal(sent.length,1); assert.ok(!('use_tiktok_session' in sent[0]));
      scanPhase='idle'; el('tiktokSessionUse').checked=true; tiktokCookieState={configured:false,state:'none',generation:''};
      startScraping([],'Data');
      assert.equal(sent.length,1); assert.equal(opened,true); assert.equal(warnings[0][1],'warn');
      renderTikTokCookieStatus({configured:true,state:'valid',generation:'g1',count:3});
      assert.equal(el('tiktokSessionUse').checked,false);
      el('tiktokSessionUse').checked=true;
      startScraping([],'Data');
      assert.equal(sent.length,2); assert.equal(sent[1].use_tiktok_session,true);
      assert.ok(!JSON.stringify(sent).includes('sessionid'));
      applyPlatformUI('threads'); assert.equal(el('tiktokSessionControls').hidden,true);
      applyPlatformUI('tiktok'); assert.equal(el('tiktokSessionControls').hidden,false);
    """)
