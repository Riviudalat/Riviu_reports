"""Local, fail-closed Threads session import and read-only authentication checks.

Cookie values and browser content never belong in exceptions, logs, or API status.
Only OS-native secret storage is supported; there is no plaintext fallback.
"""
from __future__ import annotations

import asyncio
import copy
import ctypes
import hashlib
import importlib
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from pathlib import Path
from urllib.parse import urljoin, urlsplit

MAX_IMPORT_BYTES = 256 * 1024
MAX_COOKIES = 256
VERIFY_TIMEOUT_SECONDS = 25
CLEANUP_TIMEOUT_SECONDS = 3
NAVIGATION_HEADERS = {
    "Accept": "text/html",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}
_ALLOWED_DOMAINS = frozenset({"threads.com", "www.threads.com", ".threads.com", "threads.net", "www.threads.net", ".threads.net"})
_ALLOWED_HOSTS = frozenset({"threads.com", "www.threads.com", "threads.net", "www.threads.net"})
_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_MESSAGES = {
    "none": "Chưa lưu cookie Threads.",
    "unchecked": "Cookie đã lưu an toàn, chưa kiểm tra trong lần mở app này.",
    "valid": "Đã xác nhận phiên đăng nhập Threads tại thời điểm kiểm tra.",
    "expired": "Cookie Threads đã hết hạn. Import phiên mới.",
    "invalid": "Threads không xác nhận đăng nhập với cookie này.",
    "checkpoint": "Tài khoản cần xác minh trên Threads. Thực hiện bên ngoài app này.",
    "unknown": "Chưa xác minh được đăng nhập; kiểm tra kết nối và thử kiểm lại.",
    "storage_unavailable": "Kho bảo mật không khả dụng. Phiên đã lưu trước đó không bị thay đổi.",
    "bad_import": "JSON cookie không hợp lệ hoặc không được hỗ trợ.",
    "too_large": "JSON cookie vượt giới hạn dung lượng.",
    "missing_session": "Không có cookie sessionid Threads dùng được.",
    "stale": "Phiên cookie đã thay đổi. Tải lại trạng thái trước khi quét.",
    "redirect": "Threads chuyển hướng phiên tới địa chỉ không được phép.",
    "proxy_unsupported": ("Chromium không hỗ trợ proxy SOCKS5 có tài khoản/mật khẩu nên không kiểm tra được cookie Threads "
                          "qua proxy này. Đổi sang proxy HTTP hoặc tắt cookie Threads."),
}
# Route/navigation problems say nothing about whether the saved cookie still works.
_NOT_COOKIE_EVIDENCE = frozenset({"redirect", "proxy_unsupported"})


class SessionError(ValueError):
    """An intentionally fixed, non-secret error suitable for API responses."""

    def __init__(self, state: str = "bad_import"):
        self.state = state if state in _MESSAGES else "bad_import"
        super().__init__(_MESSAGES[self.state])


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def allowed_session_url(url: str) -> bool:
    """Allow HTTPS, exact Threads hosts, no userinfo, and only the HTTPS port."""
    try:
        if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in url):
            return False
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in _ALLOWED_HOSTS
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except (TypeError, ValueError, AttributeError):
        return False


def _decode_payload(payload):
    try:
        if isinstance(payload, bytes):
            if len(payload) > MAX_IMPORT_BYTES:
                raise SessionError("too_large")
            payload = json.loads(payload.decode("utf-8-sig"))
        elif isinstance(payload, str):
            if len(payload.encode("utf-8")) > MAX_IMPORT_BYTES:
                raise SessionError("too_large")
            payload = json.loads(payload.lstrip("﻿"))
        elif isinstance(payload, (dict, list, tuple)):
            if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_IMPORT_BYTES:
                raise SessionError("too_large")
        else:
            raise SessionError()
        if isinstance(payload, dict):
            payload = payload.get("cookies")
        if not isinstance(payload, (list, tuple)) or len(payload) > MAX_COOKIES:
            raise SessionError()
        return payload
    except SessionError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise SessionError() from None


def _normalize(payload, *, allow_expired: bool = False) -> list[dict]:
    entries = _decode_payload(payload)
    cookies = []
    scopes = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("domain"), str):
            raise SessionError()
        domain = entry["domain"].lower()
        # Exports commonly contain cookies for many sites. Never retarget them.
        if domain not in _ALLOWED_DOMAINS:
            continue
        name, value = entry.get("name"), entry.get("value")
        if not isinstance(name, str) or len(name) > 256 or not _COOKIE_NAME.fullmatch(name):
            raise SessionError()
        if not isinstance(value, str) or len(value.encode("utf-8")) > 4096:
            raise SessionError()
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 or char == ";" for char in value):
            raise SessionError()
        path = entry.get("path", "/")
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 2048:
            raise SessionError()
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in path):
            raise SessionError()
        for flag in ("secure", "httpOnly", "hostOnly"):
            if flag in entry and not isinstance(entry[flag], bool):
                raise SessionError()
        host_only = entry.get("hostOnly", not domain.startswith("."))
        if host_only and domain.startswith("."):
            raise SessionError()
        same_site = entry.get("sameSite")
        if same_site is None:
            same_site = "Lax"
        elif not isinstance(same_site, str):
            raise SessionError()
        else:
            same_site = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None", "unspecified": "Lax"}.get(same_site.lower())
            if same_site is None:
                raise SessionError()
        cookie = {"name": name, "value": value, "domain": domain, "path": path,
                  "secure": True, "httpOnly": entry.get("httpOnly", False),
                  "sameSite": same_site, "hostOnly": host_only}
        expires = entry.get("expires", entry.get("expirationDate"))
        if expires is not None:
            if isinstance(expires, bool) or not isinstance(expires, (int, float)):
                raise SessionError()
            try:
                if not math.isfinite(expires):
                    raise SessionError()
                if expires != -1:  # Playwright's session-cookie sentinel.
                    cookie["expires"] = float(expires)
            except (OverflowError, ValueError):
                raise SessionError() from None
        scope = (name, domain.lstrip("."), path)
        previous = scopes.get(scope)
        if previous is not None:
            if previous != cookie:
                raise SessionError()
            continue
        scopes[scope] = cookie
        cookies.append(cookie)
    session_cookies = [cookie for cookie in cookies if cookie["name"] == "sessionid" and cookie["value"]]
    if not session_cookies:
        raise SessionError("missing_session")
    if not allow_expired and not any("expires" not in cookie or cookie["expires"] > time.time() for cookie in session_cookies):
        raise SessionError("expired")
    return cookies


def normalize_cookies(payload: bytes | str | dict | list) -> list[dict]:
    """Normalize an export using exact-domain filtering and strict cookie types."""
    try:
        return _normalize(payload)
    except SessionError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise SessionError() from None


def _expired(cookies) -> bool:
    return not any(cookie["name"] == "sessionid" and cookie["value"] and
                   ("expires" not in cookie or cookie["expires"] > time.time()) for cookie in cookies)


def cookies_to_playwright(cookies) -> list[dict]:
    result = []
    for cookie in normalize_cookies(cookies):
        converted = {key: cookie[key] for key in ("name", "value", "secure", "httpOnly", "sameSite")}
        if "expires" in cookie and cookie["expires"] <= time.time():
            continue  # Expired ancillary cookies must not defeat a live session.
        if cookie["hostOnly"] and cookie["path"] == "/":
            converted["url"] = f"https://{cookie['domain']}/"
        else:
            # Playwright derives '/' from a URL '/path', losing the imported
            # path. An exact, non-dot domain preserves host-only for other paths.
            converted["domain"] = cookie["domain"] if cookie["hostOnly"] else "." + cookie["domain"].lstrip(".")
            converted["path"] = cookie["path"]
        if "expires" in cookie:
            converted["expires"] = cookie["expires"]
        result.append(converted)
    return result


def cookie_jar(cookies) -> CookieJar:
    jar = CookieJar(policy=DefaultCookiePolicy(strict_ns_domain=DefaultCookiePolicy.DomainStrictNonDomain))
    for cookie in normalize_cookies(cookies):
        jar.set_cookie(Cookie(
            version=0, name=cookie["name"], value=cookie["value"], port=None, port_specified=False,
            domain=cookie["domain"], domain_specified=not cookie["hostOnly"],
            domain_initial_dot=cookie["domain"].startswith("."), path=cookie["path"], path_specified=True,
            secure=True, expires=int(cookie["expires"]) if "expires" in cookie else None,
            discard="expires" not in cookie, comment=None, comment_url=None,
            rest={"HttpOnly": None} if cookie["httpOnly"] else {}, rfc2109=False,
        ))
    return jar


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, entropy: bytes, *, decrypt: bool) -> bytes:
    # Current-user DPAPI; do not set CRYPTPROTECT_LOCAL_MACHINE or permit UI.
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(entropy)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    salt = _DataBlob(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.POINTER(_DataBlob),
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DataBlob)]
    function.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    try:
        if not function(ctypes.byref(source), None, ctypes.byref(salt), None, None, 1, ctypes.byref(output)):
            raise SessionError("storage_unavailable")
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        if output.pbData:
            kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


class SecureStore:
    """DPAPI on Windows; explicitly selected native Keychain/SecretService elsewhere."""

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir).resolve()
        root = os.path.normcase(str(self.base_dir)).encode("utf-8")
        self.entropy = hashlib.sha256(b"Riviu.ThreadsSession.v1\0" + root).digest()
        self.account = self.entropy.hex()
        self.path = self.base_dir / "data" / "threads_session.enc"
        self._backend = None

    def _native_backend(self):
        if self._backend is None:
            module_name = {"darwin": "keyring.backends.macOS", "linux": "keyring.backends.SecretService"}.get(sys.platform)
            if module_name is None:
                raise SessionError("storage_unavailable")
            # Never call get_keyring(): plugin/chain/plaintext backends are not allowed.
            backend_type = importlib.import_module(module_name).Keyring
            backend = backend_type()
            if type(backend).__module__ != module_name or backend.priority <= 0:
                raise SessionError("storage_unavailable")
            self._backend = backend
        return self._backend

    def load(self) -> list[dict] | None:
        try:
            if sys.platform == "win32":
                if not self.path.exists():
                    return None
                if self.path.stat().st_size > MAX_IMPORT_BYTES + 16384:
                    raise SessionError("storage_unavailable")
                raw = _dpapi(self.path.read_bytes(), self.entropy, decrypt=True)
            else:
                raw = self._native_backend().get_password("Riviu.ThreadsSession", self.account)
                if raw is None:
                    return None
            return _normalize(raw, allow_expired=True)
        except Exception:
            raise SessionError("storage_unavailable") from None

    def save(self, cookies) -> None:
        temporary = None
        try:
            raw = json.dumps(normalize_cookies(cookies), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if sys.platform != "win32":
                self._native_backend().set_password("Riviu.ThreadsSession", self.account, raw)
                return
            encrypted = _dpapi(raw.encode("utf-8"), self.entropy, decrypt=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".threads-session-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except Exception:
            raise SessionError("storage_unavailable") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def delete(self) -> None:
        try:
            if sys.platform == "win32":
                self.path.unlink(missing_ok=True)
            else:
                backend = self._native_backend()
                if backend.get_password("Riviu.ThreadsSession", self.account) is not None:
                    backend.delete_password("Riviu.ThreadsSession", self.account)
        except Exception:
            raise SessionError("storage_unavailable") from None


def chromium_proxy_unsupported(proxy_config) -> bool:
    """Chromium cannot authenticate to SOCKS5; never drop credentials to try anyway."""
    return bool(isinstance(proxy_config, dict) and proxy_config.get("type") == "socks5" and proxy_config.get("username"))


class _JSONScripts(HTMLParser):
    """Collect raw <script type="application/json"> bodies, never captions or HTML text."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts = []
        self._parts = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/json":
            self._parts = []

    def handle_data(self, data):
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._parts is not None:
            self.scripts.append("".join(self._parts))
            self._parts = None


def json_script_documents(content: str) -> list:
    """Parse a page's JSON data scripts once; undecodable scripts are skipped.

    Shared by session evidence and Threads metric extraction so one snapshot is
    tokenized a single time.
    """
    parser = _JSONScripts()
    parser.feed(content)
    documents = []
    for script in parser.scripts:
        try:
            documents.append(json.loads(script))
        except (ValueError, RecursionError):
            continue
    return documents


def _account_id(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9]+", value)) and any(char != "0" for char in value)


def auth_evidence(content: str, final_url: str, documents: list | None = None) -> str:
    """Classify typed root viewer evidence, never arbitrary isLoggedIn/HTTP 200.

    documents: json_script_documents(content), when the caller already parsed it.
    """
    if not allowed_session_url(final_url):
        return "unknown"
    path = urlsplit(final_url).path.lower()
    if any(path == prefix or path.startswith(prefix + "/") for prefix in ("/checkpoint", "/challenge", "/accounts/suspended")):
        return "checkpoint"
    if any(path == prefix or path.startswith(prefix + "/") for prefix in ("/login", "/accounts/login")):
        return "invalid"
    if not isinstance(content, str) or len(content) > 8 * 1024 * 1024:
        return "unknown"
    if documents is None:
        try:
            documents = json_script_documents(content)
        except Exception:
            return "unknown"
    evidence = set()
    identities = set()
    for document in documents:
        if not isinstance(document, dict):
            continue
        # Only document boot tables are authoritative. Do not recursively search
        # captions, recommendation payloads, post authors, or arbitrary objects.
        tables = [document.get("define"), document.get("require")]
        requirements = document.get("require")
        if isinstance(requirements, list):
            for requirement in requirements:
                if not (isinstance(requirement, list) and len(requirement) >= 4
                        and requirement[0] == "ScheduledServerJS" and requirement[1] == "handle"
                        and requirement[2] is None and isinstance(requirement[3], list)):
                    continue
                for argument in requirement[3]:
                    bbox = argument.get("__bbox") if isinstance(argument, dict) else None
                    if isinstance(bbox, dict):
                        tables.extend((bbox.get("define"), bbox.get("require")))
        for table in tables:
            if not isinstance(table, list):
                continue
            for module in table:
                if not (isinstance(module, list) and len(module) >= 3
                        and module[0] == "BarcelonaSharedData" and isinstance(module[1], list)
                        and isinstance(module[2], dict)):
                    continue
                payload = module[2]
                if "viewer" not in payload:
                    evidence.add("unknown")
                    continue
                viewer = payload["viewer"]
                if viewer is None:
                    evidence.add("invalid")
                elif isinstance(viewer, dict):
                    ids = {str(viewer[key]) for key in ("id", "pk") if _account_id(viewer.get(key))}
                    if len(ids) == 1:
                        identities.update(ids)
                        evidence.add("valid")
                    else:
                        evidence.add("unknown")
                else:
                    evidence.add("unknown")
    if len(identities) > 1:
        return "unknown"
    return next(iter(evidence)) if len(evidence) == 1 else "unknown"


def _check_result(state: str, route: str = "direct") -> dict:
    return {"state": state, "checkedAt": _timestamp(), "route": route, "message": _MESSAGES[state]}


def _playwright_factory():
    from playwright.async_api import async_playwright
    return async_playwright()


async def _close(resource, method: str):
    if resource is not None:
        try:
            await asyncio.wait_for(getattr(resource, method)(), timeout=CLEANUP_TIMEOUT_SECONDS)
        except (Exception, asyncio.CancelledError):
            pass


async def _cleanup_resources(context, browser, playwright):
    async def close_all():
        await _close(context, "close")
        await _close(browser, "close")
        await _close(playwright, "stop")

    cleanup = asyncio.create_task(close_all())
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        # Cancellation during a successful check's cleanup must not be swallowed
        # or interrupt closure of the remaining browser resources.
        await asyncio.shield(cleanup)
        raise


async def install_session_navigation_guard(context) -> dict:
    """Install a fail-closed guard; navigate through navigate_session_page only.

    Fetch allowed hops manually with the context jar, never forwarded request
    headers. Chromium does not reliably re-intercept fulfilled HTTP redirects,
    so a changed final URL requires an explicitly scoped, guarded second goto.
    """
    evidence = {"final_url": "", "_context": context, "_active": None}

    async def guard(route):
        request = route.request
        if not request.is_navigation_request():
            parsed = urlsplit(request.url)
            host = parsed.hostname or ""
            threads_host = any(host == domain or host.endswith("." + domain) for domain in ("threads.com", "threads.net"))
            if parsed.scheme == "https" and (not threads_host or allowed_session_url(request.url)):
                await route.continue_()
            else:
                await route.abort()
            return
        scope = evidence["_active"]
        attempt = scope["attempt"] if scope is not None else None
        if (scope is None or attempt is None or request.frame is not scope["frame"]
                or request.frame.page is not scope["page"] or request.url != attempt["url"]
                or getattr(request, "method", "GET") != "GET"
                or attempt["claimed"] or not allowed_session_url(request.url)):
            await route.abort()
            return
        attempt["claimed"] = True
        task = asyncio.current_task()
        scope["tasks"].add(task)

        async def fetch_navigation():
            target = request.url
            response = None
            try:
                while True:
                    if (evidence["_active"] is not scope or scope["attempt"] is not attempt
                            or scope["remaining"] <= 0 or not allowed_session_url(target)):
                        await route.abort()
                        return
                    remaining = scope["deadline"] - time.monotonic()
                    if remaining <= 0:
                        await route.abort()
                        return
                    scope["remaining"] -= 1
                    # This API context owns the per-host cookie jar. Never copy
                    # browser headers (especially Cookie) to a different host.
                    response = await context.request.get(
                        target, headers=dict(NAVIGATION_HEADERS), max_redirects=0,
                        timeout=max(1, min(15000, int(remaining * 1000))))
                    if evidence["_active"] is not scope or scope["attempt"] is not attempt:
                        await route.abort()
                        return
                    if 300 <= response.status < 400:
                        # Never fulfill any 30x, including malformed/unsupported
                        # redirects: the browser might follow it without routing.
                        location = response.headers.get("location")
                        if response.status not in (301, 302, 303, 307, 308) or not isinstance(location, str) or not location:
                            await route.abort()
                            return
                        following = urljoin(target, location)
                        if scope["remaining"] <= 0 or not allowed_session_url(following):
                            await route.abort()
                            return
                        await response.dispose()
                        response = None
                        target = following
                        continue
                    evidence["final_url"] = target
                    if target != request.url:
                        if scope["remaining"] <= 0:
                            await route.abort()
                            return
                        # Only this intentional abort can authorize a relocation.
                        scope["pending"] = {"attempt": attempt, "url": target, "request": request}
                        await route.abort("aborted")
                        return
                    headers = {key: value for key, value in response.headers.items()
                               if key.lower() not in {"set-cookie", "content-encoding", "content-length", "transfer-encoding"}}
                    body = await response.body()
                    if evidence["_active"] is not scope or scope["attempt"] is not attempt:
                        await route.abort()
                        return
                    await route.fulfill(status=response.status, headers=headers, body=body)
                    return
            finally:
                if response is not None:
                    try:
                        await response.dispose()
                    except Exception:
                        pass

        try:
            remaining = scope["deadline"] - time.monotonic()
            if remaining <= 0:
                await route.abort()
            else:
                await asyncio.wait_for(fetch_navigation(), timeout=remaining)
        except asyncio.CancelledError:
            scope["pending"] = None
            # Settle Chromium's pending navigation after the wrapper revokes
            # the scope; this abort cannot authorize a continuation.
            try:
                await route.abort("aborted")
            except Exception:
                pass
            raise
        except Exception:
            scope["pending"] = None
            await route.abort()
        finally:
            scope["tasks"].discard(task)

    await context.route("**/*", guard)
    return evidence


async def navigate_session_page(page, url, guard, timeout=30000):
    """One logical GET navigation, bounded across redirects and final refetch.

    Returns the successful goto response; guard['final_url'] is retained. Only
    this helper's intentional ERR_ABORTED plus matching single-use evidence can
    trigger another goto. Errors, forbidden targets and cancellation never do.
    """
    from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

    if guard.get("_active") is not None:
        raise SessionError("unknown")
    guard["final_url"] = ""
    if (page.context is not guard.get("_context") or not allowed_session_url(url)
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise SessionError("unknown")
    scope = {"page": page, "frame": page.main_frame, "token": object(), "attempt": None,
             "deadline": time.monotonic() + timeout / 1000, "remaining": 6,
             "pending": None, "tasks": set()}
    guard["_active"] = scope
    succeeded = False
    navigation_task = None
    try:
        target = url
        while True:
            remaining = scope["deadline"] - time.monotonic()
            if remaining <= 0 or scope["remaining"] <= 0:
                raise SessionError("unknown")
            attempt = {"url": target, "claimed": False}
            scope["attempt"] = attempt
            scope["pending"] = None
            try:
                # Keep goto alive long enough to retrieve its abort result
                # during cleanup, rather than cancelling Playwright's waiter.
                navigation_task = asyncio.create_task(
                    page.goto(target, wait_until="domcontentloaded", timeout=0))
                response = await asyncio.wait_for(asyncio.shield(navigation_task), timeout=remaining)
            except PlaywrightError as error:
                pending = scope["pending"]
                scope["pending"] = None  # Consume before any second navigation.
                if (isinstance(error, PlaywrightTimeoutError)
                        or not re.match(r"^(?:Page\.goto: )?net::ERR_ABORTED(?: at |$)", str(error))
                        or guard["_active"] is not scope
                        or pending is None or pending["attempt"] is not attempt
                        or pending["request"].frame is not scope["frame"]
                        or pending["request"].url != attempt["url"]
                        or not allowed_session_url(pending["url"])):
                    raise
                target = pending["url"]
                continue
            succeeded = True
            return response
    finally:
        scope["pending"] = None
        scope["attempt"] = None
        if guard["_active"] is scope:
            guard["_active"] = None
        if not succeeded:
            guard["final_url"] = ""
        tasks = tuple(scope["tasks"])
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if navigation_task is not None and not navigation_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(navigation_task), timeout=CLEANUP_TIMEOUT_SECONDS)
            except (Exception, asyncio.CancelledError):
                navigation_task.cancel()
                await asyncio.gather(navigation_task, return_exceptions=True)


async def verify_cookies(cookies, proxy_config=None) -> dict:
    """One bounded, ephemeral browser check. No login/checkpoint interactions."""
    route = "proxy" if proxy_config is not None else "direct"
    try:
        normalized = normalize_cookies(cookies)
    except SessionError as error:
        return _check_result(error.state, route)
    if chromium_proxy_unsupported(proxy_config):
        return _check_result("proxy_unsupported", route)

    async def check():
        playwright = browser = context = None
        try:
            playwright = await _playwright_factory().start()
            launch_options = {"headless": True}
            context_options = {"service_workers": "block"}
            if proxy_config is not None:
                from proxy_utils import playwright_proxy_settings
                if not isinstance(proxy_config, dict) or not proxy_config.get("enabled", True):
                    return "unknown"
                proxy = playwright_proxy_settings(proxy_config)
                if sys.platform == "win32":
                    launch_options["proxy"] = proxy
                else:
                    context_options["proxy"] = proxy
            browser = await playwright.chromium.launch(**launch_options)
            context = await browser.new_context(**context_options)
            await context.add_cookies(cookies_to_playwright(normalized))
            navigation = await install_session_navigation_guard(context)
            page = await context.new_page()
            await navigate_session_page(page, "https://www.threads.com/", navigation, timeout=18000)
            # Hydration may arrive after DOMContentLoaded; do not infer auth from navigation.
            for _ in range(4):
                state = auth_evidence(await page.content(), navigation["final_url"] or page.url)
                if state != "unknown":
                    return state
                await asyncio.sleep(0.25)
            return "unknown"
        finally:
            await _cleanup_resources(context, browser, playwright)

    try:
        state = await asyncio.wait_for(check(), timeout=VERIFY_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        state = "unknown"
    return _check_result(state, route)


class ThreadsSession:
    """Cached local state with opaque generations and stale-check protection.

    status/snapshot/clear are blocking disk entry points; callers should use their
    blocking executor. Async methods use to_thread for native secret-store I/O.
    """

    def __init__(self, base_dir, store=None):
        self.store = store if store is not None else SecureStore(base_dir)
        self._loaded = False
        self._cookies = None
        self._generation = uuid.uuid4().hex
        self._state = "none"
        self._checked_at = None
        self._lock = asyncio.Lock()
        self._state_lock = threading.RLock()
        self._verify_sequence = 0

    def _load(self):
        with self._state_lock:
            if self._loaded:
                return
            try:
                cookies = self.store.load()
                self._cookies = _normalize(cookies, allow_expired=True) if cookies is not None else None
                self._state = "expired" if self._cookies and _expired(self._cookies) else "unchecked" if self._cookies else "none"
                self._loaded = True
            except Exception:
                # Stay unloaded: a briefly locked keychain is retried on next access.
                self._state = "storage_unavailable"
                self._cookies = None

    def status(self) -> dict:
        with self._state_lock:
            self._load()
            state = "expired" if self._cookies and _expired(self._cookies) else self._state
            return {"configured": self._cookies is not None, "state": state, "count": len(self._cookies or ()),
                    "generation": self._generation, "checkedAt": self._checked_at, "message": _MESSAGES[state]}

    def snapshot(self, generation=None) -> tuple[dict, ...]:
        with self._state_lock:
            self._load()
            if generation is not None and generation != self._generation:
                raise SessionError("stale")
            if not self._cookies:
                raise SessionError("storage_unavailable" if self._state == "storage_unavailable" else "none")
            if _expired(self._cookies):
                raise SessionError("expired")
            return tuple(copy.deepcopy(self._cookies))

    def _commit(self, cookies, generation, result):
        with self._state_lock:
            if generation != self._generation:
                raise SessionError("stale")
            try:
                self.store.save(cookies)
            except Exception:
                raise SessionError("storage_unavailable") from None
            self._cookies = copy.deepcopy(cookies)
            self._loaded = True
            self._generation = uuid.uuid4().hex
            self._state = "valid"
            self._checked_at = result["checkedAt"]

    async def import_cookie(self, payload) -> dict:
        async with self._lock:
            await asyncio.to_thread(self._load)
            generation = self._generation
            try:
                cookies = normalize_cookies(payload)
            except SessionError as error:
                return {"success": False, "status": self.status(), "check": _check_result(error.state)}
            result = await verify_cookies(cookies)
            if result["state"] != "valid":
                return {"success": False, "status": self.status(), "check": result}
            # A cancelled to_thread continues running. Shield the transaction and
            # settle it before releasing the import lock or propagating cancellation.
            commit = asyncio.create_task(asyncio.to_thread(self._commit, cookies, generation, result))
            try:
                await asyncio.shield(commit)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(commit)
                except Exception:
                    pass
                raise
            return {"success": True, "status": self.status(), "check": result}

    async def verify(self) -> dict:
        await asyncio.to_thread(self._load)
        with self._state_lock:
            generation = self._generation
            self._verify_sequence += 1
            sequence = self._verify_sequence
        try:
            cookies = self.snapshot(generation)
        except SessionError as error:
            return {"success": False, "status": self.status(), "check": _check_result(error.state)}
        result = await verify_cookies(cookies)
        with self._state_lock:
            if generation != self._generation or sequence != self._verify_sequence:
                raise SessionError("stale")
            self._state = result["state"]
            self._checked_at = result["checkedAt"]
            return {"success": result["state"] == "valid", "status": self.status(), "check": result}

    def invalidate(self, generation, state="unknown") -> dict:
        """Record scan auth failure without removing or replacing saved cookies."""
        with self._state_lock:
            self._load()
            if generation != self._generation or self._cookies is None or state in _NOT_COOKIE_EVIDENCE:
                return self.status()
            self._state = state if state in {"invalid", "checkpoint", "unknown"} else "unknown"
            self._checked_at = _timestamp()
            # A previously started verification must not overwrite this newer
            # scan evidence even though the cookie generation did not change.
            self._verify_sequence += 1
            return self.status()

    def clear(self) -> dict:
        with self._state_lock:
            self._load()
            try:
                self.store.delete()
            except Exception:
                raise SessionError("storage_unavailable") from None
            self._cookies = None
            self._loaded = True
            self._generation = uuid.uuid4().hex
            self._state = "none"
            self._checked_at = None
            return self.status()
