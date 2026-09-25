"""Regression checks execute the browser script with controlled DOM/network edges."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]

NODE_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
function element(id = '') {
  const classes = new Set();
  return {id, value:'', checked:false, disabled:false, hidden:false, dataset:{},
    textContent:'', innerText:'', innerHTML:'', style:{}, children:[],
    classList:{add(...x){x.forEach(v=>classes.add(v));}, remove(...x){x.forEach(v=>classes.delete(v));},
      contains(x){return classes.has(x);}, toggle(x,on){on ? classes.add(x) : classes.delete(x);}},
    addEventListener(){}, setAttribute(){}, appendChild(x){this.children.push(x);},
    prepend(x){this.children.unshift(x);}, querySelector(){return null;}, querySelectorAll(){return [];},
    scrollTo(){}, focus(){}, closest(){return null;}};
}
function el(id) {if(!elements.has(id)) elements.set(id,element(id)); return elements.get(id);}
const buttons = [el('platformTikTok'),el('platformThreads')];
const document = {getElementById:el, body:element('body'), createElement:element,
  querySelector(){return null;}, querySelectorAll(selector){return selector==='.platform-option'?buttons:[];},
  addEventListener(){}};
el('cancelBtn').disabled=true;
el('workerCountSelect').value='2'; el('scrapeModeSelect').value='request';
el('scanSheetSelect').value='Sheet A';
const sent=[]; const sockets=[];
class WebSocket {static OPEN=1; constructor(url){this.url=url;this.readyState=1;sockets.push(this);} send(x){sent.push(JSON.parse(x));}}
const context = vm.createContext({console,document,WebSocket,Map,Set,Date,Number,JSON,Promise,URL,
  URLSearchParams,
  window:{location:{protocol:'http:',hostname:'localhost',host:'localhost:1231',port:'1231'},
    matchMedia(){return {matches:true};},addEventListener(){},setInterval(){},__TAURI__:null},
  setTimeout(){},clearTimeout(){},localStorage:{getItem(){return null;},setItem(){}},
  fetch:async()=>({ok:true,json:async()=>({})}),assert,el,sent,sockets,buttons});
vm.runInContext(fs.readFileSync('static/app.js','utf8'),context);
// Suppress unrelated presentation / asynchronous API edges, not scan routing/rendering.
vm.runInContext(`const originalLoadPreview=loadPreview; const originalUpdateFileList=updateFileList;
  addLog=()=>{}; notify=()=>{}; setGooglePushState=()=>{};
  updateFileList=async()=>{}; loadPreview=async()=>{}; syncProxyCardActiveState=()=>{};
  closeReportModal=()=>{}; closeCompactDrawers=()=>{}; setWorkspaceTab=()=>{};
  clearDuplicateLinks=()=>{}; clearFailedLinks=()=>{}; appendFailedLink=()=>{};
  updatePendingLiveResults=()=>{}; showToast=()=>{};`,context);
"""


def run_js(source):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser script regression tests")
    command = NODE_HARNESS + "\n(async()=>{await vm.runInContext(" + json.dumps(source) + ",context);})().catch(e=>{console.error(e);process.exitCode=1;});"
    result = subprocess.run([node, "-e", command], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_partner_refresh_uses_the_modal_sheet():
    run_js("""
      ws=new WebSocket('test'); activePlatform='threads';
      reportSheetName='Sheet B'; reportPartners=[{name:'Partner B'}]; selectedPartners=new Set(['Partner B']);
      refreshPartnerLinks();
      assert.equal(sent[0].sheet_name,'Sheet B');
      assert.equal(JSON.stringify(sent[0].partners),'["Partner B"]');
    """)


def test_generated_result_tabs_are_not_scan_sources():
    run_js("""
      assert.equal(JSON.stringify(filterDataSheets(['T9','Data','25-09-2026-14-30',
        'T9 25-09-2026-14:30','T9 25-09-2026-1430-2','Report Seeding Threads', 'Tổng kết T9'])),
        '["T9","Data"]');
    """)


@pytest.mark.parametrize('loader', ['preview', 'summary', 'partners', 'files'])
def test_async_read_drops_response_after_file_switch(loader):
    calls = {
        'preview': "originalLoadPreview('Sheet A')",
        'summary': "renderSummaryDashboard('Sheet A')",
        'partners': "loadReportPartners('Sheet A')",
        'files': "originalUpdateFileList()",
    }
    run_js("""
      (async()=>{
        currentFileId='Book A.xlsx'; currentSheetName='Sheet A';
        let finish; fetch=()=>new Promise(resolve=>{finish=resolve;});
        renderSheetTabs=()=>{}; renderScanSheetOptions=()=>{}; renderPushSheetOptions=()=>{};
        syncCompactSourceSummary=()=>{}; refreshGoogleOauthStatus=async()=>{};
        setPreviewTableVisible=()=>{}; renderReportSheetOptions=()=>{}; renderPartnerList=()=>{};
        updateReportSummary=()=>{};
        const pending=CALL;
        currentFileId='Book B.xlsx'; currentSheetName='Sheet B';
        reportPartners=[{name:'Partner B'}]; reportSheetName='Sheet B';
        el('summaryDashboard').innerHTML='Summary B'; el('previewBody').innerHTML='Preview B';
        finish({ok:true,json:async()=>({file:'Book A.xlsx',current:'Book A.xlsx',currentSheet:'Sheet A',
          sheets:['Sheet A'],columns:['Link'],data:[],rows:[],partners:[{name:'Partner A'}],files:[]})});
        await pending;
        assert.equal(currentFileId,'Book B.xlsx'); assert.equal(currentSheetName,'Sheet B');
        assert.equal(reportPartners[0].name,'Partner B'); assert.equal(reportSheetName,'Sheet B');
        assert.equal(el('summaryDashboard').innerHTML,'Summary B');
        assert.equal(el('previewBody').innerHTML,'Preview B');
      })()
    """.replace('CALL', calls[loader]))


def test_latest_preview_request_wins_for_same_workbook():
    run_js("""
      (async()=>{
        currentFileId='Book.xlsx'; currentSheetName='Sheet A';
        const pending=[]; fetch=()=>new Promise(resolve=>pending.push(resolve));
        renderSheetTabs=()=>{}; renderScanSheetOptions=()=>{}; renderPushSheetOptions=()=>{};
        syncCompactSourceSummary=()=>{}; setPreviewTableVisible=()=>{};
        const first=originalLoadPreview('Sheet A'); currentSheetName='Sheet B';
        const second=originalLoadPreview('Sheet B');
        pending[1]({ok:true,json:async()=>({file:'Book.xlsx',currentSheet:'Sheet B',sheets:['Sheet A','Sheet B'],
          columns:['Link'],data:[]})}); await second;
        pending[0]({ok:true,json:async()=>({file:'Book.xlsx',currentSheet:'Sheet A',sheets:['Sheet A','Sheet B'],
          columns:['Link'],data:[]})}); await first;
        assert.equal(currentSheetName,'Sheet B');
      })()
    """)


def test_preview_response_does_not_render_after_platform_change():
    run_js("""
      (async()=>{
        currentFileId='Book.xlsx'; currentSheetName='Data'; activePlatform='tiktok';
        let finish; fetch=()=>new Promise(resolve=>{finish=resolve;});
        const pending=originalLoadPreview('Data');
        activePlatform='threads'; el('previewBody').innerHTML='Threads data';
        finish({ok:true,json:async()=>({file:'Book.xlsx',currentSheet:'Data',message:'Old TikTok result'})});
        await pending; assert.equal(el('previewBody').innerHTML,'Threads data');
      })()
    """)


def test_websocket_preserves_same_origin_and_restores_running_snapshot():
    run_js("""
      connectWS(); assert.equal(sockets[0].url,'ws://localhost:1231/ws');
      sockets[0].onmessage({data:JSON.stringify({type:'session',data:{runId:'run-1',platform:'threads',
        fileId:'Book.xlsx',sheetName:'Sheet B',running:true,status:{phase:'running',total:4,processed:1,success:1}}})});
      assert.equal(document.body.dataset.platform,'threads');
      assert.equal(el('liveSavedHeader').textContent,'REPOST');
      assert.equal(el('startBtn').disabled,true); assert.equal(el('cancelBtn').disabled,false);
      assert.equal(el('scanSheetSelect').disabled,true);
      assert.equal(currentFileId,'Book.xlsx'); assert.equal(currentScanSheetName,'Sheet B');
      assert.equal(el('processedLinks').textContent,1);
    """)


def test_idle_session_and_completed_old_run_do_not_lock_or_change_selected_file():
    run_js("""
      connectWS();
      sockets[0].onmessage({data:JSON.stringify({type:'session',data:{running:false,status:{},results:[]}})});
      assert.equal(el('startBtn').disabled,false);
      currentFileId='Other.xlsx'; scanPhase='running';
      sockets[0].onmessage({data:JSON.stringify({type:'session',data:{runId:'old',fileId:'Old.xlsx',platform:'threads',
        running:false,status:{phase:'completed',done:true}}})});
      assert.equal(el('startBtn').disabled,false); assert.equal(currentFileId,'Other.xlsx');
      sockets[0].onmessage({data:JSON.stringify({type:'log',runId:'old',fileId:'Old.xlsx',platform:'threads',message:'heartbeat'})});
      assert.equal(currentFileId,'Other.xlsx');
    """)


def test_threads_result_metadata_preserves_unknown_metrics_when_ui_was_tiktok():
    run_js("""
      activePlatform='tiktok';
      appendData({id:1,platform:'threads',url:'https://www.threads.com/@author/post/abc',status:'Partial: views',
        views:372,likes:null,comments:1,saves:null,shares:2});
      const html=el('dataFeed').children[0].innerHTML;
      assert.match(html,/data-label="Repost"[^>]*><\\/td>/);
      assert.match(html,/data-label="Tim"[^>]*><\\/td>/);
      assert.match(html,/>372<\\/td>/);
    """)


@pytest.mark.parametrize("platform", ["tiktok", "threads"])
def test_progress_requires_final_saved_confirmation_and_reports_failure(platform):
    run_js(f"""
      activePlatform={json.dumps(platform)};
      updateProgress({{phase:'running',total:1,processed:1,success:1}});
      assert.equal(scanCompletedForCurrentFile,false); assert.equal(el('startBtn').disabled,true);
      updateProgress({{phase:'saving',total:1,processed:1,success:1}});
      assert.equal(scanCompletedForCurrentFile,false); assert.equal(el('cancelBtn').disabled,true);
      assert.notEqual(el('progressStatus').className,'progress-status success');
      updateProgress({{phase:'failed',total:1,processed:1,success:1,error:1,done:true}});
      assert.equal(scanCompletedForCurrentFile,false); assert.equal(el('startBtn').disabled,false);
      assert.notEqual(el('progressStatus').className,'progress-status success');
      updateProgress({{phase:'completed',total:1,processed:1,success:1,done:true}});
      assert.equal(scanCompletedForCurrentFile,true);
      assert.equal(el('progressStatus').className,'progress-status success');
    """)


def test_desktop_update_does_not_install_during_scan_or_on_reconnect_before_snapshot():
    run_js("""
      (async()=>{
        const calls=[];
        window.__TAURI__={core:{invoke:async(name)=>{calls.push(name);return '0.2.0';}}};
        await checkDesktopUpdate(); assert.equal(calls.length,0);
        connectWS();
        sockets[0].onmessage({data:JSON.stringify({type:'session',data:{runId:'run-1',platform:'threads',
          fileId:'Book.xlsx',sheetName:'Sheet B',running:true,status:{phase:'running',total:1,processed:0,success:0}}})});
        await checkDesktopUpdate(); assert.equal(calls.length,0);
        updateProgress({phase:'completed',total:1,processed:1,success:1,done:true});
        await checkDesktopUpdate(); assert.deepEqual(calls,['check_for_update','install_update']);
      })()
    """)


@pytest.mark.skipif(os.name != "nt", reason="Windows batch expansion regression")
def test_update_batch_finds_venv_python_in_a_fresh_shell(tmp_path):
    source = (ROOT / "capnhat.bat").read_text(encoding="utf-8")
    block = source[source.index('echo %UI_ORANGE%[4/4]'):source.index('for /f "delims=" %%H')]
    # Execute the real block, substituting only the external pip install with a read-only probe.
    block = block.replace('-m pip install -r requirements.txt --upgrade --quiet', '--version')
    python_path = tmp_path / ".venv" / "Scripts" / "python.exe"
    python_path.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, python_path)
    (python_path.parent.parent / 'pyvenv.cfg').write_text(f'home = {sys.base_prefix}\n', encoding='utf-8')
    script = tmp_path / "check.bat"
    script.write_text("@echo off\n" + block, encoding="utf-8")
    env = dict(os.environ)
    env.pop("VENV_PY", None)
    env["PYTHONHOME"] = sys.base_prefix
    result = subprocess.run(["cmd", "/d", "/c", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert "Python 3." in result.stdout, result.stdout + result.stderr


def test_desktop_update_gate_rejects_busy_and_unauthenticated_requests(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    import app as app_state
    import desktop_server

    isolated_app = FastAPI()
    monkeypatch.setattr(desktop_server, 'app', isolated_app)
    monkeypatch.setattr(app_state, 'DESKTOP_UPDATE_PENDING', False, raising=False)
    monkeypatch.setattr(app_state, 'SOURCE_BUSY', False, raising=False)
    monkeypatch.setattr(app_state, 'scan_running', lambda: True)
    server = SimpleNamespace(should_exit=False)
    desktop_server.register_desktop_routes(server, 'fixture-token')
    client = TestClient(isolated_app)
    headers = {'X-Riviu-Shutdown': 'fixture-token'}
    assert client.post('/_desktop/prepare-update').status_code == 403
    assert client.post('/_desktop/prepare-update', headers=headers).status_code == 409
    assert app_state.DESKTOP_UPDATE_PENDING is False
    monkeypatch.setattr(app_state, 'scan_running', lambda: False)
    monkeypatch.setattr(app_state, 'SOURCE_BUSY', True)
    assert client.post('/_desktop/prepare-update', headers=headers).status_code == 409
    monkeypatch.setattr(app_state, 'SOURCE_BUSY', False)
    assert client.post('/_desktop/prepare-update', headers=headers).status_code == 200
    assert app_state.DESKTOP_UPDATE_PENDING is True
    assert client.post('/_desktop/cancel-update', headers=headers).status_code == 200
    assert app_state.DESKTOP_UPDATE_PENDING is False
    assert client.post('/_desktop/shutdown', headers=headers).status_code == 200
    assert server.should_exit is True


def test_real_dom_threads_snapshot_keeps_blank_metrics_and_locked_controls():
    from playwright.sync_api import sync_playwright

    markup = (ROOT / 'templates' / 'index.html').read_text(encoding='utf-8')
    markup = re.sub(r'<script\b[^>]*>.*?</script>', '', markup, flags=re.DOTALL)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_content(markup)
            page.add_style_tag(content=(ROOT / 'static' / 'styles.css').read_text(encoding='utf-8'))
            page.evaluate("""() => {
                window.WebSocket = class {static OPEN=1; constructor(){window.testSocket=this;this.readyState=1;}};
            }""")
            page.add_script_tag(content=(ROOT / 'static' / 'app.js').read_text(encoding='utf-8'))
            page.evaluate("""() => {
                connectWS();
                window.testSocket.onmessage({data:JSON.stringify({type:'session',data:{
                    runId:'fixture-run',platform:'threads',fileId:'Book.xlsx',sheetName:'Sheet B',running:true,
                    status:{phase:'running',total:2,processed:1,success:0,hidden:1,error:0},
                    results:[{id:1,url:'https://www.threads.com/@author/post/fixture',views:372,
                        likes:null,comments:1,saves:null,shares:2,status:'Partial: likes, repost'}]
                }})});
            }""")
            assert page.locator('#liveSavedHeader').inner_text() == 'REPOST'
            assert page.locator('#dataFeed td[data-label="Repost"]').inner_text() == ''
            assert page.locator('#dataFeed td[data-label="Tim"]').inner_text() == ''
            assert page.locator('#reportMinViewRow').is_hidden()
            assert page.locator('#startBtn').is_disabled()
            assert page.locator('#cancelBtn').is_enabled()
            assert page.locator('#excelFileSelect').is_disabled()
            assert page.locator('#scanSheetSelect').is_disabled()
            page.evaluate("updateProgress({phase:'saving',total:2,processed:2,success:1,hidden:1,error:0})")
            assert page.locator('#cancelBtn').is_disabled()
            assert page.locator('#startBtn').is_disabled()
            assert 'Đang lưu' in page.locator('#progressStatus').inner_text()
        finally:
            browser.close()
