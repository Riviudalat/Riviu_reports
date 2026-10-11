"""The saved Threads cookie session: status, import, verify and delete (see docs/threads-scanning.md)."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from riviu import scan_state
from riviu import server_context as context
from riviu.platforms.threads_session import MAX_IMPORT_BYTES, SessionError


router = APIRouter()


@router.get("/threads-session/status")
async def threads_session_status():
    try:
        return await context.complete_blocking(context.threads_session_manager().status)
    except Exception:
        return JSONResponse({"configured": False, "state": "storage_unavailable", "message": "Không truy cập được kho cookie an toàn."}, status_code=503)


async def bounded_session_body(request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_IMPORT_BYTES:
            raise SessionError("too_large")
    return bytes(data)


@router.post("/threads-session/import")
async def import_threads_session(request: Request):
    if scan_state.source_mutation_blocked():
        return JSONResponse({"error": "Đang quét hoặc xử lý dữ liệu; không đổi cookie lúc này."}, status_code=409)
    with scan_state.source_operation():
        try:
            payload = await bounded_session_body(request)
            return await context.threads_session_manager().import_cookie(payload)
        except SessionError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "Không import được phiên Threads."}, status_code=503)


@router.post("/threads-session/verify")
async def verify_threads_session():
    if scan_state.source_mutation_blocked():
        return JSONResponse({"error": "Đang quét hoặc xử lý dữ liệu; chờ để kiểm tra cookie."}, status_code=409)
    with scan_state.source_operation():
        try:
            return await context.threads_session_manager().verify()
        except SessionError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "Không kiểm tra được phiên Threads."}, status_code=503)


@router.delete("/threads-session")
async def delete_threads_session():
    if scan_state.source_mutation_blocked():
        return JSONResponse({"error": "Đang quét hoặc xử lý dữ liệu; không xóa cookie lúc này."}, status_code=409)
    with scan_state.source_operation():
        try:
            return await context.complete_blocking(context.threads_session_manager().clear)
        except SessionError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "Không xóa được phiên Threads trong kho an toàn."}, status_code=503)
