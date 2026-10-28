"""Process-wide scan lifecycle state: the WebSocket event hub and the scan/operation gates.

Workbook storage runs one scan at a time (`SCRAPE_TASK`, `manager`). On Riviu Data storage
(`store_config.ACTIVE.postgres`) every platform has its own lane (`LANES`), so a TikTok and a
Threads scan run side by side; each lane has its own run channel, task, start hold and retry
skip. `lane(platform)` hides the difference: in workbook mode it returns the single lane, whose
attributes are these module names. Source mutations (file selection, uploads, Google syncs,
cookie changes) must not overlap any scan. Read and write these names through the module object
(`scan_state.SOURCE_BUSY`), never with `from riviu.scan_state import SOURCE_BUSY`, so tests and
`riviu.app` see one value.
"""

import secrets
import sys
from collections import deque
from contextlib import contextmanager

from fastapi import WebSocket

from riviu.store import config as store_config


class Hub:
    """The open sockets; every run channel sends through the same hub."""

    def __init__(self):
        self.connections: list[WebSocket] = []

    def disconnect(self, websocket: WebSocket):
        if websocket in self.connections:
            self.connections.remove(websocket)

    async def send_all(self, payload):
        # Copy first: a disconnect during an await must not make the loop skip another tab.
        for connection in list(self.connections):
            try:
                await connection.send_json(payload)
            except Exception:
                self.disconnect(connection)


class ConnectionManager:
    """One run channel: the run context every event carries, its last status and results."""

    def __init__(self, hub=None):
        self.hub = hub if hub is not None else Hub()
        self.context = {}
        self.running = False
        self.last_status = {}
        self.results = deque(maxlen=500)

    @property
    def active_connections(self):
        return self.hub.connections

    def begin_run(self, platform, file_id, sheet_name):
        self.context = {"runId": secrets.token_hex(12), "platform": platform, "fileId": file_id, "sheetName": sheet_name}
        self.running = True
        self.last_status = {"total": 0, "processed": 0, "success": 0, "error": 0, "hidden": 0, "done": False, "phase": "running"}
        self.results.clear()
        return dict(self.context)

    def snapshot(self):
        return {**self.context, "running": self.running, "status": {**self.last_status, **self.context}, "results": list(self.results)}

    def session_message(self):
        return {"type": "session", "data": self.snapshot(), **self.context}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.hub.connections.append(websocket)
        # One session message per lane, so a page that (re)connects sees every running scan.
        for channel in session_channels(self):
            await websocket.send_json(channel.session_message())

    def disconnect(self, websocket: WebSocket):
        self.hub.disconnect(websocket)

    async def _send_all(self, payload):
        await self.hub.send_all(payload)

    async def broadcast_log(self, message: str, *, level: str = "", details=None):
        payload = {"type": "log", "message": message, **self.context}
        if level:
            payload["level"] = level
        if details:
            payload["details"] = details
        await self._send_all(payload)

    async def broadcast_status(self, status: dict):
        status = {**status, **self.context}
        self.last_status = status
        if status.get("done"):
            self.running = False
        await self._send_all({"type": "status", "data": status, **self.context})

    async def broadcast_data(self, row: dict):
        row = {**row, **self.context}
        self.results.append(row)
        await self._send_all({"type": "data", "row": row, **self.context})

    async def broadcast_duplicates(self, data: dict):
        await self._send_all({"type": "duplicates", "data": data, **self.context})


HUB = Hub()
# The single scan's channel in workbook mode; on Riviu Data storage, the channel for messages
# that belong to no run (scheduler and resume notices).
manager = ConnectionManager(HUB)
SCRAPE_TASK = None
SCAN_STARTING = False
SOURCE_BUSY = False
# Run ids the user cancelled: their journal is dropped, so they are never auto-resumed.
USER_CANCELLED_RUNS = set()
# Set while a scan waits out the cooldown before its retry pass; setting it skips that pass.
RETRY_SKIP = None


class Lane:
    """One platform's scans on Riviu Data storage."""

    def __init__(self, platform):
        self.platform = platform
        self.manager = ConnectionManager(HUB)
        self.task = None
        self.starting = False
        self.retry_skip = None

    def running(self):
        return self.starting or self.manager.running or bool(self.task and not self.task.done())


class _SingleLane:
    """Workbook mode: the one lane, backed by the module names (so tests can set them)."""

    platform = None
    _module = sys.modules[__name__]

    @property
    def manager(self):
        return self._module.manager

    @property
    def task(self):
        return self._module.SCRAPE_TASK

    @task.setter
    def task(self, value):
        self._module.SCRAPE_TASK = value

    @property
    def starting(self):
        return self._module.SCAN_STARTING

    @starting.setter
    def starting(self, value):
        self._module.SCAN_STARTING = value

    @property
    def retry_skip(self):
        return self._module.RETRY_SKIP

    @retry_skip.setter
    def retry_skip(self, value):
        self._module.RETRY_SKIP = value

    def running(self):
        return self.starting or self.manager.running or bool(self.task and not self.task.done())


SINGLE_LANE = _SingleLane()
LANES = {}


def lanes_enabled():
    return store_config.ACTIVE.postgres


def lane(platform=None):
    """The lane that runs `platform`'s scans (the single lane on workbook storage)."""
    if not lanes_enabled() or platform is None:
        return SINGLE_LANE
    if platform not in LANES:
        LANES[platform] = Lane(platform)
    return LANES[platform]


def session_channels(fallback):
    """The channels a newly connected page gets a session message for."""
    if lanes_enabled() and LANES:
        return [item.manager for item in LANES.values()]
    return [fallback]


def scan_running(platform=None):
    """A scan of `platform` (any scan when None) is running or starting."""
    if not lanes_enabled():
        return SINGLE_LANE.running()
    if SCAN_STARTING:
        return True
    if platform is not None:
        return lane(platform).running()
    return any(item.running() for item in LANES.values())


def source_mutation_blocked():
    return scan_running() or SOURCE_BUSY


def start_blocked(platform):
    """A new scan of `platform` must wait: that lane is busy (any scan, on workbook storage) or a
    source mutation is running."""
    return scan_running(platform) or SOURCE_BUSY


@contextmanager
def source_operation():
    """Hold the source gate for one mutation; callers check `source_mutation_blocked()` first."""
    global SOURCE_BUSY
    SOURCE_BUSY = True
    try:
        yield
    finally:
        SOURCE_BUSY = False


@contextmanager
def scan_starting(platform=None):
    """Count a start that is still loading its Threads session (or a scheduled run that is still
    syncing its sheet) as a running scan of that platform (of every platform when None, and always
    on workbook storage). Re-entrant: an inner use keeps an outer hold."""
    target = lane(platform) if platform is not None else SINGLE_LANE
    previous = target.starting
    target.starting = True
    try:
        yield
    finally:
        target.starting = previous
