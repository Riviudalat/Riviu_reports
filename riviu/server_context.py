"""Process-wide server context shared by the routers and the scan service.

Holds the resource/data directories, the legacy "current" selection used by callers that
omit `file_id`, the Google I/O locks and the Threads cookie-session singleton. Read and
write these names through the module object (`server_context.EXCEL_DIR`) so that a test
monkeypatching `riviu.app.EXCEL_DIR` (forwarded here) changes what every router sees.
"""

import asyncio
import os
import threading

from riviu import paths, server_mode
from riviu.platforms.threads_session import ThreadsSession


# Web resources (templates, static, logo) and user data; see riviu/paths.py.
APP_RESOURCE_DIR = paths.WEB_DIR
EXCEL_DIR = paths.data_dir()
LOGO_PATH = paths.LOGO_PATH

# Legacy selection: only requests without an explicit file_id read or change it.
CURRENT_SELECTED_FILE = ""
CURRENT_SELECTED_SHEET = ""
CURRENT_SCAN_SHEET = ""

THREADS_SESSION = None
THREADS_SESSION_ROOT = ""


def threads_session_manager():
    global THREADS_SESSION, THREADS_SESSION_ROOT
    root = os.path.realpath(EXCEL_DIR)
    if THREADS_SESSION is None or THREADS_SESSION_ROOT != root:
        # Server mode swaps the OS-native store for the key-file encrypted one.
        THREADS_SESSION = ThreadsSession(root, store=server_mode.threads_cookie_store(root))
        THREADS_SESSION_ROOT = root
    return THREADS_SESSION


GOOGLE_IO_LOCK = threading.RLock()
# The interactive OAuth flow waits on the user's browser, so it must never hold GOOGLE_IO_LOCK.
GOOGLE_LOGIN_LOCK = threading.Lock()


def google_io(function, *args, **kwargs):
    with GOOGLE_IO_LOCK:
        return function(*args, **kwargs)


async def complete_blocking(function, *args, **kwargs):
    """A cancelled coroutine must not release a write gate while its thread runs."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
