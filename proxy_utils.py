import json
import os
import threading
import urllib.request
from urllib.parse import quote

PROXY_CONFIG_FILENAME = "proxy_config.json"

_session_proxy = None
_session_proxy_lock = threading.Lock()


def proxy_config_candidates(base_dir):
    data_path = os.path.join(base_dir, "data", PROXY_CONFIG_FILENAME)
    root_path = os.path.join(base_dir, PROXY_CONFIG_FILENAME)
    return [data_path, root_path]


def proxy_config_path(base_dir):
    for path in proxy_config_candidates(base_dir):
        if os.path.exists(path):
            return path
    return proxy_config_candidates(base_dir)[0]


def normalize_proxy_config(raw):
    if not isinstance(raw, dict):
        return None
    host = str(raw.get("host") or "").strip()
    if not host:
        return None
    username = str(raw.get("username") or "").strip()
    password = str(raw.get("password") or "")
    if not username:
        return None
    proxy_type = str(raw.get("type") or "http").strip().lower()
    if proxy_type not in {"http", "socks5"}:
        proxy_type = "http"
    return {
        "enabled": bool(raw.get("enabled", True)),
        "type": proxy_type,
        "host": host,
        "port": int(raw.get("port") or 49472),
        "socks_port": int(raw.get("socks_port") or raw.get("socksPort") or 59472),
        "username": username,
        "password": password,
    }


def load_proxy_config(base_dir, *, enabled_only=False):
    for path in proxy_config_candidates(base_dir):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                config = normalize_proxy_config(json.load(file_obj))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if config and (not enabled_only or config.get("enabled")):
            return config
    return None


def build_http_proxy_url(config):
    user = quote(config["username"], safe="")
    password = quote(config["password"], safe="")
    return f"http://{user}:{password}@{config['host']}:{config['port']}"


def playwright_proxy_settings(config):
    if config.get("type") == "socks5":
        server = f"socks5://{config['host']}:{config['socks_port']}"
    else:
        server = f"http://{config['host']}:{config['port']}"
    return {
        "server": server,
        "username": config["username"],
        "password": config["password"],
    }


def proxy_status(base_dir):
    path = proxy_config_path(base_dir)
    config = load_proxy_config(base_dir)
    if not config:
        return {
            "configured": False,
            "enabled": False,
            "path": path,
            "host": "",
            "type": "",
            "port": None,
        }
    return {
        "configured": True,
        "enabled": bool(config.get("enabled")),
        "path": path,
        "host": config.get("host", ""),
        "type": config.get("type", "http"),
        "port": config.get("port") if config.get("type") != "socks5" else config.get("socks_port"),
    }


def set_session_proxy(config):
    global _session_proxy
    with _session_proxy_lock:
        _session_proxy = config


def get_session_proxy():
    with _session_proxy_lock:
        return _session_proxy


def urlopen_request(request, timeout=30):
    config = get_session_proxy()
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
