"""Browser (Chromium) mode: guarded navigation, page polling and the DOM channel-name fallbacks."""
import asyncio
import random
import time
from urllib.parse import urljoin

from riviu.proxy_utils import playwright_proxy_settings
from riviu.workbook_utils import (
    clean_text,
    is_generated_username_channel,
    is_generic_tiktok_channel_name,
    is_scrapable_tiktok_url,
    normalize_tiktok_url,
)
from riviu.platforms.tiktok import cookies, gone
from riviu.platforms.tiktok.constants import (
    BROWSER_GEOLOCATION,
    BROWSER_LOCALE,
    BROWSER_TIMEZONE,
    DEFAULT_USER_AGENT,
)
from riviu.platforms.tiktok.parsing import (
    counts_match,
    empty_metrics,
    extract_media_id,
    extract_profile_username,
    handle_fallback,
    is_tiktok_error_page,
    is_tiktok_item_not_found,
    looks_like_error_text,
    media_redirect_mismatch,
    no_metrics_status,
    normalize_text,
    parse_channel_name_from_page,
    parse_counts,
    parse_profile_channel_name,
    profile_text_candidates,
    valid_channel_candidate,
    validate_metrics,
)
from riviu.platforms.tiktok.statuses import (
    STATUS_MEDIA_REDIRECT_MISMATCH,
    STATUS_METRICS_UNREADABLE,
    STATUS_TIKTOK_NOT_FOUND_UNCONFIRMED,
    STATUS_TIKTOK_UNAVAILABLE,
    is_suspected_gone_status,
    should_clear_stale_metrics,
)


PAGE_TIMEOUT_MS = 45000
PROFILE_PAGE_TIMEOUT_MS = 18000
# One redirect hop or route refetch; the page timeout bounds the whole chain.
HOP_TIMEOUT_MS = 15000
MAX_REDIRECT_HOPS = 10
STATE_SCRIPT_WAIT_MS = 12000
# Pages render their embedded state late: poll until two reads agree.
METRIC_POLL_ROUNDS = 24
PROFILE_POLL_ROUNDS = 20
POLL_INTERVAL_MS = 500
BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "texttrack"}


async def read_channel_name_from_dom(page, profile_username):
    if not profile_username:
        return ""

    selectors = [
        f'a[href*="@{profile_username}"]',
        f'a[href="/@{profile_username}"]',
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            if count == 0:
                continue
            for index in range(min(count, 8)):
                try:
                    text = clean_text(await locator.nth(index).inner_text(timeout=1500))
                except Exception:
                    continue
                if not text:
                    continue
                normalized = text.lstrip("@").strip()
                if normalize_text(normalized) == normalize_text(profile_username):
                    continue
                if text.startswith("@"):
                    continue
                candidate = valid_channel_candidate(text, profile_username)
                if candidate:
                    return candidate
        except Exception:
            continue
    return ""


async def read_profile_channel_name_from_dom(page, profile_username):
    selectors = [
        '[data-e2e="user-title"]',
        'h1[data-e2e="user-title"]',
        "main h1",
        "h1",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            for index in range(min(count, 5)):
                try:
                    text = await locator.nth(index).inner_text(timeout=1200)
                except Exception:
                    continue
                for line in profile_text_candidates(text):
                    candidate = valid_channel_candidate(line, profile_username)
                    if candidate:
                        return candidate
        except Exception:
            continue
    return ""


async def read_profile_channel_name(page, profile_username, channel_cache=None, timeout_ms=PROFILE_PAGE_TIMEOUT_MS):
    if not profile_username:
        return ""

    cache_key = profile_username.casefold()
    if isinstance(channel_cache, dict):
        cached = clean_text(channel_cache.get(cache_key))
        if (
            cached
            and not is_generated_username_channel(cached, f"https://www.tiktok.com/@{profile_username}")
            and not is_generic_tiktok_channel_name(cached)
        ):
            return cached

    profile_url = normalize_tiktok_url(f"https://www.tiktok.com/@{profile_username}")
    if not profile_url:
        return ""
    try:
        await navigate_tiktok_page(page, profile_url, timeout_ms=timeout_ms)
        if not is_scrapable_tiktok_url(page.url):
            return ""
        for _ in range(PROFILE_POLL_ROUNDS):
            content = await page.content()
            candidate = parse_profile_channel_name(content, profile_username)
            if not candidate:
                candidate = await read_profile_channel_name_from_dom(page, profile_username)
            if candidate:
                if isinstance(channel_cache, dict):
                    channel_cache[cache_key] = candidate
                return candidate
            await page.wait_for_timeout(POLL_INTERVAL_MS)
    except Exception:
        return ""
    return ""


async def block_heavy_resources(route):
    request = route.request
    if request.is_navigation_request():
        if not is_scrapable_tiktok_url(request.url):
            await route.abort()
            return
        response = None
        try:
            # Routes are not re-entered for server redirects. Fetch one hop and
            # never fulfill a 30x: Chromium could otherwise follow it unchecked.
            response = await route.fetch(max_redirects=0, timeout=HOP_TIMEOUT_MS)
            if 300 <= response.status < 400:
                await route.abort()
            else:
                await route.fulfill(response=response)
        except Exception:
            await route.abort()
        finally:
            if response is not None:
                await response.dispose()
    elif request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def navigate_tiktok_page(page, url, *, timeout_ms=PAGE_TIMEOUT_MS):
    """Resolve only allowed redirect hops, then perform a no-redirect navigation.

    The context's navigation route still checks the final refetch, so a changed
    redirect response fails closed instead of sending Chromium to a new host.
    """
    target = normalize_tiktok_url(url)
    if not target:
        raise ValueError("URL TikTok không hợp lệ.")
    deadline = time.monotonic() + timeout_ms / 1000
    for _hop in range(MAX_REDIRECT_HOPS):
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            raise TimeoutError("Hết thời gian kiểm tra chuyển hướng TikTok.")
        response = await page.context.request.get(
            target, max_redirects=0, timeout=min(HOP_TIMEOUT_MS, remaining_ms),
            headers={"Accept": "text/html", "Referer": "https://www.tiktok.com/"},
        )
        try:
            if 300 <= response.status < 400:
                location = response.headers.get("location")
                if response.status not in {301, 302, 303, 307, 308} or not location:
                    raise ValueError("Chuyển hướng TikTok không hợp lệ.")
                next_target = normalize_tiktok_url(urljoin(target, location))
                if not next_target:
                    raise ValueError("Chuyển hướng TikTok tới URL không được phép.")
                target = next_target
                continue
        finally:
            await response.dispose()
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            raise TimeoutError("Hết thời gian kiểm tra chuyển hướng TikTok.")
        return await page.goto(target, wait_until="domcontentloaded", timeout=remaining_ms)
    raise ValueError("Quá nhiều chuyển hướng TikTok.")


async def make_browser_context(browser, proxy_configs=None):
    context_kwargs = {
        "user_agent": DEFAULT_USER_AGENT,
        "viewport": {"width": 390, "height": 844},
        "locale": BROWSER_LOCALE,
        "timezone_id": BROWSER_TIMEZONE,
        "geolocation": BROWSER_GEOLOCATION,
        "permissions": ["geolocation"],
        "service_workers": "block",
    }
    configs = [item for item in (proxy_configs or []) if item and item.get("enabled")]
    if configs:
        proxy_config = random.choice(configs)
        context_kwargs["proxy"] = playwright_proxy_settings(proxy_config)
    context = await browser.new_context(**context_kwargs)
    try:
        # Only while "Cookie TikTok" is on for this scan; the context's own proxy carries them.
        session_cookies = cookies.playwright_cookies()
        if session_cookies:
            await context.add_cookies(session_cookies)
        await context.route("**/*", block_heavy_resources)
    except BaseException:
        await asyncio.gather(context.close(), return_exceptions=True)
        raise
    return context


async def scrape_single_link(page, url, channel_cache=None, timeout_ms=PAGE_TIMEOUT_MS):
    data = empty_metrics()
    channel_name = ""
    url = normalize_tiktok_url(url)
    if not url:
        return data, channel_name, "Error: URL TikTok không hợp lệ", ""
    profile_username = extract_profile_username(url)
    try:
        await navigate_tiktok_page(page, url, timeout_ms=timeout_ms)
        try:
            # Script tags are never "visible": wait for them to be attached, or the
            # default visible state burns the whole timeout on every link.
            await page.wait_for_selector(
                'script#__UNIVERSAL_DATA_FOR_REHYDRATION__, script#SIGI_STATE, script#api-data',
                state="attached",
                timeout=STATE_SCRIPT_WAIT_MS,
            )
        except Exception:
            pass

        # Resolve final URL after redirect (vt.tiktok.com/... -> www.tiktok.com/@user/...)
        page_url = url
        try:
            page_url = page.url or url
        except Exception:
            page_url = url
        page_url = normalize_tiktok_url(page_url)
        if not page_url:
            return data, channel_name, "Error: URL chuyển hướng không thuộc TikTok.", ""
        if not profile_username:
            profile_username = extract_profile_username(page_url)
        source_media_id = extract_media_id(url)
        final_media_id = extract_media_id(page_url)
        if media_redirect_mismatch(url, page_url):
            return data, channel_name, STATUS_MEDIA_REDIRECT_MISMATCH, url
        media_id = source_media_id or final_media_id
        if not media_id:
            return data, handle_fallback(profile_username), STATUS_METRICS_UNREADABLE, page_url

        previous_metrics = None
        found = False
        content = ""
        for _ in range(METRIC_POLL_ROUNDS):
            content = await page.content()

            candidate, candidate_found = parse_counts(content, media_id=media_id)
            if candidate_found and validate_metrics(candidate):
                if previous_metrics and counts_match(previous_metrics, candidate):
                    data = candidate
                    found = True
                    break
                previous_metrics = candidate
            elif is_tiktok_item_not_found(content):
                # The server-rendered state will not change by polling. Live posts
                # get it intermittently too, so it is only a suspicion that
                # scrape_with_retries retries and confirms.
                return data, "", STATUS_TIKTOK_NOT_FOUND_UNCONFIRMED, page_url
            parsed_channel = parse_channel_name_from_page(content, profile_username, media_id=media_id)
            if parsed_channel:
                channel_name = parsed_channel
            if profile_username and not channel_name:
                dom_channel = await read_channel_name_from_dom(page, profile_username)
                if dom_channel and not is_generated_username_channel(dom_channel, url) and not is_generic_tiktok_channel_name(dom_channel):
                    channel_name = dom_channel
            await page.wait_for_timeout(POLL_INTERVAL_MS)

        if profile_username:
            needs_profile_channel = (
                not channel_name
                or is_generated_username_channel(channel_name, url)
                or is_generic_tiktok_channel_name(channel_name)
            )
            if needs_profile_channel:
                profile_channel = await read_profile_channel_name(page, profile_username, channel_cache=channel_cache)
                if profile_channel:
                    channel_name = profile_channel

        # Final guard against accidentally captured error text
        if looks_like_error_text(channel_name):
            channel_name = ""

        # Fallback to @handle when we can detect the profile but no nickname is parsed.
        # Auto-generated user-ID handles (user1234567890) stay empty -> mark as "Lỗi".
        if not channel_name and profile_username and not is_generated_username_channel(profile_username):
            channel_name = f"@{profile_username}"

        if not found and previous_metrics and validate_metrics(previous_metrics):
            data = previous_metrics
            found = True

        if not found and is_tiktok_error_page(content):
            # Error-looking content that outlived the polling window: still unconfirmed.
            return data, "", STATUS_TIKTOK_NOT_FOUND_UNCONFIRMED, page_url
        if not found:
            return data, channel_name, no_metrics_status(content, media_id=media_id), page_url

        return data, channel_name, "Success", page_url
    except Exception as error:
        return data, channel_name, f"Error: {str(error)}", url


async def scrape_with_retries(page, url, retries, channel_cache=None):
    last_data = empty_metrics()
    last_status = "Error: Chưa chạy"
    last_channel = ""
    last_resolved_url = ""
    confirmed_gone = None

    for attempt in range(retries + 1):
        if attempt > 0:
            await asyncio.sleep(random.uniform(1.5, 3.5))
        data, channel_name, status, resolved_url = await scrape_single_link(page, url, channel_cache=channel_cache)
        if resolved_url:
            last_resolved_url = resolved_url
        last_data, last_channel, last_status = data, channel_name, status
        if status == "Success":
            return data, channel_name, status, attempt + 1, last_resolved_url
        if is_suspected_gone_status(status):
            if confirmed_gone is None:
                confirmed_gone = await asyncio.to_thread(gone.confirm_tiktok_post_gone, url, last_resolved_url)
            if confirmed_gone:
                return data, channel_name, STATUS_TIKTOK_UNAVAILABLE, attempt + 1, last_resolved_url
            continue
        if should_clear_stale_metrics(status):
            return data, channel_name, status, attempt + 1, last_resolved_url

    return last_data, last_channel, last_status, retries + 1, last_resolved_url
