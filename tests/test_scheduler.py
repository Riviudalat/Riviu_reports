"""Server mode scheduled runs ("Lịch tự động"): configuration, timing and one run.

A run syncs the platform's Google Sheet (stubbed: no network), adopts the new workbook as the
platform's source and scans it through the real prepare/start path with a stubbed scanner. It
never pushes to Google. Synthetic workbooks and a fake clock only.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from riviu import app as backend
from riviu import scan_service, scan_state, scheduler, server_mode
from riviu.api import google as google_api
from riviu.api import sources as sources_api
from riviu.google_sheets_sync import GoogleLoginRequired
from riviu.workbook_utils import register_google_sheet_source

from test_platform_source_api import make_source
from test_server_mode import PUBLIC, login, server  # noqa: F401  (fixture)


TZ = scheduler.SCHEDULE_TZ
SHEET_URL = "https://docs.google.com/spreadsheets/d/SCHEDULED/edit"


def at(hour, minute, second=0, day=28):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=TZ)


def config(**platforms):
    return scheduler.normalize_schedule({"platforms": {
        key: {"slots": [{"time": time, "enabled": enabled} for time, enabled in slots]} for key, slots in platforms.items()
    }})


# ---- Configuration -------------------------------------------------------------------------

def test_schedule_normalizes_sorts_and_fills_defaults():
    schedule = scheduler.normalize_schedule({"platforms": {
        "tiktok": {"slots": [{"time": "17:30", "enabled": False}, {"time": "08:00"}], "options": {"workers": 20, "scrapeMode": "hybrid", "useProxy": True, "useSession": False}},
    }})
    assert schedule == {"version": 1, "platforms": {
        "tiktok": {"slots": [{"time": "08:00", "enabled": True}, {"time": "17:30", "enabled": False}],
                   "options": {"workers": 20, "scrapeMode": "hybrid", "useProxy": True, "useSession": False}},
        "threads": {"slots": [], "options": {"workers": 10, "scrapeMode": "hybrid", "useProxy": False, "useSession": False}},
    }}


@pytest.mark.parametrize("platforms, message", [
    ({"tiktok": {"slots": [{"time": "24:00"}]}}, "HH:MM"),
    ({"tiktok": {"slots": [{"time": "8:00"}]}}, "HH:MM"),
    ({"tiktok": {"slots": [{"time": "08:00 "}]}}, "HH:MM"),
    ({"tiktok": {"slots": [{"time": "08:00"}, {"time": "08:00", "enabled": False}]}}, "Trùng"),
    ({"tiktok": {"slots": [{"time": "08:00", "enabled": "yes"}]}}, "không hợp lệ"),
    ({"tiktok": {"slots": [{"time": f"{hour:02d}:00"} for hour in range(24)] + [{"time": "00:30"}]}}, "tối đa"),
    ({"tiktok": {"options": {"workers": 7}}}, "luồng"),
    ({"tiktok": {"options": {"workers": True}}}, "luồng"),
    ({"threads": {"options": {"scrapeMode": "fast"}}}, "Chế độ"),
    ({"threads": {"options": {"useProxy": 1}}}, "không hợp lệ"),
    ({"youtube": {"slots": []}}, "Nền tảng"),
])
def test_schedule_rejects_invalid_slots_and_options(platforms, message):
    with pytest.raises(ValueError, match=message):
        scheduler.normalize_schedule({"platforms": platforms})


def test_schedule_and_run_log_persist_atomically_in_the_data_dir(tmp_path):
    saved = scheduler.save_schedule({"platforms": {"threads": {"slots": [{"time": "09:15"}]}}}, str(tmp_path))
    assert json.loads((tmp_path / "data" / "scan_schedule.json").read_text(encoding="utf-8")) == saved
    assert scheduler.load_schedule(str(tmp_path)) == saved
    for index in range(scheduler.RUN_LOG_LIMIT + 5):
        scheduler.record_run({"id": str(index), "platform": "tiktok", "status": "skipped"}, str(tmp_path))
    runs = scheduler.load_runs(str(tmp_path))
    assert len(runs) == scheduler.RUN_LOG_LIMIT and runs[0]["id"] == str(scheduler.RUN_LOG_LIMIT + 4)
    assert not [name for name in os.listdir(tmp_path / "data") if name.endswith(".tmp")]
    (tmp_path / "data" / "scan_schedule.json").write_text("{broken", encoding="utf-8")
    assert scheduler.load_schedule(str(tmp_path)) == scheduler.empty_schedule()


# ---- Timing --------------------------------------------------------------------------------

def test_due_slots_fire_once_per_minute_and_catch_a_slightly_late_tick():
    schedule = config(tiktok=[("08:00", True), ("08:01", True), ("09:00", False)], threads=[("08:01", True)])
    assert scheduler.due_slots(schedule, None, at(8, 0, 2)) == [("tiktok", "08:00", at(8, 0))]
    # The same minute again: nothing.
    assert scheduler.due_slots(schedule, at(8, 0, 2), at(8, 0, 40)) == []
    # A tick that ran late (08:00:10 -> 08:02:05) still fires 08:01 for both platforms, TikTok first.
    assert scheduler.due_slots(schedule, at(8, 0, 10), at(8, 2, 5)) == [("tiktok", "08:01", at(8, 1)), ("threads", "08:01", at(8, 1))]
    assert scheduler.due_slots(schedule, at(8, 59), at(9, 0)) == []  # disabled


def test_missed_slots_after_downtime_are_skipped_not_replayed():
    schedule = config(tiktok=[("08:00", True), ("12:00", True)])
    # Down from 07:30 to 12:00:20: 08:00 is not replayed; only the current minute's slot runs.
    assert scheduler.due_slots(schedule, at(7, 30), at(12, 0, 20)) == [("tiktok", "12:00", at(12, 0))]
    assert scheduler.due_slots(schedule, at(7, 30), at(12, 5)) == []
    # First tick after a start, mid-day: nothing from the morning either.
    assert scheduler.due_slots(schedule, None, at(10, 0)) == []


def test_due_slots_cross_midnight_and_next_run():
    schedule = config(threads=[("00:00", True), ("23:59", True)])
    assert scheduler.due_slots(schedule, at(23, 58, 30), at(0, 0, 5, day=29)) == [
        ("threads", "23:59", at(23, 59)), ("threads", "00:00", at(0, 0, day=29)),
    ]
    assert scheduler.next_run_at(schedule, "threads", at(12, 0)) == at(23, 59)
    assert scheduler.next_run_at(schedule, "threads", at(23, 59, 30)) == at(0, 0, day=29)
    assert scheduler.next_run_at(schedule, "tiktok", at(12, 0)) is None


def test_tick_after_downtime_starts_no_run(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "EXCEL_DIR", str(tmp_path))
    scheduler.save_schedule({"platforms": {"tiktok": {"slots": [{"time": "08:00"}]}}}, str(tmp_path))
    now = [at(7, 59, 50)]
    instance = scheduler.Scheduler(clock=lambda: now[0])
    started = []
    monkeypatch.setattr(instance, "run_jobs", lambda jobs: started.append(jobs) or asyncio.sleep(0))

    async def ticks():
        await instance.tick()
        now[0] = at(9, 30)  # the server was down over 08:00
        await instance.tick()
        now[0] = at(9, 31)
        await instance.tick()

    asyncio.run(ticks())
    assert started == []


# ---- One run -------------------------------------------------------------------------------

@pytest.fixture
def run_env(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "EXCEL_DIR", str(tmp_path))
    for name, value in (("SCRAPE_TASK", None), ("SCAN_STARTING", False), ("SOURCE_BUSY", False), ("DESKTOP_UPDATE_PENDING", False)):
        monkeypatch.setattr(scan_state, name, value)
    monkeypatch.setattr(scan_state, "manager", scan_state.ConnectionManager())
    monkeypatch.setattr(backend, "CURRENT_SELECTED_FILE", "")

    def never_push(*_args, **_kwargs):
        pytest.fail("a scheduled run must never push to Google")

    monkeypatch.setattr(google_api, "push_rows_to_new_sheet", never_push)
    monkeypatch.setattr(scan_service, "push_rows_to_new_sheet", never_push)
    make_source(tmp_path / "Old.xlsx", "Old")
    register_google_sheet_source(str(tmp_path), "Old.xlsx", SHEET_URL)
    sources_api.save_source_preferences({"sources": {"threads": {
        "fileId": "Old.xlsx", "displaySheet": "Old Other", "scanSheet": "Other", "pushSheet": "", "url": SHEET_URL,
    }}})
    scheduler.save_schedule({"platforms": {"threads": {"slots": [{"time": "08:00"}], "options": {"workers": 3, "scrapeMode": "request", "useProxy": False, "useSession": False}}}}, str(tmp_path))
    return tmp_path


def synced_workbook(tmp_path, calls, name="Synced.xlsx"):
    async def sync(url, platform):
        calls.append(("sync", url, platform, scan_state.source_mutation_blocked()))
        make_source(tmp_path / name, "New")
        register_google_sheet_source(str(tmp_path), name, url)
        return {"file_id": name, "currentSheet": "New Data", "dataSheets": ["New Data", "New Other"]}
    return sync


def test_run_syncs_then_scans_the_new_workbook_and_never_pushes(run_env, monkeypatch):
    calls = []

    async def runner(target_path, options, run_context=None):
        calls.append(("scan", os.path.basename(target_path), options))
        await scan_state.manager.broadcast_status({"total": 4, "processed": 4, "success": 3, "error": 1, "hidden": 0, "done": True, "phase": "completed"})

    monkeypatch.setattr(scan_service, "run_scraper_safely", runner)
    original_source = scheduler.platform_source
    # The gate is taken before the first await after the busy check, so nothing can slip in.
    monkeypatch.setattr(scheduler, "platform_source", lambda platform: calls.append(("source", scan_state.source_mutation_blocked())) or original_source(platform))
    instance = scheduler.Scheduler(clock=lambda: at(8, 0, 1), sync=synced_workbook(run_env, calls))
    entry = asyncio.run(instance.run_job("threads", "08:00", at(8, 0)))

    assert calls.pop(0) == ("source", True)
    assert [call[0] for call in calls] == ["sync", "scan"]
    assert calls[0][1:] == (SHEET_URL, "threads", True)  # the gate is held while syncing
    _, target, options = calls[1]
    assert target == "Synced.xlsx"
    assert options["platform"] == "threads" and options["file_id"] == "Synced.xlsx"
    assert options["sheet_name"] == "New Data"  # the saved "Other" is not in the new workbook
    assert options["worker_count"] == 3 and options["scrape_mode"] == "request" and not options["use_proxy"]
    assert options["push_to_google"] is False and options["partners"] == []
    saved = sources_api.load_source_preferences()["sources"]["threads"]
    assert saved["fileId"] == "Synced.xlsx" and saved["scanSheet"] == saved["displaySheet"] == "New Data" and saved["url"] == SHEET_URL
    assert entry["status"] == "completed" and entry["fileId"] == "Synced.xlsx" and entry["sheet"] == "New Data"
    assert entry["counts"] == {"total": 4, "processed": 4, "success": 3, "error": 1, "hidden": 0}
    assert scheduler.load_runs()[0]["id"] == entry["id"]
    assert not scan_state.scan_running() and not scan_state.SCAN_STARTING


def test_run_keeps_the_saved_scan_sheet_when_the_new_workbook_has_it(run_env, monkeypatch):
    sources_api.save_source_preferences({"sources": {"threads": {"fileId": "Old.xlsx", "scanSheet": "New Other", "url": SHEET_URL}}})
    seen = []

    async def runner(target_path, options, run_context=None):
        seen.append(options["sheet_name"])
        await scan_state.manager.broadcast_status({"done": True, "phase": "completed"})

    monkeypatch.setattr(scan_service, "run_scraper_safely", runner)
    asyncio.run(scheduler.Scheduler(sync=synced_workbook(run_env, [])).run_job("threads", "08:00"))
    assert seen == ["New Other"]


def test_run_is_skipped_while_a_scan_or_source_operation_runs(run_env, monkeypatch):
    calls = []
    monkeypatch.setattr(scan_state, "SOURCE_BUSY", True)
    entry = asyncio.run(scheduler.Scheduler(sync=synced_workbook(run_env, calls)).run_job("threads", "08:00"))
    assert entry["status"] == "skipped" and "đang có phiên quét" in entry["message"]
    assert calls == [] and scheduler.load_runs()[0]["status"] == "skipped"


def test_run_without_a_google_sheet_link_is_skipped(run_env):
    sources_api.save_source_preferences({"sources": {}})
    entry = asyncio.run(scheduler.Scheduler(sync=synced_workbook(run_env, [])).run_job("tiktok", "08:00"))
    assert entry["status"] == "skipped" and "Google Sheet" in entry["message"]


def test_failed_sync_records_the_error_and_skips_the_scan(run_env, monkeypatch):
    async def sync(url, platform):
        raise GoogleLoginRequired()

    async def runner(*_args, **_kwargs):
        pytest.fail("no scan after a failed sync")

    monkeypatch.setattr(scan_service, "run_scraper_safely", runner)
    logs = []

    async def log(message, **kwargs):
        logs.append((message, kwargs.get("level")))

    monkeypatch.setattr(scan_state.manager, "broadcast_log", log)
    entry = asyncio.run(scheduler.Scheduler(sync=sync).run_job("threads", "08:00"))
    assert entry["status"] == "failed" and "Nạp sheet thất bại" in entry["message"] and "đăng nhập Google" in entry["message"]
    assert sources_api.load_source_preferences()["sources"]["threads"]["fileId"] == "Old.xlsx"
    assert logs[-1][1] == "ERROR" and "Lịch tự động Threads 08:00" in logs[-1][0]
    assert not scan_state.SCAN_STARTING


def test_threads_cookie_option_without_a_verified_session_fails_before_scanning(run_env, monkeypatch):
    scheduler.save_schedule({"platforms": {"threads": {"options": {"workers": 3, "scrapeMode": "request", "useProxy": False, "useSession": True}}}})

    async def runner(*_args, **_kwargs):
        pytest.fail("no scan without the cookie session")

    monkeypatch.setattr(scan_service, "run_scraper_safely", runner)
    monkeypatch.setattr(backend, "THREADS_SESSION", None)

    class NoCookies:
        def status(self):
            return {"configured": False, "generation": ""}

    monkeypatch.setattr(scheduler.context, "threads_session_manager", lambda: NoCookies())
    entry = asyncio.run(scheduler.Scheduler(sync=synced_workbook(run_env, [])).run_job("threads", "08:00"))
    assert entry["status"] == "failed" and "cookie Threads" in entry["message"]


def test_a_run_stopped_early_by_threads_429_is_recorded_as_stopped(run_env, monkeypatch):
    async def runner(target_path, options, run_context=None):
        await scan_state.manager.broadcast_status({"total": 9, "processed": 2, "success": 2, "done": True, "phase": "completed", "stopReason": "rate_limited"})

    monkeypatch.setattr(scan_service, "run_scraper_safely", runner)
    entry = asyncio.run(scheduler.Scheduler(sync=synced_workbook(run_env, [])).run_job("threads", "08:00"))
    assert entry["status"] == "stopped"


def test_jobs_due_together_run_one_after_another(run_env, monkeypatch):
    sources_api.save_source_preferences({"sources": {
        "threads": {"fileId": "Old.xlsx", "url": SHEET_URL}, "tiktok": {"fileId": "Old.xlsx", "url": SHEET_URL},
    }})
    events = []

    async def runner(target_path, options, run_context=None):
        events.append(("start", options["platform"], scan_state.scan_running()))
        await asyncio.sleep(0.01)
        events.append(("end", options["platform"]))
        await scan_state.manager.broadcast_status({"done": True, "phase": "completed"})

    monkeypatch.setattr(scan_service, "run_scraper_safely", runner)
    names = iter(["T1.xlsx", "T2.xlsx"])

    async def sync(url, platform):
        return await synced_workbook(run_env, [], next(names))(url, platform)

    instance = scheduler.Scheduler(sync=sync)
    asyncio.run(instance.run_jobs([("tiktok", "08:00", at(8, 0)), ("threads", "08:00", at(8, 0))]))
    assert events == [("start", "tiktok", True), ("end", "tiktok"), ("start", "threads", True), ("end", "threads")]
    assert [run["status"] for run in scheduler.load_runs()] == ["completed", "completed"]


# ---- API -----------------------------------------------------------------------------------

def test_schedule_api_is_admin_only_and_validates(server):
    assert server.get("/schedule").status_code == 401
    assert server.post("/schedule", headers={"Origin": PUBLIC}, json={"platforms": {}}).status_code == 401
    login(server)
    overview = server.get("/schedule").json()
    assert overview["schedule"] == scheduler.empty_schedule() and overview["runs"] == []
    assert overview["timezone"].startswith("Asia/Ho_Chi_Minh")
    bad = server.post("/schedule", headers={"Origin": PUBLIC}, json={"platforms": {"tiktok": {"slots": [{"time": "25:00"}]}}})
    assert bad.status_code == 400 and "HH:MM" in bad.json()["error"]
    saved = server.post("/schedule", headers={"Origin": PUBLIC}, json={"platforms": {"tiktok": {"slots": [{"time": "08:00"}, {"time": "20:30", "enabled": False}]}}})
    assert saved.status_code == 200 and saved.json()["success"]
    assert [slot["time"] for slot in scheduler.load_schedule()["platforms"]["tiktok"]["slots"]] == ["08:00", "20:30"]
    assert saved.json()["platforms"]["tiktok"]["nextRun"].endswith("+07:00")


def test_scheduler_loop_runs_only_in_server_mode(tmp_path, monkeypatch, server):
    # The `server` fixture's client started the app lifespan in server mode.
    assert scheduler._LOOP_TASK is not None and not scheduler._LOOP_TASK.done()
