# Riviu Reports Desktop

Riviu Reports ships as a Tauri desktop application for Windows, macOS, and Linux. The
Tauri shell launches the bundled FastAPI server only on `127.0.0.1`, then loads
the existing report UI in the desktop window. Playwright's Chromium headless
shell is bundled with the sidecar, so a fresh desktop installation can run scans
without a separate Playwright browser install.

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

`desktop/build_sidecar.py` builds `riviu-server` with PyInstaller. Its entry
script is `desktop/sidecar_entry.py`, which runs `riviu.desktop_server.main()`
with the repository root on PyInstaller's search path. The web assets in
`riviu/web/` (templates, static files, logo) are added at the same relative path
in the bundle, so `riviu/paths.py` finds them under `sys._MEIPASS` exactly as it
finds them in the repository. Chromium is bundled inside it
(`PLAYWRIGHT_BROWSERS_PATH=0`).

### What the bundle contains

Every browser launch in the app (`riviu/platforms/tiktok.py`,
`riviu/platforms/threads.py`, `riviu/platforms/threads_session.py`) is headless Chromium without a `channel`. Playwright runs
those launches with `chromium-headless-shell`, never with the full Chromium
build. The build therefore runs `playwright install --only-shell chromium`, and
the hooks in `desktop/pyinstaller-hooks/` (using `desktop/bundle_contents.py`)
leave out the following:

- **Every browser folder that the headless-shell install does not list.** This
  includes a full `chromium-<rev>` that an earlier `playwright install chromium`
  left in the package-local browsers folder. Nothing is deleted from that
  folder. The build fails if the finished bundle holds any other browser folder.
- **FFmpeg.** Playwright only uses it to record videos, and the app never
  records them. `winldd` stays, because Playwright runs it to re-check
  Chromium's DLLs once the bundled `DEPENDENCIES_VALIDATED` marker is older than
  30 days.
- **Google discovery documents other than `sheets.v4`.** `googleapiclient`
  ships about 600 of them, 100 MB in total, and `riviu/google_sheets_sync.py` only
  builds the Sheets v4 client. A test fails if the code builds an API that is
  not in `GOOGLE_DISCOVERY_DOCS`, and another fails if a launch stops being
  headless Chromium.

Effect on the Windows build, measured on the same test machine:

| | Before | After |
| --- | --- | --- |
| NSIS installer | 271 MB | 150 MB |
| Installed files | 2,620 | 1,708 |
| Installed size | 1,003 MB | 494 MB |
| Silent fresh install (two runs) | 76 s, 85 s | 54 s, 28 s |
| Server's first response, first launch | 3.5 s, 6.6 s | 2.3 s, 2.5 s |
| Server's first response, later launches | 2.5-4.1 s | 1.6-1.8 s |

The installed app answered 2.2-2.6 s after its process started. The machine
was busy with other work during these runs, so treat the times as rough.

The layout depends on the platform:

- **Windows: one-folder build shipped as bundle resources.** The build is copied
  to `src-tauri/binaries/riviu-server/`. `tauri.windows.conf.json` maps that
  folder to `<install dir>\riviu-server\`, and `lib.rs` starts
  `riviu-server\riviu-server.exe` from the resource dir. A one-file build would
  unpack Python and Chromium (about 1 GB) into a new `%TEMP%\_MEI*` folder on
  every launch, and Windows Defender rescans it each time. On a test machine the
  server took 24-31 s to answer its first request as one file, and 1.6-2.7 s as
  one folder (7.7 s on the very first launch, while Defender scans the newly
  installed files). In the installed app it answered about 2.2 s after the app
  process started. The trade-off is that the installer writes one file per
  bundled file (about 1,700; see the table above).
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

The installed app checks the release `latest.json` as soon as the UI receives
its first WebSocket session snapshot, and then every five minutes. A check is
skipped while a scan runs. When a newer release is available, it downloads,
installs, and restarts automatically.

### Pull request check

Pull requests into `main` run `.github/workflows/pr-check.yml`. It runs the same
tests and builds every target above, but never creates a release, so a broken
platform shows up on the PR instead of in a half-published release. Release
builds run the same smoke step before packaging.

Both workflows run `desktop/smoke_sidecar.py` after building the server:

- `riviu-server --self-check` launches the bundled headless Chromium once;
- the server starts with a temp data dir and serves `/`, `/static/app.js` and `/logo.png`;
- the token-protected `/_desktop/shutdown` stops it.

PR builds are unsigned and skip updater artifacts (`desktop/tauri-pr-check.json`),
so they need no release secrets.

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
  uninstalling first. `src-tauri/windows/installer-hooks.nsh` runs before any
  file is copied. It does the following, in order:
  1. Runs Tauri's own running-app check first. In passive or silent mode this
     closes the app, and in an interactive install the user can still cancel.
  2. Stops every process whose executable is inside the install folder, together
     with its process tree: the old one-file `riviu-server.exe`, and anything
     under `riviu-server\` (server, Playwright driver, Chromium). Processes are
     selected by executable path, never by image name alone.
  3. Deletes `<install dir>\riviu-server` and the stale root-level
     `riviu-server.exe`, so files that the new build no longer ships (an old
     Chromium revision, stale extension modules) do not pile up.

  The uninstaller deletes only the files it installed, so the hook file also
  stops leftover servers before uninstalling, and removes `riviu-server` and
  `riviu-server.exe` afterwards. Otherwise runtime files such as Chromium's
  `debug.log` would keep the install folder behind. The uninstaller keeps the
  app-data folder unless its "delete app data" box is ticked. It also always
  keeps the install-location key `HKCU\Software\riviu\Riviu Reports`; both are
  Tauri defaults.

## Updating from a one-file release (0.1.17 and earlier)

Releases up to 0.1.17 ship `riviu-server.exe` as a one-file sidecar in the
install folder. Their `lib.rs` does not stop the sidecar before the updater
exits, so when such an app updates, its server (a PyInstaller bootloader plus a
Python child, unpacked into `%TEMP%\_MEI*`) is still running when the new
installer starts. Without the hook, the update itself succeeded, but three
things went wrong:

- The old server kept running next to the new one, holding its port and the
  shared app-data folder.
- Its roughly 1 GB `_MEI*` folder stayed in `%TEMP%`.
- The 415 MB root `riviu-server.exe` stayed in the install folder, and the
  uninstaller later left it and the folder behind.

The hook kills the Python child first. The bootloader then removes its `_MEI*`
folder and exits on its own, and the hook deletes the stale executable.

Tested on Windows 11 by installing the published 0.1.17 into the default
per-user folder, starting it, and then running a new installer the way
`tauri-plugin-updater` does (`/P /R /UPDATE /ARGS`). Two cases were covered: the
old app exits right away, as `std::process::exit` does, leaving its server
orphaned; and the old app is still running. Both updates finished in
55-70 s without a file-lock prompt. The old server processes and their `_MEI*`
folder were gone, the root `riviu-server.exe` was deleted, and the restarted app
served from `riviu-server\riviu-server.exe`. Files in
`%APPDATA%\com.riviu.reports` were unchanged. An update and an uninstall that
ran while the new app and a browser scan were active also stopped the server,
the Playwright driver and Chromium before touching any files.

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
