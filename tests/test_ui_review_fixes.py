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
    insertAdjacentHTML(_position, html){this.innerHTML += html; registerRenderedMarkup(html);},
    scrollTo(){}, focus(){}, closest(){return null;}};
}
function el(id) {if(!elements.has(id)) elements.set(id,element(id)); return elements.get(id);}
// Markup that app.js renders itself (the PLATFORMS source rows) becomes the elements the
// tests drive, so they exercise the generated rows rather than lazily invented stubs.
const renderedIds = new Set();
function registerRenderedMarkup(html) {
  for (const [, tag, attrs] of html.matchAll(/<([a-z]+)\b([^>]*)>/g)) {
    const id = attrs.match(/\sid="([^"]+)"/)?.[1];
    if (!id) continue;
    assert.ok(!elements.has(id), `rendered id ${id} already exists`);
    const node = el(id); renderedIds.add(id);
    node.tagName = tag.toUpperCase(); node.disabled = /\sdisabled(?=[\s>]|$)/.test(attrs);
  }
}
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
  URLSearchParams,TextEncoder,
  window:{location:{protocol:'http:',hostname:'localhost',host:'localhost:1231',port:'1231'},
    matchMedia(){return {matches:true};},addEventListener(){},setInterval(){},__TAURI__:null},
  setTimeout(){},clearTimeout(){},localStorage:{getItem(){return null;},setItem(){}},
  fetch:async()=>({ok:true,json:async()=>({})}),assert,el,sent,sockets,buttons});
vm.runInContext(fs.readFileSync('riviu/web/static/app.js','utf8'),context);
for (const config of Object.values(vm.runInContext('PLATFORMS',context))) {
  for (const [role, id] of Object.entries(config.dom)) {
    if (role !== 'button') assert.ok(renderedIds.has(id), `${config.key} source row did not render #${id}`);
  }
}
// Suppress unrelated presentation / asynchronous API edges, not scan routing/rendering.
vm.runInContext(`const originalLoadPreview=loadPreview; const originalUpdateFileList=updateFileList;
  const originalSetGooglePushState=setGooglePushState;
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


@pytest.mark.parametrize("platform", ["threads", "tiktok"])
@pytest.mark.parametrize("use_proxy", [False, True])
@pytest.mark.parametrize("has_proxy", [False, True])
def test_scan_start_shared_proxy_settings_and_safe_logs(platform, use_proxy, has_proxy):
    run_js(f"""
      ws=new WebSocket('test'); activePlatform={json.dumps(platform)};
      currentFileId='Fixture.xlsx'; currentSheetName='Data';
      el('proxyUseCheckbox').checked={json.dumps(use_proxy)};
      el('proxyTextInput').value={json.dumps('http://fixture-user:fixture-password@proxy.example:8080' if has_proxy else '')};
      const logs=[]; const warnings=[]; const consoleLogs=[]; let modalOpened=false;
      addLog=(...args)=>logs.push(args); notify=(...args)=>warnings.push(args);
      console={{log:(...args)=>consoleLogs.push(args),warn:(...args)=>consoleLogs.push(args),
        error:(...args)=>consoleLogs.push(args)}};
      openProxyModal=()=>{{modalOpened=true;}};
      startScraping(['Fixture partner'],'Data');
      if ({json.dumps(use_proxy and not has_proxy)}) {{
        assert.equal(sent.length,0); assert.equal(modalOpened,true);
        assert.equal(warnings.length,1); assert.equal(warnings[0][1],'warn');
        assert.match(warnings[0][0],/proxy/); assert.equal(scanPhase,'idle');
      }} else {{
        assert.equal(sent.length,1); assert.equal(sent[0].platform,{json.dumps(platform)});
        assert.equal(sent[0].use_proxy,{json.dumps(use_proxy)});
        assert.equal(sent[0].proxy_text,{json.dumps('http://fixture-user:fixture-password@proxy.example:8080' if use_proxy and has_proxy else '')});
        assert.equal(modalOpened,false); assert.equal(warnings.length,0);
        assert.equal(scanPhase,'starting');
      }}
      const output=JSON.stringify([logs,warnings,consoleLogs]);
      assert.ok(!output.includes('fixture-user')); assert.ok(!output.includes('fixture-password'));
    """)


def test_proxy_settings_survive_platform_switch_and_use_saved_text():
    run_js("""
      ws=new WebSocket('test'); el('proxyUseCheckbox').checked=true;
      proxyListText='proxy.example:8080'; el('proxyTextInput').value='';
      applyPlatformUI('threads'); applyPlatformUI('tiktok'); applyPlatformUI('threads');
      assert.equal(el('proxyUseCheckbox').checked,true);
      assert.equal(currentProxyText(),'proxy.example:8080');
      startScraping([],'Data');
      assert.equal(sent[0].platform,'threads'); assert.equal(sent[0].use_proxy,true);
      assert.equal(sent[0].proxy_text,'proxy.example:8080');
      updateProxyModalSummary(2);
      assert.match(el('proxyModalSummary').textContent,/phân bổ/);
      assert.ok(!el('proxyModalSummary').textContent.includes('ngẫu nhiên'));
    """)


def test_threads_proxy_toolbar_is_not_hidden_by_platform_css():
    css = (ROOT / 'riviu' / 'web' / 'static' / 'styles.css').read_text(encoding='utf-8')
    assert not re.search(r'\[data-platform\s*=\s*[\"\']threads[\"\']\]\s+\.toolbar-proxy\s*\{[^}]*display\s*:\s*none', css)


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


def test_opened_summary_tab_is_requested_by_its_own_title():
    # A mixed sheet has a TikTok and a Threads summary tab; resolving the tab to its data
    # sheet would make the server read the TikTok summary instead of the opened one.
    run_js("""
      (async()=>{
        activePlatform='threads'; currentFileId='Book.xlsx'; currentSheetName='Tổng kết Threads data';
        renderSheetTabs=()=>{}; renderScanSheetOptions=()=>{}; renderPushSheetOptions=()=>{};
        syncCompactSourceSummary=()=>{}; setPreviewTableVisible=()=>{};
        const urls=[];
        fetch=async url=>{urls.push(url); return {ok:true,json:async()=>({file:'Book.xlsx',
          currentSheet:'Tổng kết Threads data',sheets:['Data','Tổng kết data','Tổng kết Threads data'],
          summarySource:'Data',columns:[],data:[],rows:[]})};};
        await originalLoadPreview('Tổng kết Threads data');
        const summary=urls.find(url=>url.startsWith('/summary-dashboard?'));
        assert.equal(new URLSearchParams(summary.split('?')[1]).get('sheet_name'),'Tổng kết Threads data');
      })()
    """)


def test_each_platform_shows_only_its_own_summary_tabs():
    run_js("""
      const sheets=['Data','Tổng kết','Tổng kết data','Tổng kết Threads data','08-10-2026-10-30'];
      assert.deepEqual(visibleSheetTabs(sheets,'tiktok'),['Data','Tổng kết','Tổng kết data','08-10-2026-10-30']);
      assert.deepEqual(visibleSheetTabs(sheets,'threads'),['Data','Tổng kết Threads data']);
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


def test_rejected_handshake_probes_the_page_at_once_and_reconnects_when_it_answers():
    # After a quick server restart the old session cookie is rejected. Loading / issues a
    # fresh one, so the first failure must probe at once and reconnect without a backoff wait.
    run_js("""(async()=>{
      const timers=[]; setTimeout=(fn,ms)=>{timers.push({fn,ms});};
      const logs=[]; addLog=message=>logs.push(message);
      const probes=[]; fetch=async(url,options)=>{probes.push([url,options.credentials]);
        return {ok:true,status:200,json:async()=>({})};};
      const flush=async()=>{for(let i=0;i<5;i++) await Promise.resolve();};
      activePlatform='tiktok'; currentFileId='A.xlsx';
      connectWS(); sockets[0].onopen();
      sockets[0].onmessage({data:JSON.stringify({type:'session',data:{runId:'run-1',platform:'tiktok',
        fileId:'A.xlsx',sheetName:'S',running:true,status:{phase:'running',total:4,processed:1,success:1}}})});
      sockets[0].onclose();
      assert.deepEqual(probes,[['/','same-origin']]);
      assert.equal(el('connectionBanner').hidden,false);
      assert.equal(el('connectionBanner').textContent,'Đang kết nối lại tới server…');
      assert.equal(el('cancelBtn').disabled,true);
      await flush();
      assert.equal(sockets.length,2); assert.equal(timers.length,0);
      sockets[1].onopen();
      assert.equal(el('connectionBanner').hidden,true); assert.equal(el('cancelBtn').disabled,false);
      sockets[1].onmessage({data:JSON.stringify({type:'session',data:{runId:'run-1',platform:'tiktok',
        fileId:'A.xlsx',sheetName:'S',running:true,status:{phase:'running',total:4,processed:2,success:2}}})});
      assert.equal(scanPhase,'running'); assert.equal(currentRunContext.runId,'run-1');
      assert.equal(el('processedLinks').textContent,2);
      assert.equal(logs.filter(m=>String(m).includes('Mất kết nối')).length,1);
      // The probe answered but the socket still fails: back off instead of spinning.
      sockets[1].onclose(); await flush(); sockets[2].onclose();
      assert.deepEqual(timers.map(t=>t.ms),[1000]);
    })()""")


@pytest.mark.parametrize("outage", ["offline", "forbidden"])
def test_long_outage_shows_lost_banner_keeps_probing_and_recovers_on_its_own(outage):
    run_js("""(async()=>{
      let now=0; Date.now=()=>now;
      const timers=[]; setTimeout=(fn,ms)=>{timers.push({fn,ms});};
      const logs=[]; addLog=message=>logs.push(message);
      let reloads=0; updateFileList=async()=>{reloads++;}; loadPreview=async()=>{reloads++;};
      let up=false; let probes=0; fetch=async()=>{probes++;
        if(!up && OUTAGE==='offline') throw Error('fixture offline');
        return {ok:up,status:up?200:403,json:async()=>({})};};
      const flush=async()=>{for(let i=0;i<5;i++) await Promise.resolve();};
      activePlatform='tiktok'; currentFileId='A.xlsx';
      connectWS(); sockets[0].onopen();
      const snapshot=(running,phase)=>({data:JSON.stringify({type:'session',data:{runId:'live-run',platform:'tiktok',
        fileId:'A.xlsx',sheetName:'S',running,results:[],status:{phase,done:!running,total:1,processed:1,success:1}}})});
      sockets[0].onmessage(snapshot(true,'running'));
      sockets[0].onclose(); await flush();
      const delays=[];
      while(now<40000){ const timer=timers.shift(); delays.push(timer.ms); now+=timer.ms; timer.fn(); await flush(); }
      assert.equal(sockets.length,1);
      assert.ok(probes>=8); assert.equal(Math.max(...delays),5000);
      assert.equal(el('connectionBanner').hidden,false);
      assert.equal(el('connectionBanner').dataset.state,'lost');
      assert.ok(el('connectionBanner').textContent.includes('F5'));
      scanPhase='idle'; syncScanControls();
      assert.equal(el('startBtn').disabled,true); assert.ok(el('startBtn').title.includes('Mất kết nối'));
      scanPhase='running'; syncScanControls();
      // The server is back: the next background probe reconnects without a page reload.
      up=true; assert.equal(timers.length,1); timers.shift().fn(); await flush();
      assert.equal(sockets.length,2); sockets[1].onopen();
      assert.equal(el('connectionBanner').hidden,true);
      // The run this page followed finished during the outage: it still completes once.
      sockets[1].onmessage(snapshot(false,'completed'));
      assert.equal(logs.filter(m=>String(m).includes('HOÀN TẤT')).length,1); assert.equal(reloads,2);
      assert.equal(el('startBtn').disabled,false); assert.equal(el('startBtn').title,'');
    })()""".replace('OUTAGE', json.dumps(outage)))


def test_start_button_is_disabled_with_a_reason_when_it_cannot_start():
    run_js("""
      connectWS(); sockets[0].onopen();
      sourcesInitialized=true; activePlatform='threads'; currentFileId=''; syncScanControls();
      assert.equal(el('startBtn').disabled,true);
      assert.equal(el('startBtn').title,'Chưa chọn nguồn cho nền tảng này.');
      currentFileId='Threads.xlsx'; syncScanControls();
      assert.equal(el('startBtn').disabled,false); assert.equal(el('startBtn').title,'');
      fetch=()=>new Promise(()=>{}); sockets[0].onclose();
      assert.equal(el('startBtn').disabled,true); assert.equal(el('startBtn').title,'Đang kết nối lại tới server…');
    """)


def test_hidden_sheets_are_not_tabs_and_are_marked_in_sheet_selects():
    run_js("""(async()=>{
      activePlatform='tiktok'; currentFileId='Book.xlsx'; reportPartners=[];
      renderPartnerList=()=>{}; updateReportSummary=()=>{};
      const options=select=>JSON.stringify(select.children.map(option=>[option.value,option.textContent]));
      applyPreviewSource({file:'Book.xlsx',currentSheet:'Tháng 8',hiddenSheets:['Tháng 6','Tháng 7'],
        sheets:['Tháng 6','Tháng 8','Tổng kết tháng 8','Tháng 7','T8 26-08-2026-1409']});
      assert.equal(JSON.stringify(el('sheetTabs').children.map(tab=>tab.textContent)),'["Tháng 8","Tổng kết tháng 8","T8 26-08-2026-1409"]');
      assert.equal(options(el('scanSheetSelect')),HIDDEN_OPTIONS);
      assert.equal(el('scanSheetSelect').value,'Tháng 8');
      fetch=async()=>({ok:true,json:async()=>({file:'Book.xlsx',partners:[],currentSheet:'Tháng 6',
        sheets:['Tháng 8','Tháng 6','Tháng 7'],hiddenSheets:['Tháng 6','Tháng 7']})});
      await loadReportPartners('Tháng 6');
      const report=el('reportSheetSelect').children;
      assert.equal(options(el('reportSheetSelect')),HIDDEN_OPTIONS);
      assert.equal(report.find(option=>option.selected).value,'Tháng 6');
    })()""".replace("HIDDEN_OPTIONS", json.dumps(json.dumps([["Tháng 8", "Tháng 8"], ["Tháng 6", "Tháng 6 (ẩn)"], ["Tháng 7", "Tháng 7 (ẩn)"]], ensure_ascii=False, separators=(",", ":")), ensure_ascii=False)))


def test_preview_total_badge_follows_new_workbook_while_source_is_busy():
    run_js("""(async()=>{
      sourcesInitialized=true; activePlatform='tiktok'; currentFileId='B.xlsx'; currentSheetName='S';
      renderSheetTabs=()=>{}; setPreviewTableVisible=()=>{}; syncCompactSourceSummary=()=>{};
      fetch=async()=>({ok:true,json:async()=>({file:'B.xlsx',currentSheet:'S',sheets:['S'],columns:['LINK AIR'],
        data:[{'LINK AIR':'https://www.tiktok.com/@a/video/1'},{'LINK AIR':'https://www.tiktok.com/@a/video/2'}]})});
      el('totalLinks').textContent='7'; sourceBusy=true; syncScanControls();
      assert.equal(el('startBtn').disabled,true);
      await originalLoadPreview('S');
      assert.equal(String(el('totalLinks').textContent),'2');
      sourceBusy=false; scanPhase='running'; el('totalLinks').textContent='9';
      await originalLoadPreview('S');
      assert.equal(String(el('totalLinks').textContent),'9');
    })()""")


def test_run_context_on_other_platform_reloads_that_platforms_source():
    run_js("""(async()=>{
      sourcesInitialized=true; activePlatform='tiktok';
      currentFileId='A.xlsx'; currentSheetName='A'; currentScanSheetName='A';
      Object.assign(platformSources.tiktok,{fileId:'A.xlsx',label:'A',sheets:['A'],displaySheet:'A',scanSheet:'A'});
      Object.assign(platformSources.threads,{fileId:'B.xlsx',label:'B',sheets:['B'],displaySheet:'B',scanSheet:'B'});
      const reloads=[];
      updateFileList=async()=>{reloads.push(['files',activePlatform,currentFileId]);};
      loadPreview=async()=>{reloads.push(['preview',activePlatform,currentFileId,currentSheetName]);};
      applyRunContext({runId:'run-1',platform:'threads',fileId:'C.xlsx',sheetName:'Scan C'});
      for(let i=0;i<4;i++) await Promise.resolve();
      assert.equal(document.body.dataset.platform,'threads');
      assert.equal(currentFileId,'C.xlsx'); assert.equal(currentScanSheetName,'Scan C');
      assert.equal(platformSources.tiktok.fileId,'A.xlsx');
      assert.equal(el('sourceFileThreads').textContent,'C.xlsx');
      assert.deepEqual(reloads,[['files','threads','C.xlsx'],['preview','threads','C.xlsx','Scan C']]);
      applyRunContext({runId:'run-1',platform:'threads',fileId:'C.xlsx',sheetName:'Scan C'});
      for(let i=0;i<4;i++) await Promise.resolve();
      assert.equal(reloads.length,2);
    })()""")


def test_session_snapshot_of_finished_run_does_not_replay_completion():
    run_js("""(async()=>{
      const logs=[]; addLog=message=>logs.push(message);
      let reloads=0; updateFileList=async()=>{reloads++;}; loadPreview=async()=>{reloads++;};
      activePlatform='tiktok'; currentFileId='A.xlsx';
      const snapshot=runId=>({data:JSON.stringify({type:'session',data:{runId,platform:'tiktok',fileId:'A.xlsx',
        sheetName:'S',running:false,results:[],status:{phase:'completed',done:true,total:1,processed:1,success:1}}})});
      connectWS(); sockets[0].onmessage(snapshot('old-run'));
      assert.equal(logs.filter(m=>String(m).includes('HOÀN TẤT')).length,0); assert.equal(reloads,0);
      assert.equal(el('progressStatus').className,'progress-status success');
      // A run this page was following that finished while disconnected still completes once.
      currentRunContext={runId:'live-run',platform:'tiktok',fileId:'A.xlsx',sheetName:'S'}; scanPhase='running';
      sockets[0].onmessage(snapshot('live-run')); sockets[0].onmessage(snapshot('live-run'));
      assert.equal(logs.filter(m=>String(m).includes('HOÀN TẤT')).length,1); assert.equal(reloads,2);
    })()""")


def test_google_push_buttons_follow_one_enable_rule():
    run_js("""
      setGooglePushState=originalSetGooglePushState; activePlatform='tiktok'; googleOAuthAuthorized=true;
      Object.assign(platformSources.tiktok,{fileId:'A.xlsx',pushSheet:'A',sheets:['A'],
        url:'https://docs.google.com/spreadsheets/d/Fixture/edit'});
      renderSourceRows();
      assert.equal(el('pushGoogleBtn').disabled,false);
      assert.equal(el('threadsPushGoogleBtn').disabled,true);
      googleOAuthAuthorized=false; renderSourceRows();
      assert.equal(el('pushGoogleBtn').disabled,true);
    """)


def test_mobile_tiktok_host_is_visible_in_platform_preview():
    run_js("""
      assert.equal(matchesPlatformLink('https://mobile.tiktok.com/@demo/video/123','tiktok'),true);
      assert.equal(matchesPlatformLink('https://mobile.tiktok.com.evil.example/@demo/video/123','tiktok'),false);
    """)


def test_threads_zero_result_is_ok_with_missing_detail_not_error():
    run_js("""
      activePlatform='threads';
      appendData({id:1,platform:'threads',url:'https://www.threads.com/@author/post/abc',status:'Success',
        views:0,likes:0,comments:0,saves:0,shares:null,missingMetrics:['CHIA SẺ']});
      const row=el('dataFeed').children[0];
      assert.equal(row.dataset.resultStatus,'success');
      assert.match(row.innerHTML,/>OK<\\/span>/);
      assert.match(row.innerHTML,/Nguồn chưa trả: CHIA SẺ/);
      assert.match(row.innerHTML,/data-label="Chia sẻ"[^>]*><\\/td>/);
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
      assert.equal(el('dataFeed').children[0].dataset.resultStatus,'success');
      assert.match(html,/>OK<\\/span>/);
    """)


def test_threads_legacy_partial_rows_and_progress_counts_use_same_classification():
    run_js("""
      activePlatform='threads';
      for(let id=1;id<=88;id++) appendData({id,platform:'threads',url:'https://www.threads.com/@demo/post/P'+id,
        likes:0,comments:0,saves:0,views:null,shares:null,status:'Partial: thiếu LƯỢT XEM, CHIA SẺ'});
      for(let id=89;id<=96;id++) appendData({id,platform:'threads',url:'https://www.threads.com/@demo/post/P'+id,status:'Error: hidden'});
      updateProgress({platform:'threads',total:96,processed:96,success:0,hidden:88,error:8,phase:'completed',done:true});
      assert.equal(el('successLinks').textContent,88);
      assert.equal(el('hiddenCountBadge').textContent,0);
      assert.equal(el('failedCountBadge').textContent,8);
      assert.equal(el('dataFeed').children.filter(row=>row.dataset.resultStatus==='success').length,88);
      assert.equal(el('dataFeed').children.filter(row=>row.dataset.resultStatus==='error').length,8);
    """)


def test_status_before_rows_reconciles_when_batch_is_complete():
    run_js("""
      activePlatform='threads';
      updateProgress({total:2,processed:2,success:0,hidden:2,error:0,phase:'running'});
      appendData({id:1,likes:0,status:'Partial: views'});
      assert.equal(el('hiddenCountBadge').textContent,2);
      appendData({id:2,likes:0,status:'Partial: views'});
      assert.equal(el('successLinks').textContent,2);
      assert.equal(el('hiddenCountBadge').textContent,0);
      appendData({id:2,likes:0,status:'Partial: views'});
      assert.equal(el('successLinks').textContent,2);
    """)


def test_truncated_or_tiktok_results_do_not_rewrite_server_counts():
    run_js("""
      activePlatform='threads'; appendData({id:1,likes:0,status:'Partial: shares'});
      updateProgress({total:100,processed:100,success:5,hidden:90,error:5,phase:'running'});
      assert.equal(el('successLinks').textContent,5); assert.equal(el('hiddenCountBadge').textContent,90);
      activePlatform='tiktok';
      updateProgress({platform:'tiktok',total:1,processed:1,success:0,hidden:1,error:0,phase:'running'});
      assert.equal(el('successLinks').textContent,0); assert.equal(el('hiddenCountBadge').textContent,1);
    """)


@pytest.mark.parametrize("platform", ["tiktok", "threads"])
def test_progress_requires_final_saved_confirmation_and_reports_failure(platform):
    run_js(f"""
      activePlatform={json.dumps(platform)};
      updateProgress({{phase:'running',total:1,processed:1,success:1}});
      assert.equal(el('startBtn').disabled,true);
      updateProgress({{phase:'saving',total:1,processed:1,success:1}});
      assert.equal(el('cancelBtn').disabled,true);
      assert.notEqual(el('progressStatus').className,'progress-status success');
      updateProgress({{phase:'failed',total:1,processed:1,success:1,error:1,done:true}});
      assert.equal(el('startBtn').disabled,false);
      assert.notEqual(el('progressStatus').className,'progress-status success');
      updateProgress({{phase:'completed',total:1,processed:1,success:1,done:true}});
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


def test_desktop_startup_update_check_runs_once_the_first_session_snapshot_arrives():
    # Startup order in app.js: connectWS() then scheduleDesktopUpdates(), before the socket opens.
    run_js("""
      (async()=>{
        const calls=[];
        window.__TAURI__={core:{invoke:async(name)=>{calls.push(name);return null;}}};
        connectWS(); scheduleDesktopUpdates();
        await Promise.resolve(); assert.deepEqual(calls,[]);
        const idle={type:'session',data:{running:false}};
        sockets[0].onmessage({data:JSON.stringify(idle)});
        assert.deepEqual(calls,['check_for_update']);
        await Promise.resolve(); await Promise.resolve();
        sockets[0].onmessage({data:JSON.stringify(idle)});
        assert.deepEqual(calls,['check_for_update']);
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
    from riviu import app as app_state
    from riviu import desktop_server

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
    monkeypatch.setattr(desktop_server.scraper, 'browser_cleanup_pending', lambda: True)
    assert client.post('/_desktop/prepare-update', headers=headers).status_code == 409
    monkeypatch.setattr(desktop_server.scraper, 'browser_cleanup_pending', lambda: False)
    assert client.post('/_desktop/prepare-update', headers=headers).status_code == 200
    assert app_state.DESKTOP_UPDATE_PENDING is True
    assert client.post('/_desktop/cancel-update', headers=headers).status_code == 200
    assert app_state.DESKTOP_UPDATE_PENDING is False
    assert client.post('/_desktop/shutdown', headers=headers).status_code == 200
    assert server.should_exit is True


def test_real_dom_threads_snapshot_keeps_blank_metrics_and_locked_controls():
    from playwright.sync_api import sync_playwright

    markup = (ROOT / 'riviu' / 'web' / 'templates' / 'index.html').read_text(encoding='utf-8')
    markup = re.sub(r'<script\b[^>]*>.*?</script>', '', markup, flags=re.DOTALL)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_content(markup)
            page.add_style_tag(content=(ROOT / 'riviu' / 'web' / 'static' / 'styles.css').read_text(encoding='utf-8'))
            page.evaluate("""() => {
                window.WebSocket = class {static OPEN=1; constructor(){window.testSocket=this;this.readyState=1;}};
            }""")
            page.add_script_tag(content=(ROOT / 'riviu' / 'web' / 'static' / 'app.js').read_text(encoding='utf-8'))
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
            assert page.locator('#successLinks').inner_text() == '1'
            assert page.locator('#hiddenCountBadge').inner_text() == '0'
            assert page.locator('#dataFeed .col-status').inner_text() == 'OK'
            assert page.locator('#dataFeed td[data-label="Repost"]').inner_text() == ''
            assert page.locator('#dataFeed td[data-label="Tim"]').inner_text() == ''
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
