import pytest

from riviu.platforms import threads


@pytest.fixture(autouse=True)
def _no_threads_http_pacing(monkeypatch):
    """Threads HTTP pacing waits about a second per request; tests opt into it explicitly."""
    monkeypatch.setattr(threads, "THREADS_HTTP_MIN_INTERVAL", 0)
    monkeypatch.setattr(threads, "THREADS_HTTP_JITTER", 0)


@pytest.fixture(autouse=True)
def _classic_page_without_web_app_build(monkeypatch, tmp_path_factory):
    """`/` and `/admin` serve the React build when riviu/web/app exists (after `npm run build`);
    tests get the classic page unless they point WEB_APP_INDEX at a build themselves."""
    from riviu.api import pages

    monkeypatch.setattr(pages, "WEB_APP_INDEX", str(tmp_path_factory.mktemp("no-web-app") / "index.html"))


@pytest.fixture(autouse=True)
def _fresh_scan_lanes(monkeypatch):
    """Riviu Data storage keeps one scan lane per platform for the process; every test starts with none."""
    from riviu import scan_state

    monkeypatch.setattr(scan_state, "LANES", {})
