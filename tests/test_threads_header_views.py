"""Exact permalink column headers, not captions, provide authenticated views."""
import asyncio
import json

import pytest
from playwright.async_api import async_playwright

from riviu.platforms import threads
from test_threads_transport import URL, html_fixture


def header(count="314 views", url="/@demo/post/Fixture"):
    return f'<div><div><a aria-label="Column title" href="{url}"><h1><span>Thread</span></h1></a></div><div><span><span>{count}</span></span></div></div>'


@pytest.mark.parametrize("count,expected", [("314 views", 314), ("0 views", 0), ("2.7K views", 2700), ("1,234 views", 1234), ("1 view", 1)])
def test_verified_header_views(count, expected):
    assert threads._permalink_header_views(header(count), URL) == expected
    result = threads.parse_threads_http(header(count), URL, URL)
    assert result["metrics"]["views"] == expected


@pytest.mark.parametrize("html", [
    '<p>999 views</p>', header(url="/@other/post/Fixture"), header(url="/@demo/post/Other"),
    header().replace('aria-label="Column title"', ''), header().replace("h1", "h2"),
    '<article>' + header() + '</article>',
    '<div><a aria-label="Column title" href="/@demo/post/Fixture"><h1>Thread</h1></a><p>999 views</p></div>',
    '<div><a aria-label="Column title" href="/@demo/post/Fixture"><h1>Thread</h1></a><button>999 views</button></div>',
    '<div><a aria-label="Column title" href="/@demo/post/Fixture"><h1>Thread</h1></a><span>99 views</span><a href="/@other/post/Other">recommended</a></div>',
    '<script>' + header() + '</script>', header("no count"), header("314 views caption"),
])
def test_header_decoys_do_not_supply_views(html):
    assert threads._permalink_header_views(html, URL) is None


def test_multiple_headers_conflict_or_deduplicate():
    assert threads._permalink_header_views(header() + header("999 views"), URL) is None
    assert threads._permalink_header_views(header() + header(), URL) == 314
    assert threads._permalink_header_views(header() + header("999 views", "/@other/post/Other"), URL) == 314


def test_http_verified_json_views_stay_authoritative():
    content = header() + html_fixture()
    assert threads.parse_threads_http(content, URL, URL)["metrics"]["views"] == 15


def test_profile_redirect_cannot_supply_header_metrics():
    result = threads.parse_threads_http(header(), URL, "https://www.threads.com/@demo")
    assert result["error_kind"] == "profile_redirect"
    assert all(value is None for value in result["metrics"].values())


def test_header_not_dependent_on_action_card():
    result = threads.parse_threads_http(header("0 views"), URL, URL)
    assert result["metrics"]["views"] == 0
    assert result["metrics"]["likes"] is None
    assert threads.result_status(result) == "Success"


@pytest.mark.parametrize("delayed", [False, True])
def test_browser_header_views_outside_card_and_delayed_hydration(delayed):
    # No view JSON/control in the target card. A caption number is a decoy.
    # Construct a target payload without the logged-out counter explicitly.
    payload = {"require": [["RelayPrefetchedStreamCache", "next", [], [
        "adp_BarcelonaPostPageTargetQueryRelayPreloader_fixture", {"__bbox": {"result": {"data": {"media": {
            "code": "Fixture", "user": {"username": "demo"}, "like_count": 0, "like_and_view_counts_disabled": False,
            "text_post_app_info": {"direct_reply_count": 0, "repost_count": 0, "reshare_count": None},
        }}}}},
    ]]]}
    html = '<html><body><article><a href="/@demo/post/Fixture">demo</a><p>99999 views</p><button><svg title="Like"></svg></button><button><svg title="Comment"></svg></button></article><script type="application/json">' + json.dumps(payload) + '</script>'
    if delayed:
        html += '<script>setTimeout(() => document.body.insertAdjacentHTML("afterbegin", ' + json.dumps(header()) + '), 1500);</script>'
    else:
        html += header()
    html += '</body></html>'
    async def check():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            original = browser.new_page
            async def page_factory():
                page = await original()
                await page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))
                return page
            browser.new_page = page_factory
            try: return await threads.fetch_threads_browser(browser, URL)
            finally: await browser.close()
    result = asyncio.run(check())
    assert result["metrics"] == {"views": 314, "likes": 0, "comments": 0, "reposts": 0, "shares": None}
