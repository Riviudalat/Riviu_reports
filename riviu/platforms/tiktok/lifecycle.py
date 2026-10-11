"""Owned async work and the Chromium/Playwright lifecycle: lazy launch, bounded close and driver stop.

Threads reuses finish_pending_task, close_browser_bounded and playwright_session.
"""
import asyncio
import contextlib
import sys


# A finished scan waits at most this long for Chromium to exit gracefully, then
# force-kills it (close_browser_bounded). It then waits at most
# PLAYWRIGHT_STOP_TIMEOUT for the driver; slower reaping continues in the
# background (see playwright_session).
BROWSER_CLOSE_TIMEOUT = 0.5
PLAYWRIGHT_STOP_TIMEOUT = 0.5
_BROWSER_REAPERS = set()


async def finish_pending_task(task):
    """Wait for owned cleanup/write work before propagating cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError()
    return result


async def _launch_browser(playwright):
    return await playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
        ],
    )


class _SharedBrowser:
    """The run's one Chromium, launched when the first browser worker needs it.

    A hybrid run whose Request results all stand never starts Chromium (~650 MB).
    """

    def __init__(self, playwright, websocket_manager=None, launch_notice=""):
        self._playwright = playwright
        self._manager = websocket_manager
        self._launch_notice = launch_notice
        self._launch = None

    @property
    def launched(self):
        return self._launch is not None

    async def get(self):
        if self._launch is None:
            self._launch = asyncio.ensure_future(_launch_browser(self._playwright))
            if self._manager and self._launch_notice:
                await self._manager.broadcast_log(self._launch_notice)
        # Shielded: one worker's cancellation must not abort the launch the others share.
        return await asyncio.shield(self._launch)

    async def close(self):
        launch = self._launch
        if launch is None:
            return
        if not launch.done():
            # Stopping the Playwright driver afterwards kills a half-started Chromium.
            launch.cancel()
            await asyncio.gather(launch, return_exceptions=True)
        if launch.cancelled() or launch.exception() is not None:
            return
        await close_browser_bounded(launch.result())


def browser_cleanup_pending():
    """True while a Chromium from a finished scan is still being force-killed."""
    return any(not task.done() for task in _BROWSER_REAPERS)


def _track_reaper(task):
    _BROWSER_REAPERS.add(task)

    def finished(done):
        _BROWSER_REAPERS.discard(done)
        if not done.cancelled():
            # Retrieved here: a teardown error after the scan is not a scan failure.
            done.exception()

    task.add_done_callback(finished)
    return task


def _kill_browser_processes(browser):
    """Force-kill every Chromium process under this browser's Playwright driver (Windows).

    Only for the end of a scan, right before the driver stops: stopping it kills
    the same processes, but only after spawning taskkill, which took 10+ s on a
    busy host. Returns True when TerminateProcess was issued.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        driver = browser._impl_obj._connection._transport._proc.pid  # Playwright 1.59 internals
        if not isinstance(driver, int):
            return False

        class Entry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return False
        children = {}
        try:
            entry = Entry(dwSize=ctypes.sizeof(Entry))
            found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while found:
                children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
                found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        pending, tree = [driver], []
        while pending:
            for pid in children.get(pending.pop(), []):
                if pid not in tree and pid != driver:
                    tree.append(pid)
                    pending.append(pid)
        for pid in tree:
            handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if handle:
                kernel32.TerminateProcess(handle, 1)
                kernel32.CloseHandle(handle)
        return bool(tree)
    except Exception:
        # The driver stop still kills the browser; this is only a head start.
        return False


async def close_browser_bounded(browser):
    """Ask Chromium to exit, but never let a slow exit hold a finished scan open.

    Contexts close in parallel (routes dropped first), then the browser gets
    BROWSER_CLOSE_TIMEOUT to exit before its processes are force-killed. A killed
    Chromium can still take seconds to leave the process list on a loaded
    Windows host; the close keeps reaping in the background meanwhile.
    """
    if browser is None:
        return

    async def close():
        contexts = list(getattr(browser, "contexts", []))
        # A pending route handler must not hold a context open.
        await asyncio.gather(*(ctx.unroute_all(behavior="ignoreErrors") for ctx in contexts
                               if hasattr(ctx, "unroute_all")), return_exceptions=True)
        await asyncio.gather(*(ctx.close() for ctx in contexts), return_exceptions=True)
        await browser.close()

    closing = _track_reaper(asyncio.ensure_future(close()))
    done, _pending = await asyncio.wait({closing}, timeout=BROWSER_CLOSE_TIMEOUT)
    if not done:
        _kill_browser_processes(browser)


@contextlib.asynccontextmanager
async def playwright_session(factory):
    """``async with factory()`` whose driver stop is bounded by PLAYWRIGHT_STOP_TIMEOUT.

    Stopping closes the driver's stdin at once; the driver then force-kills its
    browsers (taskkill /T /F on Windows) and exits on its own, even if this
    process does not wait. A stop still running after the bound keeps reaping in
    the background and is reported by browser_cleanup_pending().
    """
    manager = factory()
    playwright = await manager.__aenter__()
    try:
        yield playwright
    finally:
        stop = _track_reaper(asyncio.ensure_future(manager.__aexit__(*sys.exc_info())))
        done, _pending = await asyncio.wait({stop}, timeout=PLAYWRIGHT_STOP_TIMEOUT)
        if done:
            stop.result()
