import pytest

from riviu.platforms import threads


@pytest.fixture(autouse=True)
def _no_threads_http_pacing(monkeypatch):
    """Threads HTTP pacing waits about a second per request; tests opt into it explicitly."""
    monkeypatch.setattr(threads, "THREADS_HTTP_MIN_INTERVAL", 0)
    monkeypatch.setattr(threads, "THREADS_HTTP_JITTER", 0)
