"""Filesystem locations shared by the server and the report builder.

Resources (templates, static files, logo) ship inside the package at
``riviu/web``. In source mode that is ``<repo>/riviu/web``; in the frozen
PyInstaller build it is ``<sys._MEIPASS>/riviu/web`` (build_sidecar.py adds it
there with the same relative path).

User data (``data/`` workbooks, OAuth client and token, proxy list, history,
cookie store) lives in ``RIVIU_DATA_DIR`` when set (the desktop app passes the
OS app-data dir). Otherwise it is the repository root in source mode, where
existing installs already keep their data, and the bundle root when frozen.
"""

import os
import sys

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PACKAGE_DIR)


def resource_root() -> str:
    """Bundle root when frozen, otherwise the repository root."""
    return os.path.abspath(getattr(sys, "_MEIPASS", REPO_ROOT))


RESOURCE_ROOT = resource_root()
WEB_DIR = os.path.join(RESOURCE_ROOT, "riviu", "web")
TEMPLATES_DIR = os.path.join(WEB_DIR, "templates")
STATIC_DIR = os.path.join(WEB_DIR, "static")
LOGO_PATH = os.path.join(WEB_DIR, "logo.png")
PLATFORM_ICONS_DIR = os.path.join(STATIC_DIR, "platform-icons")


def data_dir() -> str:
    """RIVIU_DATA_DIR when set, otherwise the repository (or bundle) root."""
    return os.path.abspath(os.environ.get("RIVIU_DATA_DIR", RESOURCE_ROOT))
