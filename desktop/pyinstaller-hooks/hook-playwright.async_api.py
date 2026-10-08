"""Bundle Playwright while preserving the nested Chromium app as data on macOS."""

import sys
from pathlib import Path

from PyInstaller.depend import bindepend
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bundle_contents import bundled_browsers_from_env, keep_playwright_data  # noqa: E402


# Playwright's browser app contains nested executables that are already a
# complete bundle. Treat those files as archive data so PyInstaller does not
# attempt to re-sign or rewrite the bundle during one-file assembly.
_classify_binary_vs_data = bindepend.classify_binary_vs_data


def _classify_playwright_browser(path):
    normalized = str(path).replace("\\", "/")
    if "/playwright/driver/package/.local-browsers/" in normalized:
        return "DATA"
    return _classify_binary_vs_data(path)


bindepend.classify_binary_vs_data = _classify_playwright_browser


# Ship only the browser folders build_sidecar.py asked for (the headless shell),
# even when the package-local browsers folder also holds a full Chromium.
# Browser DLLs are also found by collect_dynamic_libs, so filter both lists.
_bundled_browsers = bundled_browsers_from_env()
hiddenimports = collect_submodules("playwright")
datas = [
    (source, target)
    for source, target in collect_data_files("playwright")
    if keep_playwright_data(source, _bundled_browsers)
]
binaries = [
    (source, target)
    for source, target in collect_dynamic_libs("playwright")
    if keep_playwright_data(source, _bundled_browsers)
]
