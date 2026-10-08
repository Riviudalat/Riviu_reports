"""Evidence-backed Relay aliases, media-linked exact views and auth regressions."""
import asyncio
import json

import pytest
from riviu.platforms import threads
from riviu.platforms import threads_session as sessions
from test_threads_transport import URL, metrics, NoBrowser, workbook
from test_threads_header_views import header
from test_threads_cookie_integration import COOKIE, auth_html

MEDIA_ID = "12345_678"


def target_module(module="RelayPrefetchedStreamCache", **changes):
    media = {"id": MEDIA_ID, "code": "Fixture", "user": {"username": "demo"}, "like_count": 108,
             "like_and_view_counts_disabled": False,
             "text_post_app_info": {"direct_reply_count": 8, "repost_count": 21, "reshare_count": 109}}
    media.update(changes)
    return [module, "next", [], ["adp_BarcelonaPostPageTargetQueryRelayPreloader_fixture", {"__bbox": {"result": {"data": {"media": media}}}}]]


def view_module(count=15174, media_id=MEDIA_ID, module="RelayPrefetchedStreamCache"):
    media = {"id": media_id, "text_post_app_info": {"impression_count": count}}
    return [module, "next", [], ["adp_BarcelonaPostViewCountQueryRelayPreloader_fixture", {"__bbox": {"result": {"data": {"media": media}}}}]]


def scripts(*modules):
    return '<script type="application/json">' + json.dumps({"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"require": list(modules)}}]]]}) + '</script>'


@pytest.mark.parametrize("module", ["RelayPrefetchedStreamCache", "RelayPrefetchedStreamCache@d8bab6749b2b17896201f70690864017"])
def test_relay_version_alias_extracts_target_and_exact_views(module):
    content = scripts(view_module(module=module), target_module(module)) + header("15.1K views")
    result = threads.parse_threads_http(content, URL, URL)
    assert result["metrics"] == {"views": 15174, "likes": 108, "comments": 8, "reposts": 21, "shares": 109}
    assert result["metricSources"]["views"] == "view_query_exact"


@pytest.mark.parametrize("module", ["RelayPrefetchedStreamCache@bad", "RelayPrefetchedStreamCache@" + "a" * 31,
                                    "RelayPrefetchedStreamCache@" + "a" * 33, "RelayPrefetchedStreamCacheOther"])
def test_unverified_relay_alias_rejected(module):
    result = threads.parse_threads_http(scripts(target_module(module), view_module(module=module)), URL, URL)
    assert all(value is None for value in result["metrics"].values())


@pytest.mark.parametrize("count,expected", [(0, 0), (15174, 15174), (None, None), (-1, None), (True, None), ("15174", None), (15.2, None)])
def test_exact_view_validation_never_substitutes_header_for_unknown(count, expected):
    result = threads.parse_threads_http(scripts(target_module(), view_module(count)) + header("15.1K views"), URL, URL)
    assert result["metrics"]["views"] == expected
    assert result["metricSources"]["views"] == ("view_query_exact" if expected is not None else "view_query_unknown")


@pytest.mark.parametrize("change", [{"code": "Other"}, {"user": {"username": "other"}}, {"id": None}])
def test_exact_views_require_target_identity_and_media_id(change):
    result = threads.parse_threads_http(scripts(target_module(**change), view_module()), URL, URL)
    assert result["metrics"]["views"] is None


def test_other_media_view_count_cannot_replace_target():
    result = threads.parse_threads_http(scripts(target_module(), view_module(999999, "other-id")), URL, URL)
    assert result["metrics"]["views"] is None


def test_conflicting_exact_views_remain_unknown():
    result = threads.parse_threads_http(scripts(target_module(), view_module(15), view_module(16)) + header(), URL, URL)
    assert result["metrics"]["views"] is None
    assert result["metricSources"]["views"] == "view_query_unknown"


@pytest.mark.parametrize("fallback", [threads.failure("missing browser", "browser_start"), threads.failure("timeout", "transport"), metrics(views=0)])
def test_http_auth_unknown_not_resolved_without_explicit_verification(fallback):
    result = threads.merge_results(threads.failure("not verified", "auth_unknown"), fallback)
    assert result["error_kind"] == "auth_unknown"
    assert all(value is None for value in result["metrics"].values())


def test_explicitly_authenticated_browser_can_resolve_unknown_http():
    browser = {**metrics(views=0, likes=0), "auth_verified": True}
    result = threads.merge_results(threads.failure("not verified", "auth_unknown"), browser)
    assert result["error"] == "" and result["auth_verified"] is True
    assert result["metrics"]["views"] == 0


@pytest.mark.parametrize("verified", [False, True])
def test_browser_exception_cannot_publish_unknown_auth_metrics(monkeypatch, verified):
    class Response:
        status = 200
        async def text(self): return (auth_html() if verified else "") + scripts(target_module(), view_module())
    class Page:
        url = URL
        async def goto(self, *_args, **_kwargs): return Response()
        async def content(self): raise RuntimeError("synthetic-cookie-secret")
    class Context:
        async def add_cookies(self, _cookies): pass
        async def new_page(self): return Page()
        async def close(self): pass
    class Browser:
        async def new_context(self, **_kwargs): return Context()
    async def guard(_context): return {"final_url": URL}
    async def navigate(page, url, _guard, **_kwargs): return await page.goto(url)
    monkeypatch.setattr(sessions, "install_session_navigation_guard", guard)
    monkeypatch.setattr(sessions, "navigate_session_page", navigate)
    result = asyncio.run(threads.fetch_threads_browser(Browser(), URL, session_cookies=[COOKIE]))
    if verified:
        assert result["metrics"]["views"] == 15174 and result["auth_verified"] is True
    else:
        assert result["error_kind"] == "auth_unknown"
        assert all(value is None for value in result["metrics"].values())
    assert COOKIE["value"] not in json.dumps(result)


@pytest.mark.parametrize("viewer", [{}, {"id": True}, {"id": "456"}, "123", {"id": "123", "pk": "456"}])
def test_duplicate_root_viewer_conflicts_or_malformed_are_unknown(viewer):
    valid = auth_html()
    other = '<script type="application/json">' + json.dumps({"define": [["BarcelonaSharedData", [], {"viewer": viewer}, 1]]}) + '</script>'
    assert sessions.auth_evidence(valid + other, URL) == "unknown"


def test_exact_views_replace_display_only_and_null_is_not_backfilled():
    display = {**metrics(views=15100), "metricSources": {"views": "header_display"}}
    exact = {**metrics(views=15174), "metricSources": {"views": "view_query_exact"}}
    merged = threads.merge_results(display, exact)
    assert merged["metrics"]["views"] == 15174
    assert merged["metricSources"]["views"] == "view_query_exact"
    unknown = {**metrics(), "metricSources": {"views": "view_query_unknown"}}
    assert threads.merge_results(unknown, display)["metrics"]["views"] is None
    assert threads.merge_results(unknown, exact)["metrics"]["views"] == 15174


def test_initial_auth_unknown_and_browser_launch_failure_stops_run(tmp_path, monkeypatch):
    path = workbook(tmp_path)
    async def preflight(*_args): return {"state": "valid"}
    class Chromium:
        async def launch(self, **_kwargs): raise RuntimeError("browser unavailable")
    class Playwright(NoBrowser): chromium = Chromium()
    monkeypatch.setattr(sessions, "verify_cookies", preflight)
    monkeypatch.setattr(threads, "async_playwright", Playwright)
    monkeypatch.setattr(threads, "fetch_threads_http", lambda *_args, **_kwargs: threads.failure("not verified", "auth_unknown"))
    with pytest.raises(sessions.SessionError):
        asyncio.run(threads.run_threads_scraper(path, mode="hybrid", session_cookies=[COOKIE]))
