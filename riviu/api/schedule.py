"""Server mode: the admin's "Lịch tự động" settings and run log (see `riviu.scheduler`).

Not in the viewer allowlist, so the middleware already requires the admin login; outside
server mode there is no scheduler and the routes answer 404.
"""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from riviu import scheduler, server_mode


router = APIRouter()
NO_STORE = {"Cache-Control": "no-store"}


def _not_found():
    return JSONResponse({"error": "Not Found"}, status_code=404)


@router.get("/schedule")
async def get_schedule():
    if server_mode.ACTIVE is None:
        return _not_found()
    return JSONResponse(await asyncio.to_thread(scheduler.overview), headers=NO_STORE)


@router.post("/schedule")
async def update_schedule(request: Request):
    if server_mode.ACTIVE is None:
        return _not_found()
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > scheduler.MAX_BODY_BYTES:
            return JSONResponse({"error": "Cấu hình lịch tự động quá lớn."}, status_code=413)
    try:
        payload = json.loads(data)
        await asyncio.to_thread(scheduler.save_schedule, payload)
    except (ValueError, TypeError) as error:
        message = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else ""
        return JSONResponse({"error": message or "Cấu hình lịch tự động không hợp lệ."}, status_code=400)
    except OSError:
        return JSONResponse({"error": "Không lưu được lịch tự động."}, status_code=503)
    return JSONResponse({"success": True, **await asyncio.to_thread(scheduler.overview)}, headers=NO_STORE)
