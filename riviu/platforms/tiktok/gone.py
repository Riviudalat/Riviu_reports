"""Independent confirmation, through TikTok's embed player page, that a post is really gone."""
import random
import time
import urllib.error
import urllib.request

from riviu.proxy_utils import urlopen_request
from riviu.workbook_utils import is_scrapable_tiktok_url
from riviu.platforms.tiktok import throttle
from riviu.platforms.tiktok.constants import USER_AGENTS
from riviu.platforms.tiktok.parsing import extract_media_id


TIKTOK_EMBED_ENDPOINT = "https://www.tiktok.com/embed/v2/"
CONFIRM_TIMEOUT_SECONDS = 15
# A post counts as gone only when this many embed answers in a row say so.
GONE_ANSWERS_NEEDED = 2
GONE_RECHECK_PAUSE_SECONDS = (1.0, 2.5)
# The embed page of a live post carries its stats; a deleted, private or made-up id gets
# HTTP 400 and this error block ("Video currently unavailable", statusCode 10204).
LIVE_MARKER = '"playCount"'
GONE_MARKER = 'data-e2e="VideoError'


def tiktok_embed_url(url, resolved_url=""):
    """The embed player page of the post (by id: photo and video posts alike, any handle)."""
    media_id = extract_media_id(url) or extract_media_id(resolved_url)
    return f"{TIKTOK_EMBED_ENDPOINT}{media_id}" if media_id else ""


def embed_answer(target, timeout=CONFIRM_TIMEOUT_SECONDS):
    """One embed page fetch: True (gone), False (live) or None (no clear answer)."""
    request = urllib.request.Request(
        target,
        headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    throttle.wait_if_request_blocked()
    try:
        with throttle._request_semaphore:
            with urlopen_request(request, timeout=timeout, redirect_validator=is_scrapable_tiktok_url) as response:
                body = response.read().decode("utf-8", errors="replace")
                return False if LIVE_MARKER in body else None
    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        finally:
            error.close()
        # 503 is TikTok throttling the IP (live posts get it too); a 400 without the
        # error block (for example from a proxy) proves nothing.
        return True if error.code == 400 and GONE_MARKER in body else None
    except Exception:
        return None


def confirm_tiktok_post_gone(url, resolved_url="", timeout=CONFIRM_TIMEOUT_SECONDS):
    """Independent check that a post is gone, via TikTok's embed player page.

    History: until Oct 2026 this asked TikTok's oEmbed endpoint, which answered 400 for
    deleted ids. Since then oEmbed answers 200 with the same bare player for every id,
    a made-up one included (checked again 2026-11-07), so it can no longer tell.

    Live sample (2026-11-07, through the scan proxies): of 30 live posts, the embed page
    gave stats for 26 and 503 for 4, never the error block; of 60 posts whose page said
    "item not found", 49 gave the error block and 11 gave 503, none were live. A post
    counts as gone only after GONE_ANSWERS_NEEDED error-block answers in a row.
    Returns True (gone), False (live) or None (no clear answer; keeps the old numbers).
    """
    target = tiktok_embed_url(url, resolved_url)
    if not target:
        return None
    for check in range(GONE_ANSWERS_NEEDED):
        if check:
            time.sleep(random.uniform(*GONE_RECHECK_PAUSE_SECONDS))
        answer = embed_answer(target, timeout=timeout)
        if answer is not True:
            return answer
    return True
