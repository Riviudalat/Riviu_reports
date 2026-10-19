"""The saved TikTok cookie session ("Cookie TikTok"): status, import, verify and delete.

Opt-in per scan; see riviu/platforms/tiktok_session.py. Responses never carry cookie values.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from riviu import scan_state
from riviu import server_context as context
from riviu.platforms.tiktok_session import MAX_IMPORT_BYTES, TikTokSessionError


router = APIRouter()


@router.get("/tiktok-session/status")
async def tiktok_session_status():
    try:
        return await context.complete_blocking(context.tiktok_session_manager().status)
    except Exception:
        return JSONResponse({"configured": False, "state": "storage_unavailable", "message": "Không truy cập được kho cookie an toàn."}, status_code=503)


async def bounded_body(request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_IMPORT_BYTES:
            raise TikTokSessionError("too_large")
    return bytes(data)


@router.post("/tiktok-session/import")
async def import_tiktok_session(request: Request):
    if scan_state.source_mutation_blocked():
        return JSONResponse({"error": "Đang quét hoặc xử lý dữ liệu; không đổi cookie lúc này."}, status_code=409)
    with scan_state.source_operation():
        try:
            payload = await bounded_body(request)
            return await context.complete_blocking(context.tiktok_session_manager().import_cookie, payload)
        except TikTokSessionError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "Không import được phiên TikTok."}, status_code=503)


@router.post("/tiktok-session/verify")
async def verify_tiktok_session():
    if scan_state.source_mutation_blocked():
        return JSONResponse({"error": "Đang quét hoặc xử lý dữ liệu; chờ để kiểm tra cookie."}, status_code=409)
    with scan_state.source_operation():
        try:
            return await context.complete_blocking(context.tiktok_session_manager().verify)
        except TikTokSessionError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "Không kiểm tra được phiên TikTok."}, status_code=503)


@router.delete("/tiktok-session")
async def delete_tiktok_session():
    if scan_state.source_mutation_blocked():
        return JSONResponse({"error": "Đang quét hoặc xử lý dữ liệu; không xóa cookie lúc này."}, status_code=409)
    with scan_state.source_operation():
        try:
            return await context.complete_blocking(context.tiktok_session_manager().clear)
        except TikTokSessionError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "Không xóa được phiên TikTok trong kho an toàn."}, status_code=503)
