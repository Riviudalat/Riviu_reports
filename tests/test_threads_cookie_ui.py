"""Cookie UI redesign: offline fixtures only, never access backend or vault."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from test_ui_review_fixes import ROOT, run_js


def test_source_switch_clears_both_inputs():
    run_js("""
      el('threadsCookieText').value='secret'; el('threadsCookieFile').value='cookie.json';
      setThreadsCookieSource('paste');
      assert.equal(threadsCookieSource,'paste');
      assert.equal(el('threadsCookieText').value,''); assert.equal(el('threadsCookieFile').value,'');
      assert.equal(el('threadsCookiePanelFile').hidden,true);
      assert.equal(el('threadsCookiePanelPaste').hidden,false);
    """)


def test_file_read_owns_busy_gate_and_source():
    run_js("""(async()=>{
      let finish; const calls=[];
      el('threadsCookieFile').files=[{size:20,text:()=>new Promise(resolve=>finish=resolve)}];
      fetch=async(url,options)=>{calls.push([url,options]);return {ok:true,json:async()=>({success:true,status:{configured:true,state:'valid',generation:'new',count:1}})}};
      const task=importThreadsCookie();
      assert.equal(threadsCookieBusy,true);
      setThreadsCookieSource('paste'); assert.equal(threadsCookieSource,'file');
      closeThreadsCookieModal(); assert.equal(threadsCookieBusy,true);
      importThreadsCookie(); assert.equal(calls.length,0);
      finish('[{"synthetic":"only"}]'); await task;
      assert.equal(calls.length,1); assert.equal(calls[0][0],'/threads-session/import');
      assert.equal(calls[0][1].body,'[{"synthetic":"only"}]');
      assert.equal(threadsCookieBusy,false); assert.equal(el('threadsCookieText').value,'');
    })()""")


def test_paste_does_not_read_hidden_file_and_delete_needs_confirmation():
    run_js("""(async()=>{
      const calls=[]; threadsCookieSource='paste'; threadsCookieState={configured:true,state:'valid',generation:'same'};
      el('threadsCookieFile').files=[{size:20,text:()=>{throw Error('hidden file read')}}];
      el('threadsCookieText').value='[{"synthetic":"paste"}]';
      fetch=async(url,options)=>{calls.push([url,options]);return {ok:true,json:async()=>({success:true,status:{configured:true,state:'valid',generation:'same'}})}};
      await importThreadsCookie(); assert.equal(calls[0][1].body,'[{"synthetic":"paste"}]');
      deleteThreadsCookie(); assert.equal(calls.length,1); assert.equal(el('threadsCookieDeleteConfirm').hidden,false);
      cancelThreadsCookieDelete(); await confirmThreadsCookieDelete(); assert.equal(calls.length,1);
      deleteThreadsCookie(); await confirmThreadsCookieDelete(); assert.equal(calls.length,2);
      assert.equal(calls[1][1].method,'DELETE');
    })()""")


def test_stale_status_cannot_replace_import_result():
    run_js("""(async()=>{
      let resolveStatus; threadsCookieSource='paste'; el('threadsCookieText').value='[]';
      fetch=async(url)=>url.endsWith('/status')?await new Promise(resolve=>resolveStatus=resolve):{ok:true,json:async()=>({success:true,status:{configured:true,state:'valid',generation:'new'}})};
      const pending=refreshThreadsCookieStatus();
      await importThreadsCookie();
      resolveStatus({json:async()=>({configured:false,state:'none',generation:'old'})});
      await pending; assert.equal(threadsCookieState.generation,'new'); assert.equal(threadsCookieState.state,'valid');
    })()""")


@pytest.mark.parametrize("width", [1440, 1180, 1024, 960, 760, 390])
def test_cookie_modal_real_dom_offline_responsive(width, tmp_path):
    html = (ROOT / "templates/index.html").read_text(encoding="utf-8").replace("{{ asset_version }}", "fixture")
    css = (ROOT / "static/styles.css").read_text(encoding="utf-8")
    js = (ROOT / "static/app.js").read_text(encoding="utf-8")
    state = {"configured": True, "state": "valid", "count": 8, "generation": "fixture", "checkedAt": "01/10/2026 12:00", "message": "Phiên fixture đã xác minh."}
    calls = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": 1000 if width > 760 else 844})
            page.add_init_script("""window.WebSocket=class {static OPEN=1;constructor(){this.readyState=1;}send(){}close(){}};""")
            def route_request(route):
                url = route.request.url
                if url.startswith("http://fixture.local/"):
                    path = url.split("fixture.local", 1)[1].split("?", 1)[0]
                    if path == "/": return route.fulfill(body=html, content_type="text/html")
                    if path == "/static/styles.css": return route.fulfill(body=css, content_type="text/css")
                    if path == "/static/app.js": return route.fulfill(body=js, content_type="text/javascript")
                    if path in {"/static/platform-icons/tiktok.svg", "/static/platform-icons/threads.svg"}:
                        return route.fulfill(body=(ROOT / path.lstrip('/')).read_text(encoding='utf-8'), content_type='image/svg+xml')
                    if path.startswith("/threads-session"):
                        calls.append((path, route.request.method))
                        if route.request.method == "DELETE":
                            state.update(configured=False,state="none",count=0,generation="deleted")
                            return route.fulfill(json=state)
                        if route.request.method == "POST":
                            return route.fulfill(json={"success":True,"status":state,"check":{"state":"valid"}})
                        return route.fulfill(json=state)
                    if path == "/proxy-list": return route.fulfill(json={"configured":False,"count":0,"text":""})
                    if path == "/list-files": return route.fulfill(json={"files":[],"currentFile":""})
                    return route.fulfill(json={})
                route.abort()
            page.route("**/*", route_request)
            errors=[]
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("http://fixture.local/", wait_until="domcontentloaded")
            page.wait_for_function("threadsCookieState.configured")
            page.locator("#platformThreads").click()
            if width <= 760:
                page.evaluate("openScanSettingsDrawer()")
            page.locator("#threadsCookieBtn").click()
            assert page.locator("#threadsCookieModal").is_visible()
            assert page.locator("#threadsCookieSavedBadge").inner_text()=="Hợp lệ"
            page.locator("#threadsCookieTabFile").focus()
            page.keyboard.press('ArrowRight')
            assert page.locator('#threadsCookieTabPaste').get_attribute('aria-selected') == 'true'
            assert page.locator("#threadsCookieText").is_visible()
            assert page.locator("#threadsCookiePanelFile").is_hidden()
            page.locator('#threadsCookieImport').focus()
            page.keyboard.press('Tab')
            assert page.locator('.threads-cookie-modal .modal-header .threads-cookie-close').evaluate('node => node === document.activeElement')
            page.locator("#threadsCookieText").fill('[{"synthetic":"only"}]')
            page.locator("#threadsCookieImport").click()
            page.wait_for_function("!threadsCookieBusy")
            assert page.locator("#threadsCookieText").input_value()==""
            page.locator("#threadsCookieVerify").click()
            page.wait_for_function("!threadsCookieBusy")
            assert page.locator('.threads-cookie-modal').evaluate('node => node.scrollWidth <= node.clientWidth + 1')
            assert page.locator('#threadsCookieModal .modal-body').evaluate('node => node.scrollWidth <= node.clientWidth + 1')
            modal_box=page.locator(".threads-cookie-modal").bounding_box()
            assert modal_box and modal_box["x"] >= 0 and modal_box["width"] <= width
            assert page.locator("#threadsCookieDeleteConfirm").is_hidden()
            page.screenshot(path=str(tmp_path / f"cookie-{width}.png"), full_page=True)
            page.locator("#threadsCookieDelete").click()
            assert not any(method=='DELETE' for _,method in calls)
            page.locator("#threadsCookieDeleteAccept").click()
            page.wait_for_function("!threadsCookieBusy && !threadsCookieState.configured")
            assert page.locator("#threadsCookieVerify").is_disabled()
            page.keyboard.press("Escape")
            assert page.locator("#threadsCookieModal").is_hidden()
            page.evaluate('openSourceDrawer()')
            page.wait_for_timeout(250)
            rows = page.locator('[data-source-platform]')
            assert rows.count() == 2
            assert rows.nth(0).get_attribute('data-source-platform') == 'tiktok'
            assert rows.nth(1).get_attribute('data-source-platform') == 'threads'
            assert page.locator('#googleSheetUrlInput').is_visible()
            assert page.locator('#threadsGoogleSheetUrlInput').is_visible()
            assert not page.locator('#threadsCookieStatus').evaluate('node => node.offsetWidth > 1 && node.offsetHeight > 1')
            page.screenshot(path=str(tmp_path / f'sources-{width}.png'), full_page=True)
            assert errors==[],errors
        finally:
            browser.close()
