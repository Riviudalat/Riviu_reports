# Riviu Reports Desktop

Riviu Reports ships as a Tauri desktop application for Windows, macOS, and Linux. The
Tauri shell launches the bundled FastAPI server only on `127.0.0.1`, then loads
the existing report UI in the desktop window. Chromium is bundled with the
sidecar, so a fresh desktop installation can run scans without a separate
Playwright browser install.

## Local development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm install
npm run desktop:dev
```

On Windows, use `.venv\\Scripts\\python.exe` in place of `.venv/bin/python`.

`desktop:dev` first builds the current platform's `riviu-server` sidecar. Data
created through the desktop app is stored in the operating system's app-data
directory rather than in the installed application bundle.

## Server packaging

`desktop/build_sidecar.py` builds `riviu-server` with PyInstaller. Chromium is
bundled inside it (`PLAYWRIGHT_BROWSERS_PATH=0`). The layout depends on the
platform:

- **Windows: one-folder build shipped as bundle resources.** The build is copied
  to `src-tauri/binaries/riviu-server/`. `tauri.windows.conf.json` maps that
  folder to `<install dir>\riviu-server\`, and `lib.rs` starts
  `riviu-server\riviu-server.exe` from the resource dir. A one-file build would
  unpack Python and Chromium (about 1 GB) into a new `%TEMP%\_MEI*` folder on
  every launch, and Windows Defender rescans it each time. On a test machine the
  server took 24-31 s to answer its first request as one file, and 1.6-2.7 s as
  one folder (7.7 s on the very first launch, while Defender scans the newly
  installed files). In the installed app it answered about 2.2 s after the app
  process started. The trade-off is that the installer now writes about 2,600
  files. A silent install took 40-65 s on the test machine.
- **macOS and Linux: one-file `externalBin` sidecar**
  (`binaries/riviu-server-<target triple>`, set in `tauri.macos.conf.json` and
  `tauri.linux.conf.json`). The one-folder layout depends on symlinks (the nested
  `Chromium.app` framework, and the libraries PyInstaller links into place), and
  Tauri's resource copy does not keep directory symlinks.

The deepest bundled Chromium file is about 160 characters below the
`riviu-server` folder. On Windows without long-path support, building from a
deeply nested checkout (for example a `.claude/worktrees/...` worktree) fails
with `FileNotFoundError` in PyInstaller's COLLECT step. Build from a short path.
The default per-user install path (`%LOCALAPPDATA%\Riviu Reports`) is well
within the limit.

## Release pipeline

Every push to `main` runs `.github/workflows/desktop-release.yml`. It creates a
GitHub Release using `0.1.<GitHub run number>`, builds these installers, signs
the updater payloads, and uploads `latest.json` with the platform artifacts:

- Windows x64 NSIS installer
- macOS Intel DMG
- macOS Apple Silicon DMG
- Linux x64 AppImage, DEB, and RPM packages

The installed app checks the release `latest.json` at startup and then every
five minutes. When a newer release is available, it downloads, installs, and
restarts automatically.

## Sidecar lifecycle

- At launch the Tauri shell generates a random 256-bit shutdown token (OS
  CSPRNG) and passes it to the sidecar as `RIVIU_SHUTDOWN_TOKEN`. The
  `/_desktop/prepare-update`, `/_desktop/cancel-update` and `/_desktop/shutdown`
  routes skip the session cookie, so they accept only this token (compared in
  constant time); an empty token rejects every request.
- The sidecar is spawned from Rust. The bundled loading page has only
  `core:default`, and the loopback UI receives just the `check_for_update` and
  `install_update` commands through the runtime `loopback-updater` capability;
  no web page gets shell permissions.
- When the app exits, the shell asks the sidecar to shut down and waits up to
  5 seconds for it to exit before it force-kills the process. The wait lets
  Python close Playwright and Chromium and, on macOS/Linux, lets the one-file
  bootloader remove its `_MEI*` temp folder. On Windows the force-kill uses
  `taskkill /T` to kill the whole process tree, because the one-folder build has
  no bootloader job object to take the children down with it.
- An update first calls `prepare-update`, which is refused while a scan or
  source operation is running. On Windows the updater starts the NSIS installer
  and ends the process without a normal exit event, so the sidecar is stopped in
  the updater's pre-exit hook. That keeps `riviu-server.exe` and Chromium from
  holding the executable open while the installer replaces it.
- Tauri's NSIS installer copies an update over the old files without
  uninstalling first. In update mode, `src-tauri/windows/installer-hooks.nsh`
  first deletes `<install dir>\riviu-server`, so files that the new build no
  longer ships (an old Chromium revision, stale extension modules) do not pile
  up. A manual install skips this step, because the app may still be running.
  The uninstaller deletes only the files it installed, so the same hook file
  also removes `riviu-server` after uninstalling. Otherwise runtime files such
  as Chromium's `debug.log` would keep the install folder behind.

The repository secrets `TAURI_SIGNING_PRIVATE_KEY` and
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD` are required for the updater and have been
configured in the repository. Keep the private key outside the repository.

For a trusted macOS install without Gatekeeper warnings, configure these
additional repository secrets before the first public macOS release:

- `APPLE_SIGNING_IDENTITY`
- `APPLE_CERTIFICATE`
- `APPLE_CERTIFICATE_PASSWORD`
- `APPLE_ID`
- `APPLE_PASSWORD`
- `APPLE_TEAM_ID`

The macOS secrets enable Apple's signing and notarization in the Tauri build.
