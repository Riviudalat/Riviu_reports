"""Filesystem locations shared by the server and the report builder.

Resources (templates, static files, logo) ship inside the package at
``<repo>/riviu/web``.

User data (``data/`` workbooks, OAuth client and token, proxy list, history,
cookie store) lives in ``RIVIU_DATA_DIR`` when set (the server-mode image sets
it to its ``/data`` volume). Otherwise it is the repository root, where existing
source installs already keep their data.
"""

import os

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PACKAGE_DIR)
WEB_DIR = os.path.join(PACKAGE_DIR, "web")
TEMPLATES_DIR = os.path.join(WEB_DIR, "templates")
STATIC_DIR = os.path.join(WEB_DIR, "static")
LOGO_PATH = os.path.join(WEB_DIR, "logo.png")
PLATFORM_ICONS_DIR = os.path.join(STATIC_DIR, "platform-icons")
# Square, trimmed 3x renditions for partner Excel reports (tools/make_report_icons.py).
REPORT_ICONS_DIR = os.path.join(WEB_DIR, "report-icons")


def data_dir() -> str:
    """RIVIU_DATA_DIR when set, otherwise the repository root."""
    return os.path.abspath(os.environ.get("RIVIU_DATA_DIR", REPO_ROOT))
