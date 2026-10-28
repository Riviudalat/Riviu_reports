"""The admin's per-platform scan settings (see `riviu.scan_settings`).

Not in the viewer allowlist, so in server mode the middleware already requires the admin login;
desktop and source mode use the same routes behind the local session cookie.
"""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from riviu import scan_settings


router = APIRouter()
NO_STORE = {"Cache-Control": "no-store"}


@router.get("/scan-settings")
async def get_scan_settings():
    return JSONResponse(await asyncio.to_thread(scan_settings.load_settings), headers=NO_STORE)


@router.post("/scan-settings")
async def update_scan_settings(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > scan_settings.MAX_BODY_BYTES:
            return JSONResponse({"error": "Cấu hình quét quá lớn."}, status_code=413)
    try:
        settings = await asyncio.to_thread(scan_settings.save_settings, json.loads(data))
    except (ValueError, TypeError):
        return JSONResponse({"error": "Cấu hình quét không hợp lệ."}, status_code=400)
    except OSError:
        return JSONResponse({"error": "Không lưu được cấu hình quét."}, status_code=503)
    return JSONResponse({"success": True, **settings}, headers=NO_STORE)
