"""Decide which optional data files the PyInstaller server bundle ships.

Used by desktop/build_sidecar.py and by the hooks in desktop/pyinstaller-hooks.
Keep this module free of PyInstaller imports so tests can load it directly.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable


# build_sidecar.py passes the browser folders to keep to the PyInstaller hooks.
BUNDLED_BROWSERS_ENV = "RIVIU_BUNDLED_BROWSERS"
LOCAL_BROWSERS_DIR = ".local-browsers"

# The app only drives Chromium headless (scraper.py, threads_scraper.py,
# threads_session.py), which Playwright runs with chromium-headless-shell. FFmpeg
# is only used to record videos (`record_video_dir`), which the app never does.
# winldd stays: Playwright runs it to re-check Chromium's DLLs when the bundled
# DEPENDENCIES_VALIDATED marker is older than 30 days.
UNUSED_BROWSER_PREFIXES = ("ffmpeg-",)

# googleapiclient.discovery.build() reads static discovery documents shipped with
# the library (one JSON file per API version, about 100 MB in total). Bundle only
# the APIs google_sheets_sync.py builds.
GOOGLE_DISCOVERY_DOCS = ("sheets.v4",)


def browser_dirs_from_install_dry_run(output: str) -> list[str]:
    """Browser folders that `playwright install --dry-run ...` lists, minus unused ones."""
    names = []
    for line in output.splitlines():
        label, _, location = line.strip().partition(":")
        if label != "Install location" or not location.strip():
            continue
        name = re.split(r"[\\/]", location.strip().rstrip("\\/"))[-1]
        if not name.startswith(UNUSED_BROWSER_PREFIXES):
            names.append(name)
    if not any(name.startswith("chromium_headless_shell-") for name in names):
        raise RuntimeError(f"Playwright did not report a headless shell install location:\n{output}")
    return names


def bundled_browsers_from_env(environ=os.environ) -> frozenset[str]:
    value = environ.get(BUNDLED_BROWSERS_ENV, "")
    names = frozenset(name for name in value.split(os.pathsep) if name)
    if not names:
        raise RuntimeError(f"{BUNDLED_BROWSERS_ENV} is not set; build the server with desktop/build_sidecar.py.")
    return names


def browser_dir_of(path: str) -> str | None:
    """The `.local-browsers/<name>` folder that `path` is inside, if any."""
    parts = re.split(r"[\\/]+", str(path))
    try:
        index = parts.index(LOCAL_BROWSERS_DIR)
    except ValueError:
        return None
    return parts[index + 1] if index + 1 < len(parts) else None


def keep_playwright_data(source: str, bundled_browsers: Iterable[str]) -> bool:
    """Drop browser files the app cannot launch, such as a full Chromium build left
    in the package-local browsers folder by an earlier `playwright install`."""
    name = browser_dir_of(source)
    return name is None or name in set(bundled_browsers)


def bundled_browser_dirs(paths: Iterable[str]) -> set[str]:
    """Browser folders present in a built bundle, from its file paths."""
    return {name for name in map(browser_dir_of, paths) if name}
