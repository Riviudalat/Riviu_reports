"""Build the bundled FastAPI server with PyInstaller for a Tauri target."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIDECAR_NAME = "riviu-server"


def default_target() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc"
    if sys.platform == "darwin":
        return "aarch64-apple-darwin" if machine in {"arm64", "aarch64"} else "x86_64-apple-darwin"
    raise RuntimeError("Pass --target when building this sidecar on an unsupported platform.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=os.environ.get("TAURI_TARGET_TRIPLE", default_target()))
    args = parser.parse_args()

    extension = ".exe" if sys.platform == "win32" else ""
    build_root = ROOT / "build" / "desktop-sidecar"
    dist_dir = build_root / "dist"
    work_dir = build_root / "work"
    spec_dir = build_root / "spec"
    output_dir = ROOT / "src-tauri" / "binaries"
    output_dir.mkdir(parents=True, exist_ok=True)

    shutil.rmtree(build_root, ignore_errors=True)
    for directory in (dist_dir, work_dir, spec_dir):
        directory.mkdir(parents=True, exist_ok=True)

    add_data = lambda source, target: f"{source}{os.pathsep}{target}"
    playwright_env = os.environ.copy()
    # Store Chromium under the Playwright package so PyInstaller collects it.
    playwright_env["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        cwd=ROOT,
        check=True,
        env=playwright_env,
    )
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        SIDECAR_NAME,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--add-data",
        add_data(ROOT / "templates", "templates"),
        "--add-data",
        add_data(ROOT / "static", "static"),
        "--add-data",
        add_data(ROOT / "logo.png", "."),
        "--collect-all",
        "uvicorn",
        "--collect-all",
        "jinja2",
        "--collect-all",
        "playwright",
        str(ROOT / "desktop_server.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True, env=playwright_env)

    built_binary = dist_dir / f"{SIDECAR_NAME}{extension}"
    if not built_binary.exists():
        raise FileNotFoundError(f"PyInstaller did not create {built_binary}")

    bundled_binary = output_dir / f"{SIDECAR_NAME}-{args.target}{extension}"
    shutil.copy2(built_binary, bundled_binary)
    print(bundled_binary)


if __name__ == "__main__":
    main()
