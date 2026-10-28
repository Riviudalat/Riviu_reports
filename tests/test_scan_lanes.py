"""Riviu Data storage: one scan lane per platform, so TikTok and Threads scan side by side."""

import asyncio

import pytest

from riviu import scan_journal, scan_service, scan_state
from riviu import server_context as context
from riviu.store import config as store_config


@pytest.fixture
def lanes(monkeypatch, tmp_path):
    monkeypatch.setattr(store_config, "ACTIVE", store_config.StoreConfig(backend="postgres", database_url="postgresql://riviu_scanner@fake/db"))
    monkeypatch.setattr(context, "EXCEL_DIR", str(tmp_path))
    return tmp_path


def test_each_platform_has_its_own_lane_for_start_skip_and_cancel(lanes):
    async def scenario():
        tiktok = scan_state.lane("tiktok")
        tiktok.task = asyncio.create_task(asyncio.sleep(10))
        threads_wait = asyncio.create_task(scan_service.wait_retry_cooldown(10, "threads"))
        await asyncio.sleep(0)
        assert scan_state.start_blocked("tiktok") and not scan_state.start_blocked("threads")
        assert scan_state.source_mutation_blocked()  # a cookie or source change still waits for every lane
        assert scan_service.skip_retry_wait("tiktok") is False  # TikTok is not in a cooldown
        assert scan_service.skip_retry_wait("threads") is True
        assert await threads_wait is False
        assert scan_service.user_cancel_scan("threads") is False
        assert scan_service.user_cancel_scan("tiktok") is True
        await asyncio.gather(tiktok.task, return_exceptions=True)
        assert tiktok.task.cancelled() and not scan_state.scan_running()

    asyncio.run(scenario())


def test_workbook_storage_keeps_one_lane_for_every_platform(monkeypatch):
    monkeypatch.setattr(store_config, "ACTIVE", store_config.WORKBOOK)

    async def scenario():
        monkeypatch.setattr(scan_state, "SCRAPE_TASK", asyncio.create_task(asyncio.sleep(10)))
        assert scan_state.lane("threads") is scan_state.lane("tiktok") is scan_state.SINGLE_LANE
        assert scan_state.start_blocked("threads")
        scan_state.SCRAPE_TASK.cancel()
        await asyncio.gather(scan_state.SCRAPE_TASK, return_exceptions=True)

    asyncio.run(scenario())


def test_interrupted_scans_of_both_lanes_resume_side_by_side(lanes, monkeypatch):
    for platform in ("tiktok", "threads"):
        options = scan_service.scan_options(platform=platform, file_id=scan_service.RIVIU_DATA_FILE_ID, sheet_name="2026-10")
        journal = scan_journal.RunJournal.begin(str(lanes), options, scan_service.journal_payload(options), lane=platform)
        asyncio.run(journal.save())
        assert (lanes / "data" / f"scan_journal.{platform}.json").exists()
    started = []

    async def prepare(payload):
        return None, scan_service.scan_options(platform=payload["platform"], file_id=payload["file_id"], sheet_name=payload["sheet_name"])

    async def runner(target_path, options, run_context=None):
        started.append((options["platform"], options["resume"]["lane"], scan_state.scan_running("tiktok"), scan_state.scan_running("threads")))
        await asyncio.sleep(0.02)
        await scan_state.lane(options["platform"]).manager.broadcast_status({"done": True, "phase": "completed"})

    monkeypatch.setattr(scan_service, "prepare_scan_start", prepare)
    monkeypatch.setattr(scan_service, "run_scraper_safely", runner)

    async def restart():
        tasks = await scan_service.resume_all_interrupted()
        await asyncio.gather(*tasks)

    asyncio.run(restart())
    assert sorted(item[:2] for item in started) == [("threads", "threads"), ("tiktok", "tiktok")]
    assert started[-1][2:] == (True, True)  # the second resume started while the first still ran
