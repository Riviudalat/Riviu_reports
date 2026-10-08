import json
import http.client
import os
import random
import re
import socket
import threading
import urllib.error
import urllib.request
from urllib.parse import quote, unquote, urlparse

PROXY_LIST_FILENAME = "proxy_list.txt"
PROXY_TEST_BUILD = "7"
IP_CHECK_URL = "https://api.ipify.org?format=json"
# Link mẫu ổn định — probe phải giống luồng quét (trang chủ TikTok thường không có số liệu).
TIKTOK_PROBE_URL = "https://www.tiktok.com/@demo/photo/764002"
TIKTOK_PROBE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.tiktok.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}
TIKTOK_HTML_MARKERS = (
    "SIGI_STATE",
    "__UNIVERSAL_DATA_FOR_REHYDRATION__",
    "itemStruct",
    "playCount",
    "statsV2",
)

_session_proxies = []
_session_proxy_lock = threading.Lock()
_thread_local = threading.local()
_opener_cache = {}
_opener_cache_lock = threading.Lock()


def _config_cache_key(config):
    if not config:
        return None
    return (
        config.get("type"),
        config.get("host"),
        config.get("port"),
        config.get("socks_port"),
        config.get("username"),
        config.get("password"),
    )


def _direct_opener():
    with _opener_cache_lock:
        opener = _opener_cache.get("direct")
        if opener is None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            _opener_cache["direct"] = opener
        return opener


def _http_proxy_opener(config):
    key = _config_cache_key(config)
    with _opener_cache_lock:
        opener = _opener_cache.get(key)
        if opener is None:
            proxy_url = build_http_proxy_url(config)
            handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            opener = urllib.request.build_opener(handler)
            _opener_cache[key] = opener
        return opener


def _socks_urlopen(request, config, timeout, extra_handlers=()):
    try:
        import socks  # PySocks
    except ImportError as error:
        raise RuntimeError("Chưa cài PySocks. Chạy: pip install PySocks") from error

    # Each urllib opener owns its connections. Never change socket.socket or
    # PySocks defaults: other request workers may use a different route.
    proxy = dict(config)

    def connect(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **_kwargs):
        return socks.create_connection(
            address,
            timeout=timeout,
            source_address=source_address,
            proxy_type=socks.SOCKS5,
            proxy_addr=proxy["host"],
            proxy_port=proxy["socks_port"],
            proxy_rdns=True,
            proxy_username=proxy.get("username") or None,
            proxy_password=proxy.get("password") or None,
        )

    class SocksHTTPConnection(http.client.HTTPConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = connect

    class SocksHTTPSConnection(http.client.HTTPSConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = connect

    class SocksHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(SocksHTTPConnection, req)

    class SocksHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(SocksHTTPSConnection, req, context=self._context)

    if extra_handlers:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SocksHTTPHandler(), SocksHTTPSHandler(), *extra_handlers)
    else:
        key = _config_cache_key(config)
        with _opener_cache_lock:
            opener = _opener_cache.get(key)
            if opener is None:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}), SocksHTTPHandler(), SocksHTTPSHandler(),
                )
                _opener_cache[key] = opener
    return opener.open(request, timeout=timeout)


def proxy_list_path(base_dir):
    return os.path.join(base_dir, "data", PROXY_LIST_FILENAME)


def looks_like_host(value):
    text = str(value or "").strip()
    if not text:
        return False
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", text):
        return True
    return "." in text and not text.isdigit()


def _valid_port(value):
    if isinstance(value, bool):
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def normalize_proxy_config(raw):
    if not isinstance(raw, dict):
        return None
    host = str(raw.get("host") or "").strip()
    if not host:
        return None
    username = str(raw.get("username") or "").strip()
    password = str(raw.get("password") or "")
    proxy_type = str(raw.get("type") or "http").strip().lower()
    if proxy_type.startswith("socks"):
        proxy_type = "socks5"
    elif proxy_type not in {"http", "socks5"}:
        proxy_type = "http"
    port = _valid_port(raw.get("port"))
    socks_port = _valid_port(raw.get("socks_port") or raw.get("socksPort") or port)
    if port is None or socks_port is None:
        return None
    if username and not password:
        return None
    return {
        "enabled": bool(raw.get("enabled", True)),
        "type": proxy_type,
        "host": host,
        "port": port,
        "socks_port": socks_port,
        "username": username,
        "password": password,
    }


def parse_proxy_line(line):
    text = str(line or "").strip()
    if not text or text.startswith("#"):
        return None

    if text.startswith("{"):
        try:
            return normalize_proxy_config(json.loads(text))
        except json.JSONDecodeError:
            return None

    if "://" in text:
        parsed = urlparse(text)
        scheme = (parsed.scheme or "http").lower()
        proxy_type = "socks5" if scheme.startswith("socks") else "http"
        try:
            port = parsed.port
        except ValueError:  # Out-of-range or non-numeric URL port.
            return None
        host = parsed.hostname or ""
        if not host or not port:
            return None
        # URL userinfo is percent-encoded; store raw credentials so each
        # transport encodes them exactly once.
        return normalize_proxy_config(
            {
                "enabled": True,
                "type": proxy_type,
                "host": host,
                "port": port,
                "socks_port": port,
                "username": unquote(parsed.username or ""),
                "password": unquote(parsed.password or ""),
            }
        )

    if "@" in text:
        auth, hostport = text.rsplit("@", 1)
        if ":" not in auth or ":" not in hostport:
            return None
        username, password = auth.split(":", 1)
        host, port_text = hostport.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            return None
        return normalize_proxy_config(
            {
                "enabled": True,
                "type": "http",
                "host": host.strip(),
                "port": port,
                "username": username.strip(),
                "password": password,
            }
        )

    parts = text.split(":")
    if len(parts) >= 4:
        if looks_like_host(parts[0]):
            host = parts[0]
            port_text = parts[1]
            username = parts[2]
            password = ":".join(parts[3:])
        elif looks_like_host(parts[2]):
            username = parts[0]
            password = parts[1]
            host = parts[2]
            port_text = parts[3]
        else:
            host = parts[0]
            port_text = parts[1]
            username = parts[2]
            password = ":".join(parts[3:])
        try:
            port = int(port_text)
        except ValueError:
            return None
        return normalize_proxy_config(
            {
                "enabled": True,
                "type": "http",
                "host": host.strip(),
                "port": port,
                "username": username.strip(),
                "password": password,
            }
        )

    if len(parts) == 2 and parts[1].isdigit():
        return normalize_proxy_config(
            {
                "enabled": True,
                "type": "http",
                "host": parts[0].strip(),
                "port": int(parts[1]),
                "username": "",
                "password": "",
            }
        )
    return None


def parse_proxy_text(text):
    configs = []
    seen = set()
    for line in str(text or "").splitlines():
        config = parse_proxy_line(line)
        if not config:
            continue
        key = (
            config["type"],
            config["host"],
            config["port"],
            config["username"],
            config["password"],
        )
        if key in seen:
            continue
        seen.add(key)
        configs.append(config)
    return configs


def load_proxy_list_text(base_dir):
    path = proxy_list_path(base_dir)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            return file_obj.read()
    except OSError:
        return ""


def save_proxy_list_text(base_dir, text):
    path = proxy_list_path(base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_obj:
        file_obj.write(str(text or "").strip() + ("\n" if str(text or "").strip() else ""))
    return path


def resolve_proxy_configs(base_dir, proxy_text=""):
    configs = parse_proxy_text(proxy_text)
    if configs:
        return configs
    return parse_proxy_text(load_proxy_list_text(base_dir))


def proxy_label(config):
    auth = f"{config['username']}@" if config.get("username") else ""
    port = config["socks_port"] if config.get("type") == "socks5" else config["port"]
    return f"{config['type'].upper()} {auth}{config['host']}:{port}"


def build_http_proxy_url(config):
    if config.get("username"):
        user = quote(config["username"], safe="")
        password = quote(config["password"], safe="")
        return f"http://{user}:{password}@{config['host']}:{config['port']}"
    return f"http://{config['host']}:{config['port']}"


def playwright_proxy_settings(config):
    port = config["socks_port"] if config.get("type") == "socks5" else config["port"]
    scheme = "socks5" if config.get("type") == "socks5" else "http"
    settings = {"server": f"{scheme}://{config['host']}:{port}"}
    if config.get("username"):
        settings["username"] = config["username"]
        settings["password"] = config["password"]
    return settings


def set_session_proxies(configs):
    global _session_proxies
    with _session_proxy_lock:
        _session_proxies = [
            item
            for item in (configs or [])
            if isinstance(item, dict) and item.get("enabled", True)
        ]
    with _opener_cache_lock:
        _opener_cache.clear()
    _thread_local.proxy_key = None
    _thread_local.proxy_config = None
    _thread_local.worker_index = None
    _thread_local.proxy_rotation = 0


def get_session_proxies():
    with _session_proxy_lock:
        return list(_session_proxies)


def pick_session_proxy():
    with _session_proxy_lock:
        pool = list(_session_proxies)
    if not pool:
        return None
    sticky = getattr(_thread_local, "proxy_config", None)
    sticky_key = getattr(_thread_local, "proxy_key", None)
    if sticky and sticky_key == _config_cache_key(sticky):
        for item in pool:
            if _config_cache_key(item) == sticky_key:
                return item
    chosen = random.choice(pool)
    _thread_local.proxy_config = chosen
    _thread_local.proxy_key = _config_cache_key(chosen)
    return chosen


def assign_worker_proxy(worker_index):
    """Gán proxy theo round-robin cho luồng worker_index (0-based).

    Đảm bảo chia đều: N luồng / M proxy -> mỗi proxy được ~N/M luồng dùng,
    thay vì random.choice() có thể dồn nhiều luồng vào cùng 1 proxy do may rủi.
    """
    with _session_proxy_lock:
        pool = list(_session_proxies)
    _thread_local.worker_index = worker_index
    _thread_local.proxy_rotation = 0
    if not pool:
        _thread_local.proxy_config = None
        _thread_local.proxy_key = None
        return None
    chosen = pool[worker_index % len(pool)]
    _thread_local.proxy_config = chosen
    _thread_local.proxy_key = _config_cache_key(chosen)
    return chosen


def release_thread_proxy():
    """Chuyển luồng hiện tại sang proxy kế tiếp trong pool (round-robin xoay vòng).

    Dùng khi proxy hiện tại bị 403/429 - xoay sang proxy khác thay vì random,
    vẫn giữ việc chia tải đều giữa các luồng.
    """
    worker_index = getattr(_thread_local, "worker_index", None)
    with _session_proxy_lock:
        pool = list(_session_proxies)
    if worker_index is None or not pool:
        _thread_local.proxy_key = None
        _thread_local.proxy_config = None
        return None
    rotation = getattr(_thread_local, "proxy_rotation", 0) + 1
    _thread_local.proxy_rotation = rotation
    chosen = pool[(worker_index + rotation) % len(pool)]
    _thread_local.proxy_config = chosen
    _thread_local.proxy_key = _config_cache_key(chosen)
    return chosen


class SessionRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        from riviu.platforms.threads_session import allowed_session_url, SessionError
        if not allowed_session_url(newurl):
            raise SessionError("redirect")
        return super().redirect_request(request, fp, code, message, headers, newurl)


class ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, validator):
        self.validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.validator(newurl):
            raise ValueError("URL chuyển hướng không thuộc nền tảng được phép.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen_with_config(request, config, timeout=30, cookiejar=None, redirect_validator=None):
    # urllib runs only the first handler's redirect_request, so a validator and
    # the cookie-session guard can never both be enforced on one opener.
    if redirect_validator is not None and cookiejar is not None:
        raise ValueError("redirect_validator và cookiejar không dùng chung một request.")
    handlers = ()
    if redirect_validator is not None:
        if not redirect_validator(request.full_url):
            raise ValueError("URL không thuộc nền tảng được phép.")
        handlers = (ValidatedRedirectHandler(redirect_validator),)
    elif cookiejar is not None:
        from riviu.platforms.threads_session import allowed_session_url, SessionError
        if not allowed_session_url(request.full_url):
            raise SessionError("unknown")
        handlers = (urllib.request.HTTPCookieProcessor(cookiejar), SessionRedirectHandler())
    route = config if config and config.get("enabled") else None
    if route and route.get("type") == "socks5":
        return _socks_urlopen(request, route, timeout, extra_handlers=handlers)
    if handlers:
        # Handler state (cookies, validators) is per request: never cache this opener.
        proxies = {"http": build_http_proxy_url(route), "https": build_http_proxy_url(route)} if route else {}
        return urllib.request.build_opener(urllib.request.ProxyHandler(proxies), *handlers).open(request, timeout=timeout)
    opener = _http_proxy_opener(route) if route else _direct_opener()
    return opener.open(request, timeout=timeout)


def urlopen_request(request, timeout=30, redirect_validator=None):
    config = pick_session_proxy()
    return urlopen_with_config(request, config, timeout=timeout, redirect_validator=redirect_validator)


def fetch_ip_via_config(config, timeout=25, retries=2):
    request = urllib.request.Request(
        IP_CHECK_URL,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    last_error = ""
    for attempt in range(max(retries, 1)):
        try:
            with urlopen_with_config(request, config, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return str(payload.get("ip") or "").strip()
        except Exception as error:
            last_error = str(error)
            if attempt + 1 >= retries:
                break
    raise urllib.error.URLError(last_error or "Không lấy được IP")


def tiktok_html_looks_valid(body):
    text = str(body or "")
    if len(text) < 500:
        return False
    if any(marker in text for marker in TIKTOK_HTML_MARKERS):
        return True
    lowered = text.lower()
    return "tiktok.com" in lowered and ("webapp" in lowered or "pumbaa-rule" in lowered)


def probe_tiktok_via_config(config, timeout=25, retries=2):
    last_error = ""
    for attempt in range(max(retries, 1)):
        try:
            request = urllib.request.Request(TIKTOK_PROBE_URL, headers=TIKTOK_PROBE_HEADERS)
            with urlopen_with_config(request, config, timeout=timeout) as response:
                body = response.read(65536).decode("utf-8", errors="replace")
                return response.status, len(body), tiktok_html_looks_valid(body), ""
        except Exception as error:
            last_error = str(error)
    return 0, 0, False, last_error


def proxy_display_name(config):
    username = str(config.get("username") or "").strip()
    region_match = re.search(r"region-([A-Za-z0-9_-]+)", username, re.IGNORECASE)
    if region_match:
        return region_match.group(1).upper()
    if username:
        return username if len(username) <= 28 else username[:25] + "..."
    return f"{config['host']}:{config['port']}"


def test_proxy_config(config, timeout=8, *, check_tiktok=False):
    result = {
        "label": proxy_label(config),
        "name": proxy_display_name(config),
        "host": config.get("host", ""),
        "port": config.get("port"),
        "ok": False,
        "ip": "",
        "tiktokOk": False,
        "tiktokError": "",
        "error": "",
    }
    try:
        result["ip"] = fetch_ip_via_config(config, timeout=timeout, retries=2)
        result["ok"] = bool(result["ip"])
    except Exception as error:
        result["error"] = str(error)
        return result

    if not result["ok"] or not check_tiktok:
        return result

    try:
        _, _, tiktok_ok, tiktok_error = probe_tiktok_via_config(config, timeout=timeout, retries=1)
        result["tiktokOk"] = tiktok_ok
        if tiktok_error:
            result["tiktokError"] = tiktok_error
        elif not tiktok_ok:
            result["tiktokError"] = "Không đọc được HTML TikTok có số liệu"
    except Exception as error:
        result["tiktokError"] = str(error)
    return result


def test_proxy_text(text, timeout=8):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    configs = parse_proxy_text(text)
    if not configs:
        return {
            "count": 0,
            "okCount": 0,
            "results": [],
            "message": "Không đọc được proxy nào. Mỗi dòng 1 proxy.",
        }

    results = [None] * len(configs)
    with ThreadPoolExecutor(max_workers=min(len(configs), 6)) as pool:
        future_map = {
            pool.submit(test_proxy_config, config, timeout): index
            for index, config in enumerate(configs)
        }
        for future in as_completed(future_map):
            index = future_map[future]
            item = future.result()
            item["line"] = index + 1
            results[index] = item

    ok_count = sum(1 for item in results if item and item.get("ok"))
    return {
        "build": PROXY_TEST_BUILD,
        "count": len(configs),
        "okCount": ok_count,
        "results": results,
        "message": f"{ok_count}/{len(configs)} proxy OK",
    }
