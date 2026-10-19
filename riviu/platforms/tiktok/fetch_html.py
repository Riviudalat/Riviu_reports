"""One rate-limited HTTP GET of a TikTok page, with every redirect hop validated."""
import random
import urllib.request

from riviu.proxy_utils import urlopen_request
from riviu.workbook_utils import is_scrapable_tiktok_url, normalize_tiktok_url
from riviu.platforms.tiktok import cookies, throttle
from riviu.platforms.tiktok.constants import DEFAULT_REQUEST_TIMEOUT, USER_AGENTS


def fetch_tiktok_html(url, timeout=DEFAULT_REQUEST_TIMEOUT):
    url = normalize_tiktok_url(url)
    if not url:
        raise ValueError("URL TikTok không hợp lệ.")
    throttle.wait_if_request_blocked()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.tiktok.com/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        },
    )
    # Only while "Cookie TikTok" is on for this scan; redirects stay on TikTok hosts.
    cookie_header = cookies.cookie_header_for(url)
    if cookie_header:
        request.add_header("Cookie", cookie_header)
    with throttle._request_semaphore:
        with urlopen_request(request, timeout=timeout, redirect_validator=is_scrapable_tiktok_url) as response:
            final_url = normalize_tiktok_url(response.geturl())
            if not final_url:
                raise ValueError("Chuyển hướng TikTok tới URL không được phép.")
            return final_url, response.read().decode("utf-8", errors="replace")
