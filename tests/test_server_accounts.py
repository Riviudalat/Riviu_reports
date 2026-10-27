"""Server-mode accounts: per-person logins, admin vs viewer permissions, account CLI.

Synthetic passwords only; no network. The shared-password login itself stays owned by
test_server_mode.py.
"""

import pytest

from riviu import scan_state, server_mode, server_users
from riviu.api import security
from tests.test_server_mode import PUBLIC, login, server  # noqa: F401  (fixture)

ADMIN_PASSWORD = "synthetic-admin-password"
VIEWER_PASSWORD = "synthetic-viewer-password"


@pytest.fixture
def accounts(server):  # noqa: F811
    store = server_users.active_store()
    store.add("Quan.Ly", ADMIN_PASSWORD, "admin")
    store.add("nhanvien", VIEWER_PASSWORD, "viewer")
    return store


def account_login(client, username, password):
    return client.post("/login", data={"username": username, "password": password}, headers={"Origin": PUBLIC})


def test_accounts_replace_the_shared_password_and_report_the_role(server, accounts):  # noqa: F811
    page = server.get("/login")
    assert 'name="username"' in page.text
    refused = login(server)  # the shared password alone no longer logs in
    assert refused.status_code == 401 and "Sai tên đăng nhập hoặc mật khẩu" in refused.text
    assert account_login(server, "nhanvien", ADMIN_PASSWORD).status_code == 401
    assert account_login(server, "nobody", VIEWER_PASSWORD).status_code == 401

    assert account_login(server, " QUAN.LY ", ADMIN_PASSWORD).status_code == 303  # names are case-insensitive
    assert server.get("/auth-status").json() == {
        "authenticated": True, "user": {"username": "quan.ly", "role": "admin"},
    }


def test_shared_password_works_only_until_the_first_account_exists(server):  # noqa: F811
    assert 'name="username"' not in server.get("/login").text
    assert login(server).status_code == 303
    assert server.get("/auth-status").json()["user"] == {"username": "admin", "role": "admin"}
    server_users.active_store().add("quanly", ADMIN_PASSWORD, "admin")
    # The open shared-password session ends with it.
    assert server.get("/auth-status").json() == {"authenticated": False, "user": None}
    assert server.get("/list-files").status_code == 401


def test_viewer_can_browse_and_download_partner_reports_but_change_nothing(server, accounts):  # noqa: F811
    account_login(server, "nhanvien", VIEWER_PASSWORD)
    for path in ("/list-files", "/source-preferences", "/scrape-history", "/api/version"):
        assert server.get(path).status_code == 200, path
    # Read routes pass the gate (their own validation decides the rest).
    assert server.get("/report-partners").status_code != 403
    assert server.post("/export-report", json={}).status_code != 403
    forbidden = [
        ("POST", "/upload-excel"), ("POST", "/sync-google-sheet"), ("POST", "/push-google-sheet"),
        ("POST", "/source-preferences"), ("POST", "/select-file"), ("GET", "/download-excel"),
        ("GET", "/proxy-list"), ("POST", "/proxy-test"), ("GET", "/google-oauth-status"),
        ("GET", "/threads-session/status"), ("DELETE", "/tiktok-session"),
    ]
    for method, path in forbidden:
        response = server.request(method, path)
        assert response.status_code == 403, (method, path)
        assert response.json()["error"] == security.VIEWER_FORBIDDEN

    account_login(server, "quan.ly", ADMIN_PASSWORD)
    assert server.get("/proxy-list").status_code == 200


def test_viewer_cannot_start_or_cancel_a_scan_over_the_websocket(server, accounts, monkeypatch):  # noqa: F811
    account_login(server, "nhanvien", VIEWER_PASSWORD)
    token = server.cookies.get(security.AUTH_COOKIE)
    started = []
    monkeypatch.setattr("riviu.scan_service.prepare_scan_start", lambda payload: started.append(payload))
    headers = {"Origin": PUBLIC, "Cookie": f"{security.AUTH_COOKIE}={token}"}
    with server.websocket_connect("wss://113-161-254-76.sslip.io/ws", headers=headers) as ws:
        assert ws.receive_json()["type"] == "session"  # viewers still watch progress
        for action in ("start", "cancel"):
            ws.send_json({"action": action, "platform": "tiktok"})
            assert ws.receive_json() == {"type": "log", "message": security.VIEWER_FORBIDDEN, "level": "ERROR"}
            assert ws.receive_json()["data"]["phase"] == "failed"
    assert started == [] and scan_state.SCRAPE_TASK is None


def test_password_role_and_account_changes_apply_to_open_sessions(server, accounts):  # noqa: F811
    account_login(server, "nhanvien", VIEWER_PASSWORD)
    assert server.get("/proxy-list").status_code == 403
    accounts.set_role("nhanvien", "admin")  # promotion applies without logging in again
    assert server.get("/proxy-list").status_code == 200
    accounts.set_password("nhanvien", "a-brand-new-password")  # a new password ends the session
    assert server.get("/auth-status").json()["authenticated"] is False

    account_login(server, "quan.ly", ADMIN_PASSWORD)
    accounts.remove("nhanvien")
    accounts.add("viewer2", VIEWER_PASSWORD, "viewer")
    assert server.get("/auth-status").json()["user"]["role"] == "admin"
    with pytest.raises(server_users.UserError, match="ít nhất một tài khoản admin"):
        accounts.set_role("quan.ly", "viewer")
    with pytest.raises(server_users.UserError, match="ít nhất một tài khoản admin"):
        accounts.remove("quan.ly")


def test_account_cli_manages_users_in_the_data_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RIVIU_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server_mode, "_read_new_password", lambda: "cli-password-123")
    assert server_mode.main(["add-user", "Admin1", "admin"]) == 0
    assert server_mode.main(["add-user", "nv", "viewer"]) == 0
    assert server_mode.main(["add-user", "nv", "viewer"]) == 1  # duplicate
    assert server_mode.main(["add-user", "x", "owner"]) == 1  # bad name and role
    assert server_mode.main(["set-role", "admin1", "viewer"]) == 1  # last admin
    capsys.readouterr()
    assert server_mode.main(["list-users"]) == 0
    assert capsys.readouterr().out.split() == ["admin1", "admin", "nv", "viewer"]
    store = server_users.store_for(str(tmp_path))
    assert store.verify("nv", "cli-password-123")["role"] == "viewer"
    assert "cli-password-123" not in (tmp_path / "data" / "server_users.json").read_text(encoding="utf-8")
    assert server_mode.main(["remove-user", "nv"]) == 0 and store.get("nv") is None
    assert server_mode.main(["bogus"]) == 2
