"""Opt-in server mode: one shared password in front of the app, hosted behind nginx.

Off unless ``RIVIU_SERVER_MODE=1``; source mode never reads the other
variables. ``load_config`` refuses to start (``ServerModeError``) when a required
setting is missing, so a misconfigured server never comes up without a login.

Environment:
    RIVIU_SERVER_MODE          1/true/yes/on enables server mode
    RIVIU_HOST, RIVIU_PORT     bind address (default 127.0.0.1:1231)
    RIVIU_PUBLIC_ORIGIN        e.g. https://113-161-254-76.sslip.io (required)
    RIVIU_PASSWORD_HASH        output of `python -m riviu.server_mode hash-password`
    RIVIU_PASSWORD_HASH_FILE   ... or a file holding it (required: one of the two)
    RIVIU_SECRET_KEY_FILE      32-byte base64 key that encrypts the Threads and TikTok cookie
                               stores and the Google service-account key at rest (required),
                               owner-only (0600) and outside the data dir
    RIVIU_FORWARDED_ALLOW_IPS  proxies whose X-Forwarded-* headers are trusted (default 127.0.0.1)
    RIVIU_SESSION_DAYS         login lifetime in days (default 7)
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import secrets
import stat
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit

from riviu import paths


TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1231
DEFAULT_SESSION_DAYS = 7
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 2 ** 26
HASH_PREFIX = "scrypt"
SECRET_KEY_BYTES = 32


class ServerModeError(RuntimeError):
    """A server-mode setting is missing or invalid; the server must not start."""


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    public_origin: str
    public_scheme: str
    public_host: str
    public_port: int
    password_hash: str
    secret_key_file: str
    data_dir: str
    forwarded_allow_ips: str = "127.0.0.1"
    session_seconds: int = DEFAULT_SESSION_DAYS * 86400

    @property
    def sessions_path(self) -> str:
        return os.path.join(self.data_dir, "data", "server_sessions.json")


# The configuration of the running process; None means source mode.
ACTIVE: ServerConfig | None = None


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("RIVIU_SERVER_MODE", "")).strip().lower() in TRUE_VALUES


# ---- Password hashing ----------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    """`scrypt$n$r$p$salt$hash` with a random 16-byte salt."""
    if not password:
        raise ValueError("Mật khẩu không được để trống.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=32)
    return f"{HASH_PREFIX}${n}${r}${p}${_b64(salt)}${_b64(digest)}"


def _parse_hash(encoded: str):
    parts = str(encoded or "").strip().split("$")
    if len(parts) != 6 or parts[0] != HASH_PREFIX:
        raise ValueError("RIVIU_PASSWORD_HASH không đúng định dạng scrypt$n$r$p$salt$hash.")
    n, r, p = (int(value) for value in parts[1:4])
    if n < 2 ** 14 or n & (n - 1) or not 1 <= r <= 32 or not 1 <= p <= 16:
        raise ValueError("Tham số scrypt trong RIVIU_PASSWORD_HASH không hợp lệ.")
    salt, digest = _unb64(parts[4]), _unb64(parts[5])
    if len(salt) < 16 or len(digest) < 32:
        raise ValueError("RIVIU_PASSWORD_HASH không hợp lệ.")
    return n, r, p, salt, digest


def verify_password(password: str, encoded: str) -> bool:
    try:
        n, r, p, salt, expected = _parse_hash(encoded)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.scrypt(
        str(password or "").encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=len(expected),
    )
    return hmac.compare_digest(candidate, expected)


def password_fingerprint(encoded: str) -> str:
    """Binds login sessions to the current password: changing the hash logs everyone out."""
    return hashlib.sha256(str(encoded).strip().encode("utf-8")).hexdigest()[:32]


# ---- Secret key for the encrypted stores (cookies, service-account key) ---------------------------------------

def read_secret_key(path: str) -> bytes:
    """The 32-byte key in `path` (base64 text, as `openssl rand -base64 32` writes it)."""
    try:
        info = os.stat(path)
        with open(path, "rb") as stream:
            text = stream.read(4096).strip()
    except OSError as error:
        raise ServerModeError(f"Không đọc được RIVIU_SECRET_KEY_FILE ({path}): {error.strerror}") from None
    if os.name == "posix" and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ServerModeError(f"RIVIU_SECRET_KEY_FILE ({path}) phải có quyền 0600 (chỉ chủ sở hữu đọc được).")
    try:
        key = base64.b64decode(text, validate=True)
    except ValueError:
        key = b""
    if len(key) != SECRET_KEY_BYTES:
        raise ServerModeError(
            "RIVIU_SECRET_KEY_FILE phải chứa 32 byte dạng base64 (tạo bằng `openssl rand -base64 32`)."
        )
    return key


def _inside(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


# ---- Configuration -----------------------------------------------------------------

def _parse_origin(value: str):
    origin = str(value or "").strip().rstrip("/")
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        parts, port = None, None
    if (
        not parts or parts.scheme not in {"http", "https"} or not parts.hostname
        or parts.username or parts.password or parts.path or parts.query or parts.fragment
    ):
        raise ServerModeError(
            "RIVIU_PUBLIC_ORIGIN phải là địa chỉ gốc dạng https://ten-mien (không có đường dẫn)."
        )
    port = port or (443 if parts.scheme == "https" else 80)
    default = (parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80)
    host = parts.hostname.lower()
    normalized = f"{parts.scheme}://{host}" + ("" if default else f":{port}")
    return normalized, parts.scheme, host, port


def load_config(env=None) -> ServerConfig | None:
    """None when server mode is off; otherwise a validated config or ServerModeError."""
    env = os.environ if env is None else env
    if not enabled(env):
        return None
    missing = []
    origin_text = str(env.get("RIVIU_PUBLIC_ORIGIN", "")).strip()
    if not origin_text:
        missing.append("RIVIU_PUBLIC_ORIGIN")
    password_hash = str(env.get("RIVIU_PASSWORD_HASH", "")).strip()
    hash_file = str(env.get("RIVIU_PASSWORD_HASH_FILE", "")).strip()
    if not password_hash and hash_file:
        try:
            with open(hash_file, "r", encoding="utf-8") as stream:
                password_hash = stream.read().strip()
        except OSError as error:
            raise ServerModeError(f"Không đọc được RIVIU_PASSWORD_HASH_FILE ({hash_file}): {error.strerror}") from None
    if not password_hash:
        missing.append("RIVIU_PASSWORD_HASH (hoặc RIVIU_PASSWORD_HASH_FILE)")
    secret_key_file = str(env.get("RIVIU_SECRET_KEY_FILE", "")).strip()
    if not secret_key_file:
        missing.append("RIVIU_SECRET_KEY_FILE")
    if missing:
        raise ServerModeError("Chế độ server thiếu cấu hình: " + ", ".join(missing))

    public_origin, scheme, public_host, public_port = _parse_origin(origin_text)
    try:
        _parse_hash(password_hash)
    except ValueError as error:
        raise ServerModeError(str(error)) from None

    data_dir = os.path.abspath(str(env.get("RIVIU_DATA_DIR", "")).strip() or paths.REPO_ROOT)
    if _inside(secret_key_file, data_dir):
        raise ServerModeError("RIVIU_SECRET_KEY_FILE phải nằm ngoài thư mục dữ liệu (RIVIU_DATA_DIR).")
    read_secret_key(secret_key_file)

    try:
        port = int(str(env.get("RIVIU_PORT", "")).strip() or DEFAULT_PORT)
        days = float(str(env.get("RIVIU_SESSION_DAYS", "")).strip() or DEFAULT_SESSION_DAYS)
    except ValueError:
        raise ServerModeError("RIVIU_PORT / RIVIU_SESSION_DAYS phải là số.") from None
    if not 1 <= port <= 65535 or not 0 < days <= 365:
        raise ServerModeError("RIVIU_PORT hoặc RIVIU_SESSION_DAYS nằm ngoài khoảng cho phép.")

    return ServerConfig(
        host=str(env.get("RIVIU_HOST", "")).strip() or DEFAULT_HOST,
        port=port,
        public_origin=public_origin,
        public_scheme=scheme,
        public_host=public_host,
        public_port=public_port,
        password_hash=password_hash,
        secret_key_file=os.path.abspath(secret_key_file),
        data_dir=data_dir,
        forwarded_allow_ips=str(env.get("RIVIU_FORWARDED_ALLOW_IPS", "")).strip() or "127.0.0.1",
        session_seconds=int(days * 86400),
    )


def activate_from_environment(env=None) -> ServerConfig | None:
    """Load once per process; later calls return the active config."""
    global ACTIVE
    if ACTIVE is None:
        ACTIVE = load_config(env)
    return ACTIVE


def threads_cookie_store(base_dir):
    """The encrypted-file cookie store in server mode; None keeps the OS-native store."""
    config = ACTIVE
    if config is None:
        return None
    from riviu.platforms.threads_session import EncryptedFileStore

    return EncryptedFileStore(base_dir, read_secret_key(config.secret_key_file))


def tiktok_cookie_store(base_dir):
    """The TikTok cookie vault: same key file, its own file and binding; None off server mode."""
    config = ACTIVE
    if config is None:
        return None
    from riviu.platforms.threads_session import EncryptedFileStore
    from riviu.platforms.tiktok_session import TIKTOK_STORE

    return EncryptedFileStore(base_dir, read_secret_key(config.secret_key_file), spec=TIKTOK_STORE)


def secret_key():
    """The server's 32-byte at-rest key (RIVIU_SECRET_KEY_FILE), or None off server mode."""
    config = ACTIVE
    return None if config is None else read_secret_key(config.secret_key_file)


# ---- CLI -----------------------------------------------------------------------------

def _read_new_password(stdin=None) -> str:
    stdin = sys.stdin if stdin is None else stdin
    if stdin.isatty():
        first = getpass.getpass("Mật khẩu mới: ")
        if first != getpass.getpass("Nhập lại: "):
            raise ValueError("Hai lần nhập không khớp.")
        return first
    return stdin.readline().rstrip("\r\n")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv != ["hash-password"]:
        print("Dùng: python -m riviu.server_mode hash-password  (đọc mật khẩu từ stdin hoặc hỏi ẩn)", file=sys.stderr)
        return 2
    try:
        password = _read_new_password()
        if len(password) < 8:
            raise ValueError("Mật khẩu cần ít nhất 8 ký tự.")
        print(hash_password(password))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
