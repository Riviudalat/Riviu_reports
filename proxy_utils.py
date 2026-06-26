import json
import os
import random
import re
import threading
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

PROXY_LIST_FILENAME = "proxy_list.txt"
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


def proxy_list_path(base_dir):
    return os.path.join(base_dir, "data", PROXY_LIST_FILENAME)


def looks_like_host(value):
    text = str(value or "").strip()
    if not text:
        return False
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", text):
        return True
    return "." in text and not text.isdigit()


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
    try:
        port = int(raw.get("port") or 0)
    except (TypeError, ValueError):
        return None
    if port <= 0:
        return None
    socks_port = int(raw.get("socks_port") or raw.get("socksPort") or port)
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
        host = parsed.hostname or ""
        port = parsed.port
        if not host or not port:
            return None
        return normalize_proxy_config(
            {
                "enabled": True,
                "type": proxy_type,
                "host": host,
                "port": port,
                "socks_port": port,
                "username": parsed.username or "",
                "password": parsed.password or "",
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
        return open(path, "r", encoding="utf-8").read()
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


def proxy_status(base_dir):
    text = load_proxy_list_text(base_dir)
    configs = parse_proxy_text(text)
    return {
        "configured": bool(configs),
        "count": len(configs),
        "text": text,
    }


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


def set_session_proxy(config):
    set_session_proxies([config] if config else [])


def get_session_proxies():
    with _session_proxy_lock:
        return list(_session_proxies)


def pick_session_proxy():
    with _session_proxy_lock:
        if not _session_proxies:
            return None
        return random.choice(_session_proxies)


def urlopen_with_config(request, config, timeout=30):
    if not config or not config.get("enabled"):
        return urllib.request.urlopen(request, timeout=timeout)

    if config.get("type") == "socks5":
        try:
            import socks  # PySocks
        except ImportError as error:
            raise RuntimeError("Chưa cài PySocks. Chạy: pip install PySocks") from error

        proxy_url = build_http_proxy_url({**config, "port": config["socks_port"]})
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        opener = urllib.request.build_opener(handler)
        return opener.open(request, timeout=timeout)

    proxy_url = build_http_proxy_url(config)
    handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(handler)
    return opener.open(request, timeout=timeout)


def urlopen_request(request, timeout=30):
    config = pick_session_proxy()
    return urlopen_with_config(request, config, timeout=timeout)


def fetch_ip_via_config(config, timeout=25):
    request = urllib.request.Request(
        IP_CHECK_URL,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urlopen_with_config(request, config, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return str(payload.get("ip") or "").strip()


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


def test_proxy_config(config, timeout=25):
    result = {
        "label": proxy_label(config),
        "host": config.get("host", ""),
        "port": config.get("port"),
        "ok": False,
        "ip": "",
        "tiktokOk": False,
        "tiktokError": "",
        "error": "",
    }
    try:
        result["ip"] = fetch_ip_via_config(config, timeout=timeout)
        result["ok"] = bool(result["ip"])
    except Exception as error:
        result["error"] = str(error)
        return result

    if not result["ok"]:
        return result

    try:
        _, _, tiktok_ok, tiktok_error = probe_tiktok_via_config(config, timeout=timeout)
        result["tiktokOk"] = tiktok_ok
        if tiktok_error:
            result["tiktokError"] = tiktok_error
        elif not tiktok_ok:
            result["tiktokError"] = "Không đọc được HTML TikTok có số liệu"
    except Exception as error:
        result["tiktokError"] = str(error)
    return result


def test_proxy_text(text, attempts=3, timeout=25):
    configs = parse_proxy_text(text)
    if not configs:
        return {
            "count": 0,
            "uniqueIps": 0,
            "results": [],
            "message": "Không đọc được proxy nào. Mỗi dòng 1 proxy.",
        }

    results = []
    ips = []
    rotation_configs = configs if len(configs) > 1 else configs * max(attempts, 1)
    sample_count = min(max(attempts, 1), 5)
    for index in range(sample_count):
        config = rotation_configs[index % len(configs)]
        item = test_proxy_config(config, timeout=timeout)
        item["attempt"] = index + 1
        results.append(item)
        if item.get("ip"):
            ips.append(item["ip"])

    unique_ips = sorted(set(ips))
    tiktok_hits = sum(1 for item in results if item.get("tiktokOk"))
    return {
        "count": len(configs),
        "attempts": sample_count,
        "uniqueIps": len(unique_ips),
        "ips": unique_ips,
        "results": results,
        "message": (
            f"Đọc được {len(configs)} proxy • {len(unique_ips)} IP khác • TikTok OK {tiktok_hits}/{len(results)}"
            if ips
            else f"Đọc được {len(configs)} proxy nhưng chưa test thành công"
        ),
    }
