"""Server mode (RIVIU_SERVER_MODE=1): shared-password login, origin checks, cookie store, Google paste-back.

Synthetic password, key, cookies and OAuth client only; no network. Source mode
behaviour stays owned by the existing security tests (test_backend_review_fixes.py).
"""

import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from riviu import app as backend
from riviu import google_sheets_sync, server_context, server_mode
from riviu.api import google as google_api
from riviu.api import security
from riviu.platforms import threads_session as session


PUBLIC = "https://113-161-254-76.sslip.io"
PASSWORD = "synthetic-team-password"
SECRET = "synthetic-cookie-value-only"
# n=2**14 keeps the suite fast; verification reads the cost from the hash.
PASSWORD_HASH = server_mode.hash_password(PASSWORD, n=2 ** 14)


def server_env(tmp_path, **changes):
    key_file = tmp_path / "keys" / "secret.key"
    if not key_file.exists():
        key_file.parent.mkdir(parents=True)
        key_file.write_text(base64.b64encode(b"k" * 32).decode() + "\n")
    env = {
        "RIVIU_SERVER_MODE": "1",
        "RIVIU_PUBLIC_ORIGIN": PUBLIC,
        "RIVIU_PASSWORD_HASH": PASSWORD_HASH,
        "RIVIU_SECRET_KEY_FILE": str(key_file),
        "RIVIU_DATA_DIR": str(tmp_path / "srv"),
    }
    env.update(changes)
    return {key: value for key, value in env.items() if value is not None}


@pytest.fixture
def server(tmp_path, monkeypatch):
    config = server_mode.load_config(server_env(tmp_path))
    data_dir = tmp_path / "srv"
    data_dir.mkdir()
    monkeypatch.setattr(server_mode, "ACTIVE", config)
    monkeypatch.setattr(backend, "EXCEL_DIR", str(data_dir))
    monkeypatch.setattr(backend, "THREADS_SESSION", None)
    monkeypatch.setattr(security, "LOGIN_RATE_LIMITER", security.LoginRateLimiter())
    monkeypatch.setattr(security, "_SESSIONS", {})
    monkeypatch.setattr(google_api, "PENDING_GOOGLE_LOGIN", None)
    with TestClient(backend.app, base_url=PUBLIC, follow_redirects=False) as client:
        yield client


def login(client, password=PASSWORD, origin=PUBLIC):
    return client.post("/login", data={"password": password}, headers={"Origin": origin})


def cookie_header(response):
    return response.headers.get("set-cookie", "")


def test_server_mode_is_off_by_default_and_refuses_incomplete_config(tmp_path):
    assert server_mode.load_config({}) is None
    assert server_mode.load_config({"RIVIU_SERVER_MODE": "0", "RIVIU_PUBLIC_ORIGIN": PUBLIC}) is None
    for missing in ("RIVIU_PASSWORD_HASH", "RIVIU_PUBLIC_ORIGIN", "RIVIU_SECRET_KEY_FILE"):
        with pytest.raises(server_mode.ServerModeError, match=missing):
            server_mode.load_config(server_env(tmp_path, **{missing: None}))
    with pytest.raises(server_mode.ServerModeError, match="RIVIU_PUBLIC_ORIGIN"):
        server_mode.load_config(server_env(tmp_path, RIVIU_PUBLIC_ORIGIN="https://host.example/app"))
    with pytest.raises(server_mode.ServerModeError, match="ngoài thư mục dữ liệu"):
        server_mode.load_config(server_env(tmp_path, RIVIU_DATA_DIR=str(tmp_path)))


def test_entrypoint_refuses_to_start_without_password_hash(tmp_path, monkeypatch):
    for key, value in server_env(tmp_path, RIVIU_PASSWORD_HASH=None).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("RIVIU_PASSWORD_HASH_FILE", raising=False)
    monkeypatch.setattr(server_mode, "ACTIVE", None)
    from riviu import __main__ as entry

    monkeypatch.setattr(entry.uvicorn, "run", lambda *args, **kwargs: pytest.fail("server started"))
    with pytest.raises(SystemExit) as stopped:
        entry.main()
    assert stopped.value.code == 2


def test_password_hash_verifies_only_the_password_and_hides_it():
    assert PASSWORD not in PASSWORD_HASH
    assert server_mode.verify_password(PASSWORD, PASSWORD_HASH)
    assert not server_mode.verify_password(PASSWORD + "x", PASSWORD_HASH)
    assert not server_mode.verify_password(PASSWORD, "not-a-hash")
    assert server_mode.hash_password(PASSWORD, n=2 ** 14) != PASSWORD_HASH  # random salt


def test_unauthenticated_requests_redirect_or_get_401(server):
    # The public viewer and its allowlist are owned by test_server_viewer_admin.py; here the
    # admin side: HTML goes to the login (back to /admin after it), APIs answer 401.
    page = server.get("/admin", headers={"Accept": "text/html"})
    assert page.status_code == 303 and page.headers["location"] == "/login?next=/admin"
    assert server.get("/proxy-list").status_code == 401
    health = server.get("/healthz")
    assert health.status_code == 200 and health.text == "ok"
    login_page = server.get("/login")
    assert login_page.status_code == 200 and 'name="password"' in login_page.text
    assert "Nhập mật khẩu chung" not in login_page.text  # the subtitle was removed
    assert server.get("/static/styles.css").status_code == 200
    assert server.get("/logo.png").status_code == 200


def test_wrong_password_is_rejected_without_a_session(server):
    response = login(server, "wrong-password")
    assert response.status_code == 401
    assert "Sai mật khẩu" in response.text
    assert security.AUTH_COOKIE not in cookie_header(response)
    assert server.get("/proxy-list").status_code == 401


def test_correct_password_sets_secure_cookie_and_grants_access(server):
    response = login(server)
    assert response.status_code == 303 and response.headers["location"] == "/admin"
    header = cookie_header(response).lower()
    assert security.AUTH_COOKIE.lower() in header
    for flag in ("httponly", "secure", "samesite=strict", "max-age=604800", "path=/"):
        assert flag in header
    page = server.get("/admin")
    assert page.status_code == 200
    assert "Đăng xuất" in page.text and 'action="/logout"' in page.text
    assert 'id="guideButton"' in page.text  # the usage guide is in every mode
    assert server.get("/proxy-list").status_code == 200
    # Only a digest of the token is persisted.
    token = server.cookies.get(security.AUTH_COOKIE)
    stored = json.loads(open(server_mode.ACTIVE.sessions_path, encoding="utf-8").read())
    assert token not in json.dumps(stored) and len(stored["sessions"]) == 1


def test_logout_revokes_the_session_token(server):
    login(server)
    token = server.cookies.get(security.AUTH_COOKIE)
    response = server.post("/logout", headers={"Origin": PUBLIC})
    # Back to the public viewer, which needs no login.
    assert response.status_code == 303 and response.headers["location"] == "/"
    server.cookies.clear()
    replay = server.get("/proxy-list", headers={"Cookie": f"{security.AUTH_COOKIE}={token}"})
    assert replay.status_code == 401


def test_password_change_invalidates_existing_sessions(server, tmp_path, monkeypatch):
    login(server)
    assert server.get("/proxy-list").status_code == 200
    new_hash = server_mode.hash_password("another-synthetic-password", n=2 ** 14)
    monkeypatch.setattr(server_mode, "ACTIVE", server_mode.load_config(server_env(tmp_path, RIVIU_PASSWORD_HASH=new_hash)))
    assert server.get("/proxy-list").status_code == 401


def test_failed_logins_are_rate_limited_per_client(server):
    for _ in range(security.LOGIN_FAILURE_LIMIT - 1):
        assert login(server, "wrong-password").status_code == 401
    assert login(server, "wrong-password").status_code == 429
    # Locked out even with the right password until the window passes.
    blocked = login(server)
    assert blocked.status_code == 429 and security.AUTH_COOKIE not in cookie_header(blocked)


def test_rate_limit_window_expires():
    now = [0.0]
    limiter = security.LoginRateLimiter(limit=2, window=60, clock=lambda: now[0])
    limiter.record_failure("1.2.3.4")
    limiter.record_failure("1.2.3.4")
    assert limiter.blocked("1.2.3.4") and not limiter.blocked("5.6.7.8")
    now[0] = 61
    assert not limiter.blocked("1.2.3.4")


def test_foreign_host_and_origin_are_rejected_public_origin_accepted(server):
    assert server.get("/healthz", headers={"Host": "evil.example"}).status_code == 403
    assert server.get("/healthz", headers={"Host": "113-161-254-76.sslip.io:8443"}).status_code == 403
    assert login(server, origin="https://evil.example").status_code == 403
    assert login(server, origin="http://113-161-254-76.sslip.io").status_code == 403
    assert login(server).status_code == 303
    assert server.post("/select-file", headers={"Origin": "https://evil.example"}, json={"filename": "x.xlsx"}).status_code == 403
    cross_site = server.get("/list-files", headers={"Sec-Fetch-Site": "cross-site"})
    assert cross_site.status_code == 403
    # Loopback Host stays usable for the container health check.
    assert server.get("/healthz", headers={"Host": "127.0.0.1:1231"}).status_code == 200


def test_websocket_requires_the_public_origin(server):
    # Without a login the socket is the read-only viewer's (test_server_viewer_admin.py).
    with pytest.raises(WebSocketDisconnect):
        with server.websocket_connect("wss://113-161-254-76.sslip.io/ws", headers={"Origin": "https://evil.example"}):
            pass
    login(server)
    token = server.cookies.get(security.AUTH_COOKIE)
    with pytest.raises(WebSocketDisconnect):
        with server.websocket_connect("wss://113-161-254-76.sslip.io/ws", headers={
            "Origin": "https://evil.example", "Cookie": f"{security.AUTH_COOKIE}={token}",
        }):
            pass
    with server.websocket_connect("wss://113-161-254-76.sslip.io/ws", headers={
        "Origin": PUBLIC, "Cookie": f"{security.AUTH_COOKIE}={token}",
    }) as ws:
        assert ws.receive_json()["type"] == "session"


def synthetic_cookie():
    return {"name": "sessionid", "value": SECRET, "domain": ".threads.com", "path": "/", "secure": True,
            "httpOnly": True, "sameSite": "no_restriction", "expirationDate": time.time() + 3600}


def test_encrypted_cookie_store_round_trips_and_never_writes_plaintext(tmp_path):
    store = session.EncryptedFileStore(tmp_path, b"k" * 32)
    assert store.load() is None
    normalized = session.normalize_cookies([synthetic_cookie()])
    store.save(normalized)
    blob = store.path.read_bytes()
    assert SECRET.encode() not in blob and b"sessionid" not in blob
    assert store.load() == normalized
    for other in (session.EncryptedFileStore(tmp_path, b"x" * 32), session.EncryptedFileStore(tmp_path / "other", b"k" * 32)):
        other.path = store.path
        with pytest.raises(session.SessionError) as caught:
            other.load()
        assert caught.value.state == "storage_unavailable"
    store.delete()
    assert store.load() is None


def test_server_mode_threads_session_uses_the_encrypted_file_store(server):
    manager = server_context.threads_session_manager()
    assert isinstance(manager.store, session.EncryptedFileStore)


def write_oauth_client(base_dir):
    google_sheets_sync.save_oauth_client(str(base_dir), {"installed": {
        "client_id": "synthetic.apps.googleusercontent.com", "client_secret": "synthetic-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }})


def test_google_paste_back_validates_state_and_stores_token(server, monkeypatch):
    write_oauth_client(backend.EXCEL_DIR)
    exchanged = []

    def fake_fetch_token(flow, code):
        exchanged.append((code, flow.code_verifier, flow.redirect_uri))
        flow.oauth2session.token = {
            "access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "token_type": "Bearer",
            "expires_at": time.time() + 3600, "scope": google_sheets_sync.SCOPES,
        }

    monkeypatch.setattr(google_sheets_sync.Flow, "fetch_token", lambda self, **kw: fake_fetch_token(self, kw["code"]))
    login(server)
    started = server.post("/google-oauth-login", headers={"Origin": PUBLIC})
    assert started.status_code == 200 and started.json()["pasteBack"] is True
    query = parse_qs(urlsplit(started.json()["authUrl"]).query)
    redirect_uri, state = query["redirect_uri"][0], query["state"][0]
    assert urlsplit(redirect_uri).hostname == "localhost"
    assert query["code_challenge_method"] == ["S256"]

    forged = server.post("/google-oauth-complete", headers={"Origin": PUBLIC},
                         json={"url": f"{redirect_uri}?state=forged&code=synthetic-code"})
    assert forged.status_code == 400 and "state" in forged.json()["error"]
    assert exchanged == []

    done = server.post("/google-oauth-complete", headers={"Origin": PUBLIC},
                       json={"url": f"{redirect_uri}?state={state}&code=synthetic-code&scope=x"})
    assert done.status_code == 200 and done.json()["success"] is True
    [(code, verifier, used_redirect)] = exchanged
    assert code == "synthetic-code" and used_redirect == redirect_uri
    assert len(verifier) >= 43  # PKCE verifier from the start step, kept server side
    token = json.loads(open(google_sheets_sync.token_path(backend.EXCEL_DIR), encoding="utf-8").read())
    assert token["refresh_token"] == "synthetic-refresh"
    # The pending login is single-use.
    again = server.post("/google-oauth-complete", headers={"Origin": PUBLIC},
                        json={"url": f"{redirect_uri}?state={state}&code=synthetic-code"})
    assert again.status_code == 400


def test_paste_back_rejects_expired_and_denied_logins():
    pending = {"state": "s", "codeVerifier": "v", "redirectUri": "http://localhost:50000/", "expiresAt": time.time() + 60}
    assert google_sheets_sync.paste_back_code(pending, "http://localhost:50000/?state=s&code=c") == "c"
    assert google_sheets_sync.paste_back_code(pending, "c-only") == "c-only"
    with pytest.raises(ValueError, match="từ chối"):
        google_sheets_sync.paste_back_code(pending, "http://localhost:50000/?error=access_denied&state=s")
    with pytest.raises(ValueError, match="hết hạn"):
        google_sheets_sync.paste_back_code({**pending, "expiresAt": time.time() - 1}, "http://localhost:50000/?state=s&code=c")
