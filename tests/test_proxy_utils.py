import json
from unittest.mock import patch

import urllib.request

from proxy_utils import (
    assign_worker_proxy,
    build_http_proxy_url,
    normalize_proxy_config,
    parse_proxy_line,
    parse_proxy_text,
    pick_session_proxy,
    playwright_proxy_settings,
    proxy_status,
    release_thread_proxy,
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

    socks_form = parse_proxy_line("socks5://1.2.3.4:1080")
    assert socks_form["type"] == "socks5"
    assert socks_form["host"] == "1.2.3.4"
    assert socks_form["socks_port"] == 1080

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


def test_proxy_display_name_uses_region():
    from proxy_utils import proxy_display_name

    config = normalize_proxy_config({
        "host": "us.cliproxy.io",
        "port": 3010,
        "username": "dmt61183931-region-VN",
        "password": "secret",
    })
    assert proxy_display_name(config) == "VN"


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


def test_release_thread_proxy_clears_sticky():
    configs = [
        normalize_proxy_config({"host": "1.1.1.1", "port": 8080}),
        normalize_proxy_config({"host": "2.2.2.2", "port": 8080}),
    ]
    set_session_proxies(configs)
    try:
        first = pick_session_proxy()
        release_thread_proxy()
        second = pick_session_proxy()
        assert first is not None
        assert second is not None
    finally:
        set_session_proxies([])


def test_assign_worker_proxy_round_robin_even_split():
    configs = [
        normalize_proxy_config({"host": f"1.1.1.{i}", "port": 8080})
        for i in range(10)
    ]
    set_session_proxies(configs)
    try:
        # 20 luồng / 10 proxy -> mỗi proxy đúng 2 luồng (worker_index và worker_index+10).
        assigned_hosts = [assign_worker_proxy(i)["host"] for i in range(20)]
        from collections import Counter

        counts = Counter(assigned_hosts)
        assert len(counts) == 10
        assert all(count == 2 for count in counts.values())
        assert assign_worker_proxy(0)["host"] == assign_worker_proxy(10)["host"]
    finally:
        set_session_proxies([])


def test_assign_worker_proxy_no_pool_returns_none():
    set_session_proxies([])
    assert assign_worker_proxy(0) is None


def test_release_thread_proxy_rotates_after_worker_assignment():
    configs = [
        normalize_proxy_config({"host": "1.1.1.1", "port": 8080}),
        normalize_proxy_config({"host": "2.2.2.2", "port": 8080}),
        normalize_proxy_config({"host": "3.3.3.3", "port": 8080}),
    ]
    set_session_proxies(configs)
    try:
        first = assign_worker_proxy(0)
        second = release_thread_proxy()
        third = release_thread_proxy()
        assert first["host"] == "1.1.1.1"
        assert second["host"] == "2.2.2.2"
        assert third["host"] == "3.3.3.3"
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


def test_restore_thread_socket_after_socks_session():
    import proxy_utils
    import socket

    class FakeSocksSocket:
        pass

    socket.socket = FakeSocksSocket
    proxy_utils._thread_local.socks_key = ("socks5", "1.1.1.1", 1080)
    try:
        proxy_utils._restore_thread_socket()
        assert socket.socket is proxy_utils._ORIGINAL_SOCKET_CLASS
        assert getattr(proxy_utils._thread_local, "socks_key", None) is None
    finally:
        socket.socket = proxy_utils._ORIGINAL_SOCKET_CLASS
        proxy_utils._thread_local.socks_key = None


def test_set_session_proxies_restores_socket():
    import proxy_utils
    import socket

    class FakeSocksSocket:
        pass

    socket.socket = FakeSocksSocket
    proxy_utils._thread_local.socks_key = ("socks5", "1.1.1.1", 1080)
    try:
        set_session_proxies([])
        assert socket.socket is proxy_utils._ORIGINAL_SOCKET_CLASS
    finally:
        socket.socket = proxy_utils._ORIGINAL_SOCKET_CLASS
        proxy_utils._thread_local.socks_key = None
