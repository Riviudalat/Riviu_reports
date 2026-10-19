"""The TikTok cookies of the running scan, when "Cookie TikTok" is switched on for it.

run_scraper activates the saved, normalized cookies for one run and always clears them
again; a scan without the toggle activates nothing, so its requests carry no Cookie
header and its Chromium contexts get no cookies (the behaviour before this feature).
Request mode adds the header in fetch_html; Browser mode adds them to each context in
fetch_browser.make_browser_context; Hybrid does both. Both go out over whatever route
(direct or proxy) the request itself uses. Values never reach logs or WebSocket events.
"""
import threading
import time
from urllib.parse import urlsplit

_lock = threading.Lock()
_active = ()


def activate(cookies):
    """Use these cookies for the scan that is starting; an empty value turns them off."""
    global _active
    with _lock:
        _active = tuple(dict(cookie) for cookie in (cookies or ()))


def deactivate():
    activate(())


def active():
    with _lock:
        return _active


def _matches(cookie, host, path, now):
    if "expires" in cookie and cookie["expires"] <= now:
        return False
    domain = cookie["domain"].lstrip(".")
    if cookie.get("hostOnly"):
        host_ok = host == domain
    else:
        host_ok = host == domain or host.endswith("." + domain)
    cookie_path = cookie.get("path") or "/"
    path_ok = path == cookie_path or path.startswith(cookie_path.rstrip("/") + "/") or cookie_path == "/"
    return host_ok and path_ok


def cookie_header_for(url, cookies=None):
    """The Cookie header for one HTTPS TikTok URL ('' when none apply)."""
    cookies = active() if cookies is None else cookies
    if not cookies:
        return ""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "tiktok.com" or host.endswith(".tiktok.com")):
        return ""
    now = time.time()
    path = parsed.path or "/"
    return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies if _matches(cookie, host, path, now))


def playwright_cookies(cookies=None):
    """The cookies in Playwright's add_cookies shape; expired ones are skipped."""
    cookies = active() if cookies is None else cookies
    now = time.time()
    result = []
    for cookie in cookies:
        if "expires" in cookie and cookie["expires"] <= now:
            continue
        converted = {key: cookie[key] for key in ("name", "value", "httpOnly", "sameSite")}
        converted["secure"] = True
        if cookie.get("hostOnly") and cookie.get("path", "/") == "/":
            converted["url"] = f"https://{cookie['domain']}/"
        else:
            converted["domain"] = cookie["domain"] if cookie.get("hostOnly") else "." + cookie["domain"].lstrip(".")
            converted["path"] = cookie.get("path") or "/"
        if "expires" in cookie:
            converted["expires"] = cookie["expires"]
        result.append(converted)
    return result
