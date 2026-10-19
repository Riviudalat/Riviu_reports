# Riviu Reports in server mode (shared password, behind nginx). Build from the repo root:
#   docker compose -f deploy/server/compose.yaml build
# The build context is an allowlist (Dockerfile.dockerignore): only pyproject.toml,
# uv.lock and riviu/ are sent, so data/, tokens and OAuth files never enter the image.

# CI tests and the desktop sidecar run on 3.12 (pyproject: >=3.11,<3.15).
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# Dependencies first (cached until uv.lock changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Headless Chromium shell (the app only launches headless) plus its system libraries and
# fonts; tzdata so TZ gives local timestamps in sheet names and reports.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && python -m playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY riviu ./riviu

RUN groupadd --system --gid 10001 riviu \
    && useradd --system --uid 10001 --gid riviu --home-dir /home/riviu --create-home riviu \
    && mkdir -p /data \
    && chown riviu:riviu /data

ENV TZ=Asia/Ho_Chi_Minh \
    RIVIU_SERVER_MODE=1 \
    RIVIU_HOST=0.0.0.0 \
    RIVIU_PORT=8000 \
    RIVIU_DATA_DIR=/data

USER riviu
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]

CMD ["python", "-m", "riviu"]
