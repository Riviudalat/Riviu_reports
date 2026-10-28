"""Server-mode accounts: one login per person, each with a role.

Roles:
    admin   everything: scans, source changes, Google sync/push, proxy, cookie sessions
    viewer  read-only: browse the sheets and download partner reports

Accounts live in ``<data dir>/data/server_users.json`` as scrypt hashes (the same format as
RIVIU_PASSWORD_HASH). While the file has no account, the shared RIVIU_PASSWORD_HASH still
logs in as an admin so an existing server keeps working until its first account exists;
after that every login needs a username. Manage accounts with
``python -m riviu.server_mode add-user|set-password|set-role|remove-user|list-users``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

from riviu import server_mode
from riviu.workbook_utils import write_json_atomic


ROLE_ADMIN = "admin"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_ADMIN, ROLE_VIEWER)
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
USERS_FILENAME = "server_users.json"
MIN_PASSWORD_LENGTH = 8


class UserError(ValueError):
    """An account operation was refused (bad name, role or password, unknown user)."""


def normalize_username(value) -> str:
    return str(value or "").strip().lower()


def validate_username(value) -> str:
    username = normalize_username(value)
    if not USERNAME_PATTERN.match(username):
        raise UserError("Tên đăng nhập gồm 2-32 ký tự: chữ thường, số, dấu chấm, gạch ngang hoặc gạch dưới.")
    return username


def validate_role(value) -> str:
    role = str(value or "").strip().lower()
    if role not in ROLES:
        raise UserError(f"Vai trò phải là một trong: {', '.join(ROLES)}.")
    return role


def validate_password(value) -> str:
    password = str(value or "")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Mật khẩu cần ít nhất {MIN_PASSWORD_LENGTH} ký tự.")
    return password


class UserStore:
    """The accounts file. Reads are cached by mtime so role changes apply without a restart."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._cache = None
        self._cache_key = None

    def _read(self) -> dict:
        try:
            stat = os.stat(self.path)
        except OSError:
            self._cache, self._cache_key = {}, None
            return self._cache
        key = (stat.st_mtime_ns, stat.st_size)
        if self._cache is None or key != self._cache_key:
            try:
                with open(self.path, "r", encoding="utf-8") as stream:
                    payload = json.load(stream)
                users = payload.get("users", {}) if isinstance(payload, dict) else {}
            except (OSError, ValueError):
                users = {}
            self._cache = {
                name: record for name, record in users.items()
                if isinstance(record, dict) and record.get("role") in ROLES and record.get("hash")
            }
            self._cache_key = key
        return self._cache

    def users(self) -> dict:
        with self._lock:
            return {name: dict(record) for name, record in self._read().items()}

    def has_users(self) -> bool:
        with self._lock:
            return bool(self._read())

    def get(self, username) -> dict | None:
        with self._lock:
            record = self._read().get(normalize_username(username))
            return dict(record) if record else None

    def verify(self, username, password) -> dict | None:
        """The account when the password matches, else None (scrypt runs either way)."""
        record = self.get(username)
        # A dummy hash keeps the timing of unknown names close to that of wrong passwords.
        encoded = record["hash"] if record else _dummy_hash()
        if server_mode.verify_password(password, encoded) and record:
            return {"username": normalize_username(username), "role": record["role"], "hash": record["hash"]}
        return None

    def _write(self, users: dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        write_json_atomic(self.path, {"users": users})
        self._cache = None

    def add(self, username, password, role) -> None:
        username, role, password = validate_username(username), validate_role(role), validate_password(password)
        with self._lock:
            users = dict(self._read())
            if username in users:
                raise UserError(f"Tài khoản {username} đã tồn tại.")
            users[username] = {"hash": server_mode.hash_password(password), "role": role, "created": int(time.time())}
            self._write(users)

    def set_password(self, username, password) -> None:
        username, password = normalize_username(username), validate_password(password)
        with self._lock:
            users = dict(self._read())
            if username not in users:
                raise UserError(f"Không có tài khoản {username}.")
            # A new hash changes the fingerprint, which ends that person's open sessions.
            users[username] = {**users[username], "hash": server_mode.hash_password(password)}
            self._write(users)

    def set_role(self, username, role) -> None:
        username, role = normalize_username(username), validate_role(role)
        with self._lock:
            users = dict(self._read())
            if username not in users:
                raise UserError(f"Không có tài khoản {username}.")
            if users[username]["role"] == ROLE_ADMIN and role != ROLE_ADMIN and _admin_count(users) == 1:
                raise UserError("Phải còn ít nhất một tài khoản admin.")
            users[username] = {**users[username], "role": role}
            self._write(users)

    def remove(self, username) -> None:
        username = normalize_username(username)
        with self._lock:
            users = dict(self._read())
            if username not in users:
                raise UserError(f"Không có tài khoản {username}.")
            if users[username]["role"] == ROLE_ADMIN and _admin_count(users) == 1 and len(users) > 1:
                raise UserError("Phải còn ít nhất một tài khoản admin.")
            del users[username]
            self._write(users)


def _admin_count(users: dict) -> int:
    return sum(1 for record in users.values() if record.get("role") == ROLE_ADMIN)


_DUMMY = []
_STORES = {}


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(server_mode.hash_password("riviu-dummy-password"))
    return _DUMMY[0]


def users_path(data_dir: str) -> str:
    return os.path.join(data_dir, "data", USERS_FILENAME)


def store_for(data_dir: str) -> UserStore:
    path = users_path(data_dir)
    if path not in _STORES:
        _STORES[path] = UserStore(path)
    return _STORES[path]


def active_store() -> UserStore | None:
    config = server_mode.ACTIVE
    return None if config is None else store_for(config.data_dir)
