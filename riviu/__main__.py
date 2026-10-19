"""`python -m riviu` starts the web server.

Source mode (default): http://127.0.0.1:1231, as always. Server mode
(RIVIU_SERVER_MODE=1, see riviu/server_mode.py): binds RIVIU_HOST:RIVIU_PORT and trusts
X-Forwarded-For/Proto only from RIVIU_FORWARDED_ALLOW_IPS (default 127.0.0.1, the nginx in front).
"""

import sys

import uvicorn

from riviu import server_mode


def main() -> None:
    try:
        config = server_mode.activate_from_environment()
    except server_mode.ServerModeError as error:
        print(f"Không khởi động được chế độ server: {error}", file=sys.stderr)
        raise SystemExit(2) from None

    from riviu.app import app

    if config is None:
        uvicorn.run(app, host="127.0.0.1", port=1231)
        return
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        proxy_headers=True,
        forwarded_allow_ips=config.forwarded_allow_ips,
        server_header=False,
    )


if __name__ == "__main__":
    main()
