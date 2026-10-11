"""Independent confirmation, through TikTok's oEmbed endpoint, that a post is really gone."""
import random
import urllib.error
import urllib.request
from urllib.parse import quote

from riviu.proxy_utils import urlopen_request
from riviu.workbook_utils import is_scrapable_tiktok_url
from riviu.platforms.tiktok import throttle
from riviu.platforms.tiktok.constants import USER_AGENTS
from riviu.platforms.tiktok.parsing import extract_media_id, extract_profile_username, json_loads_safe


TIKTOK_OEMBED_ENDPOINT = "https://www.tiktok.com/oembed?url="


def tiktok_oembed_target(url, resolved_url=""):
    media_id = extract_media_id(url) or extract_media_id(resolved_url)
    if not media_id:
        return ""
    # oEmbed resolves the post by id (a renamed handle still answers 200) but
    # rejects every /photo/ URL with 400, so always ask in the /video/ form.
    username = extract_profile_username(url) or extract_profile_username(resolved_url) or "_"
    return f"https://www.tiktok.com/@{username.lstrip('@')}/video/{media_id}"


def confirm_tiktok_post_gone(url, resolved_url="", timeout=15):
    """Independent check that a post is gone, via TikTok's oEmbed endpoint.

    Live sample (Oct 2026): the post page returned 10204 on about a third of
    fetches of live posts, while oEmbed answered 200 for every live post (photo
    posts included) and 400 for every deleted or nonexistent id.
    Returns True (gone), False (live) or None (no answer).
    """
    target = tiktok_oembed_target(url, resolved_url)
    if not target:
        return None
    request = urllib.request.Request(
        TIKTOK_OEMBED_ENDPOINT + quote(target, safe=""),
        headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        },
    )
    throttle.wait_if_request_blocked()
    try:
        with throttle._request_semaphore:
            with urlopen_request(request, timeout=timeout, redirect_validator=is_scrapable_tiktok_url) as response:
                payload = json_loads_safe(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        try:
            body = json_loads_safe(error.read().decode("utf-8", errors="replace"))
        except Exception:
            body = None
        finally:
            error.close()
        if error.code in (404, 410):
            return True
        # TikTok answers unknown ids with 400 and a JSON error body; a bare 400
        # (for example from a proxy) proves nothing.
        return True if error.code == 400 and isinstance(body, dict) else None
    except Exception:
        return None
    return False if isinstance(payload, dict) and payload.get("type") else None
