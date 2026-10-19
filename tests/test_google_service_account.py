"""Google service account for Sheets: upload validation, encryption at rest in server mode,
precedence over OAuth, the status field, and the 403/404 message that names the email.

Synthetic keys only (a throwaway RSA key is generated per test run); no network.
"""

import base64
import json

import httplib2
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from google.oauth2 import service_account
from googleapiclient.errors import HttpError

from riviu import app as backend
from riviu import google_sheets_sync as sync
from riviu import server_mode
from riviu.api import google as google_api


EMAIL = "riviu-reports@synthetic-project.iam.gserviceaccount.com"
FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nSYNTHETIC-NOT-A-KEY\n-----END PRIVATE KEY-----\n"


def key_json(private_key=FAKE_KEY, **changes):
    payload = {
        "type": "service_account", "project_id": "synthetic-project", "private_key_id": "synthetic-id",
        "private_key": private_key, "client_email": EMAIL, "client_id": "123",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    }
    payload.update(changes)
    return {key: value for key, value in payload.items() if value is not None}


@pytest.fixture(scope="module")
def real_format_key():
    """A freshly generated RSA key, so google-auth can build credentials; never a real account."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode("ascii")


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mode, "ACTIVE", None)
    monkeypatch.setattr(backend, "EXCEL_DIR", str(tmp_path))
    with TestClient(backend.app, base_url="http://127.0.0.1:1231") as client:
        assert client.get("/").status_code == 200
        yield client


def server_config(tmp_path):
    key_file = tmp_path / "keys" / "secret.key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(base64.b64encode(b"s" * 32).decode())
    data_dir = tmp_path / "srv"
    data_dir.mkdir(exist_ok=True)
    return server_mode.load_config({
        "RIVIU_SERVER_MODE": "1", "RIVIU_PUBLIC_ORIGIN": "https://113-161-254-76.sslip.io",
        "RIVIU_PASSWORD_HASH": server_mode.hash_password("synthetic", n=2 ** 14),
        "RIVIU_SECRET_KEY_FILE": str(key_file), "RIVIU_DATA_DIR": str(data_dir),
    }), data_dir


def upload(client, payload, name="key.json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return client.post("/google-service-account", files={"file": (name, body, "application/json")})


@pytest.mark.parametrize("payload, message", [
    (key_json(type="authorized_user"), "service_account"),
    (key_json(client_email=None), "client_email"),
    (key_json(client_email="not an email"), "client_email"),
    (key_json(private_key=None), "private_key"),
    (key_json(token_uri="https://attacker.example/token"), "googleapis.com"),
    ({"installed": {"client_id": "x"}}, "service_account"),
])
def test_upload_rejects_files_that_are_not_service_account_keys(desktop, tmp_path, payload, message):
    response = upload(desktop, payload)
    assert response.status_code == 400 and message in response.json()["error"]
    assert "SYNTHETIC-NOT-A-KEY" not in response.text
    assert not (tmp_path / "data" / sync.SERVICE_ACCOUNT_FILENAME).exists()


def test_upload_rejects_non_json_and_oversized_files(desktop):
    assert upload(desktop, b"not json").status_code == 400
    assert upload(desktop, key_json(), name="key.txt").status_code == 400
    huge = json.dumps(key_json(padding="x" * (sync.SERVICE_ACCOUNT_MAX_BYTES + 1))).encode()
    assert upload(desktop, huge).status_code == 400


def test_desktop_upload_stores_key_shows_email_and_status_then_removes_it(desktop, tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "try_load_credentials", lambda _base: None)
    before = desktop.get("/google-oauth-status").json()
    assert before["activeCredential"] == "none" and before["serviceAccount"]["configured"] is False

    response = upload(desktop, key_json())
    assert response.status_code == 200 and response.json() == {"success": True, "email": EMAIL}
    stored = json.loads((tmp_path / "data" / sync.SERVICE_ACCOUNT_FILENAME).read_text(encoding="utf-8"))
    # Desktop keeps it like the OAuth client file; only the fields google-auth needs.
    assert stored["client_email"] == EMAIL and "auth_provider_x509_cert_url" not in stored

    status = desktop.get("/google-oauth-status").json()
    assert status["activeCredential"] == "service_account" and status["ready"] is True
    assert status["serviceAccount"] == {"configured": True, "email": EMAIL, "error": ""}
    assert "SYNTHETIC-NOT-A-KEY" not in json.dumps(status)

    assert desktop.delete("/google-service-account").json() == {"success": True}
    assert not (tmp_path / "data" / sync.SERVICE_ACCOUNT_FILENAME).exists()
    assert desktop.get("/google-oauth-status").json()["activeCredential"] == "none"


def test_server_mode_encrypts_the_key_at_rest_and_binds_it_to_the_data_root(tmp_path, monkeypatch):
    config, data_dir = server_config(tmp_path)
    monkeypatch.setattr(server_mode, "ACTIVE", config)
    assert sync.save_service_account(str(data_dir), key_json()) == EMAIL
    raw = (data_dir / "data" / sync.SERVICE_ACCOUNT_FILENAME).read_text(encoding="utf-8")
    assert json.loads(raw)["format"] == sync.SEALED_FORMAT
    assert "SYNTHETIC-NOT-A-KEY" not in raw and EMAIL not in raw and "PRIVATE KEY" not in raw
    assert sync.load_service_account_info(str(data_dir))["client_email"] == EMAIL

    other_root = tmp_path / "copied"
    (other_root / "data").mkdir(parents=True)
    (other_root / "data" / sync.SERVICE_ACCOUNT_FILENAME).write_text(raw, encoding="utf-8")
    with pytest.raises(sync.ServiceAccountError):
        sync.load_service_account_info(str(other_root))
    # The sealed file cannot be read without the server key either.
    monkeypatch.setattr(server_mode, "ACTIVE", None)
    assert sync.service_account_status(str(data_dir))["error"]
    assert sync.oauth_status(str(data_dir))["activeCredential"] == "none"


def test_service_account_wins_over_oauth_and_oauth_is_unchanged_without_it(tmp_path, monkeypatch, real_format_key):
    monkeypatch.setattr(server_mode, "ACTIVE", None)
    oauth = type("OAuth", (), {"valid": True})()
    monkeypatch.setattr(sync, "try_load_credentials", lambda _base: oauth)
    assert sync.google_credentials(str(tmp_path)) is oauth
    assert sync.load_credentials(str(tmp_path)) is oauth

    sync.save_service_account(str(tmp_path), key_json(private_key=real_format_key))
    creds = sync.load_credentials(str(tmp_path))
    assert isinstance(creds, service_account.Credentials)
    assert creds.service_account_email == EMAIL
    assert creds.scopes == ["https://www.googleapis.com/auth/spreadsheets"]

    sync.delete_service_account(str(tmp_path))
    monkeypatch.setattr(sync, "try_load_credentials", lambda _base: None)
    with pytest.raises(sync.GoogleLoginRequired):
        sync.load_credentials(str(tmp_path))


def http_error(status):
    return HttpError(httplib2.Response({"status": status}), b'{"error": {"message": "synthetic"}}')


class DeniedService:
    def __init__(self, status):
        self.status = status

    def spreadsheets(self):
        return self

    def get(self, **_kwargs):
        return self

    def execute(self):
        raise http_error(self.status)


@pytest.mark.parametrize("status", [403, 404])
def test_push_without_sheet_access_names_the_service_account_email(tmp_path, monkeypatch, status):
    monkeypatch.setattr(server_mode, "ACTIVE", None)
    sync.save_service_account(str(tmp_path), key_json())
    monkeypatch.setattr(sync, "sheets_service", lambda _base: DeniedService(status))
    with pytest.raises(sync.ServiceAccountAccessError) as caught:
        sync.push_rows_to_new_sheet(str(tmp_path), "sheet-id", [["Stt"]])
    message = str(caught.value)
    assert EMAIL in message and "Người chỉnh sửa" in message and str(status) in message


def test_oauth_push_errors_are_not_rewritten(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mode, "ACTIVE", None)
    monkeypatch.setattr(sync, "sheets_service", lambda _base: DeniedService(403))
    with pytest.raises(HttpError):
        sync.push_rows_to_new_sheet(str(tmp_path), "sheet-id", [["Stt"]])


def test_sync_of_a_private_sheet_the_service_account_cannot_open_names_the_email(tmp_path, monkeypatch, real_format_key):
    monkeypatch.setattr(server_mode, "ACTIVE", None)
    sync.save_service_account(str(tmp_path), key_json(private_key=real_format_key))
    monkeypatch.setattr(sync, "build", lambda *_args, **_kwargs: DeniedService(403))

    def private_sheet(_url, _destination):
        raise RuntimeError("HTTP Error 401: Unauthorized")

    monkeypatch.setattr(google_api, "download_google_sheet", private_sheet)
    with pytest.raises(sync.ServiceAccountAccessError) as caught:
        google_api.sync_download_google_sheet(str(tmp_path), "https://docs.google.com/spreadsheets/d/x/edit", "x", str(tmp_path / "out.xlsx"))
    assert EMAIL in str(caught.value)


def test_drawer_shows_the_service_account_email_and_the_share_instruction():
    from test_ui_review_fixes import run_js

    run_js("""(async()=>{
      fetch=async()=>({ok:true,json:async()=>({configured:false,authorized:false,valid:false,ready:true,
        activeCredential:'service_account',serviceAccount:{configured:true,email:'robot@demo.iam.gserviceaccount.com',error:''}})});
      setGooglePushState=()=>{};
      await refreshGoogleOauthStatus();
      assert.equal(googleOAuthAuthorized,true);
      assert.equal(el('googleServiceAccountBox').hidden,false);
      assert.equal(el('googleServiceAccountEmail').textContent,'robot@demo.iam.gserviceaccount.com');
      assert.equal(el('googleServiceAccountBtn').style.display,'none');
      let copied=''; navigator={clipboard:{writeText:async text=>{copied=text;}}}; notify=()=>{};
      await copyGoogleServiceAccountEmail(); assert.equal(copied,'robot@demo.iam.gserviceaccount.com');
      fetch=async()=>({ok:true,json:async()=>({configured:true,valid:false,serviceAccount:{configured:false,email:'',error:''},activeCredential:'none'})});
      await refreshGoogleOauthStatus();
      assert.equal(googleOAuthAuthorized,false); assert.equal(el('googleServiceAccountBox').hidden,true);
    })()""")


def test_index_page_has_the_upload_button_share_instruction_and_remove_action(desktop):
    html = desktop.get("/").text
    assert "Nạp tài khoản dịch vụ" in html and "Gỡ tài khoản dịch vụ" in html
    assert "Chia sẻ Google Sheet cho email này với quyền <b>Người chỉnh sửa</b>" in html
