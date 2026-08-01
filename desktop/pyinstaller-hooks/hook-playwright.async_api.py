"""Bundle Playwright while preserving the nested Chromium app as data on macOS."""

from PyInstaller.depend import bindepend
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


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


hiddenimports = collect_submodules("playwright")
datas = collect_data_files("playwright")
binaries = collect_dynamic_libs("playwright")
