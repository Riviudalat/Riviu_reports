"""Server mode: scheduled automatic runs ("Lịch tự động").

The admin keeps, per platform, a list of daily time slots (`HH:MM`, Asia/Ho_Chi_Minh) and the
scan options to use. At a slot the server, for that platform:

1. syncs the platform's Google Sheet like "Nạp sheet" (`riviu.api.google.sync_google_sheet_source`)
   and makes the new workbook that platform's saved source (`data/source_preferences.json`),
2. starts one scan of it through `scan_service.prepare_scan_start` / `start_scan_task`, so open
   pages follow it live over `/ws` like a manual scan,
3. never pushes to Google: "Tạo sheet" stays a manual admin action.

Option `retryFailed` (default on) adds the scan's retry pass over transient failures; a scheduled
run interrupted by a restart is resumed at startup (riviu/scan_journal.py) and still never pushes.

A slot is skipped (and logged) when a scan or a source operation is already running, and a failed
sync records the error and skips the scan. From the sync until the scan task exists the run holds
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
from datetime import datetime, timedelta, timezone

from riviu import scan_service, scan_state
from riviu import server_context as context
from riviu import source_files
from riviu.workbook_utils import PLATFORMS, google_sheet_source_for_file, write_json_atomic

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
SCRAPE_MODES = ("request", "hybrid", "browser")
WORKER_CHOICES = (1, 3, 5, 8, 10, 20, 25, 30, 50)

logger = logging.getLogger("riviu.scheduler")


def schedule_path(base_dir=None):
    return os.path.join(base_dir or context.EXCEL_DIR, "data", "scan_schedule.json")


def runs_path(base_dir=None):
    return os.path.join(base_dir or context.EXCEL_DIR, "data", "scan_schedule_runs.json")


# The page's defaults (PLATFORMS[*].defaultScrapeMode in app.js, 10 workers).
DEFAULT_SCRAPE_MODES = {"tiktok": "request", "threads": "hybrid"}
LOG_LEVELS = {"completed": "OK", "skipped": "WARN", "stopped": "WARN", "cancelled": "WARN", "failed": "ERROR"}


def default_options(platform):
    return {"workers": 10, "scrapeMode": DEFAULT_SCRAPE_MODES.get(platform, "request"), "useProxy": False, "useSession": False,
            "retryFailed": True}


def empty_schedule():
    return {"version": SCHEDULE_VERSION, "platforms": {key: {"slots": [], "options": default_options(key)} for key in PLATFORMS}}


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
        slots.append({"time": time_text, "enabled": enabled})
    return sorted(slots, key=lambda slot: slot["time"])


def normalize_schedule(payload):
    """`{"version": 1, "platforms": {platform: {"slots": [{"time": "HH:MM", "enabled": bool}],
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
        return empty_schedule()
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


def record_run(entry, base_dir=None):
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
    "Nạp sheet". The saved scan sheet is kept when the new workbook still has it."""
    from riviu.api.sources import save_source_preferences

    data_sheets = synced.get("dataSheets") or []
    saved = source.get("scanSheet", "")
    sheet = saved if saved in data_sheets else synced.get("currentSheet", "")
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


def _result_from_status(status, task):
    if task.cancelled() or status.get("cancelled") or status.get("phase") == "cancelled":
        result = "cancelled"
    elif status.get("phase") == "failed":
        result = "failed"
    elif status.get("stopReason"):
        result = "stopped"
    else:
        result = "completed"
    counts = {key: status.get(key) for key in ("total", "processed", "success", "error", "hidden") if key in status}
    return result, counts


class Scheduler:
    """One background loop per server process; `run_job` is the unit tests drive."""

    def __init__(self, clock=local_now, sleep=asyncio.sleep, sync=None):
        self.clock = clock
        self.sleep = sleep
        self._sync = sync
        self.runner = None
        self.previous_tick = None

    async def sync(self, url, platform):
        if self._sync is not None:
            return await self._sync(url, platform)
        from riviu.api.google import sync_google_sheet_source

        return await sync_google_sheet_source(url, platform, activate=False)

    def running(self):
        return bool(self.runner and not self.runner.done())

    async def tick(self):
        now = self.clock()
        due = due_slots(await asyncio.to_thread(load_schedule), self.previous_tick, now)
        self.previous_tick = now
        if not due:
            return None
        if self.running():
            for platform, slot, scheduled in due:
                await self.record(platform, slot, scheduled, "skipped", "Lượt tự động trước vẫn đang chạy.")
            return None
        self.runner = asyncio.create_task(self.run_jobs(due))
        return self.runner

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
        # Sequential: a second platform due at the same minute starts after the first scan ends.
        for platform, slot, scheduled in jobs:
            try:
                await self.run_job(platform, slot, scheduled)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Lịch tự động %s %s lỗi", platform, slot)
                await self.record(platform, slot, scheduled, "failed", f"Lỗi kỹ thuật: {error}")

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
        await scan_state.manager.broadcast_log(f"Lịch tự động {label} {slot}: {message}", level=level)
        return entry

    async def run_job(self, platform, slot, scheduled=None):
        started = self.clock()
        scheduled = scheduled or started
        if scan_state.source_mutation_blocked():
            return await self.record(platform, slot, scheduled, "skipped", "Bỏ qua: đang có phiên quét hoặc thao tác dữ liệu khác.", started)
        label = PLATFORMS[platform].label
        # Taken right after the check (no await in between) and held until the scan task exists:
        # a manual start or source change in the meantime is refused as busy.
        with scan_state.scan_starting():
            preferences, source, url = await asyncio.to_thread(platform_source, platform)
            if not url:
                return await self.record(platform, slot, scheduled, "skipped", "Bỏ qua: nền tảng này chưa có link Google Sheet nguồn.", started)
            options = (await asyncio.to_thread(load_schedule))["platforms"][platform]["options"]
            await scan_state.manager.broadcast_log(f"Lịch tự động {label} {slot}: đang nạp Google Sheet...")
            try:
                synced = await self.sync(url, platform)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return await self.record(platform, slot, scheduled, "failed", f"Nạp sheet thất bại, không quét: {error}", started)
            file_id = synced["file_id"]
            try:
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
                f"Lịch tự động {label} {slot}: bắt đầu quét sheet \"{scan_options['sheet_name']}\" • file: {file_id} • "
                f"{scan_options['scrape_mode']}{' • proxy bật' if scan_options['use_proxy'] else ''} • luồng: {scan_options['worker_count']}."
            )
            task, run_context = scan_service.start_scan_task(target_path, scan_options)
            await scan_state.manager.broadcast_status(scan_state.manager.last_status)
        # Wait without letting a scheduler shutdown cancel the scan itself.
        await asyncio.wait({task})
        manager = scan_state.manager
        status = manager.last_status if manager.context.get("runId") == run_context["runId"] else {}
        result, counts = _result_from_status(status, task)
        messages = {
            "completed": "Đã nạp sheet và quét xong.",
            "stopped": "Quét dừng sớm, kết quả chưa đầy đủ.",
            "cancelled": "Phiên quét đã bị hủy.",
            "failed": f"Quét thất bại: {status.get('message') or 'lỗi không rõ'}",
        }
        return await self.record(
            platform, slot, scheduled, result, messages[result], started,
            fileId=file_id, sheet=scan_options["sheet_name"], counts=counts,
        )


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
