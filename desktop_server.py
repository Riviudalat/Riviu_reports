"""Bundled FastAPI entrypoint used by the Riviu Reports desktop sidecar."""

import os
import secrets

import uvicorn
from fastapi import HTTPException, Request

import app as app_state
from app import app


def register_desktop_routes(server, shutdown_token: str) -> None:
    def require_desktop_token(request: Request) -> None:
        supplied = request.headers.get("x-riviu-shutdown", "")
        if not shutdown_token or not secrets.compare_digest(supplied, shutdown_token):
            raise HTTPException(status_code=403)

    @app.post("/_desktop/prepare-update", include_in_schema=False)
    async def prepare_update(request: Request):
        require_desktop_token(request)
        # No await between checking the single-process state and acquiring the gate.
        if app_state.scan_running() or app_state.SOURCE_BUSY or app_state.DESKTOP_UPDATE_PENDING:
            raise HTTPException(status_code=409, detail="Scan or file operation is in progress")
        app_state.DESKTOP_UPDATE_PENDING = True
        return {"ready": True}

    @app.post("/_desktop/cancel-update", include_in_schema=False)
    async def cancel_update(request: Request):
        require_desktop_token(request)
        app_state.DESKTOP_UPDATE_PENDING = False
        return {"ready": False}

    @app.post("/_desktop/shutdown", include_in_schema=False)
    async def desktop_shutdown(request: Request):
        require_desktop_token(request)
        server.should_exit = True
        return {"stopping": True}


def main() -> None:
    # Only the packaged process uses the bundled Chromium; importing route helpers
    # must not redirect browser discovery for tests or source deployments.
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    port = int(os.environ.get("RIVIU_PORT", "1231"))
    shutdown_token = os.environ.get("RIVIU_SHUTDOWN_TOKEN", "")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    register_desktop_routes(server, shutdown_token)
    server.run()


if __name__ == "__main__":
    main()
