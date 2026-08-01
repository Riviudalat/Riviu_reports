"""Bundled FastAPI entrypoint used by the Riviu Reports desktop sidecar."""

import os

import uvicorn
from fastapi import HTTPException, Request

# Use the Chromium copy bundled alongside the frozen Playwright package.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

from app import app


def main() -> None:
    port = int(os.environ.get("RIVIU_PORT", "1231"))
    shutdown_token = os.environ.get("RIVIU_SHUTDOWN_TOKEN", "")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)

    @app.post("/_desktop/shutdown", include_in_schema=False)
    async def desktop_shutdown(request: Request):
        if request.headers.get("x-riviu-shutdown") != shutdown_token:
            raise HTTPException(status_code=403)
        server.should_exit = True
        return {"stopping": True}

    server.run()


if __name__ == "__main__":
    main()
