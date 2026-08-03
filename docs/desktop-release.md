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
four hours. When a newer release is available, it offers to download, install,
and restart.

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
