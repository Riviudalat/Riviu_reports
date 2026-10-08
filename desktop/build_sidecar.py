"""Build the bundled FastAPI server with PyInstaller for a Tauri target."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

try:
    from bundle_contents import BUNDLED_BROWSERS_ENV, browser_dirs_from_install_dry_run, bundled_browser_dirs
except ModuleNotFoundError:  # imported as desktop.build_sidecar (tests)
    from desktop.bundle_contents import BUNDLED_BROWSERS_ENV, browser_dirs_from_install_dry_run, bundled_browser_dirs


ROOT = Path(__file__).resolve().parents[1]
SIDECAR_NAME = "riviu-server"
# Templates, static files and the logo keep their package-relative path in the
# bundle, so riviu/paths.py finds them under sys._MEIPASS the same way it finds
# them under the repository.
WEB_ASSETS_TARGET = "riviu/web"


def bundle_paths(built: Path) -> list[str]:
    """Paths of the files in a one-folder build, or in a one-file executable's archive."""
    if built.is_dir():
        return [path.relative_to(built).as_posix() for path in built.rglob("*")]
    from PyInstaller.archive.readers import CArchiveReader

    return list(CArchiveReader(str(built)).toc)


def check_bundled_browsers(built: Path, expected: Iterable[str]) -> None:
    """Fail the build unless it holds exactly the expected browser folders."""
    found = bundled_browser_dirs(bundle_paths(built))
    if found != set(expected):
        raise RuntimeError(f"Bundled browsers {sorted(found)} do not match {sorted(expected)}")


def uses_onedir(platform_name: str) -> bool:
    """Windows ships a one-folder build as Tauri resources (see docs/desktop-release.md).

    A one-file build unpacks Python and the bundled Chromium into a fresh temp
    folder on every launch, which Windows Defender then rescans. macOS and Linux
    keep the one-file externalBin sidecar, because Tauri's resource copy does not
    keep the directory symlinks that the nested Chromium.app framework and
    PyInstaller's symlinked libraries rely on.
    """
    return platform_name == "win32"


def default_target() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc"
    if sys.platform == "darwin":
        return "aarch64-apple-darwin" if machine in {"arm64", "aarch64"} else "x86_64-apple-darwin"
    if sys.platform == "linux":
        if machine in {"x86_64", "amd64"}:
            return "x86_64-unknown-linux-gnu"
        if machine in {"aarch64", "arm64"}:
            return "aarch64-unknown-linux-gnu"
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
    onedir = uses_onedir(sys.platform)

    shutil.rmtree(build_root, ignore_errors=True)
    for directory in (dist_dir, work_dir, spec_dir):
        directory.mkdir(parents=True, exist_ok=True)

    add_data = lambda source, target: f"{source}{os.pathsep}{target}"
    playwright_env = os.environ.copy()
    # Store Chromium under the Playwright package so PyInstaller collects it.
    playwright_env["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    # Every launch is headless, which Playwright runs with chromium-headless-shell,
    # so the full Chromium build is never needed.
    playwright_cli = [sys.executable, "-m", "playwright", "install"]
    subprocess.run([*playwright_cli, "--only-shell", "chromium"], cwd=ROOT, check=True, env=playwright_env)
    dry_run = subprocess.run(
        [*playwright_cli, "--dry-run", "--only-shell", "chromium"],
        cwd=ROOT,
        check=True,
        env=playwright_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    browser_dirs = browser_dirs_from_install_dry_run(dry_run.stdout)
    # The Playwright hook keeps only these folders, so a full Chromium left in
    # the package-local browsers folder by an earlier install is not bundled.
    playwright_env[BUNDLED_BROWSERS_ENV] = os.pathsep.join(browser_dirs)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir" if onedir else "--onefile",
        "--name",
        SIDECAR_NAME,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--additional-hooks-dir",
        str(ROOT / "desktop" / "pyinstaller-hooks"),
        "--paths",
        str(ROOT),
        "--add-data",
        add_data(ROOT / WEB_ASSETS_TARGET, WEB_ASSETS_TARGET),
        "--collect-all",
        "uvicorn",
        "--collect-all",
        "jinja2",
        str(ROOT / "desktop" / "sidecar_entry.py"),
    ]
    # PyInstaller's default keyring hook collects *all* backends. Override that
    # hook locally for this build; the runtime explicitly instantiates only the
    # native backend and never uses plugin/chain discovery.
    native_backend = {
        "darwin": "keyring.backends.macOS",
        "linux": "keyring.backends.SecretService",
    }.get(sys.platform)
    if native_backend:
        native_hooks = build_root / "native-hooks"
        native_hooks.mkdir()
        (native_hooks / "hook-keyring.py").write_text(
            "from PyInstaller.utils.hooks import copy_metadata\n"
            f"hiddenimports = [{native_backend!r}]\n"
            "datas = copy_metadata('keyring')\n",
            encoding="utf-8",
        )
        command[-1:-1] = [
            "--additional-hooks-dir", str(native_hooks),
            "--hidden-import", native_backend,
            "--exclude-module", "keyrings.alt",
            "--exclude-module", "keyring.backends.chainer",
        ]
        if sys.platform == "linux":
            command[-1:-1] = ["--collect-all", "secretstorage", "--collect-all", "jeepney"]
    else:
        # Windows uses ctypes DPAPI and does not install or bundle keyring.
        command[-1:-1] = ["--exclude-module", "keyring"]
    subprocess.run(command, cwd=ROOT, check=True, env=playwright_env)

    if onedir:
        # tauri.windows.conf.json maps this folder to <install dir>/riviu-server.
        built_dir = dist_dir / SIDECAR_NAME
        if not (built_dir / f"{SIDECAR_NAME}{extension}").exists():
            raise FileNotFoundError(f"PyInstaller did not create {built_dir}")
        check_bundled_browsers(built_dir, browser_dirs)
        bundled_dir = output_dir / SIDECAR_NAME
        shutil.rmtree(bundled_dir, ignore_errors=True)
        shutil.copytree(built_dir, bundled_dir)
        print(bundled_dir)
        return

    built_binary = dist_dir / f"{SIDECAR_NAME}{extension}"
    if not built_binary.exists():
        raise FileNotFoundError(f"PyInstaller did not create {built_binary}")
    check_bundled_browsers(built_binary, browser_dirs)

    bundled_binary = output_dir / f"{SIDECAR_NAME}-{args.target}{extension}"
    shutil.copy2(built_binary, bundled_binary)
    print(bundled_binary)


if __name__ == "__main__":
    main()
