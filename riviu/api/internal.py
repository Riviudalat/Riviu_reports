"""Machine-to-machine routes for Riviu Data (RIVIU_STORAGE=postgres only; 404 otherwise).

POST /internal/scan
    Authorization: Bearer <token with scope scan:run>      (RIVIU_SCAN_TOKEN_FILE)
    {"platform": "tiktok"|"threads", "thang": "YYYY-MM", "trigger": "timer"|"thu_cong",
     "started_by"?: str, "scan_run_id"?: int}
    200  trigger "timer": the scan finished (phase hoan_tat, or dung_som after a Threads 429)
    202  any other trigger: accepted, the scan runs in the background
    401  missing or unknown bearer token     403  token without scan:run, or a browser request
    404  scan_run_id unknown / finished, or the Postgres backend is off
    409  a scan of that platform is running (advisory lock, or its lane in this process)
    422  invalid body       500 timer scan failed (phase loi)     503 timer scan cancelled

The middleware lets these paths through without a session cookie; the bearer token is the
only credential, compared in constant time (SHA-256 digests, hmac.compare_digest).
"""

from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from riviu import internal_scan
from riviu.store import config as store_config
from riviu.store.postgres import PHASE_CANCELLED, PHASE_FAILED, ScanRunNotFound

router = APIRouter()

INTERNAL_SCAN_PATH = "/internal/scan"
INTERNAL_PATHS = frozenset({INTERNAL_SCAN_PATH})
MAX_BODY_BYTES = 4096


def _error(status_code, message, **headers):
    return JSONResponse({"error": message}, status_code=status_code, headers=headers or None)


def authorize(request: Request, scope: str):
    """None when the bearer token holds `scope`; otherwise the 401/403 response."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or len(token) > 512:
        return _error(401, "Cần bearer token", **{"WWW-Authenticate": "Bearer"})
    digest = store_config.token_digest(token)
    match = None
    for entry in store_config.ACTIVE.scan_tokens:  # every entry is compared: no early exit
        if hmac.compare_digest(entry.digest, digest):
            match = entry
    if match is None:
        return _error(401, "Token không hợp lệ", **{"WWW-Authenticate": 'Bearer error="invalid_token"'})
    if scope not in match.scopes:
        return _error(403, f"Token không có quyền {scope}")
    return None


@router.post(INTERNAL_SCAN_PATH)
async def internal_scan_route(request: Request):
    if not store_config.ACTIVE.postgres:
        return _error(404, "Not Found")
    # Browsers send Origin / Sec-Fetch-*; this route is for the timer and Riviu Data only.
    if request.headers.get("origin") or request.headers.get("sec-fetch-site"):
        return _error(403, "Nguồn truy cập không hợp lệ")
    denied = authorize(request, store_config.SCAN_SCOPE)
    if denied is not None:
        return denied
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return _error(413, "Body quá lớn")
    try:
        scan_request = internal_scan.parse_request(json.loads(raw or b"null"))
    except (ValueError, UnicodeDecodeError) as error:
        return _error(422, str(error) if isinstance(error, internal_scan.ScanRequestError) or "thang" in str(error) else "Body không phải JSON hợp lệ")
    wait = scan_request.trigger == "timer"
    try:
        summary = await internal_scan.start_scan(scan_request, wait=wait)
    except internal_scan.ScanBusy as error:
        return _error(409, str(error))
    except ScanRunNotFound as error:
        return _error(404, str(error))
    if not wait:
        return JSONResponse(summary, status_code=202)
    if summary.get("phase") == PHASE_FAILED:
        return JSONResponse(summary, status_code=500)
    if summary.get("phase") == PHASE_CANCELLED:
        return JSONResponse(summary, status_code=503)
    return JSONResponse(summary, status_code=200)
