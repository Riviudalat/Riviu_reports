"""`python -m riviu` starts the source-mode web server on http://127.0.0.1:1231."""

import uvicorn

from riviu.app import app


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=1231)


if __name__ == "__main__":
    main()
