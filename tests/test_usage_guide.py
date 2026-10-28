"""The in-app usage guide: a header button in every mode, and every screenshot it shows ships.

Opening the modal is covered by the Node harness (test_ui_review_fixes.py).
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from riviu import app as backend


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "riviu" / "web" / "static"


def guide_page(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "EXCEL_DIR", str(tmp_path))
    with TestClient(backend.app, base_url="http://127.0.0.1:1231") as client:
        assert client.get("/").status_code == 200  # sets the local session cookie
        page = client.get("/classic")
        assert page.status_code == 200
        return page.text


def test_header_has_guide_button_and_modal_in_source_mode(tmp_path, monkeypatch):
    html = guide_page(tmp_path, monkeypatch)
    button = re.search(r'<button[^>]*id="guideButton"[^>]*>', html)
    assert button and 'onclick="openGuideModal()"' in button.group(0)
    assert 'aria-label="Hướng dẫn sử dụng"' in button.group(0)
    assert 'id="guideModal"' in html and "Lưu ý" in html


def test_every_guide_screenshot_exists_as_png(tmp_path, monkeypatch):
    html = guide_page(tmp_path, monkeypatch)
    modal = html[html.index('id="guideModal"'):]
    sources = re.findall(r'<img[^>]+src="(/static/guide/[^"]+)"', modal)
    assert len(sources) >= 12
    for source in sources:
        path = STATIC_DIR / source.removeprefix("/static/")
        assert path.is_file(), f"missing guide screenshot {source}"
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", source
