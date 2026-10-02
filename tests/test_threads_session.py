"""Synthetic-only tests: no real credentials, profiles, or external network."""
import asyncio
import copy
import json
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

import pytest
import threads_session as session

SECRET = "synthetic-session-test-only"


def cookie(**changes):
    result = {"name": "sessionid", "value": SECRET, "domain": ".threads.com", "path": "/", "secure": True,
              "httpOnly": True, "sameSite": "no_restriction", "expirationDate": time.time() + 3600}
    result.update(changes)
    return result


def viewer_html(viewer):
    data = {"require": [["BarcelonaSharedData", [], {"viewer": viewer}, 17]]}
    return '<script type="application/json">' + json.dumps(data) + '</script>'


def run(coroutine):
    return asyncio.run(coroutine)


class Vault:
    def __init__(self, cookies=None):
        self.cookies = copy.deepcopy(cookies)
        self.loads = self.saves = 0
        self.fail = False

    def load(self):
        self.loads += 1
        if self.fail:
            raise RuntimeError(SECRET)
        return copy.deepcopy(self.cookies)

    def save(self, cookies):
        if self.fail:
            raise RuntimeError(SECRET)
        self.saves += 1
        self.cookies = copy.deepcopy(cookies)

    def delete(self):
        if self.fail:
            raise RuntimeError(SECRET)
        self.cookies = None


@pytest.mark.parametrize("payload", [lambda c: [c], lambda c: {"cookies": [c], "metadata": "ignored"},
                                     lambda c: json.dumps([c]), lambda c: json.dumps([c]).encode()])
def test_export_formats_and_no_input_mutation(payload):
    original = cookie()
    before = copy.deepcopy(original)
    normalized = session.normalize_cookies(payload(original))
    assert original == before
    assert normalized[0]["sameSite"] == "None" and normalized[0]["secure"] is True
    assert normalized[0]["expires"] == original["expirationDate"] and normalized[0]["hostOnly"] is False
    assert set(normalized[0]) == {"name", "value", "domain", "path", "secure", "httpOnly", "sameSite", "hostOnly", "expires"}


@pytest.mark.parametrize("as_file", [False, True])
def test_cookie_editor_full_export_imports_without_changing_values(as_file):
    expiry = time.time() + 3600
    entries = [
        {"domain": ".threads.com", "expirationDate": expiry, "hostOnly": False, "httpOnly": False,
         "name": "csrftoken", "path": "/", "sameSite": None, "secure": True, "session": False, "storeId": None, "value": "synthetic-csrf"},
        {"domain": ".threads.com", "hostOnly": False, "httpOnly": True,
         "name": "rur", "path": "/", "sameSite": "lax", "secure": True, "session": True, "storeId": None,
         "value": '"HIL\\054123\\054999:synthetic-routing"'},
        {"domain": ".threads.com", "expirationDate": expiry, "hostOnly": False, "httpOnly": True,
         "name": "sessionid", "path": "/", "sameSite": None, "secure": True, "session": False, "storeId": None,
         "value": "123%3Asynthetic-session%3A20%3Atest-only"},
    ]
    for name, same_site in [("ps_n", "no_restriction"), ("ps_l", "lax"), ("ds_user_id", "no_restriction"), ("ig_did", "no_restriction"), ("mid", None)]:
        entries.append({"domain": ".threads.com", "expirationDate": expiry, "hostOnly": False, "httpOnly": True,
                        "name": name, "path": "/", "sameSite": same_site, "secure": True, "session": False,
                        "storeId": None, "value": "synthetic-" + name})
    export = json.dumps(entries, indent=4)
    normalized = session.normalize_cookies(export.encode("utf-8-sig") if as_file else export)
    assert len(normalized) == 8
    assert {c["name"]: c["value"] for c in normalized} == {c["name"]: c["value"] for c in entries}
    assert all("storeId" not in c and "session" not in c for c in normalized)
    assert "expires" not in next(c for c in normalized if c["name"] == "rur")
    assert next(c for c in normalized if c["name"] == "ps_n")["sameSite"] == "None"
    playwright_cookies = session.cookies_to_playwright(normalized)
    assert len(playwright_cookies) == 8
    assert all(c["domain"] == ".threads.com" for c in playwright_cookies)


@pytest.mark.parametrize("domain", ["threads.com", "www.threads.com", ".threads.com", "threads.net", "www.threads.net", ".threads.net"])
def test_exact_allowed_domains(domain):
    assert session.normalize_cookies([cookie(domain=domain)])[0]["domain"] == domain


@pytest.mark.parametrize("domain", ["evilthreads.com", "threads.com.evil.test", ".facebook.com", ".com", "sub.threads.com", "threads.com."])
def test_unrelated_domains_filtered_never_retargeted(domain):
    assert len(session.normalize_cookies([cookie(), cookie(domain=domain)])) == 1
    with pytest.raises(session.SessionError) as rejected:
        session.normalize_cookies([cookie(domain=domain)])
    assert rejected.value.state == "missing_session"


@pytest.mark.parametrize("changes", [
    {"name": "bad;name"}, {"name": "méta"}, {"name": ""}, {"name": 1}, {"value": None},
    {"value": "bad\nvalue"}, {"value": "bad;value"}, {"value": "bad\x00value"}, {"value": "bad\x85value"},
    {"value": "x" * 4097}, {"path": "not-a-path"}, {"path": "/bad\npath"}, {"path": 1},
    {"secure": 1}, {"httpOnly": "true"}, {"hostOnly": None}, {"hostOnly": True},
    {"sameSite": "unrecognized"}, {"sameSite": 2}, {"expires": True}, {"expires": "123"},
    {"expires": float("inf")}, {"domain": None},
])
def test_invalid_cookie_types_and_controls_safe_error(changes):
    with pytest.raises(session.SessionError) as caught:
        session.normalize_cookies([cookie(**changes)])
    assert SECRET not in str(caught.value) and caught.value.__cause__ is None


@pytest.mark.parametrize("payload", [None, 1, True, {}, {"cookies": {}}, [None], "not-json", b"\xff"])
def test_invalid_payloads(payload):
    with pytest.raises(session.SessionError):
        session.normalize_cookies(payload)


def test_import_size_and_count_boundaries():
    with pytest.raises(session.SessionError) as caught:
        session.normalize_cookies(b" " * (session.MAX_IMPORT_BYTES + 1))
    assert caught.value.state == "too_large"
    with pytest.raises(session.SessionError):
        session.normalize_cookies([cookie()] * (session.MAX_COOKIES + 1))


def test_duplicate_conflict_rejected_identical_deduplicated():
    first = cookie()
    assert len(session.normalize_cookies([first, copy.deepcopy(first)])) == 1
    for second in (dict(first, value="other-synthetic"), dict(first, domain="threads.com", hostOnly=True)):
        with pytest.raises(session.SessionError):
            session.normalize_cookies([first, second])


def test_expiration_and_ancillary_cookie():
    normalized = session.normalize_cookies([cookie(expires=None), cookie(name="ancillary", expires=1)])
    assert not session._expired(normalized) and len(session.cookies_to_playwright(normalized)) == 1
    with pytest.raises(session.SessionError) as caught:
        session.normalize_cookies([cookie(expires=1)])
    assert caught.value.state == "expired"
    assert "expires" not in session.normalize_cookies([cookie(expires=-1)])[0]
    assert session.normalize_cookies([cookie(sameSite=None)])[0]["sameSite"] == "Lax"
    assert session.normalize_cookies([cookie(secure=False)])[0]["secure"] is True


def test_playwright_host_only_and_paths():
    converted = session.cookies_to_playwright([cookie(domain="www.threads.com", hostOnly=True)])
    assert converted[0]["url"] == "https://www.threads.com/"
    assert not {"domain", "path", "hostOnly"}.intersection(converted[0])
    converted = session.cookies_to_playwright([cookie(domain="www.threads.com", hostOnly=True, path="/profile")])
    assert converted[0]["domain"] == "www.threads.com" and converted[0]["path"] == "/profile"
    assert session.cookies_to_playwright([cookie()])[0]["domain"] == ".threads.com"


def test_cookie_jar_enforces_host_only_and_secure():
    jar = session.cookie_jar([cookie(domain="threads.com", hostOnly=True, expires=None)])
    stored = next(iter(jar))
    assert stored.secure and not stored.domain_specified and stored.discard and stored.has_nonstandard_attr("HttpOnly")
    for url, expected in [("https://threads.com/", True), ("https://www.threads.com/", False),
                          ("http://threads.com/", False), ("https://threads.com.evil.test/", False)]:
        request = urllib.request.Request(url)
        jar.add_cookie_header(request)
        assert bool(request.get_header("Cookie")) is expected


@pytest.mark.parametrize("url", ["https://www.threads.com/", "https://threads.net/x", "https://www.threads.net:443/"])
def test_allowlisted_urls(url):
    assert session.allowed_session_url(url)


@pytest.mark.parametrize("url", ["http://www.threads.com/", "https://threads.com.evil.test/", "https://sub.threads.com/",
                                  "https://user@threads.com/", "https://threads.com:444/", "https://threads.com.:443/",
                                  "https://threads.com\n/", "https://threads.com\\evil.test/", None])
def test_rejected_urls(url):
    assert not session.allowed_session_url(url)


@pytest.mark.parametrize("viewer,state", [({"id": "123"}, "valid"), ({"pk": 456}, "valid"), (None, "invalid"),
                                         ({"id": True}, "unknown"), ({"id": "0"}, "unknown"),
                                         ({"id": "some-name"}, "unknown"), ({"isLoggedIn": True}, "unknown")])
def test_typed_auth_viewer(viewer, state):
    assert session.auth_evidence(viewer_html(viewer), "https://www.threads.com/") == state


def test_auth_no_broad_flags_or_post_author():
    html = '<script type="application/json">' + json.dumps({"isLoggedIn": True, "viewer": {"id": "123"}, "post": {"user": {"pk": "123"}}}) + '</script>'
    assert session.auth_evidence(html, "https://www.threads.com/@someone/post/abc") == "unknown"
    malformed = '<script type="application/json">' + json.dumps(["BarcelonaSharedData", {"not": "deps"}, {"viewer": {"id": "123"}}]) + '</script>'
    assert session.auth_evidence(malformed, "https://www.threads.com/") == "unknown"
    assert session.auth_evidence("authenticated sessionid", "https://www.threads.com/") == "unknown"


def test_auth_url_overrides_root_evidence_and_conflicts():
    valid = viewer_html({"id": "123"})
    assert session.auth_evidence(valid, "https://www.threads.com/accounts/login/") == "invalid"
    assert session.auth_evidence(valid, "https://www.threads.com/checkpoint/123/") == "checkpoint"
    assert session.auth_evidence(valid, "https://www.threads.com/challenge/") == "checkpoint"
    assert session.auth_evidence(valid, "https://evil.test/checkpoint/") == "unknown"
    assert session.auth_evidence(valid + viewer_html(None), "https://www.threads.com/") == "unknown"


def test_cached_status_snapshot_copy_and_clear(tmp_path):
    vault = Vault([cookie()])
    controller = session.ThreadsSession(tmp_path, vault)
    status = controller.status()
    assert status["configured"] and status["state"] == "unchecked" and status["count"] == 1
    assert SECRET not in json.dumps(status)
    assert controller.status()["generation"] == status["generation"] and vault.loads == 1
    snapshot = controller.snapshot(status["generation"])
    assert isinstance(snapshot, tuple)
    snapshot[0]["value"] = "mutated"
    assert controller.snapshot()[0]["value"] == SECRET
    cleared = controller.clear()
    assert not cleared["configured"] and cleared["generation"] != status["generation"]
    with pytest.raises(session.SessionError) as caught:
        controller.snapshot(status["generation"])
    assert caught.value.state == "stale"


def test_expired_load_status_safe(tmp_path):
    controller = session.ThreadsSession(tmp_path, Vault([cookie(expires=1)]))
    assert controller.status()["state"] == "expired" and controller.status()["configured"]
    with pytest.raises(session.SessionError) as caught:
        controller.snapshot()
    assert caught.value.state == "expired"


def test_unavailable_store_no_secret_exception(tmp_path):
    vault = Vault([cookie()])
    vault.fail = True
    controller = session.ThreadsSession(tmp_path, vault)
    assert controller.status()["state"] == "storage_unavailable"
    with pytest.raises(session.SessionError) as caught:
        controller.snapshot()
    assert SECRET not in str(caught.value)


def fake_result(state):
    return {"state": state, "checkedAt": "2026-01-01T00:00:00+00:00", "route": "direct", "message": session._MESSAGES[state]}


@pytest.mark.parametrize("state", ["invalid", "expired", "checkpoint", "unknown"])
def test_failed_candidate_preserves_saved_session(tmp_path, monkeypatch, state):
    vault = Vault([cookie()])
    controller = session.ThreadsSession(tmp_path, vault)
    before = controller.status()
    async def verify(*args, **kwargs):
        return fake_result(state)
    monkeypatch.setattr(session, "verify_cookies", verify)
    result = run(controller.import_cookie([cookie(value="candidate-synthetic")]))
    assert result == {"success": False, "status": before, "check": fake_result(state)}
    assert controller.snapshot()[0]["value"] == SECRET and vault.saves == 0


def test_successful_import_and_verify_contract(tmp_path, monkeypatch):
    vault = Vault()
    controller = session.ThreadsSession(tmp_path, vault)
    before = controller.status()
    async def verify(*args, **kwargs):
        return fake_result("valid")
    monkeypatch.setattr(session, "verify_cookies", verify)
    imported = run(controller.import_cookie([cookie()]))
    assert imported["success"] and imported["status"]["state"] == "valid"
    assert imported["status"]["generation"] != before["generation"]
    verified = run(controller.verify())
    assert verified["success"] and verified["status"]["generation"] == imported["status"]["generation"]
    assert vault.saves == 1


def test_storage_failure_preserves_previous_session(tmp_path, monkeypatch):
    vault = Vault([cookie()])
    controller = session.ThreadsSession(tmp_path, vault)
    before = controller.status()
    async def verify(*args, **kwargs):
        return fake_result("valid")
    monkeypatch.setattr(session, "verify_cookies", verify)
    vault.fail = True
    with pytest.raises(session.SessionError) as caught:
        run(controller.import_cookie([cookie(value="candidate-synthetic")]))
    assert caught.value.state == "storage_unavailable" and SECRET not in str(caught.value)
    assert controller.status() == before and controller.snapshot()[0]["value"] == SECRET
    with pytest.raises(session.SessionError):
        controller.clear()
    assert controller.status() == before


def test_stale_verification_after_clear(tmp_path, monkeypatch):
    controller = session.ThreadsSession(tmp_path, Vault([cookie()]))
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def verify(*args, **kwargs):
            started.set()
            await release.wait()
            return fake_result("valid")
        monkeypatch.setattr(session, "verify_cookies", verify)
        task = asyncio.create_task(controller.verify())
        await started.wait()
        controller.clear()
        release.set()
        with pytest.raises(session.SessionError) as caught:
            await task
        assert caught.value.state == "stale" and controller.status()["state"] == "none"
    run(scenario())


def test_newest_verification_wins_same_generation(tmp_path, monkeypatch):
    controller = session.ThreadsSession(tmp_path, Vault([cookie()]))
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def verify(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                started.set()
                await release.wait()
                return fake_result("invalid")
            return fake_result("valid")
        monkeypatch.setattr(session, "verify_cookies", verify)
        first = asyncio.create_task(controller.verify())
        await started.wait()
        assert (await controller.verify())["success"]
        release.set()
        with pytest.raises(session.SessionError):
            await first
        assert controller.status()["state"] == "valid"
    run(scenario())


def test_cancel_during_commit_settles_disk_and_cache(tmp_path, monkeypatch):
    vault = Vault()
    started, release = threading.Event(), threading.Event()
    original_save = vault.save
    def save(cookies):
        started.set()
        assert release.wait(timeout=5)
        original_save(cookies)
    vault.save = save
    controller = session.ThreadsSession(tmp_path, vault)
    async def verify(*args, **kwargs):
        return fake_result("valid")
    monkeypatch.setattr(session, "verify_cookies", verify)
    async def scenario():
        task = asyncio.create_task(controller.import_cookie([cookie()]))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert controller.snapshot()[0]["value"] == vault.cookies[0]["value"] == SECRET
        assert controller.status()["state"] == "valid"
    run(scenario())


class FakeBrowser:
    def __init__(self, content=None, url="https://www.threads.com/", raises=False, blocks=False):
        self.html = viewer_html({"id": "123"}) if content is None else content
        self.url, self.raises, self.blocks = url, raises, blocks
        self.closed = []
        self.launch_options = self.context_options = self.cookies = self.started = None

    def factory(self):
        owner = self
        class Manager:
            async def start(self):
                return self
            @property
            def chromium(self):
                return self
            async def launch(self, **kwargs):
                owner.launch_options = kwargs
                return Browser()
            async def stop(self):
                owner.closed.append("manager")
        class Browser:
            async def new_context(self, **kwargs):
                owner.context_options = kwargs
                return Context()
            async def close(self):
                owner.closed.append("browser")
        class Context:
            async def route(self, pattern, handler):
                assert pattern == "**/*"
                owner.route_handler = handler
            async def add_cookies(self, cookies):
                owner.cookies = cookies
            async def new_page(self):
                return Page(self)
            async def close(self):
                owner.closed.append("context")
        class Page:
            url = owner.url
            def __init__(self, context):
                self.context = context
                self.main_frame = types.SimpleNamespace(page=self)
            async def goto(self, url, **kwargs):
                assert url == "https://www.threads.com/"
                if owner.started is not None:
                    owner.started.set()
                if owner.raises:
                    raise RuntimeError(SECRET)
                if owner.blocks:
                    await asyncio.Event().wait()
            async def content(self):
                return owner.html
        return Manager()


@pytest.mark.parametrize("content,url,state", [(viewer_html({"pk": "123"}), "https://www.threads.com/", "valid"),
                                               (viewer_html(None), "https://www.threads.com/", "invalid"),
                                               ("", "https://www.threads.com/checkpoint/", "checkpoint"),
                                               ("HTTP 200", "https://www.threads.com/", "unknown")])
def test_browser_verification_only_typed_evidence_and_cleanup(monkeypatch, content, url, state):
    fake = FakeBrowser(content, url)
    monkeypatch.setattr(session, "_playwright_factory", fake.factory)
    result = run(session.verify_cookies([cookie()]))
    assert result["state"] == state and result["route"] == "direct"
    assert fake.closed == ["context", "browser", "manager"]
    assert all("hostOnly" not in item for item in fake.cookies) and SECRET not in json.dumps(result)


def test_browser_failure_and_timeout_cleanup(monkeypatch):
    for fake in (FakeBrowser(raises=True), FakeBrowser(blocks=True)):
        monkeypatch.setattr(session, "_playwright_factory", fake.factory)
        monkeypatch.setattr(session, "VERIFY_TIMEOUT_SECONDS", 0.03)
        assert run(session.verify_cookies([cookie()]))["state"] == "unknown"
        assert fake.closed == ["context", "browser", "manager"]


def test_browser_cancellation_closes_all_resources(monkeypatch):
    fake = FakeBrowser(blocks=True)
    monkeypatch.setattr(session, "_playwright_factory", fake.factory)
    async def scenario():
        fake.started = asyncio.Event()
        task = asyncio.create_task(session.verify_cookies([cookie()]))
        await fake.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert fake.closed == ["context", "browser", "manager"]
    run(scenario())


def test_cancel_during_cleanup_is_propagated_and_settled():
    async def scenario():
        started, release, closed = asyncio.Event(), asyncio.Event(), []
        class Context:
            async def close(self):
                started.set()
                await release.wait()
                closed.append("context")
        class Browser:
            async def close(self):
                closed.append("browser")
        class Manager:
            async def stop(self):
                closed.append("manager")
        task = asyncio.create_task(session._cleanup_resources(Context(), Browser(), Manager()))
        await started.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == ["context", "browser", "manager"]
    run(scenario())


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_proxy_config_uses_shared_helper(monkeypatch, platform):
    fake = FakeBrowser()
    monkeypatch.setattr(session, "_playwright_factory", fake.factory)
    monkeypatch.setattr(session.sys, "platform", platform)
    proxy = {"enabled": True, "type": "http", "host": "proxy.invalid", "port": 3128,
             "socks_port": 3128, "username": "synthetic-user", "password": "synthetic-password"}
    result = run(session.verify_cookies([cookie()], proxy))
    assert result["state"] == "valid" and result["route"] == "proxy"
    options = fake.launch_options if platform == "win32" else fake.context_options
    assert options["proxy"]["server"] == "http://proxy.invalid:3128"
    assert "synthetic-password" not in json.dumps(result)


def test_expired_candidate_does_not_launch_browser(monkeypatch):
    def forbidden():
        raise AssertionError("Browser must not start")
    monkeypatch.setattr(session, "_playwright_factory", forbidden)
    assert run(session.verify_cookies([cookie(expires=1)]))["state"] == "expired"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows current-user DPAPI only")
def test_windows_dpapi_round_trip_only_synthetic_secret(tmp_path):
    store = session.SecureStore(tmp_path)
    assert store.load() is None
    normalized = session.normalize_cookies([cookie()])
    store.save(normalized)
    encrypted = store.path.read_bytes()
    assert SECRET.encode() not in encrypted and b"sessionid" not in encrypted
    assert store.load() == normalized
    different_root = session.SecureStore(tmp_path / "different")
    different_root.path = store.path
    with pytest.raises(session.SessionError) as caught:
        different_root.load()
    assert caught.value.state == "storage_unavailable"
    store.delete()
    assert store.load() is None


def test_windows_atomic_failure_preserves_encrypted_blob(tmp_path, monkeypatch):
    monkeypatch.setattr(session.sys, "platform", "win32")
    store = session.SecureStore(tmp_path)
    encrypted = b"synthetic encrypted original"
    store.path.parent.mkdir()
    store.path.write_bytes(encrypted)
    monkeypatch.setattr(session, "_dpapi", lambda *args, **kwargs: b"synthetic encrypted candidate")
    def fail(*args):
        raise OSError(SECRET)
    monkeypatch.setattr(session.os, "replace", fail)
    with pytest.raises(session.SessionError):
        store.save([cookie()])
    assert store.path.read_bytes() == encrypted and list(store.path.parent.glob(".threads-session-*")) == []


@pytest.mark.parametrize("platform,module_name", [("darwin", "keyring.backends.macOS"), ("linux", "keyring.backends.SecretService")])
def test_non_windows_explicit_backend_only(tmp_path, monkeypatch, platform, module_name):
    imports = []
    class Native:
        priority = 1
        secret = None
        def get_password(self, service, account):
            assert service == "Riviu.ThreadsSession" and len(account) == 64
            return self.secret
        def set_password(self, service, account, value):
            self.secret = value
        def delete_password(self, service, account):
            self.secret = None
    Native.__module__ = module_name
    def importer(name):
        imports.append(name)
        assert name == module_name
        return types.SimpleNamespace(Keyring=Native)
    monkeypatch.setattr(session.sys, "platform", platform)
    monkeypatch.setattr(session.importlib, "import_module", importer)
    store = session.SecureStore(tmp_path)
    store.save([cookie()])
    assert store.load()[0]["value"] == SECRET
    store.delete()
    assert store.load() is None and imports == [module_name] and not store.path.exists()


@pytest.mark.parametrize("backend_module,priority", [("keyrings.alt.file", 1), ("keyring.backends.SecretService", 0)])
def test_non_windows_rejects_non_native_or_unavailable_backend(tmp_path, monkeypatch, backend_module, priority):
    class Backend:
        pass
    Backend.priority, Backend.__module__ = priority, backend_module
    monkeypatch.setattr(session.sys, "platform", "linux")
    monkeypatch.setattr(session.importlib, "import_module", lambda name: types.SimpleNamespace(Keyring=Backend))
    with pytest.raises(session.SessionError):
        session.SecureStore(tmp_path).load()


@pytest.mark.parametrize("platform,expected", [("win32", None), ("darwin", "keyring.backends.macOS"),
                                               ("linux", "keyring.backends.SecretService")])
def test_sidecar_native_backend_collection_without_build(tmp_path, monkeypatch, platform, expected):
    from desktop import build_sidecar
    calls = []
    monkeypatch.setattr(build_sidecar, "ROOT", tmp_path)
    monkeypatch.setattr(build_sidecar.sys, "platform", platform)
    monkeypatch.setattr(build_sidecar.sys, "argv", ["build_sidecar.py", "--target", "synthetic-target"])
    def subprocess_run(command, **kwargs):
        calls.append(command)
        if "PyInstaller" in command:
            destination = Path(command[command.index("--distpath") + 1])
            extension = ".exe" if platform == "win32" else ""
            (destination / (build_sidecar.SIDECAR_NAME + extension)).write_bytes(b"synthetic-binary")
    monkeypatch.setattr(build_sidecar.subprocess, "run", subprocess_run)
    build_sidecar.main()
    command = calls[-1]
    if expected:
        hook = tmp_path / "build" / "desktop-sidecar" / "native-hooks" / "hook-keyring.py"
        text = hook.read_text(encoding="utf-8")
        assert f"hiddenimports = [{expected!r}]" in text and "collect_submodules" not in text and "chainer" not in text
        assert "keyrings.alt" in command and "keyring.backends.chainer" in command
    else:
        assert "keyring" in command and not (tmp_path / "build" / "desktop-sidecar" / "native-hooks").exists()
    assert len(calls) == 2


def test_non_windows_native_unavailable_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(session.sys, "platform", "linux")
    def importer(name):
        raise ImportError(SECRET)
    monkeypatch.setattr(session.importlib, "import_module", importer)
    store = session.SecureStore(tmp_path)
    for operation in (store.load, lambda: store.save([cookie()]), store.delete):
        with pytest.raises(session.SessionError) as caught:
            operation()
        assert caught.value.state == "storage_unavailable" and SECRET not in str(caught.value)
    assert not store.path.exists()


@pytest.mark.parametrize("where", ["caption", "recommendation", "post", "viewer"])
def test_nested_fake_module_is_not_auth_evidence(where):
    module = ["BarcelonaSharedData", [], {"viewer": {"id": "123"}}, 1]
    data = {where: {"require": [module], "define": [module]}}
    html = '<script type="application/json">' + json.dumps(data) + '</script>'
    assert session.auth_evidence(html, "https://www.threads.com/") == "unknown"
    # A boot module's payload is also not a boot table.
    data = {"require": [["RecommendationData", [], {"caption": module}, 1]]}
    html = '<script type="application/json">' + json.dumps(data) + '</script>'
    assert session.auth_evidence(html, "https://www.threads.com/") == "unknown"


@pytest.mark.parametrize("viewer,state", [({"id": "123"}, "valid"), (None, "invalid")])
def test_scheduled_server_js_bbox_define_viewer(viewer, state):
    module = ["BarcelonaSharedData", [], {"viewer": viewer}, 17]
    data = {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"define": [module]}}]]]}
    html = '<script type="application/json" data-sjs>' + json.dumps(data) + '</script>'
    assert session.auth_evidence(html, "https://www.threads.com/") == state


class GuardContext:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.options = []
        self.request = self
        self.handler = None

    async def route(self, pattern, handler):
        self.handler = handler

    async def get(self, url, **kwargs):
        assert session.allowed_session_url(url)
        assert kwargs["max_redirects"] == 0 and 0 < kwargs["timeout"] <= 15000
        assert kwargs["headers"] == session.NAVIGATION_HEADERS
        assert all(key.lower() != "cookie" for key in kwargs["headers"])
        self.calls.append(url)
        self.options.append(kwargs)
        response = self.responses[url]
        return response() if callable(response) else response


class GuardResponse:
    def __init__(self, status=200, location=None):
        self.status = status
        self.headers = {} if location is None else {"location": location}
        self.disposed = False

    async def body(self):
        return b"synthetic HTML"

    async def dispose(self):
        self.disposed = True


class GuardRoute:
    def __init__(self, url, navigation=True, page=None, frame=None, method="GET"):
        if page is None:
            page = types.SimpleNamespace(main_frame=None)
            page.main_frame = types.SimpleNamespace(page=page)
        self.request = types.SimpleNamespace(
            url=url, is_navigation_request=lambda: navigation,
            frame=frame or page.main_frame, method=method,
            headers={"cookie": SECRET, "authorization": SECRET})
        self.action = None
        self.error_code = None

    async def abort(self, error_code=None):
        self.action = "abort"
        self.error_code = error_code

    async def continue_(self):
        self.action = "continue"

    async def fulfill(self, **kwargs):
        assert not 300 <= kwargs["status"] < 400
        self.action = "fulfill"
        self.response = kwargs


class GuardPage:
    def __init__(self, context):
        self.context = context
        self.main_frame = types.SimpleNamespace(page=self)
        self.url = "about:blank"
        self.routes = []
        self.visits = []
        self.failure = None

    async def goto(self, url, **kwargs):
        from playwright.async_api import Error
        self.visits.append(url)
        assert kwargs["wait_until"] == "domcontentloaded" and kwargs["timeout"] == 0
        route = GuardRoute(url, page=self)
        self.routes.append(route)
        await self.context.handler(route)
        if self.failure is not None:
            raise self.failure
        if route.action == "abort":
            raise Error("net::ERR_ABORTED" if route.error_code == "aborted" else "net::ERR_FAILED")
        assert route.action == "fulfill"
        self.url = url
        return route.response


@pytest.mark.parametrize("target", ["http://threads.com/", "https://evil.test/", "https://threads.com.evil.test/", "https://sub.threads.com/"])
def test_navigation_guard_rejects_initial_target_before_request(target):
    async def scenario():
        context = GuardContext({})
        evidence = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        with pytest.raises(session.SessionError):
            await session.navigate_session_page(page, target, evidence)
        assert context.calls == [] and page.visits == [] and evidence["final_url"] == ""
        route = GuardRoute(target)
        await context.handler(route)
        assert route.action == "abort" and context.calls == []
    run(scenario())


@pytest.mark.parametrize("target", ["http://www.threads.com/", "https://evil.test/", "https://threads.com:444/", "https://user@threads.com/"])
def test_navigation_guard_rejects_redirect_before_cookie_request(target):
    async def scenario():
        from playwright.async_api import Error
        first = GuardResponse(302, target)
        context = GuardContext({"https://www.threads.com/": first})
        evidence = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        with pytest.raises(Error):
            await session.navigate_session_page(page, "https://www.threads.com/", evidence)
        assert page.routes[0].action == "abort" and context.calls == ["https://www.threads.com/"] and first.disposed
        assert evidence["_active"] is None and evidence["final_url"] == ""
    run(scenario())


def test_navigation_guard_bounded_allowed_redirects():
    async def scenario():
        first_url = "https://www.threads.com/"
        final_url = "https://www.threads.net/accounts/login/"
        first, final = GuardResponse(302, final_url), GuardResponse()
        final.headers.update({"Set-Cookie": "synthetic=yes", "Content-Encoding": "gzip", "Content-Length": "123", "Transfer-Encoding": "chunked"})
        context = GuardContext({first_url: first, final_url: final})
        evidence = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        response = await session.navigate_session_page(page, first_url, evidence)
        assert page.url == final_url and evidence["final_url"] == final_url
        assert [route.action for route in page.routes] == ["abort", "fulfill"]
        assert context.calls == [first_url, final_url, final_url]
        assert first.disposed and final.disposed and evidence["_active"] is None
        assert not {"set-cookie", "content-encoding", "content-length", "transfer-encoding"}.intersection(key.lower() for key in response["headers"])
        from playwright.async_api import Error
        looping = GuardContext({first_url: GuardResponse(302, "/")})
        evidence = await session.install_session_navigation_guard(looping)
        page = GuardPage(looping)
        with pytest.raises(Error):
            await session.navigate_session_page(page, first_url, evidence)
        assert page.routes[0].action == "abort" and len(looping.calls) == 6
    run(scenario())


@pytest.mark.parametrize("url,action", [("https://cdn.invalid/a.js", "continue"), ("http://threads.com/a.js", "abort")])
def test_navigation_guard_subresources_https_only(url, action):
    async def scenario():
        context = GuardContext({})
        await session.install_session_navigation_guard(context)
        route = GuardRoute(url, navigation=False)
        await context.handler(route)
        assert route.action == action and context.calls == []
    run(scenario())


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_navigation_relocates_supported_redirects(status):
    async def scenario():
        initial, final = "https://www.threads.com/share/test", "https://www.threads.com/@synthetic/post/test"
        first = GuardResponse(status, final)
        context = GuardContext({initial: first, final: GuardResponse()})
        guard = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        await session.navigate_session_page(page, initial, guard)
        assert page.url == guard["final_url"] == final
        assert page.visits == [initial, final] and context.calls == [initial, final, final]
        assert first.disposed
    run(scenario())


@pytest.mark.parametrize("status,location", [(300, "/"), (304, "/"), (305, "/"), (306, "/"), (302, None), (302, "")])
def test_navigation_never_fulfills_30x_or_approves_bad_redirects(status, location):
    async def scenario():
        from playwright.async_api import Error
        initial = "https://www.threads.com/"
        response = GuardResponse(status, location)
        context = GuardContext({initial: response})
        guard = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        with pytest.raises(Error):
            await session.navigate_session_page(page, initial, guard)
        assert page.visits == [initial] and page.routes[0].action == "abort"
        assert response.disposed and guard["_active"] is None and guard["final_url"] == ""
    run(scenario())


@pytest.mark.parametrize("redirect_count,success,expected_gets", [(4, True, 6), (5, False, 6), (6, False, 6)])
def test_navigation_get_budget_includes_final_refetch(redirect_count, success, expected_gets):
    async def scenario():
        from playwright.async_api import Error
        urls = [f"https://www.threads.com/hop/{index}" for index in range(redirect_count + 1)]
        responses = {url: GuardResponse(302, urls[index + 1]) for index, url in enumerate(urls[:-1])}
        responses[urls[-1]] = GuardResponse()
        context = GuardContext(responses)
        guard = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        if success:
            await session.navigate_session_page(page, urls[0], guard)
            assert page.url == urls[-1] and guard["final_url"] == urls[-1]
        else:
            with pytest.raises(Error):
                await session.navigate_session_page(page, urls[0], guard)
            assert guard["final_url"] == "" and len(page.visits) == 1
        assert len(context.calls) == expected_gets
        assert all(response.disposed for url, response in responses.items() if url in context.calls)
    run(scenario())


def test_navigation_budget_does_not_reset_on_repeated_relocations():
    async def scenario():
        from playwright.async_api import Error
        first, second, third, fourth = [f"https://www.threads.com/{index}" for index in range(4)]
        count = 0
        def moving_second():
            nonlocal count
            count += 1
            return GuardResponse() if count == 1 else GuardResponse(302, third)
        moving_count = 0
        def moving_third():
            nonlocal moving_count
            moving_count += 1
            return GuardResponse() if moving_count == 1 else GuardResponse(302, fourth)
        context = GuardContext({first: GuardResponse(302, second), second: moving_second,
                                third: moving_third, fourth: GuardResponse()})
        guard = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        with pytest.raises(Error):
            await session.navigate_session_page(page, first, guard)
        assert context.calls == [first, second, second, third, third, fourth]
        assert page.visits == [first, second, third] and guard["_active"] is None
    run(scenario())


@pytest.mark.parametrize("error_kind", ["runtime", "playwright", "timeout", "embedded_abort"])
def test_navigation_arbitrary_errors_do_not_consume_redirect_approval(error_kind):
    async def scenario():
        from playwright.async_api import Error, TimeoutError
        errors = {"runtime": RuntimeError("net::ERR_ABORTED " + SECRET),
                  "playwright": Error("net::ERR_FAILED " + SECRET),
                  "timeout": TimeoutError("net::ERR_ABORTED"),
                  "embedded_abort": Error("Unrelated error mentions net::ERR_ABORTED " + SECRET)}
        initial, final = "https://www.threads.com/", "https://www.threads.net/"
        context = GuardContext({initial: GuardResponse(302, final), final: GuardResponse()})
        guard = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        page.failure = errors[error_kind]
        with pytest.raises(type(page.failure)):
            await session.navigate_session_page(page, initial, guard)
        assert page.visits == [initial] and context.calls == [initial, final]
        assert guard["_active"] is None and guard["final_url"] == ""
        page.failure = None
        await session.navigate_session_page(page, final, guard)
        assert page.visits == [initial, final] and guard["final_url"] == final
    run(scenario())


@pytest.mark.parametrize("unbound", ["other_page", "subframe", "post", "wrong_url"])
def test_navigation_unbound_requests_cannot_approve_continuation(unbound):
    async def scenario():
        initial, final = "https://www.threads.com/", "https://www.threads.net/"
        context = GuardContext({initial: GuardResponse(302, final), final: GuardResponse()})
        guard = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        class InjectingPage(GuardPage):
            async def goto(self, url, **kwargs):
                if unbound == "other_page":
                    extra = GuardRoute(initial, page=page)
                elif unbound == "subframe":
                    extra = GuardRoute(initial, page=self, frame=types.SimpleNamespace(page=self))
                elif unbound == "post":
                    extra = GuardRoute(initial, page=self, method="POST")
                else:
                    extra = GuardRoute(final, page=self)
                await context.handler(extra)
                assert extra.action == "abort" and context.calls == []
                assert guard["_active"]["pending"] is None
                return await super().goto(url, **kwargs)
        actual = InjectingPage(context)
        # Inject only once, so the subsequent approved final navigation can run.
        original = actual.goto
        async def first_then_normal(url, **kwargs):
            actual.goto = types.MethodType(GuardPage.goto, actual)
            return await original(url, **kwargs)
        actual.goto = first_then_normal
        await session.navigate_session_page(actual, initial, guard)
        assert actual.url == final and context.calls == [initial, final, final]
    run(scenario())


def test_navigation_rejects_wrong_context_and_concurrent_logical_navigation():
    async def scenario():
        initial = "https://www.threads.com/"
        started = asyncio.Event()
        release = asyncio.Event()
        context = GuardContext({initial: GuardResponse()})
        guard = await session.install_session_navigation_guard(context)
        wrong = GuardPage(GuardContext({initial: GuardResponse()}))
        with pytest.raises(session.SessionError):
            await session.navigate_session_page(wrong, initial, guard)
        assert guard["_active"] is None and wrong.visits == []
        original = context.get
        async def waiting_get(url, **kwargs):
            started.set()
            await release.wait()
            return await original(url, **kwargs)
        context.get = waiting_get
        page = GuardPage(context)
        task = asyncio.create_task(session.navigate_session_page(page, initial, guard))
        await started.wait()
        scope = guard["_active"]
        with pytest.raises(session.SessionError):
            await session.navigate_session_page(GuardPage(context), initial, guard)
        assert guard["_active"] is scope
        release.set()
        await task
        assert page.url == initial and context.calls == [initial]
    run(scenario())


@pytest.mark.parametrize("invalid_timeout", [0, -1, True, None, float("nan"), float("inf")])
def test_navigation_rejects_invalid_timeout_without_request(invalid_timeout):
    async def scenario():
        context = GuardContext({})
        guard = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        with pytest.raises(session.SessionError):
            await session.navigate_session_page(page, "https://www.threads.com/", guard, timeout=invalid_timeout)
        assert page.visits == [] and context.calls == [] and guard["_active"] is None
    run(scenario())


def test_navigation_stale_redirect_evidence_never_authorizes_an_abort():
    async def scenario():
        from playwright.async_api import Error
        initial, final = "https://www.threads.com/", "https://www.threads.net/"
        context = GuardContext({initial: GuardResponse(302, final), final: GuardResponse()})
        guard = await session.install_session_navigation_guard(context)
        class StalePage(GuardPage):
            async def goto(self, url, **kwargs):
                self.visits.append(url)
                route = GuardRoute(url, page=self)
                await context.handler(route)
                # Delayed evidence from a different attempt cannot grant retry.
                guard["_active"]["pending"]["attempt"] = {"url": initial}
                raise Error("net::ERR_ABORTED")
        page = StalePage(context)
        with pytest.raises(Error):
            await session.navigate_session_page(page, initial, guard)
        assert page.visits == [initial] and context.calls == [initial, final]
        assert guard["_active"] is None and guard["final_url"] == ""
    run(scenario())


def test_navigation_global_deadline_spans_relocation_and_refetch():
    async def scenario():
        initial, final = "https://www.threads.com/", "https://www.threads.net/"
        context = GuardContext({initial: GuardResponse(302, final), final: GuardResponse()})
        original = context.get
        async def delayed_get(url, **kwargs):
            response = await original(url, **kwargs)
            await asyncio.sleep(0.035)
            return response
        context.get = delayed_get
        guard = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        started = time.monotonic()
        with pytest.raises((asyncio.TimeoutError, session.SessionError)):
            await session.navigate_session_page(page, initial, guard, timeout=90)
        elapsed = time.monotonic() - started
        assert elapsed < 0.2 and len(context.calls) <= 3 and page.url == "about:blank"
        assert guard["_active"] is None and guard["final_url"] == ""
        assert context.options[-1]["timeout"] < context.options[0]["timeout"]
    run(scenario())


def test_navigation_cancel_revokes_approval_and_ignores_stale_completion():
    async def scenario():
        initial, final = "https://www.threads.com/", "https://www.threads.net/"
        context = GuardContext({initial: GuardResponse(302, final), final: GuardResponse()})
        guard = await session.install_session_navigation_guard(context)
        aborted = asyncio.Event()
        stale = {}
        class CancelPage(GuardPage):
            async def goto(self, url, **kwargs):
                route = GuardRoute(url, page=self)
                await context.handler(route)
                stale["scope"] = guard["_active"]
                assert stale["scope"]["pending"] is not None
                aborted.set()
                await asyncio.Event().wait()
        page = CancelPage(context)
        task = asyncio.create_task(session.navigate_session_page(page, initial, guard))
        await aborted.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stale["scope"]["pending"] is None and stale["scope"]["attempt"] is None
        assert guard["_active"] is None and guard["final_url"] == ""
        route = GuardRoute(final, page=page)
        await context.handler(route)
        assert route.action == "abort" and context.calls == [initial, final]
        fresh = GuardPage(context)
        await session.navigate_session_page(fresh, final, guard)
        assert fresh.visits == [final] and len(context.calls) == 3
    run(scenario())


def test_navigation_cancel_during_body_disposes_and_leaves_no_tasks():
    async def scenario():
        initial = "https://www.threads.com/"
        started = asyncio.Event()
        class BlockingResponse(GuardResponse):
            async def body(self):
                started.set()
                await asyncio.Event().wait()
        response = BlockingResponse()
        context = GuardContext({initial: response})
        guard = await session.install_session_navigation_guard(context)
        page = GuardPage(context)
        task = asyncio.create_task(session.navigate_session_page(page, initial, guard))
        await started.wait()
        scope = guard["_active"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert response.disposed and not scope["tasks"] and scope["pending"] is None
        assert guard["_active"] is None and guard["final_url"] == ""
        assert page.routes[0].action != "fulfill"
    run(scenario())


def test_real_chromium_fully_routed_synthetic_valid_verification(monkeypatch):
    """Real Chromium, but every navigation fetch is intercepted before network."""
    from playwright.async_api import async_playwright, APIRequestContext
    requests = []
    data = {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {
        "define": [["BarcelonaSharedData", [], {"viewer": {"id": "123"}}, 17]]}}]]]}
    html = '<!doctype html><script type="application/json" data-sjs>' + json.dumps(data) + '</script>'

    class FixtureResponse:
        status = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        async def body(self):
            return html.encode()
        async def dispose(self):
            pass

    async def fixture_get(self, url, **kwargs):
        assert url == "https://www.threads.com/" and kwargs["max_redirects"] == 0
        requests.append(url)
        return FixtureResponse()

    monkeypatch.setattr(APIRequestContext, "get", fixture_get)
    original_guard = session.install_session_navigation_guard

    async def routed_guard(context):
        # The guard's API get is stubbed above. Any unexpected subresource is
        # aborted too, so this test cannot use external network.
        async def abort_subresource(route):
            if route.request.is_navigation_request():
                await route.fallback()
            else:
                await route.abort()
        evidence = await original_guard(context)
        await context.route("**/*", abort_subresource)
        return evidence

    monkeypatch.setattr(session, "install_session_navigation_guard", routed_guard)
    result = run(session.verify_cookies([cookie(domain="www.threads.com", hostOnly=True)]))
    assert result["state"] == "valid" and requests == ["https://www.threads.com/"]
    assert SECRET not in json.dumps(result)


def test_real_chromium_auth_redirect_location_and_native_cookie_scope(monkeypatch):
    """API fixtures only; no live cookies, proxy, profile, or external network."""
    from playwright.async_api import async_playwright, APIRequestContext, Route
    initial = "https://www.threads.com/share/synthetic"
    final = "https://www.threads.net/@synthetic/post/test"
    calls, fulfilled, responses = [], [], []
    browser_context = None
    html = '<!doctype html><html><body>' + viewer_html({"id": "123"}) + '</body></html>'

    async def fixture_get(self, url, **kwargs):
        assert kwargs["max_redirects"] == 0 and kwargs["headers"] == session.NAVIGATION_HEADERS
        assert all(key.lower() not in {"cookie", "authorization"} for key in kwargs["headers"])
        selected = {entry["name"]: entry["value"] for entry in await browser_context.cookies(url)}
        if url == initial:
            assert selected == {"sessionid": "synthetic-com-host-only"}
            # Emulate APIRequestContext's normal Set-Cookie processing against
            # this hop, not against the final redirected host.
            await browser_context.add_cookies([{"name": "from_com", "value": "synthetic-hop-cookie", "url": initial, "secure": True}])
            response = GuardResponse(302, final)
            response.headers["set-cookie"] = "from_com=synthetic-hop-cookie; Path=/; Secure"
        else:
            assert url == final and selected == {"sessionid": "synthetic-net-host-only"}
            response = GuardResponse()
            response.headers.update({"content-type": "text/html; charset=utf-8",
                                     "set-cookie": "replayed=synthetic-not-for-browser; Path=/; Secure",
                                     "content-encoding": "gzip", "content-length": "1"})
            async def body():
                return html.encode()
            response.body = body
        calls.append(url)
        responses.append(response)
        return response

    original_fulfill = Route.fulfill
    async def checked_fulfill(self, **kwargs):
        fulfilled.append(kwargs["status"])
        assert not 300 <= kwargs["status"] < 400
        assert not {"set-cookie", "content-encoding", "content-length", "transfer-encoding"}.intersection(key.lower() for key in kwargs["headers"])
        return await original_fulfill(self, **kwargs)

    monkeypatch.setattr(APIRequestContext, "get", fixture_get)
    monkeypatch.setattr(Route, "fulfill", checked_fulfill)
    async def scenario():
        nonlocal browser_context
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            browser_context = await browser.new_context(service_workers="block")
            try:
                await browser_context.add_cookies([
                    {"name": "sessionid", "value": "synthetic-com-host-only", "url": "https://www.threads.com/", "secure": True, "httpOnly": True},
                    {"name": "sessionid", "value": "synthetic-net-host-only", "url": "https://www.threads.net/", "secure": True, "httpOnly": True},
                ])
                guard = await session.install_session_navigation_guard(browser_context)
                async def no_network(route):
                    if route.request.is_navigation_request():
                        await route.fallback()
                    else:
                        await route.abort()
                await browser_context.route("**/*", no_network)
                page = await browser_context.new_page()
                await session.navigate_session_page(page, initial, guard, timeout=3000)
                assert page.url == await page.evaluate("location.href") == guard["final_url"] == final
                assert session.auth_evidence(await page.content(), page.url) == "valid"
                assert calls == [initial, final, final] and fulfilled == [200]
                assert all(response.disposed for response in responses)
                assert guard["_active"] is None
                final_cookies = await browser_context.cookies(final)
                assert {entry["name"] for entry in final_cookies} == {"sessionid"}
                assert "synthetic-com-host-only" not in await page.content()
            finally:
                await browser_context.close()
                await browser.close()
    run(scenario())


@pytest.mark.parametrize("failure", ["budget", "forbidden", "timeout", "cancel"])
def test_real_chromium_redirect_failure_clears_approval_without_external_network(monkeypatch, failure):
    from playwright.async_api import async_playwright, APIRequestContext, Error
    initial = "https://www.threads.com/share/synthetic"
    final = "https://www.threads.net/@synthetic/post/test"
    calls, responses = [], []
    started = None
    should_block = False
    async def fixture_get(self, url, **kwargs):
        assert session.allowed_session_url(url) and kwargs["max_redirects"] == 0
        assert kwargs["headers"] == session.NAVIGATION_HEADERS
        calls.append(url)
        if should_block and url == final:
            started.set()
            await asyncio.Event().wait()
        if url == initial:
            target = "https://forbidden.invalid/" if failure == "forbidden" else final
            response = GuardResponse(302, target)
        elif failure == "budget":
            response = GuardResponse(302, url + "/next")
        else:
            response = GuardResponse()
            response.headers["content-type"] = "text/html"
        responses.append(response)
        return response
    monkeypatch.setattr(APIRequestContext, "get", fixture_get)
    async def scenario():
        nonlocal started, should_block
        started = asyncio.Event()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(service_workers="block")
            try:
                guard = await session.install_session_navigation_guard(context)
                async def no_network(route):
                    if route.request.is_navigation_request():
                        await route.fallback()
                    else:
                        await route.abort()
                await context.route("**/*", no_network)
                page = await context.new_page()
                should_block = failure in {"timeout", "cancel"}
                task = asyncio.create_task(session.navigate_session_page(
                    page, initial, guard, timeout=150 if failure == "timeout" else 3000))
                if failure == "cancel":
                    await asyncio.wait_for(started.wait(), timeout=2)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises((Error, asyncio.TimeoutError, session.SessionError)):
                        await task
                assert guard["_active"] is None and guard["final_url"] == ""
                assert len(calls) <= 6 and all(response.disposed for response in responses)
                assert not await context.cookies() and page.url != final
                if failure in {"cancel", "timeout"}:
                    # A new logical navigation must not reuse cancelled approval.
                    should_block = False
                    fresh = await context.new_page()
                    await session.navigate_session_page(fresh, final, guard, timeout=2000)
                    assert fresh.url == await fresh.evaluate("location.href") == final
            finally:
                await context.close()
                await browser.close()
    run(scenario())


@pytest.mark.parametrize("state,expected", [("invalid", "invalid"), ("checkpoint", "checkpoint"),
                                           ("unknown", "unknown"), (SECRET, "unknown")])
def test_invalidate_preserves_cookies_generation_and_uses_safe_state(tmp_path, state, expected):
    vault = Vault([cookie()])
    controller = session.ThreadsSession(tmp_path, vault)
    before = controller.status()
    after = controller.invalidate(before["generation"], state)
    assert after["state"] == expected and after["checkedAt"] is not None
    assert after["configured"] and after["generation"] == before["generation"]
    assert controller.snapshot()[0]["value"] == SECRET and vault.saves == 0
    assert SECRET not in json.dumps(after)
    assert controller.invalidate("stale-generation", "checkpoint") == after
    cleared = controller.clear()
    assert controller.invalidate(cleared["generation"], "invalid") == cleared


def test_invalidate_wins_over_inflight_verification(tmp_path, monkeypatch):
    controller = session.ThreadsSession(tmp_path, Vault([cookie()]))
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def verify(*args, **kwargs):
            started.set()
            await release.wait()
            return fake_result("valid")
        monkeypatch.setattr(session, "verify_cookies", verify)
        task = asyncio.create_task(controller.verify())
        await started.wait()
        controller.invalidate(controller.status()["generation"], "checkpoint")
        release.set()
        with pytest.raises(session.SessionError) as caught:
            await task
        assert caught.value.state == "stale" and controller.status()["state"] == "checkpoint"
    run(scenario())
