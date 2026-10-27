"""Process-wide scan lifecycle state: the WebSocket event hub and the single-operation gates.

Only one scan runs at a time (`SCRAPE_TASK`), and source mutations (file selection, uploads,
Google syncs, cookie changes) must not overlap a scan or a pending desktop update. Read and
write these names through the module object (`scan_state.SOURCE_BUSY`), never with
`from riviu.scan_state import SOURCE_BUSY`, so tests and `riviu.app` see one value.
"""

import secrets
from collections import deque
from contextlib import contextmanager

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.context = {}
        self.running = False
        self.last_status = {}
        self.results = deque(maxlen=500)

    def begin_run(self, platform, file_id, sheet_name):
        self.context = {"runId": secrets.token_hex(12), "platform": platform, "fileId": file_id, "sheetName": sheet_name}
        self.running = True
        self.last_status = {"total": 0, "processed": 0, "success": 0, "error": 0, "hidden": 0, "done": False, "phase": "running"}
        self.results.clear()
        return dict(self.context)

    def snapshot(self):
        return {**self.context, "running": self.running, "status": {**self.last_status, **self.context}, "results": list(self.results)}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        await websocket.send_json({"type": "session", "data": self.snapshot(), **self.context})

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def _send_all(self, payload):
        # Copy first: a disconnect during an await must not make the loop skip another tab.
        for connection in list(self.active_connections):
            try:
                await connection.send_json(payload)
            except Exception:
                self.disconnect(connection)

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


manager = ConnectionManager()
SCRAPE_TASK = None
SCAN_STARTING = False
SOURCE_BUSY = False
DESKTOP_UPDATE_PENDING = False


def scan_running():
    return SCAN_STARTING or manager.running or bool(SCRAPE_TASK and not SCRAPE_TASK.done())


def source_mutation_blocked():
    return scan_running() or SOURCE_BUSY or DESKTOP_UPDATE_PENDING


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
def scan_starting():
    """Count a start that is still loading its Threads session (or a scheduled run that is still
    syncing its sheet) as a running scan. Re-entrant: an inner use keeps an outer hold."""
    global SCAN_STARTING
    previous = SCAN_STARTING
    SCAN_STARTING = True
    try:
        yield
    finally:
        SCAN_STARTING = previous
