import json
from unittest.mock import patch

import urllib.request

from proxy_utils import (
    build_http_proxy_url,
    normalize_proxy_config,
    parse_proxy_line,
    parse_proxy_text,
    pick_session_proxy,
    playwright_proxy_settings,
    proxy_status,
    resolve_proxy_configs,
    set_session_proxies,
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
    assert config["socks_port"] == 49472


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


def test_parse_proxy_line_formats():
    host_port_user_pass = parse_proxy_line("1.2.3.4:8080:myuser:mypass")
    assert host_port_user_pass["host"] == "1.2.3.4"
    assert host_port_user_pass["username"] == "myuser"

    user_pass_at = parse_proxy_line("user:pass@5.6.7.8:3128")
    assert user_pass_at["host"] == "5.6.7.8"
    assert user_pass_at["port"] == 3128

    url_form = parse_proxy_line("http://u:p@proxy.test:9000")
    assert url_form["host"] == "proxy.test"
    assert url_form["type"] == "http"

    plain = parse_proxy_line("9.9.9.9:80")
    assert plain["host"] == "9.9.9.9"
    assert plain["port"] == 80

    assert parse_proxy_line("# comment") is None
    assert parse_proxy_line("") is None


def test_parse_proxy_text_deduplicates():
    text = "\n".join([
        "1.2.3.4:8080:user:pass",
        "user:pass@1.2.3.4:8080",
        "1.2.3.4:8080:user:pass",
    ])
    configs = parse_proxy_text(text)
    assert len(configs) == 1


def test_resolve_proxy_configs_prefers_inline_text(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "proxy_list.txt").write_text("9.9.9.9:8080:a:b\n", encoding="utf-8")
    inline = resolve_proxy_configs(str(tmp_path), "1.1.1.1:8080:u:p")
    assert inline[0]["host"] == "1.1.1.1"
    saved = resolve_proxy_configs(str(tmp_path), "")
    assert saved[0]["host"] == "9.9.9.9"


def test_resolve_proxy_configs_ignores_legacy_json_file(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "proxy_config.json").write_text(
        json.dumps({
            "enabled": True,
            "host": "legacy.example",
            "port": 8080,
            "username": "u",
            "password": "p",
        }),
        encoding="utf-8",
    )
    assert resolve_proxy_configs(str(tmp_path), "") == []


def test_proxy_status_not_configured(tmp_path):
    status = proxy_status(str(tmp_path))
    assert status["configured"] is False
    assert status["count"] == 0


def test_tiktok_html_looks_valid():
    from proxy_utils import tiktok_html_looks_valid

    assert tiktok_html_looks_valid("<html>" + ("x" * 600) + "playCount</html>") is True
    assert tiktok_html_looks_valid("<html>" + ("x" * 600) + "pumbaa-rule</html>") is False
    assert tiktok_html_looks_valid("short") is False


def test_pick_session_proxy_random_from_pool():
    configs = [
        normalize_proxy_config({"host": "1.1.1.1", "port": 8080}),
        normalize_proxy_config({"host": "2.2.2.2", "port": 8080}),
    ]
    set_session_proxies(configs)
    try:
        picked = {pick_session_proxy()["host"] for _ in range(20)}
        assert len(picked) >= 1
    finally:
        set_session_proxies([])


def test_set_session_proxy_used_by_urlopen_request():
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

                req = urllib.request.Request("https://example.com")
                urlopen_request(req, timeout=1)
            except IOError:
                pass
            build_opener.assert_called_once()
            handler = build_opener.call_args[0][0]
            assert isinstance(handler, urllib.request.ProxyHandler)
    finally:
        set_session_proxy(None)
