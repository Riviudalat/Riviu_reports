"""Offline native-DOM regressions for source state, preview routing and preferences.

No server, accounts, production workbooks or external network are used. These
browser checks do not claim Tauri/WebView restart or real updater acceptance.
"""

from pathlib import Path
import re

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    markup = (ROOT / "riviu" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<script\b[^>]*>.*?</script>", "", markup, flags=re.DOTALL)

    def route_offline(route):
        if route.request.url == "http://riviu.fixture.invalid:1234/":
            route.fulfill(status=200, content_type="text/html", body=markup)
        else:
            route.abort()

    context.route("**/*", route_offline)
    current = context.new_page()
    current.goto("http://riviu.fixture.invalid:1234/")
    current.add_style_tag(content=(ROOT / "riviu" / "web" / "static" / "styles.css").read_text(encoding="utf-8"))
    current.evaluate("""() => {
        window.WebSocket = class {static OPEN=1; constructor(){this.readyState=1;} send(){}};
        window.fetch = async () => {throw Error('Unexpected fixture request');};
        window.fixtureSources = {
            tiktok:{fileId:'data/A.xlsx',displaySheet:'A',scanSheet:'A',pushSheet:'A',url:''},
            threads:{fileId:'data/B.xlsx',displaySheet:'B',scanSheet:'B',pushSheet:'B',url:''}
        };
    }""")
    current.add_script_tag(content=(ROOT / "riviu" / "web" / "static" / "app.js").read_text(encoding="utf-8"))
    yield current
    context.close()


def test_report_modal_discards_previous_workbook_sheet_in_native_select(page):
    requests = page.evaluate("""async () => {
        sourcesInitialized=true; activePlatform='threads'; currentFileId='data/B.xlsx';
        currentSheetName=currentScanSheetName='B';
        const select=document.getElementById('reportSheetSelect');
        select.innerHTML='<option value="Old A">Old A</option>'; select.value='Old A';
        reportSheetName='Old A';
        const calls=[];
        fetch=async url=>{calls.push(url); return {ok:true,json:async()=>({file:'data/B.xlsx',
            currentSheet:'B',sheets:['B'],partners:[{name:'Partner B',count:1}]})};};
        await openReportModal(); return calls;
    }""")
    assert len(requests) == 1
    assert "Old" not in requests[0]
    assert "file_id=data%2FB.xlsx" in requests[0]
    assert page.locator("#reportSheetSelect").input_value() == "B"
    assert "Partner B" in page.locator("#partnerList").inner_text()


@pytest.mark.parametrize("platform,valid,invalid", [
    ("threads", ["threads.com/@demo/post/Abc_1", "//www.threads.net/share/Abc-2/",
                 "https://threads.net/@demo/post/ABC?x=1"],
     ["https://threads.com.evil.test/@demo/post/ABC", "https://evil.test/threads.com/@demo/post/ABC",
      "https://threads.com/@demo", "https://threads.com/@demo/post/ABC/extra",
      "javascript:threads.com/@demo/post/ABC", "https://user:pass@threads.com/@demo/post/ABC"]),
    ("tiktok", ["tiktok.com/@demo/video/123", "//vt.tiktok.com/ABC/", "https://vm.tiktok.com/ABC/"],
     ["https://tiktok.com.evil.test/@demo/video/123", "https://evil.test/tiktok.com/@demo/video/123",
      "https://127.0.0.1/?url=tiktok.com/@demo/video/123", "ftp://tiktok.com/@demo/video/123",
      "https://user:pass@tiktok.com/@demo/video/123"]),
])
def test_platform_link_matcher_uses_exact_normalized_hosts(page, platform, valid, invalid):
    result = page.evaluate("""({platform,valid,invalid}) => ({
        valid:valid.map(url=>matchesPlatformLink(url,platform)),
        invalid:invalid.map(url=>matchesPlatformLink(url,platform))
    })""", {"platform": platform, "valid": valid, "invalid": invalid})
    assert all(result["valid"])
    assert not any(result["invalid"])


def test_threads_preview_includes_schemeless_posts_and_pins_platform(page):
    calls = page.evaluate("""async () => {
        sourcesInitialized=true; activePlatform='threads'; currentFileId='data/B.xlsx';
        currentSheetName=currentScanSheetName='B';
        const calls=[];
        fetch=async url=>{calls.push(url); return {ok:true,json:async()=>({file:'data/B.xlsx',currentSheet:'B',
            sheets:['B'],columns:['LINK AIR','TIM'],data:[
                {'LINK AIR':'threads.com/@demo/post/ABC','TIM':20},
                {'LINK AIR':'https://evil.test/threads.com/@demo/post/ABC','TIM':99},
                {'LINK AIR':'https://www.tiktok.com/@demo/video/123','TIM':10}]})};};
        await loadPreview('B'); return calls;
    }""")
    assert len(calls) == 1
    assert "platform=threads" in calls[0]
    assert "file_id=data%2FB.xlsx" in calls[0]
    assert page.locator("#totalLinks").inner_text() == "1"
    assert page.locator("#previewBody tr").count() == 1
    assert "threads.com/@demo/post/ABC" in page.locator("#previewBody").inner_text()
    assert "evil.test" not in page.locator("#previewBody").inner_text()


def test_empty_platform_clears_summary_and_shows_empty_table(page):
    page.evaluate("""async () => {
        sourcesInitialized=true; currentFileId=''; activePlatform='threads';
        setPreviewTableVisible(false);
        document.getElementById('summaryDashboard').innerHTML='<div>Old summary</div>';
        window.lastWorkbookSheets=['Old A'];
        for (const id of ['totalLinks','processedLinks','successLinks','hiddenCountBadge','failedCountBadge'])
            document.getElementById(id).textContent='99';
        await loadPreview();
    }""")
    assert not page.locator("#summaryDashboard").inner_text()
    assert page.locator("#previewTable").is_visible()
    assert "Chưa chọn nguồn" in page.locator("#previewBody").inner_text()
    for field in ("totalLinks", "processedLinks", "successLinks", "hiddenCountBadge", "failedCountBadge"):
        assert page.locator(f"#{field}").inner_text() == "0"
    assert page.evaluate("window.lastWorkbookSheets") == []


def test_summary_dashboard_keeps_unknown_partner_metrics_blank(page):
    cells = page.evaluate("""async () => {
        sourcesInitialized=true; activePlatform='tiktok'; currentFileId='data/A.xlsx'; currentSheetName='Tổng kết A';
        fetch=async()=>({ok:true,json:async()=>({file:'data/A.xlsx',
            columns:['Stt','ĐỐI TÁC','TỔNG LINK','TỔNG LƯỢT XEM','TỔNG TIM'],
            rows:[{'Stt':1,'ĐỐI TÁC':'Partner','TỔNG LINK':2,'TỔNG LƯỢT XEM':'','TỔNG TIM':0}],totals:{partners:1}})});
        await renderSummaryDashboard('A');
        return [...document.querySelectorAll('#summaryDashboard tbody td')].map(cell => cell.textContent);
    }""")
    assert cells == ["1", "Partner", "2", "", "0"]


def test_stale_summary_error_cannot_repopulate_empty_platform(page):
    result = page.evaluate("""async () => {
        sourcesInitialized=true; activePlatform='tiktok'; currentFileId='data/A.xlsx'; currentSheetName='Tổng kết A';
        let reject; fetch=()=>new Promise((resolve,failure)=>{reject=failure;});
        const pending=renderSummaryDashboard('A');
        activePlatform='threads'; currentFileId=''; currentSheetName='';
        await loadPreview(); reject(Error('Old workbook fixture failure')); await pending;
        return document.getElementById('summaryDashboard').innerHTML;
    }""")
    assert result == ''
    assert page.locator('#previewTable').is_visible()


@pytest.mark.parametrize("failure", ["check_for_update", "install_update"])
def test_updater_rejection_is_handled_and_controls_recover(page, failure):
    result = page.evaluate("""async failure => {
        websocketSessionReady=true; const calls=[];
        window.__TAURI__={core:{invoke:async command=>{
            calls.push(command); if(command===failure) throw Error('Fixture IPC failure'); return '0.2.0';
        }}};
        await checkDesktopUpdate();
        return {calls,inFlight:desktopUpdateCheckInFlight,installing:desktopUpdateInstalling};
    }""", failure)
    assert result["calls"] == (["check_for_update"] if failure == "check_for_update" else ["check_for_update", "install_update"])
    assert result["inFlight"] is False
    assert result["installing"] is False
    assert page.locator("#startBtn").is_enabled()
    assert "Fixture IPC failure" in page.locator("#logs").inner_text()


def test_preferences_load_once_and_server_beats_origin_storage(page):
    result = page.evaluate("""async () => {
        localStorage.setItem('riviuPlatformSourcesV1',JSON.stringify({
            tiktok:{fileId:'data/Old.xlsx'},threads:{fileId:''}}));
        const calls=[];
        fetch=async(url,options)=>{calls.push([url,options?.method||'GET']);
            return {ok:true,json:async()=>({sources:window.fixtureSources})};};
        const first=loadSourcePreferences(); await Promise.all([first,loadSourcePreferences()]);
        await loadSourcePreferences();
        return {calls,file:platformSources.tiktok.fileId,threads:platformSources.threads.fileId};
    }""")
    assert result == {"calls": [["/source-preferences", "GET"]], "file": "data/A.xlsx", "threads": "data/B.xlsx"}


def test_startup_loads_server_preferences_before_first_file_request(page):
    result = page.evaluate("""async () => {
        const calls=[];
        fetch=async(url,options)=>{
            calls.push([url,options?.method||'GET']);
            let data={};
            if(url==='/source-preferences') data={sources:window.fixtureSources};
            else if(url.startsWith('/list-files')) data={files:[{id:'data/A.xlsx',label:'A'},{id:'data/B.xlsx',label:'B'}],
                current:'data/A.xlsx',currentSheet:'A',scanSheet:'A',sheets:['A']};
            else if(url==='/select-file') data={success:true,sheet:'A',scanSheet:'A'};
            else if(url.startsWith('/preview-excel')) data={file:'data/A.xlsx',currentSheet:'A',sheets:['A'],columns:['LINK AIR'],data:[]};
            else if(options?.method==='POST') data={success:true};
            return {ok:true,json:async()=>data};
        };
        await window.onload();
        return {calls,file:currentFileId,threads:platformSources.threads.fileId};
    }""")
    urls = [item[0] for item in result['calls']]
    assert urls.index('/source-preferences') < next(i for i, url in enumerate(urls) if url.startswith('/list-files'))
    assert [item for item in result['calls'] if item == ['/source-preferences', 'GET']] == [['/source-preferences', 'GET']]
    assert '/select-file' in urls
    assert result['file'] == 'data/A.xlsx'
    assert result['threads'] == 'data/B.xlsx'


def test_first_load_never_adopts_server_selection_for_platform_without_source(page):
    result = page.evaluate("""async () => {
        const calls=[]; const writes=[];
        fetch=async(url,options)=>{
            calls.push(url);
            let data={};
            if(url==='/source-preferences' && options?.method==='POST') {writes.push(JSON.parse(options.body)); data={success:true};}
            else if(url==='/source-preferences') data={sources:{threads:window.fixtureSources.threads}};
            else if(url.startsWith('/list-files')) {
                const params=new URL(url,location.origin).searchParams;
                // Without file_id the server answers with its legacy global selection: the Threads workbook.
                const fileId=params.has('file_id') ? params.get('file_id') : 'data/B.xlsx';
                data={files:[{id:'data/B.xlsx',label:'B'}],current:fileId,currentLabel:fileId,
                    currentSheet:fileId?'B':'',scanSheet:fileId?'B':'',sheets:fileId?['B']:[]};
            } else if(url.startsWith('/preview-excel')) data={file:'data/B.xlsx',currentSheet:'B',sheets:['B'],columns:['LINK AIR'],data:[]};
            return {ok:true,json:async()=>data};
        };
        await window.onload();
        for(let i=0;i<10;i++) await Promise.resolve();
        return {file:currentFileId,tiktok:platformSources.tiktok.fileId,threads:platformSources.threads.fileId,
            previews:calls.filter(url=>url.startsWith('/preview-excel')).length,
            persisted:writes.map(write=>write.sources.tiktok.fileId)};
    }""")
    assert result["file"] == "" and result["tiktok"] == ""
    assert result["threads"] == "data/B.xlsx"
    assert result["previews"] == 0
    assert "data/B.xlsx" not in result["persisted"]
    assert "Chưa chọn nguồn" in page.locator("#previewBody").inner_text()
    assert page.locator("#excelFileSelect").input_value() == ""


def test_saved_source_different_from_server_current_restores_each_sheet(page):
    result = page.evaluate("""async () => {
        window.fixtureSources.tiktok={fileId:'data/A.xlsx',displaySheet:'Display',scanSheet:'Scan',pushSheet:'Push',url:''};
        const calls=[];
        fetch=async(url,options)=>{
            calls.push([url,options?.body ? JSON.parse(options.body) : null]);
            let data={};
            if(url==='/source-preferences') data={sources:window.fixtureSources};
            else if(url.startsWith('/list-files')) {
                const selected=new URL(url,location.origin).searchParams.get('file_id')==='data/A.xlsx';
                data={files:[{id:'data/A.xlsx',label:'A'},{id:'data/Old.xlsx',label:'Old'}],
                    current:selected?'data/A.xlsx':'data/Old.xlsx',
                    currentSheet:selected?'Display':'Old',scanSheet:selected?'Scan':'Old',
                    sheets:selected?['Display','Scan','Push']:['Old']};
            } else if(url==='/select-file') data={success:true,sheet:'Display',scanSheet:'Scan'};
            else if(url.startsWith('/preview-excel')) data={file:'data/A.xlsx',currentSheet:'Display',
                sheets:['Display','Scan','Push'],columns:['LINK AIR'],data:[]};
            else if(options?.method==='POST') data={success:true};
            return {ok:true,json:async()=>data};
        };
        await window.onload();
        return {display:currentSheetName,scan:currentScanSheetName,push:currentPushSheetName,calls};
    }""")
    assert result['display'] == 'Display'
    assert result['scan'] == 'Scan'
    assert result['push'] == 'Push'
    select = next(body for url, body in result['calls'] if url == '/select-file')
    assert select['sheet_name'] == 'Display'
    assert select['scan_sheet'] == 'Scan'


def test_late_preferences_get_cannot_overwrite_pending_local_selection(page):
    result = page.evaluate("""async () => {
        let finish; const writes=[];
        fetch=(url,options)=> options?.method==='POST'
            ? (writes.push(JSON.parse(options.body)),Promise.resolve({ok:true,json:async()=>({success:true})}))
            : new Promise(resolve=>{finish=resolve;});
        const pending=loadSourcePreferences();
        platformSources.tiktok.fileId='data/New.xlsx'; platformSources.tiktok.scanSheet='New';
        persistSources();
        finish({ok:true,json:async()=>({sources:window.fixtureSources})});
        await pending; await flushSourcePreferences(); await Promise.resolve();
        return {file:platformSources.tiktok.fileId,sheet:platformSources.tiktok.scanSheet,writes};
    }""")
    assert result["file"] == "data/New.xlsx"
    assert result["sheet"] == "New"
    assert len(result["writes"]) == 1
    assert result["writes"][0]["sources"]["tiktok"]["fileId"] == "data/New.xlsx"


def test_preference_writes_serialize_coalesce_and_omit_runtime_secrets(page):
    result = page.evaluate("""async () => {
        sourcePreferencesReady=true;
        const calls=[]; const finishes=[]; let active=0,maxActive=0;
        fetch=(url,options)=>{calls.push(JSON.parse(options.body)); active++; maxActive=Math.max(active,maxActive);
            return new Promise(resolve=>finishes.push(()=>{active--;resolve({ok:true,json:async()=>({success:true})});}));};
        Object.assign(platformSources.tiktok,{fileId:'data/A.xlsx',scanSheet:'A',
            url:'https://docs.google.com/spreadsheets/d/Fixture/edit?access_token=SECRET#gid=42',
            cookie:'COOKIE_SECRET',proxy:'PROXY_SECRET',label:'LABEL_SECRET'});
        persistSources(); persistSources(); await Promise.resolve();
        platformSources.tiktok.scanSheet='B'; persistSources();
        platformSources.tiktok.scanSheet='C'; persistSources();
        const firstCount=calls.length; finishes[0]();
        for(let i=0;i<6;i++) await Promise.resolve();
        finishes[1](); for(let i=0;i<6;i++) await Promise.resolve();
        return {calls,maxActive,firstCount,fallback:localStorage.getItem('riviuPlatformSourcesV1')};
    }""")
    assert result["firstCount"] == 1
    assert result["maxActive"] == 1
    assert len(result["calls"]) == 2
    assert result["calls"][1]["sources"]["tiktok"]["scanSheet"] == "C"
    assert result["calls"][0]["sources"]["tiktok"]["url"] == "https://docs.google.com/spreadsheets/d/Fixture/edit#gid=42"
    assert "SECRET" not in str(result)
    assert result["fallback"] is None
    assert set(result["calls"][0]["sources"]["tiktok"]) == {"fileId", "displaySheet", "scanSheet", "pushSheet", "url"}


@pytest.mark.parametrize("busy", ["sourceBusy", "desktopUpdateInstalling", "scanPhase"])
def test_preference_saves_wait_for_scan_source_or_update_to_finish(page, busy):
    result = page.evaluate("""async busy => {
        sourcePreferencesReady=true; const writes=[];
        fetch=async(url,options)=>{writes.push(JSON.parse(options.body));return {ok:true,json:async()=>({success:true})};};
        if(busy==='scanPhase') scanPhase='running';
        else if(busy==='sourceBusy') sourceBusy=true;
        else desktopUpdateInstalling=true;
        platformSources.tiktok.fileId='data/A.xlsx'; persistSources(); await Promise.resolve();
        const countBusy=writes.length;
        scanPhase='idle'; sourceBusy=false; desktopUpdateInstalling=false; syncScanControls();
        for(let i=0;i<6;i++) await Promise.resolve();
        return {countBusy,writes};
    }""", busy)
    assert result["countBusy"] == 0
    assert len(result["writes"]) == 1


def test_backend_busy_keeps_latest_preferences_without_retry_loop(page):
    result = page.evaluate("""async () => {
        sourcePreferencesReady=true; const writes=[];
        fetch=async(url,options)=>{
            writes.push(JSON.parse(options.body));
            return writes.length===1 ? {ok:false,status:409,json:async()=>({error:'busy'})}
                : {ok:true,status:200,json:async()=>({success:true})};
        };
        platformSources.tiktok.fileId='data/A.xlsx'; persistSources();
        for(let i=0;i<8;i++) await Promise.resolve();
        const beforeSync=writes.length; const pending=sourcePreferencesPending?.sources.tiktok.fileId;
        platformSources.tiktok.fileId='data/B.xlsx'; persistSources(); syncScanControls();
        for(let i=0;i<8;i++) await Promise.resolve();
        return {beforeSync,pending,writes,finished:sourcePreferencesPending===null};
    }""")
    assert result['beforeSync'] == 1
    assert result['pending'] == 'data/A.xlsx'
    assert len(result['writes']) == 2
    assert result['writes'][1]['sources']['tiktok']['fileId'] == 'data/B.xlsx'
    assert result['finished'] is True


def test_partial_google_url_does_not_drop_other_valid_platform_target(page):
    result = page.evaluate("""() => {
        platformSources.tiktok.url='https://docs.google.com/spreadsheets/d/Fixture/edit#gid=42';
        platformSources.threads.url='https://docs.google.com/spreadsheets/d/';
        platformSources.tiktok.scanSheet=' Sheet ';
        return sourcePreferencesSnapshot();
    }""")
    assert result['tiktok']['url'] == 'https://docs.google.com/spreadsheets/d/Fixture/edit#gid=42'
    assert result['tiktok']['scanSheet'] == ' Sheet '
    assert result['threads']['url'] == ''


def test_server_error_uses_validated_browser_fallback_without_secrets(page):
    result = page.evaluate("""async () => {
        localStorage.setItem('riviuPlatformSourcesV1',JSON.stringify({
            tiktok:{fileId:'data/Fallback.xlsx',cookie:'SECRET'},threads:{fileId:''}}));
        fetch=async()=>{throw Error('Fixture unavailable');};
        await loadSourcePreferences();
        platformSources.tiktok.url='https://docs.google.com/spreadsheets/d/Fixture/edit?token=SECRET';
        platformSources.tiktok.proxy='SECRET'; persistSources();
        await flushSourcePreferences();
        return {file:platformSources.tiktok.fileId,stored:JSON.parse(localStorage.getItem('riviuPlatformSourcesV1'))};
    }""")
    assert result["file"] == "data/Fallback.xlsx"
    assert "SECRET" not in str(result)
    assert result["stored"]["tiktok"]["url"] == "https://docs.google.com/spreadsheets/d/Fixture/edit"


@pytest.mark.parametrize("invalid", [
    {"fileId": "../secret.xlsx"}, {"fileId": "C:/secret.xlsx"}, {"scanSheet": "bad/sheet"},
    {"url": "https://user:pass@docs.google.com/spreadsheets/d/Fixture/edit"},
    {"url": "https://docs.google.com.evil.test/spreadsheets/d/Fixture/edit"}, {"fileId": 12},
])
def test_preferences_reject_invalid_server_values(page, invalid):
    result = page.evaluate("""async invalid => {
        const sources=structuredClone(window.fixtureSources); Object.assign(sources.tiktok,invalid);
        fetch=async()=>({ok:true,json:async()=>({sources})}); await loadSourcePreferences();
        return {file:platformSources.tiktok.fileId,ready:sourcePreferencesReady};
    }""", invalid)
    assert result == {"file": "", "ready": True}


def test_report_modal_opens_on_current_scan_sheet_not_first_workbook_sheet(page):
    requested = page.evaluate("""async () => {
        sourcesInitialized=true; activePlatform='tiktok'; currentFileId='data/A.xlsx';
        Object.assign(platformSources.tiktok,{sheets:['Tháng 1','Tháng 2','Tổng hợp Tháng 2'],
            displaySheet:'Tổng hợp Tháng 2',scanSheet:'Tháng 2'});
        const calls=[];
        fetch=async url=>{calls.push(url);
            // Like the server: without sheet_name the report falls back to the first data sheet.
            const sheet=new URL(url,location.origin).searchParams.get('sheet_name')||'Tháng 1';
            return {ok:true,json:async()=>({file:'data/A.xlsx',currentSheet:sheet,sheets:['Tháng 1','Tháng 2'],
                partners:[{name:'Partner '+sheet,linkCount:1}]})};};
        await openReportModal(); return calls;
    }""")
    assert len(requested) == 1
    assert "sheet_name=Th%C3%A1ng+2" in requested[0]
    assert page.locator("#reportSheetSelect").input_value() == "Tháng 2"
    assert "Partner Tháng 2" in page.locator("#partnerList").inner_text()


def test_threads_report_uses_tiktok_min_view_filter(page):
    result = page.evaluate("""async () => {
        sourcesInitialized=true; applyPlatformUI('threads'); currentFileId='data/B.xlsx';
        Object.assign(platformSources.threads,{sheets:['B'],displaySheet:'B',scanSheet:'B'});
        const calls=[];
        fetch=async(url,options)=>{calls.push([url,options?.body?JSON.parse(options.body):null]);
            if(url==='/export-report') return {ok:true,headers:{get:()=>''},blob:async()=>new Blob(['x'])};
            return {ok:true,json:async()=>({file:'data/B.xlsx',currentSheet:'B',sheets:['B'],
                partners:[{name:'Partner B',linkCount:2}]})};};
        await openReportModal();
        selectAllPartners();
        await exportPartnerReport();
        return calls;
    }""")
    partners_url = result[0][0]
    assert "platform=threads" in partners_url
    assert "apply_min_views=true" in partners_url and "min_views=100" in partners_url
    export = next(body for url, body in result if url == "/export-report")
    assert export["platform"] == "threads"
    assert export["applyMinViews"] is True and export["minViews"] == 100


def test_report_min_view_row_is_shown_for_threads(page):
    page.evaluate("""() => { applyPlatformUI('threads');
        const modal=document.getElementById('reportModal'); modal.classList.add('active'); }""")
    assert page.locator("#reportMinViewRow").is_visible()


def test_rejected_saved_sheet_on_startup_keeps_saved_file(page):
    result = page.evaluate("""async () => {
        const writes=[];
        fetch=async(url,options)=>{
            let data={}, status=200;
            if(url==='/source-preferences' && options?.method==='POST') {writes.push(JSON.parse(options.body)); data={success:true};}
            else if(url==='/source-preferences') data={sources:window.fixtureSources};
            else if(url.startsWith('/list-files')) data={files:[{id:'data/A.xlsx',label:'A'}],current:'data/A.xlsx',
                currentLabel:'A',currentSheet:'A',scanSheet:'A',sheets:['A']};
            else if(url==='/select-file') {status=409; data={error:'Đang xử lý dữ liệu'};}
            else if(url.startsWith('/preview-excel')) data={file:'data/A.xlsx',currentSheet:'A',sheets:['A'],columns:['LINK AIR'],data:[]};
            else if(options?.method==='POST') data={success:true};
            return {ok:status===200,status,json:async()=>data};
        };
        await window.onload();
        for(let i=0;i<10;i++) await Promise.resolve();
        return {file:platformSources.tiktok.fileId,scan:currentScanSheetName,
            persisted:writes.map(write=>write.sources.tiktok.fileId)};
    }""")
    assert result["file"] == "data/A.xlsx"
    assert result["scan"] == "A"
    assert "" not in result["persisted"]


def test_switching_to_platform_keeps_saved_scan_sheet_before_preview(page):
    result = page.evaluate("""async () => {
        sourcesInitialized=true; activePlatform='tiktok';
        Object.assign(platformSources.threads,{fileId:'data/B.xlsx',displaySheet:'Tháng 2',scanSheet:'Tháng 2',
            pushSheet:'',sheets:[]});
        const selects=[];
        fetch=async(url,options)=>{
            let data={};
            if(url.startsWith('/list-files')) data={files:[{id:'data/B.xlsx',label:'B'}],current:'data/B.xlsx',
                currentLabel:'B',currentSheet:'Tháng 1',scanSheet:'Tháng 1',sheets:['Tháng 1','Tháng 2']};
            else if(url==='/select-file') {const body=JSON.parse(options.body); selects.push(body);
                data={success:true,sheet:body.sheet_name||'Tháng 1',scanSheet:body.scan_sheet||'Tháng 1'};}
            else if(url.startsWith('/preview-excel')) data={file:'data/B.xlsx',currentSheet:'Tháng 2',
                sheets:['Tháng 1','Tháng 2'],columns:['LINK AIR'],data:[]};
            else if(options?.method==='POST') data={success:true};
            return {ok:true,status:200,json:async()=>data};
        };
        await setPlatform('threads');
        for(let i=0;i<10;i++) await Promise.resolve();
        return {selects,scan:currentScanSheetName};
    }""")
    assert result["selects"][-1]["scan_sheet"] == "Tháng 2"
    assert result["scan"] == "Tháng 2"
    assert page.locator("#scanSheetSelect").input_value() == "Tháng 2"
