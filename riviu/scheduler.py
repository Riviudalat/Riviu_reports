"""Server mode: scheduled automatic runs ("Lịch tự động").

The admin keeps, per platform, a list of daily time slots (`HH:MM`, Asia/Ho_Chi_Minh) and the
scan options to use. At a slot the server, for that platform:

1. syncs the platform's Google Sheet like "Nạp sheet" (`riviu.api.google.sync_google_sheet_source`)
   and makes the new workbook that platform's saved source (`data/source_preferences.json`) on
   its newest month sheet (`keep_or_newest_month_sheet`),
2. starts one scan of it through `scan_service.prepare_scan_start` / `start_scan_task`, so open
   pages follow it live over `/ws` like a manual scan,
3. never pushes to Google: "Tạo sheet" stays a manual admin action.

With `RIVIU_STORAGE=postgres` step 1 is skipped: each slot scans its `months` of the platform's
posts in Riviu Data one after another ("latest" is the newest month,
`scan_service.resolve_store_month`), and the platforms run side by side, each in its own scan
lane (`scan_state.lane`). On workbook storage the platforms run one after the other.

Option `retryFailed` (default on) adds the scan's retry pass over transient failures; a scheduled
run interrupted by a restart is resumed at startup (riviu/scan_journal.py) and still never pushes.

A slot is skipped (and logged) when a scan of that platform (any scan, on workbook storage) or a
source operation is already running, and a failed sync records the error and skips the scan. From the sync until the scan task exists the run holds
`scan_state.scan_starting()`, so a manual start or source change cannot slip in between.

The loop wakes once a minute. Slots missed while the server was down (or the loop was stalled for
longer than `CATCH_UP_LIMIT`) are skipped, never replayed. Files, both written atomically:
    data/scan_schedule.json       the admin's configuration (see `normalize_schedule`)
    data/scan_schedule_runs.json  the last `RUN_LOG_LIMIT` run results, newest first
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

from riviu import scan_service, scan_settings, scan_state
from riviu import server_context as context
from riviu import source_files
from riviu.store import config as store_config
from riviu.workbook_utils import PLATFORMS, google_sheet_source_for_file, keep_or_newest_month_sheet, write_json_atomic

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    SCHEDULE_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except (ImportError, ZoneInfoNotFoundError):  # no tz database: Vietnam has no DST
    SCHEDULE_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")

TIMEZONE_LABEL = "Asia/Ho_Chi_Minh (UTC+7)"
SCHEDULE_VERSION = 1
MAX_SLOTS = 24
RUN_LOG_LIMIT = 50
MAX_BODY_BYTES = 16384
# A tick later than this after the previous one (downtime, a stalled loop) only runs the
# current minute's slots; the minutes in between are skipped, never replayed.
CATCH_UP_LIMIT = timedelta(minutes=3)
SLOT_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
# Riviu Data months a slot scans: "latest" (the newest month, followed as months are added) or "YYYY-MM".
LATEST_MONTH = "latest"
MONTH_PATTERN = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
MAX_SLOT_MONTHS = 24
SCRAPE_MODES = scan_settings.SCRAPE_MODES
WORKER_CHOICES = scan_settings.WORKER_CHOICES

logger = logging.getLogger("riviu.scheduler")


def schedule_path(base_dir=None):
    return os.path.join(base_dir or context.EXCEL_DIR, "data", "scan_schedule.json")


def runs_path(base_dir=None):
    return os.path.join(base_dir or context.EXCEL_DIR, "data", "scan_schedule_runs.json")


DEFAULT_SCRAPE_MODES = scan_settings.DEFAULT_SCRAPE_MODES
LOG_LEVELS = {"completed": "OK", "skipped": "WARN", "stopped": "WARN", "cancelled": "WARN", "failed": "ERROR"}


def default_options(platform):
    return {**scan_settings.default_settings(platform), "retryFailed": True}


def empty_schedule(seed=None):
    """No slots; options from `seed` (the admin's saved scan settings) or the page defaults."""
    seed = seed or {}
    return {"version": SCHEDULE_VERSION, "platforms": {
        key: {"slots": [], "options": {**default_options(key), **seed.get(key, {})}} for key in PLATFORMS
    }}


# ---- Configuration ---------------------------------------------------------------------

def _invalid(message="Cấu hình lịch tự động không hợp lệ."):
    return ValueError(message)


def _normalize_options(platform, raw):
    if raw is None:
        return default_options(platform)
    if not isinstance(raw, dict):
        raise _invalid()
    options = default_options(platform)
    workers = raw.get("workers", options["workers"])
    if isinstance(workers, bool) or not isinstance(workers, int) or workers not in WORKER_CHOICES:
        raise _invalid("Số luồng của lịch tự động không hợp lệ.")
    mode = raw.get("scrapeMode", options["scrapeMode"])
    if mode not in SCRAPE_MODES:
        raise _invalid("Chế độ quét của lịch tự động không hợp lệ.")
    flags = {}
    for key in ("useProxy", "useSession", "retryFailed"):
        value = raw.get(key, options[key])
        if not isinstance(value, bool):
            raise _invalid()
        flags[key] = value
    return {"workers": workers, "scrapeMode": mode, **flags}


def _normalize_months(raw):
    if raw is None:
        return [LATEST_MONTH]
    if not isinstance(raw, list) or len(raw) > MAX_SLOT_MONTHS:
        raise _invalid(f"Mỗi khung giờ quét tối đa {MAX_SLOT_MONTHS} tháng.")
    months = []
    for value in raw:
        if not isinstance(value, str) or not (value == LATEST_MONTH or MONTH_PATTERN.match(value)):
            raise _invalid("Tháng của khung giờ phải là \"latest\" hoặc dạng YYYY-MM.")
        if value not in months:
            months.append(value)
    if not months:
        raise _invalid("Mỗi khung giờ cần ít nhất một tháng để quét.")
    # The newest month first, then the chosen months newest first.
    return sorted(months, key=lambda value: (value != LATEST_MONTH, "" if value == LATEST_MONTH else _desc(value)))


def _desc(month):
    year, number = month.split("-")
    return f"{9999 - int(year):04d}-{99 - int(number):02d}"


def _normalize_slots(raw):
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_SLOTS:
        raise _invalid(f"Mỗi nền tảng có tối đa {MAX_SLOTS} khung giờ.")
    slots, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise _invalid()
        time_text = item.get("time")
        enabled = item.get("enabled", True)
        if not isinstance(time_text, str) or not SLOT_PATTERN.match(time_text):
            raise _invalid("Giờ phải có dạng HH:MM (00:00–23:59).")
        if not isinstance(enabled, bool):
            raise _invalid()
        if time_text in seen:
            raise _invalid(f"Trùng khung giờ {time_text}.")
        seen.add(time_text)
        slots.append({"time": time_text, "enabled": enabled, "months": _normalize_months(item.get("months"))})
    return sorted(slots, key=lambda slot: slot["time"])


def normalize_schedule(payload):
    """`{"version": 1, "platforms": {platform: {"slots": [{"time": "HH:MM", "enabled": bool, "months": ["latest" | "YYYY-MM"]}],
    "options": {"workers", "scrapeMode", "useProxy", "useSession", "retryFailed"}}}}`; raises ValueError."""
    if not isinstance(payload, dict) or not isinstance(payload.get("platforms", {}), dict):
        raise _invalid()
    platforms = payload.get("platforms", {})
    unknown = set(platforms) - set(PLATFORMS)
    if unknown:
        raise _invalid("Nền tảng không hợp lệ trong lịch tự động.")
    result = empty_schedule()
    for key in PLATFORMS:
        item = platforms.get(key, {})
        if not isinstance(item, dict):
            raise _invalid()
        result["platforms"][key] = {
            "slots": _normalize_slots(item.get("slots")),
            "options": _normalize_options(key, item.get("options")),
        }
    return result


def load_schedule(base_dir=None):
    try:
        with open(schedule_path(base_dir), encoding="utf-8") as stream:
            return normalize_schedule(json.load(stream))
    except FileNotFoundError:
        # Nothing scheduled yet: a first slot starts from the admin's scan settings.
        return empty_schedule(scan_settings.load_settings(base_dir)["platforms"])
    except (OSError, ValueError, TypeError) as error:
        logger.warning("Không đọc được lịch tự động: %s", error)
        return empty_schedule()


def save_schedule(payload, base_dir=None):
    schedule = normalize_schedule(payload)
    path = schedule_path(base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json_atomic(path, schedule)
    return schedule


def load_runs(base_dir=None):
    try:
        with open(runs_path(base_dir), encoding="utf-8") as stream:
            runs = json.load(stream).get("runs", [])
        return [run for run in runs if isinstance(run, dict)][:RUN_LOG_LIMIT]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


# Platforms record their runs side by side (Riviu Data storage): one read-modify-write at a time.
_RUNS_LOCK = threading.Lock()


def record_run(entry, base_dir=None):
    with _RUNS_LOCK:
        runs = [entry] + [run for run in load_runs(base_dir) if run.get("id") != entry.get("id")]
        path = runs_path(base_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_json_atomic(path, {"runs": runs[:RUN_LOG_LIMIT]})
    return entry


# ---- Timing ----------------------------------------------------------------------------

def local_now():
    return datetime.now(SCHEDULE_TZ)


def _minute(moment):
    return moment.replace(second=0, microsecond=0)


def due_slots(schedule, previous, now):
    """Enabled `(platform, "HH:MM", scheduled_at)` slots that fall due at this tick.

    Normally that is every minute after the previous tick up to and including now's minute, so
    a tick that runs a little late still catches its slot exactly once. After downtime or a
    stall longer than CATCH_UP_LIMIT (or on the first tick) only now's minute counts.
    """
    current = _minute(now)
    minutes = [current]
    if previous is not None:
        last = _minute(previous)
        if last >= current:
            return []
        if current - last <= CATCH_UP_LIMIT:
            minutes = []
            step = last + timedelta(minutes=1)
            while step <= current:
                minutes.append(step)
                step += timedelta(minutes=1)
    by_time = {moment.strftime("%H:%M"): moment for moment in minutes}
    due = []
    for platform in PLATFORMS:
        for slot in schedule["platforms"].get(platform, {}).get("slots", []):
            if slot["enabled"] and slot["time"] in by_time:
                due.append((platform, slot["time"], by_time[slot["time"]]))
    return sorted(due, key=lambda item: (item[2], list(PLATFORMS).index(item[0])))


def slot_months(schedule, platform, time_text):
    """The months the platform's slot at `time_text` scans (the newest month if the slot is gone)."""
    for slot in schedule["platforms"].get(platform, {}).get("slots", []):
        if slot["time"] == time_text:
            return list(slot.get("months") or [LATEST_MONTH])
    return [LATEST_MONTH]


def next_run_at(schedule, platform, now):
    """The next enabled slot strictly after now's minute, or None."""
    current = _minute(now)
    candidates = []
    for slot in schedule["platforms"].get(platform, {}).get("slots", []):
        if not slot["enabled"]:
            continue
        hour, minute = (int(part) for part in slot["time"].split(":"))
        moment = current.replace(hour=hour, minute=minute)
        if moment <= current:
            moment += timedelta(days=1)
        candidates.append(moment)
    return min(candidates) if candidates else None


def seconds_until_next_minute(now):
    return max(1.0, 60 - now.second - now.microsecond / 1_000_000 + 0.5)


# ---- Runs ------------------------------------------------------------------------------

def _iso(moment):
    return moment.isoformat(timespec="seconds") if moment else None


def platform_source(platform):
    """The platform's saved source (what the admin page shows) and the Google Sheet to sync."""
    from riviu.api.sources import load_source_preferences

    preferences = load_source_preferences()
    source = dict(preferences.get("sources", {}).get(platform, {}))
    url = (source.get("url") or "").strip()
    if not url and source.get("fileId"):
        url = google_sheet_source_for_file(context.EXCEL_DIR, source["fileId"]).get("url", "")
    return preferences, source, url


def adopt_synced_source(preferences, platform, source, synced, url):
    """Make the freshly synced workbook the platform's source, as the admin page does after
    "Nạp sheet", on the newest month sheet ("Tháng 10" once it exists). The saved scan sheet is
    kept only while the new workbook still has it and holds no newer month."""
    from riviu.api.sources import save_source_preferences

    data_sheets = synced.get("dataSheets") or []
    sheet = keep_or_newest_month_sheet(data_sheets, source.get("scanSheet", "")) or synced.get("currentSheet", "")
    sources = {key: dict(value) for key, value in preferences.get("sources", {}).items()}
    sources[platform] = {
        **{key: "" for key in ("fileId", "displaySheet", "scanSheet", "pushSheet", "url")},
        **sources.get(platform, {}),
        "fileId": synced["file_id"], "displaySheet": sheet, "scanSheet": sheet, "pushSheet": sheet, "url": url,
    }
    save_source_preferences({"sources": sources})
    source_files.select_source(synced["file_id"], sheet, sheet)
    return sheet


async def scan_payload(platform, file_id, sheet, options):
    payload = {
        "action": "start", "platform": platform, "file_id": file_id, "sheet_name": sheet,
        "workers": options["workers"], "scrape_mode": options["scrapeMode"],
        # Empty text: the scan uses the saved proxy list, like the page does.
        "use_proxy": options["useProxy"], "proxy_text": "",
        "partners": [], "partner": "",
        "create_result_sheet": False, "push_to_google": False, "retry_failed": options.get("retryFailed", True),
    }
    if options["useSession"] and platform == "threads":
        status = await context.complete_blocking(context.threads_session_manager().status)
        if not status.get("configured") or not status.get("generation"):
            raise ValueError(scan_service.cookie_problem_message("threads", status.get("message") or ""))
        payload.update(use_threads_session=True, threads_session_generation=status["generation"])
    elif options["useSession"] and platform == "tiktok":
        payload["use_tiktok_session"] = True
    return payload


def scan_source_label(file_id, sheet):
    if file_id == scan_service.RIVIU_DATA_FILE_ID:
        return f"Riviu Data {scan_service.month_title(sheet)}"
    return f'sheet "{sheet}" • file: {file_id}'


def _result_from_status(status, task):
    if task.cancelled() or status.get("cancelled") or status.get("phase") == "cancelled":
        result = "cancelled"
    elif status.get("phase") == "failed":
        result = "failed"
    elif status.get("stopReason"):
        result = "stopped"
    else:
        result = "completed"
    counts = {key: status.get(key) for key in ("total", "processed", "success", "error", "hidden", "loginRequired") if key in status}
    return result, counts


class Scheduler:
    """One background loop per server process; `run_job` is the unit tests drive."""

    def __init__(self, clock=local_now, sleep=asyncio.sleep, sync=None):
        self.clock = clock
        self.sleep = sleep
        self._sync = sync
        # Running batch of slots per lane: one key per platform on Riviu Data storage, None otherwise.
        self.runners = {}
        self.previous_tick = None

    async def sync(self, url, platform):
        if self._sync is not None:
            return await self._sync(url, platform)
        from riviu.api.google import sync_google_sheet_source

        return await sync_google_sheet_source(url, platform, activate=False)

    @property
    def runner(self):
        """The most recently started batch (any lane)."""
        return next(reversed(self.runners.values()), None)

    def running(self, key=None):
        """A batch of `key`'s lane (of any lane when None) is still running."""
        tasks = list(self.runners.values()) if key is None else [self.runners.get(key)]
        return any(task is not None and not task.done() for task in tasks)

    async def tick(self):
        now = self.clock()
        due = due_slots(await asyncio.to_thread(load_schedule), self.previous_tick, now)
        self.previous_tick = now
        if not due:
            return None
        started = None
        for key, jobs in _lane_groups(due).items():
            if self.running(key):
                for platform, slot, scheduled in jobs:
                    await self.record(platform, slot, scheduled, "skipped", "Lượt tự động trước vẫn đang chạy.")
                continue
            self.runners[key] = started = asyncio.create_task(self.run_jobs(jobs))
        return started

    async def run_forever(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Lịch tự động: lỗi khi kiểm tra giờ chạy")
            await self.sleep(seconds_until_next_minute(self.clock()))

    async def run_jobs(self, jobs):
        """Each lane's slots one after another; on Riviu Data storage the lanes (platforms) run side
        by side, on workbook storage a second platform due at the same minute waits for the first."""
        groups = _lane_groups(jobs)
        await asyncio.gather(*(self._run_sequence(group) for group in groups.values()))

    async def _run_sequence(self, jobs):
        for platform, slot, scheduled in jobs:
            try:
                if store_config.ACTIVE.postgres:
                    await self.run_months(platform, slot, scheduled)
                else:
                    await self.run_job(platform, slot, scheduled)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Lịch tự động %s %s lỗi", platform, slot)
                await self.record(platform, slot, scheduled, "failed", f"Lỗi kỹ thuật: {error}")

    async def run_months(self, platform, slot, scheduled):
        """The slot's Riviu Data months, newest first; "latest" is resolved now and never scanned twice."""
        months = slot_months(await asyncio.to_thread(load_schedule), platform, slot)
        done = set()
        for month in months:
            try:
                thang = await scan_service.resolve_store_month(platform, "" if month == LATEST_MONTH else month)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self.record(platform, slot, scheduled, "failed", f"Không xác định được tháng để quét: {error}")
                continue
            if thang in done:
                continue
            done.add(thang)
            await self.run_job(platform, slot, scheduled, thang=thang)

    async def record(self, platform, slot, scheduled, status, message, started=None, **extra):
        entry = {
            "id": secrets.token_hex(8), "platform": platform, "slot": slot,
            "scheduledFor": _iso(scheduled), "startedAt": _iso(started or self.clock()),
            "finishedAt": _iso(self.clock()), "status": status, "message": message, **extra,
        }
        try:
            await asyncio.to_thread(record_run, entry)
        except Exception:
            logger.exception("Lịch tự động: không ghi được nhật ký chạy")
        level = LOG_LEVELS.get(status, "")
        label = PLATFORMS[platform].label
        await scan_state.lane(platform).manager.broadcast_log(f"Lịch tự động {label} {slot}: {message}", level=level)
        return entry

    async def run_job(self, platform, slot, scheduled=None, thang=None):
        started = self.clock()
        scheduled = scheduled or started
        if scan_state.start_blocked(platform):
            return await self.record(platform, slot, scheduled, "skipped", "Bỏ qua: đang có phiên quét hoặc thao tác dữ liệu khác.", started)
        label = PLATFORMS[platform].label
        # Taken right after the check (no await in between) and held until the scan task exists:
        # a manual start or source change in the meantime is refused as busy.
        with scan_state.scan_starting(platform):
            options = (await asyncio.to_thread(load_schedule))["platforms"][platform]["options"]
            if store_config.ACTIVE.postgres:
                # Riviu Data holds the post list: nothing to download; `thang` (default the newest month).
                file_id, sheet = scan_service.RIVIU_DATA_FILE_ID, thang or ""
            else:
                preferences, source, url = await asyncio.to_thread(platform_source, platform)
                if not url:
                    return await self.record(platform, slot, scheduled, "skipped", "Bỏ qua: nền tảng này chưa có link Google Sheet nguồn.", started)
                await scan_state.manager.broadcast_log(f"Lịch tự động {label} {slot}: đang nạp Google Sheet...")
                try:
                    synced = await self.sync(url, platform)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    return await self.record(platform, slot, scheduled, "failed", f"Nạp sheet thất bại, không quét: {error}", started)
                file_id = synced["file_id"]
            try:
                if file_id != scan_service.RIVIU_DATA_FILE_ID:
                    sheet = await context.complete_blocking(adopt_synced_source, preferences, platform, source, synced, url)
                payload = await scan_payload(platform, file_id, sheet, options)
                target_path, scan_options = await scan_service.prepare_scan_start(payload)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return await self.record(platform, slot, scheduled, "failed", f"Không bắt đầu quét được: {error}", started, fileId=file_id)
            # Pushing stays a manual "Tạo sheet"; never let a scheduled scan push (a resume keeps this).
            scan_options["push_to_google"] = False
            scan_options["scheduled"] = True
            await scan_state.manager.broadcast_log(
                f"Lịch tự động {label} {slot}: bắt đầu quét {scan_source_label(file_id, scan_options['sheet_name'])} • "
                f"{scan_options['scrape_mode']}{' • proxy bật' if scan_options['use_proxy'] else ''} • luồng: {scan_options['worker_count']}."
            )
            task, run_context = scan_service.start_scan_task(target_path, scan_options)
            await scan_state.manager.broadcast_status(scan_state.manager.last_status)
        # Wait without letting a scheduler shutdown cancel the scan itself.
        await asyncio.wait({task})
        manager = scan_state.lane(platform).manager
        status = manager.last_status if manager.context.get("runId") == run_context["runId"] else {}
        result, counts = _result_from_status(status, task)
        messages = {
            # e.g. Threads posts only a logged-in viewer sees: the run log says what to do about them.
            "completed": " ".join(filter(None, [
                "Đã quét xong." if file_id == scan_service.RIVIU_DATA_FILE_ID else "Đã nạp sheet và quét xong.", status.get("notice")])),
            "stopped": "Quét dừng sớm, kết quả chưa đầy đủ.",
            "cancelled": "Phiên quét đã bị hủy.",
            "failed": f"Quét thất bại: {status.get('message') or 'lỗi không rõ'}",
        }
        return await self.record(
            platform, slot, scheduled, result, messages[result], started,
            fileId=file_id, sheet=scan_options["sheet_name"], counts=counts,
        )


def _lane_groups(jobs):
    """Due slots per scan lane, in order: by platform on Riviu Data storage, all together otherwise."""
    groups = {}
    for job in jobs:
        groups.setdefault(job[0] if store_config.ACTIVE.postgres else None, []).append(job)
    return groups


SCHEDULER = Scheduler()
_LOOP_TASK = None


def start():
    """Start the background loop (server mode only; see the app lifespan)."""
    global _LOOP_TASK
    if _LOOP_TASK is None or _LOOP_TASK.done():
        _LOOP_TASK = asyncio.create_task(SCHEDULER.run_forever())
    return _LOOP_TASK


async def stop():
    global _LOOP_TASK
    task, _LOOP_TASK = _LOOP_TASK, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def overview(base_dir=None):
    """What the admin's "Lịch tự động" dialog shows."""
    schedule = load_schedule(base_dir)
    now = local_now()
    runs = load_runs(base_dir)
    platforms = {}
    for key in PLATFORMS:
        last = next((run for run in runs if run.get("platform") == key), None)
        platforms[key] = {"nextRun": _iso(next_run_at(schedule, key, now)), "lastRun": last}
    return {
        "schedule": schedule, "runs": runs, "platforms": platforms,
        "now": _iso(now), "timezone": TIMEZONE_LABEL, "active": SCHEDULER.running(),
    }
