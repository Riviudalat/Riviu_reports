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
  5 seconds for it to exit, so the PyInstaller one-file bootloader can remove
  its `_MEI*` temp folder, before it force-kills the process.
- An update first calls `prepare-update`, which is refused while a scan or
  source operation is running. On Windows the updater starts the NSIS installer
  and ends the process without a normal exit event, so the sidecar is stopped in
  the updater's pre-exit hook. That keeps `riviu-server.exe` and Chromium from
  holding the executable open while the installer replaces it.

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
