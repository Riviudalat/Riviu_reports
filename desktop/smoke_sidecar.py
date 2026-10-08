"""Smoke-test a built riviu-server sidecar (CI runs this after desktop/build_sidecar.py).

1. `--self-check`: the bundled headless Chromium launches.
2. The server starts with a temp data dir and serves the UI, the script and the logo.
3. The token-protected shutdown route stops it.
"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))
from build_sidecar import SIDECAR_NAME, default_target, uses_onedir  # noqa: E402

START_TIMEOUT = 120


def sidecar_path(target: str) -> Path:
    extension = ".exe" if sys.platform == "win32" else ""
    binaries = ROOT / "src-tauri" / "binaries"
    if uses_onedir(sys.platform):
        return binaries / SIDECAR_NAME / f"{SIDECAR_NAME}{extension}"
    return binaries / f"{SIDECAR_NAME}-{target}{extension}"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fetch(url: str, *, opener, method: str = "GET", headers=None) -> int:
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=b"" if method == "POST" else None)
    with opener.open(request, timeout=10) as response:
        response.read()
        return response.status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=os.environ.get("TAURI_TARGET_TRIPLE", default_target()))
    binary = sidecar_path(parser.parse_args().target)
    if not binary.exists():
        print(f"Sidecar not found: {binary}", file=sys.stderr)
        return 1

    subprocess.run([str(binary), "--self-check"], check=True, timeout=180)

    port, token = free_port(), secrets.token_hex(16)
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as data_dir:
        env = dict(os.environ, RIVIU_PORT=str(port), RIVIU_SHUTDOWN_TOKEN=token, RIVIU_DATA_DIR=data_dir)
        process = subprocess.Popen([str(binary)], env=env)
        try:
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
            started = time.monotonic()
            while True:
                try:
                    if fetch(f"{base}/", opener=opener) == 200:
                        break
                except (urllib.error.URLError, ConnectionError, TimeoutError):
                    pass
                if process.poll() is not None:
                    print(f"Sidecar exited early with code {process.returncode}", file=sys.stderr)
                    return 1
                if time.monotonic() - started > START_TIMEOUT:
                    print("Sidecar did not answer within the timeout", file=sys.stderr)
                    return 1
                time.sleep(0.5)
            print(f"Sidecar answered in {time.monotonic() - started:.1f} s", flush=True)
            for path in ("/static/app.js", "/logo.png"):
                status = fetch(f"{base}{path}", opener=opener)
                if status != 200:
                    print(f"{path} returned {status}", file=sys.stderr)
                    return 1
            fetch(f"{base}/_desktop/shutdown", opener=opener, method="POST", headers={"x-riviu-shutdown": token})
            process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=30)
    print("Sidecar smoke test passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
