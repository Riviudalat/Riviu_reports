"""Server mode: the public read-only viewer at `/` and the logged-in admin page at `/admin`.

The server is the gate: anonymous requests may use only the viewer allowlist
(`security.viewer_request_allowed`), every other route answers 401 (or sends HTML to the
login), and `/ws` refuses start/cancel without an admin login. Synthetic workbooks only.
"""

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Route

from riviu import app as backend
from riviu import scan_service, scan_state, scheduler, server_mode
from riviu.api import scan as scan_api
from riviu.api import security
from riviu.api import sources as sources_api

from test_platform_source_api import make_source
from test_server_mode import PUBLIC, login, server  # noqa: F401  (fixture)
from test_ui_review_fixes import NODE_HARNESS, run_js


WS_URL = "wss://113-161-254-76.sslip.io/ws"
# Login, logout, the probes and the paths the viewer page needs; everything else is admin.
PUBLIC_ROUTES = {("GET", "/login"), ("POST", "/login"), ("POST", "/logout"), ("GET", "/auth-status"), ("GET", "/healthz")}


@pytest.fixture
def workbook(server):
    data_dir = backend.EXCEL_DIR
    make_source(f"{data_dir}/A.xlsx", "A")
    return "A.xlsx"


def _flatten(routes):
    for route in routes:
        included = getattr(route, "original_router", None)  # FastAPI wraps include_router()
        if included is not None:
            yield from _flatten(included.routes)
        else:
            yield route


def app_routes():
    for route in _flatten(backend.app.routes):
        if isinstance(route, (APIRoute, Route)) and route.methods:
            for method in sorted(route.methods - {"HEAD"}):
                yield method, route.path


def test_every_route_outside_the_viewer_allowlist_needs_the_admin_login(server, workbook):
    """Enumerates the real routes, so a new route is admin-only unless it is allowlisted."""
    anonymous, checked = [], 0
    for method, path in app_routes():
        if (method, path) in PUBLIC_ROUTES or path in security.INTERNAL_PATHS:
            continue  # internal routes take a bearer token instead (tests/test_store_postgres.py)
        response = server.request(method, path, headers={"Origin": PUBLIC})
        if security.viewer_request_allowed(method, path):
            anonymous.append((method, path, response.status_code))
            continue
        checked += 1
        assert response.status_code == 401, (method, path, response.status_code)
        assert response.json() == {"error": "Cần đăng nhập"}
    assert checked >= 25  # upload, select, Google, proxy, cookies, download, schedule, /admin...
    assert {(method, path) for method, path, _ in anonymous} == {
        ("GET", "/"), ("GET", "/api/version"), ("GET", "/list-files"), ("GET", "/preview-excel"),
        ("GET", "/summary-dashboard"), ("GET", "/source-preferences"), ("GET", "/report-partners"),
        ("GET", "/scrape-history"), ("POST", "/export-report"), ("GET", "/logo.png"),
        ("GET", "/riviu-logo.png"), ("GET", "/favicon.ico"),
        # The same reads on Riviu Data (RIVIU_STORAGE=postgres; 404 here on workbook storage).
        ("GET", "/riviu-data/months"), ("GET", "/riviu-data/rows"), ("GET", "/riviu-data/report-partners"),
        ("POST", "/riviu-data/export-report"),
    }


def test_anonymous_viewer_reads_files_previews_partners_and_exports_reports(server, workbook, monkeypatch):
    monkeypatch.setattr(sources_api, "oauth_status", lambda _base: pytest.fail("viewer must not query Google status"))
    query = {"file_id": workbook, "platform": "threads"}
    files = server.get("/list-files", params=query)
    assert files.status_code == 200 and files.json()["current"] == workbook
    # No Google status for the public (it would take the Google I/O lock on every page view).
    assert files.json()["googleOAuthConfigured"] is False and files.json()["googlePushReady"] is False
    assert server.get("/preview-excel", params=query).json()["currentSheet"] == "A Data"
    assert server.get("/summary-dashboard", params=query).status_code == 200
    partners = server.get("/report-partners", params=query).json()["partners"]
    assert [item["name"] for item in partners] == ["A Threads"]
    assert server.get("/source-preferences").json() == {"sources": {}}
    assert server.get("/scrape-history").json() == {"history": []}
    assert server.get("/api/version").status_code == 200
    assert server.get("/static/app.js").status_code == 200
    assert server.get("/static/guide/01-dang-nhap.png").status_code == 200
    exported = server.post("/export-report", headers={"Origin": PUBLIC}, json={
        **query, "sheetName": "A Data", "partners": ["A Threads"], "applyMinViews": False,
    })
    assert exported.status_code == 200 and exported.content[:2] == b"PK"
    # Reads only: the viewer could not pick the legacy selection or save preferences.
    assert server.post("/select-file", headers={"Origin": PUBLIC}, json={"file_id": workbook}).status_code == 401
    assert server.post("/source-preferences", headers={"Origin": PUBLIC}, json={"sources": {}}).status_code == 401
    assert server.get("/download-excel", params=query).status_code == 401


def test_viewer_keeps_origin_checks_cross_site_block_and_security_headers(server, workbook):
    assert server.get("/", headers={"Host": "evil.example"}).status_code == 403
    assert server.get("/list-files", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert server.post("/export-report", headers={"Origin": "https://evil.example"}, json={}).status_code == 403
    assert server.get("/static/../download-excel").status_code in (401, 404)
    page = server.get("/", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
    assert page.status_code == 200
    assert page.headers["x-frame-options"] == "DENY" and page.headers["cache-control"] == "no-store"


ADMIN_ONLY_IDS = (
    "startBtn", "cancelBtn", "scanSettingsButton", "workerCountSelect", "proxyUseCheckbox", "proxyModal",
    "threadsCookieModal", "tiktokCookieModal", "threadsSessionUse", "tiktokSessionUse", "uploadArea", "fileInput",
    "googleServiceAccountBtn", "googleOauthBtn", "googleLoginBtn", "googlePasteModal", "refreshPartnerBtn",
    "logoutButton", "scheduleButton", "scheduleModal", "guideButton", "guideModal",
)
SHARED_IDS = ("platformBar", "excelFileSelect", "reportModal", "exportReportBtn", "historyModal", "previewTable")
# The viewer's own report layout (KPIs, partner export, link table) and the admin's scan workspace.
VIEWER_ONLY_IDS = ("vwExportButton", "vwDataPanel", "vwTableSearch")
ADMIN_WORKSPACE_IDS = ("sourceDrawerButton", "sourceDrawer", "scanSettingsDrawer")


def ids(html):
    return set(re.findall(r'\sid="([^"]+)"', html))


def test_viewer_page_renders_no_admin_controls_and_links_to_admin(server):
    page = server.get("/")
    assert page.status_code == 200 and 'data-role="viewer"' in page.text
    rendered = ids(page.text)
    assert not rendered & set(ADMIN_ONLY_IDS), rendered & set(ADMIN_ONLY_IDS)
    assert set(SHARED_IDS) | set(VIEWER_ONLY_IDS) <= rendered
    assert not rendered & set(ADMIN_WORKSPACE_IDS)
    every_id = re.findall(r'\sid="([^"]+)"', page.text)
    assert len(every_id) == len(set(every_id))  # viewer_body.html keeps the ids unique
    # No scan workspace for viewers: no status tabs, progress bar or log on screen.
    for marker in ('class="workspace-tabs"', 'class="progress-section"', 'class="scan-toolbar"'):
        assert marker not in page.text
    assert 'id="adminLink" href="/admin"' in page.text and "Quản trị" in page.text
    for text in ("downloadCurrentWorkbook()", 'action="/logout"', "startScraping()", "uploadFile(this)", "refreshPartnerLinks()", "syncGoogleSheet"):
        assert text not in page.text


def test_admin_page_has_every_control_and_no_admin_link(server):
    login(server)
    page = server.get("/admin")
    assert page.status_code == 200 and 'data-role="admin"' in page.text
    assert set(ADMIN_ONLY_IDS) | set(SHARED_IDS) | set(ADMIN_WORKSPACE_IDS) <= ids(page.text)
    assert not ids(page.text) & set(VIEWER_ONLY_IDS)
    assert "adminLink" not in page.text and "Lịch tự động" in page.text
    # `/` stays the viewer for a logged-in admin too.
    assert 'data-role="viewer"' in server.get("/").text


def test_admin_login_returns_to_admin_and_never_redirects_off_site(server):
    page = server.get("/admin", headers={"Accept": "text/html"})
    assert page.status_code == 303 and page.headers["location"] == "/login?next=/admin"
    form = server.get("/login?next=/admin")
    assert '<input type="hidden" name="next" value="/admin">' in form.text
    assert '<input type="hidden" name="next" value="/admin">' in server.get("/login?next=https://evil.example").text
    assert server.get("/auth-status").json() == {"authenticated": False, "role": "viewer", "user": None}
    response = server.post("/login", data={"password": "synthetic-team-password", "next": "https://evil.example/x"}, headers={"Origin": PUBLIC})
    assert response.status_code == 303 and response.headers["location"] == "/admin"
    assert server.get("/auth-status").json() == {"authenticated": True, "role": "admin", "user": {"username": "admin", "role": "admin"}}
    again = server.get("/login?next=/")
    assert again.status_code == 303 and again.headers["location"] == "/"
    assert server.get("/admin").status_code == 200


def test_admin_route_does_not_exist_outside_server_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mode, "ACTIVE", None)
    monkeypatch.setattr(backend, "EXCEL_DIR", str(tmp_path))
    with TestClient(backend.app, base_url="http://127.0.0.1:1231") as client:
        page = client.get("/")
        assert page.status_code == 200 and 'data-role="admin"' in page.text and 'id="startBtn"' in page.text
        assert "adminLink" not in page.text and "scheduleButton" not in page.text
        assert client.get("/admin").status_code == 404
        assert client.get("/schedule").status_code == 404
    assert scheduler._LOOP_TASK is None  # no scheduled runs outside server mode


# ---- /ws ---------------------------------------------------------------------------------

@pytest.fixture
def scan_spy(server, monkeypatch):
    calls = []

    async def prepare(payload):
        calls.append(("prepare", payload.get("platform")))
        return "fixture.xlsx", scan_service.scan_options(platform="tiktok", file_id="A.xlsx", sheet_name="A Data")

    async def runner(target_path, options, run_context=None):
        calls.append(("run", options["file_id"]))

    monkeypatch.setattr(scan_service, "prepare_scan_start", prepare)
    monkeypatch.setattr(scan_service, "run_scraper_safely", runner)
    monkeypatch.setattr(scan_state, "manager", scan_state.ConnectionManager())
    monkeypatch.setattr(scan_state, "SCRAPE_TASK", None)
    return calls


def test_anonymous_socket_watches_but_cannot_start_cancel_or_skip_the_retry(server, scan_spy, monkeypatch):
    skip = asyncio.Event()
    monkeypatch.setattr(scan_state, "RETRY_SKIP", skip)
    with server.websocket_connect(WS_URL, headers={"Origin": PUBLIC}) as ws:
        assert ws.receive_json()["type"] == "session"
        for action in ("start", "cancel", "skip_retry"):
            ws.send_text(json.dumps({"action": action, "platform": "tiktok", "file_id": "A.xlsx"}))
            refused = ws.receive_json()
            assert refused == {"type": "log", "message": scan_api.VIEWER_REFUSED, "level": "WARN"}
    assert scan_spy == [] and not skip.is_set()


def test_admin_socket_skips_the_retry_wait_only_while_one_is_pending(server, monkeypatch):
    login(server)
    token = server.cookies.get(security.AUTH_COOKIE)
    headers = {"Origin": PUBLIC, "Cookie": f"{security.AUTH_COOKIE}={token}"}
    monkeypatch.setattr(scan_state, "RETRY_SKIP", None)
    with server.websocket_connect(WS_URL, headers=headers) as ws:
        assert ws.receive_json()["type"] == "session"
        ws.send_text(json.dumps({"action": "skip_retry"}))
        assert ws.receive_json()["message"] == "Phiên quét không còn chờ quét lại."
        skip = asyncio.Event()
        monkeypatch.setattr(scan_state, "RETRY_SKIP", skip)
        ws.send_text(json.dumps({"action": "skip_retry"}))
        assert "Bỏ qua quét lại" in ws.receive_json()["message"]
    assert skip.is_set()


def test_admin_socket_starts_and_a_logout_revokes_it_at_once(server, scan_spy):
    login(server)
    token = server.cookies.get(security.AUTH_COOKIE)
    headers = {"Origin": PUBLIC, "Cookie": f"{security.AUTH_COOKIE}={token}"}
    with server.websocket_connect(WS_URL, headers=headers) as ws:
        assert ws.receive_json()["type"] == "session"
        ws.send_text(json.dumps({"action": "start", "platform": "tiktok", "file_id": "A.xlsx"}))
        assert ws.receive_json()["type"] == "status"
        assert ("prepare", "tiktok") in scan_spy
        server.post("/logout", headers={"Origin": PUBLIC})
        scan_spy.clear()
        ws.send_text(json.dumps({"action": "start", "platform": "tiktok", "file_id": "A.xlsx"}))
        message = ws.receive_json()
        while message.get("type") != "log" or message.get("message") != scan_api.VIEWER_REFUSED:
            message = ws.receive_json()
    assert ("prepare", "tiktok") not in scan_spy


# ---- The viewer page script never calls an admin endpoint --------------------------------

# The viewer's source rows have the file label and sheet select only (no Google link, sync or push).
VIEWER_HARNESS = NODE_HARNESS.replace(
    "body:element('body')", "body:Object.assign(element('body'),{dataset:{role:'viewer',serverMode:'1'}})",
).replace("if (role !== 'button') assert.ok(renderedIds.has(id)", "if (['sourceLabel','sheet'].includes(role)) assert.ok(renderedIds.has(id)")


def run_viewer_js(source):
    import test_ui_review_fixes as ui

    original = ui.NODE_HARNESS
    ui.NODE_HARNESS = VIEWER_HARNESS
    try:
        run_js(source)
    finally:
        ui.NODE_HARNESS = original


def test_viewer_script_only_reads_allowlisted_apis_and_always_shows_the_platform_source():
    assert VIEWER_HARNESS.count("role:'viewer'") == 1 and "['sourceLabel','sheet']" in VIEWER_HARNESS
    allowed_get = sorted(security.VIEWER_GET_PATHS)
    allowed_post = sorted(security.VIEWER_POST_PATHS)
    run_viewer_js(f"""(async()=>{{
      assert.equal(IS_VIEWER, true);
      // A file choice an earlier viewer page kept in this tab no longer overrides the platform's source.
      const calls=[]; const stored={{riviuViewerSourcesV1:JSON.stringify({{tiktok:{{fileId:'B.xlsx',displaySheet:'Data',scanSheet:'Data',pushSheet:'',url:''}}}})}};
      globalThis.sessionStorage={{getItem:k=>stored[k]??null,setItem:(k,v)=>{{stored[k]=v;}}}};
      const files=[{{id:'A.xlsx',label:'A'}},{{id:'B.xlsx',label:'B'}}];
      fetch=async(url,options={{}})=>{{
        const path=String(url).split('?')[0]; const method=(options.method||'GET').toUpperCase();
        calls.push([method,path,String(url)]);
        const params=new URLSearchParams(String(url).split('?')[1]||'');
        let body={{}};
        if(path==='/source-preferences') body={{sources:{{tiktok:{{fileId:'A.xlsx',displaySheet:'Data',scanSheet:'Data',pushSheet:'',url:''}}}}}};
        if(path==='/list-files') body={{files,current:params.get('file_id'),currentLabel:params.get('file_id'),sheets:['Data'],currentSheet:'Data',scanSheet:'Data'}};
        return {{ok:true,status:200,json:async()=>body,headers:{{get:()=>null}}}};
      }};
      updateFileList=originalUpdateFileList; syncProxyCardActiveState=()=>{{}};
      await window.onload();
      assert.equal(currentFileId,'A.xlsx');
      await selectFile('B.xlsx');
      assert.equal(currentFileId,'A.xlsx');  // no file choice on the viewer
      await setPlatform('threads'); await setPlatform('tiktok');
      ws=new WebSocket('test'); sent.length=0;
      startScraping(); cancelScraping(); await refreshPartnerLinks(); await syncGoogleSheet('tiktok');
      await pushCurrentSheetToGoogle(null,'tiktok'); await downloadCurrentWorkbook(); await uploadFile({{files:[{{name:'x.xlsx'}}]}});
      await refreshThreadsCookieStatus(); await refreshTikTokCookieStatus(); await refreshGoogleOauthStatus(); await refreshProxyStatus();
      persistSources(); await flushSourcePreferences();
      assert.equal(sent.length,0);
      const allowedGet=new Set({json.dumps(allowed_get)}); const allowedPost=new Set({json.dumps(allowed_post)});
      for (const [method,path,url] of calls) {{
        assert.ok(method==='GET' ? allowedGet.has(path) : allowedPost.has(path), method+' '+url);
        if (['/list-files','/preview-excel','/summary-dashboard','/report-partners'].includes(path)) assert.match(url,/file_id=/);
      }}
      assert.ok(calls.some(([,path])=>path==='/list-files'));
    }})()""")


def test_viewer_table_formats_known_metrics_and_keeps_unknown_blank():
    run_viewer_js(r"""
      const rows=[
        {STT:'1', 'LƯỢT XEM':'1.200', TIM:'0', Link:'https://www.tiktok.com/@a/video/1', 'TÊN KÊNH':'A', Spare:''},
        {STT:'2', 'LƯỢT XEM':'', TIM:'3', Link:'https://www.tiktok.com/@b/video/2', 'TÊN KÊNH':'B', Spare:''},
        {STT:'', 'LƯỢT XEM':'1200', TIM:'3', Link:'Tổng', 'TÊN KÊNH':'', Spare:''},
      ];
      renderPreviewTable({rows, columns:['STT','LƯỢT XEM','TIM','Link','TÊN KÊNH','Spare'], linkColumn:'Link'});
      const header=el('previewHeader').innerHTML, body=el('previewBody').innerHTML;
      assert.equal(el('vwRowCount').textContent,'2');            // the sheet's "Tổng" row is not a link
      assert.ok(!header.includes('Spare'));                      // a column blank on every row is left out
      // Index column first, then link, channel and metrics, in sentence case.
      assert.deepEqual([...header.matchAll(/>([^<>]+)<\/th>/g)].map(m=>m[1]), ['Stt','Link','Tên kênh','Lượt xem','Tim']);
      assert.ok(body.includes(`<td class="vw-num">${(1200).toLocaleString('vi-VN')}</td>`));
      assert.ok(body.includes('<td class="vw-num">0</td>'));     // a confirmed 0 is shown
      assert.match(body,/<td class="vw-num"><\/td>/);           // unknown views stay blank, never 0
      assert.match(body,/>@a<span class="vw-link-detail"> · video 1<\/span><\/a>/);
    """)


def test_scan_settings_are_saved_per_platform_by_the_admin_only_and_hold_flags_only(server):
    # Owner report: "Proxy xoay" / Cookie Threads were off again after switching tabs or reloading,
    # and the shared admin login is used from several machines: the server keeps them.
    settings_file = Path(backend.EXCEL_DIR) / "data" / "scan_settings.json"
    threads = {"workers": 20, "scrapeMode": "browser", "useProxy": True, "useSession": True}
    assert server.get("/scan-settings").status_code == 401
    assert server.post("/scan-settings", headers={"Origin": PUBLIC}, json={"platforms": {"threads": threads}}).status_code == 401
    assert not settings_file.exists()
    login(server)
    assert server.get("/scan-settings").json()["platforms"]["threads"] == {"workers": 10, "scrapeMode": "hybrid", "useProxy": False, "useSession": False}
    saved = server.post("/scan-settings", headers={"Origin": PUBLIC}, json={"platforms": {"threads": threads}})
    assert saved.status_code == 200 and saved.json()["success"]
    reloaded = server.get("/scan-settings").json()["platforms"]
    assert reloaded["threads"] == threads
    assert reloaded["tiktok"] == {"workers": 10, "scrapeMode": "request", "useProxy": False, "useSession": False}
    assert json.loads(settings_file.read_text(encoding="utf-8")) == {"platforms": reloaded}
    # Never proxy text, cookies or anything else: unknown keys and bad values are refused.
    for bad in ({"threads": {**threads, "proxyText": "user:pass@proxy:1"}}, {"threads": {"cookies": "sessionid=x"}},
                {"threads": {"useProxy": "yes"}}, {"threads": {"workers": 7}}, {"threads": {"scrapeMode": "fast"}},
                {"youtube": {}}):
        response = server.post("/scan-settings", headers={"Origin": PUBLIC}, json={"platforms": bad})
        assert response.status_code == 400, bad
    assert server.get("/scan-settings").json()["platforms"]["threads"] == threads
    assert "proxy:1" not in settings_file.read_text(encoding="utf-8")


def test_viewer_reads_the_retry_cooldown_as_updated_data_with_a_retry_time():
    # Owner report: "đang chờ quét lại một số link lỗi" read as an error to viewers.
    from test_ui_review_fixes import RETRY_RUN_JS

    run_viewer_js("(async()=>{" + RETRY_RUN_JS + """
      status({phase:'running',total:526,processed:100,success:90});
      assert.equal(el('vwScanState').textContent,'Đang cập nhật số liệu…');
      status(cooldown);
      const text=el('vwScanState').textContent;
      assert.equal(el('vwScanState').hidden,false);
      const at=new Date(now+300000);
      const hhmm=String(at.getHours()).padStart(2,'0')+':'+String(at.getMinutes()).padStart(2,'0');
      assert.equal(text,'Số liệu đã cập nhật. Hệ thống sẽ tự quét lại vài link chưa đọc được lúc '+hhmm+'.');
      assert.ok(!/lỗi/.test(text), text);
    })()""")
