"""Which storage backend this process uses, and the settings of the Postgres one.

``RIVIU_STORAGE`` selects the backend:

- ``workbook`` (default): today's `.xlsx` workbooks. Desktop and source mode always use it.
- ``postgres``: the Riviu Data Postgres (server mode only). Scans started by
  ``POST /internal/scan`` read and write the ``tiktok`` / ``threads`` tables as the
  column-limited role ``riviu_scanner``; ``/riviu-data/*`` reports read the views.

``dual`` (workbook first, Postgres shadow, integration-and-ops.md §1.4) is not built;
asking for it refuses to start, like any other unknown value.

Environment (postgres only):
    RIVIU_DATABASE_URL_FILE   file holding the DSN postgresql://riviu_scanner:<pw>@host:port/riviu_data
    RIVIU_DATABASE_URL        ... or the DSN itself (the file is preferred: no secret in the env)
    RIVIU_SCAN_TOKEN_FILE     bearer tokens for POST /internal/scan, one per line:
                              "<token> [scope,scope]" (scopes default to scan:run), or
                              "sha256:<hex digest of the token> [scopes]"; # starts a comment

A scan requested through /internal/scan uses the admin's scan options (the schedule's: workers,
mode, proxy, cookie session), like a timed scan; there are no scan settings in the environment.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from riviu import server_mode

BACKENDS = ("workbook", "postgres")
SCANNER_ROLE = "riviu_scanner"
SCAN_SCOPE = "scan:run"
MIN_TOKEN_LENGTH = 32


class StoreConfigError(RuntimeError):
    """A storage setting is missing or invalid; the server must not start."""


@dataclass(frozen=True)
class ScanToken:
    digest: bytes  # SHA-256 of the token
    scopes: frozenset


@dataclass(frozen=True)
class StoreConfig:
    backend: str = "workbook"
    database_url: str = field(default="", repr=False)
    scan_tokens: tuple = field(default=(), repr=False)

    @property
    def postgres(self) -> bool:
        return self.backend == "postgres"


WORKBOOK = StoreConfig()
# The configuration of the running process (workbook unless activated otherwise).
ACTIVE: StoreConfig = WORKBOOK


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def parse_scan_tokens(text: str) -> tuple:
    tokens = []
    for number, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) > 2:
            raise StoreConfigError(f"RIVIU_SCAN_TOKEN_FILE dòng {number}: dạng đúng là '<token> [scope,scope]'.")
        secret = parts[0]
        scopes = frozenset(scope for scope in (parts[1].split(",") if len(parts) == 2 else [SCAN_SCOPE]) if scope)
        if secret.startswith("sha256:"):
            hex_digest = secret.removeprefix("sha256:")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", hex_digest):
                raise StoreConfigError(f"RIVIU_SCAN_TOKEN_FILE dòng {number}: sha256 phải có 64 ký tự hex.")
            digest = bytes.fromhex(hex_digest)
        else:
            if len(secret) < MIN_TOKEN_LENGTH:
                raise StoreConfigError(f"RIVIU_SCAN_TOKEN_FILE dòng {number}: token cần ít nhất {MIN_TOKEN_LENGTH} ký tự.")
            digest = token_digest(secret)
        tokens.append(ScanToken(digest=digest, scopes=scopes))
    return tuple(tokens)


def _read_file(env_name: str, path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read()
    except OSError as error:
        raise StoreConfigError(f"Không đọc được {env_name} ({path}): {error.strerror}") from None


def database_user(dsn: str) -> str:
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return ""
    if parts.scheme not in {"postgresql", "postgres"}:
        return ""
    return unquote(parts.username or "")



def load_config(env=None) -> StoreConfig:
    env = os.environ if env is None else env
    backend = str(env.get("RIVIU_STORAGE", "")).strip().lower() or "workbook"
    if backend not in BACKENDS:
        raise StoreConfigError(f"RIVIU_STORAGE={backend!r} không hỗ trợ; dùng 'workbook' hoặc 'postgres'.")
    if backend == "workbook":
        return WORKBOOK
    # Postgres is never reachable from staff PCs: desktop and source mode stay workbook-only.
    if getattr(sys, "frozen", False) or not server_mode.enabled(env):
        raise StoreConfigError("RIVIU_STORAGE=postgres chỉ dùng được ở chế độ server (RIVIU_SERVER_MODE=1).")

    dsn_file = str(env.get("RIVIU_DATABASE_URL_FILE", "")).strip()
    dsn = _read_file("RIVIU_DATABASE_URL_FILE", dsn_file).strip() if dsn_file else str(env.get("RIVIU_DATABASE_URL", "")).strip()
    if not dsn:
        raise StoreConfigError("RIVIU_STORAGE=postgres cần RIVIU_DATABASE_URL_FILE (hoặc RIVIU_DATABASE_URL).")
    user = database_user(dsn)
    if user != SCANNER_ROLE:
        raise StoreConfigError(
            f"RIVIU_DATABASE_URL phải là DSN postgresql:// đăng nhập bằng role {SCANNER_ROLE} (đang là {user or 'không rõ'})."
        )

    token_file = str(env.get("RIVIU_SCAN_TOKEN_FILE", "")).strip()
    tokens = parse_scan_tokens(_read_file("RIVIU_SCAN_TOKEN_FILE", token_file)) if token_file else ()

    return StoreConfig(
        backend="postgres",
        database_url=dsn,
        scan_tokens=tokens,
    )


def activate_from_environment(env=None) -> StoreConfig:
    """Load once per process (riviu.app calls it at import); later calls return the active config."""
    global ACTIVE
    if ACTIVE is WORKBOOK:
        ACTIVE = load_config(env)
    return ACTIVE
