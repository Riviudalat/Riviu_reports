"""Independent platform source rows and explicit workbook request contracts."""
import json

import pytest

from test_ui_review_fixes import run_js


def test_platform_switch_restores_independent_sources_after_ack():
    run_js("""(async()=>{
      sourcesInitialized=true; currentFileId='A.xlsx'; currentSheetName='A'; currentScanSheetName='A'; currentPushSheetName='A';
      platformSources.tiktok.sheets=['A']; el('googleSheetUrlInput').value='https://docs.google.com/spreadsheets/d/A';
      Object.assign(platformSources.threads,{fileId:'B.xlsx',displaySheet:'B',scanSheet:'B',pushSheet:'B',sheets:['B'],url:'https://docs.google.com/spreadsheets/d/B'});
      let accept; fetch=async(url,options)=>await new Promise(resolve=>accept=resolve);
      const switching=setPlatform('threads');
      assert.equal(sourceBusy,true); assert.equal(activePlatform,'tiktok'); assert.equal(currentFileId,'A.xlsx');
      ws=new WebSocket('fixture'); startScraping([], 'A'); assert.equal(sent.length,0);
      accept({ok:true,json:async()=>({success:true,sheet:'B',scanSheet:'B'})}); await switching;
      assert.equal(activePlatform,'threads'); assert.equal(currentFileId,'B.xlsx'); assert.equal(currentScanSheetName,'B');
      assert.equal(platformSources.tiktok.fileId,'A.xlsx'); assert.equal(sourceBusy,false);
    })()""")


def test_inactive_row_sync_never_overwrites_active_workbook():
    run_js("""(async()=>{
      sourcesInitialized=true; currentFileId='A.xlsx'; activePlatform='tiktok';
      el('threadsGoogleSheetUrlInput').value='https://docs.google.com/spreadsheets/d/B';
      const calls=[]; fetch=async(url,options)=>{calls.push([url,JSON.parse(options.body)]);return {ok:true,json:async()=>({file:'B.xlsx',label:'B',sheets:['B'],currentSheet:'B',scanSheet:'B'})}};
      await syncGoogleSheet('threads');
      assert.equal(calls.length,1); assert.equal(calls[0][1].platform,'threads'); assert.equal(calls[0][1].activate,false);
      assert.equal(currentFileId,'A.xlsx'); assert.equal(platformSources.threads.fileId,'B.xlsx');
      assert.equal(platformSources.threads.url,'https://docs.google.com/spreadsheets/d/B');
    })()""")


def test_push_inactive_threads_row_captures_exact_file_sheet_and_platform():
    run_js("""(async()=>{
      currentFileId='A.xlsx'; activePlatform='tiktok';
      Object.assign(platformSources.threads,{fileId:'B.xlsx',pushSheet:'B',sheets:['B']});
      el('threadsGoogleSheetUrlInput').value='https://docs.google.com/spreadsheets/d/B';
      el('threadsPushSheetSelect').value='B'; el('threadsPushGoogleBtn').disabled=false;
      let captured; fetch=async(url,options)=>{captured=JSON.parse(options.body);return {ok:true,json:async()=>({success:true,sourceSheet:'B',sheetTitle:'Result'})}};
      await pushCurrentSheetToGoogle({currentTarget:el('threadsPushGoogleBtn')},'threads');
      assert.equal(captured.file_id,'B.xlsx'); assert.equal(captured.sourceSheet,'B'); assert.equal(captured.platform,'threads');
      assert.equal(currentFileId,'A.xlsx');
    })()""")


def test_empty_threads_slot_never_adopts_legacy_workbook_or_reads_preview():
    run_js("""(async()=>{
      sourcesInitialized=true; currentFileId='A.xlsx'; activePlatform='tiktok';
      let calls=0; fetch=async()=>{calls++;throw Error('must not fetch')};
      await setPlatform('threads'); await originalLoadPreview();
      assert.equal(currentFileId,''); assert.equal(calls,0);
      ws=new WebSocket('fixture'); startScraping([], ''); assert.equal(sent.length,0);
    })()""")


def test_failed_activation_keeps_old_platform_and_file():
    run_js("""(async()=>{
      sourcesInitialized=true; activePlatform='tiktok'; currentFileId='A.xlsx';
      Object.assign(platformSources.threads,{fileId:'B.xlsx',sheets:['B'],displaySheet:'B',scanSheet:'B'});
      fetch=async()=>({ok:false,json:async()=>({error:'rejected'})});
      await setPlatform('threads');
      assert.equal(activePlatform,'tiktok'); assert.equal(currentFileId,'A.xlsx'); assert.equal(sourceBusy,false);
    })()""")


def test_explicit_request_queries_pin_active_source():
    run_js("""
      activePlatform='threads'; currentFileId='B.xlsx';
      const params=sourceQuery({sheet_name:'B'});
      assert.equal(params.get('file_id'),'B.xlsx'); assert.equal(params.get('platform'),'threads');
      assert.equal(params.get('sheet_name'),'B');
    """)


def test_local_workbook_source_clears_old_google_target():
    run_js("""
      activePlatform='threads'; googleSheetUrlDirty=false;
      el('threadsGoogleSheetUrlInput').value='https://old.example';
      setGoogleSheetUrlField(''); assert.equal(el('threadsGoogleSheetUrlInput').value,'');
      assert.equal(platformSources.threads.url,'');
    """)


@pytest.mark.parametrize("known_sheets,listed_sheets", [
    (["B", "Keep"], None),            # sheet list already known
    ([], ["B", "Keep"]),              # learned from /list-files before selecting
    ([], None),                       # unknown: server rejects, retry without sheet names
])
def test_stale_saved_sheet_never_blocks_switching_platform(known_sheets, listed_sheets):
    # Saved display sheet 'Gone' was deleted from the workbook; /select-file rejects it with 400.
    run_js("""(async()=>{
      sourcesInitialized=true; activePlatform='tiktok'; currentFileId='A.xlsx';
      const listed=%s;
      Object.assign(platformSources.threads,{fileId:'B.xlsx',sheets:%s,displaySheet:'Gone',scanSheet:listed||%s.length?'Keep':'Gone',pushSheet:''});
      const bodies=[];
      fetch=async(url,options)=>{
        if(url.startsWith('/list-files')) return listed
          ? {ok:true,json:async()=>({current:'B.xlsx',currentLabel:'B',sheets:listed})}
          : {ok:false,status:500,json:async()=>({error:'offline'})};
        if(url!=='/select-file') return {ok:true,json:async()=>({})};
        const body=JSON.parse(options.body); bodies.push(body);
        if([body.sheet_name,body.scan_sheet].some(sheet=>sheet&&!['B','Keep'].includes(sheet)))
          return {ok:false,status:400,json:async()=>({error:'Sheet không tồn tại'})};
        return {ok:true,status:200,json:async()=>({success:true,sheet:body.sheet_name||'B',scanSheet:body.scan_sheet||'B'})};
      };
      await setPlatform('threads');
      assert.equal(activePlatform,'threads'); assert.equal(currentFileId,'B.xlsx');
      assert.equal(platformSources.threads.fileId,'B.xlsx');
      assert.ok(!bodies.at(-1).sheet_name);
      if(platformSources.threads.sheets.length){
        assert.equal(bodies.length,1); assert.equal(bodies[0].scan_sheet,'Keep');
      }
    })()""" % (json.dumps(listed_sheets), json.dumps(known_sheets), json.dumps(known_sheets)))
