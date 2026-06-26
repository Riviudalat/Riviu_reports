import json
from pathlib import Path
from unittest.mock import patch

from proxy_utils import (
    build_http_proxy_url,
    load_proxy_config,
    normalize_proxy_config,
    playwright_proxy_settings,
    proxy_status,
    set_session_proxy,
)


def test_normalize_proxy_config_http():
    config = normalize_proxy_config({
        "enabled": True,
        "type": "http",
        "host": "103.162.31.100",
        "port": 49472,
        "username": "user49472",
        "password": "secret",
    })
    assert config["type"] == "http"
    assert config["port"] == 49472
    assert config["socks_port"] == 59472


def test_build_http_proxy_url_encodes_credentials():
    config = normalize_proxy_config({
        "host": "103.162.31.100",
        "port": 49472,
        "username": "user49472",
        "password": "p@ss",
    })
    assert build_http_proxy_url(config) == "http://user49472:p%40ss@103.162.31.100:49472"


def test_playwright_proxy_settings():
    config = normalize_proxy_config({
        "host": "103.162.31.100",
        "port": 49472,
        "socks_port": 59472,
        "username": "user49472",
        "password": "secret",
    })
    assert playwright_proxy_settings(config) == {
        "server": "http://103.162.31.100:49472",
        "username": "user49472",
        "password": "secret",
    }


def test_load_proxy_config_from_data_dir(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "proxy_config.json").write_text(
        json.dumps({
            "enabled": True,
            "host": "1.2.3.4",
            "port": 8080,
            "username": "u",
            "password": "p",
        }),
        encoding="utf-8",
    )
    config = load_proxy_config(str(tmp_path), enabled_only=True)
    assert config["host"] == "1.2.3.4"


def test_proxy_status_not_configured(tmp_path):
    status = proxy_status(str(tmp_path))
    assert status["configured"] is False


def test_set_session_proxy_used_by_urlopen_request(tmp_path):
    config = normalize_proxy_config({
        "enabled": True,
        "host": "103.162.31.100",
        "port": 49472,
        "username": "user49472",
        "password": "secret",
    })
    set_session_proxy(config)
    try:
        with patch("proxy_utils.urllib.request.build_opener") as build_opener:
            build_opener.return_value.open.side_effect = IOError("stop")
            try:
                from proxy_utils import urlopen_request
                import urllib.request

                req = urllib.request.Request("https://example.com")
                urlopen_request(req, timeout=1)
            except IOError:
                pass
            build_opener.assert_called_once()
            handler = build_opener.call_args[0][0]
            assert isinstance(handler, urllib.request.ProxyHandler)
    finally:
        set_session_proxy(None)
