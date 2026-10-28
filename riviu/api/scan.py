"""The `/ws` scan channel (start/cancel/skip_retry and progress events) and the scrape history.

Server mode: the socket also serves the public viewer, read-only. Starlette's HTTP
middleware never sees websockets, so the admin check for the scan commands lives here.
"""

import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from riviu import scan_service, scan_state, server_mode
from riviu import server_context as context
from riviu.api import security
from riviu.platforms.tiktok import read_scrape_history
from riviu.source_files import SourceRequestError
from riviu.workbook.platforms import PLATFORMS


router = APIRouter()
VIEWER_REFUSED = "Chỉ quản trị viên mới bắt đầu hoặc hủy phiên quét. Đăng nhập tại /admin."
# Commands that change a scan; only an admin may send them.
SCAN_COMMANDS = {"start", "cancel", "skip_retry"}


def websocket_admitted(websocket) -> bool:
    """Trusted origin plus, outside server mode, the local session cookie. In server mode a
    connection without a login is admitted as a read-only viewer."""
    if not security.trusted_local_request(websocket, require_origin=True):
        return False
    return server_mode.ACTIVE is not None or security.valid_local_session(websocket)


@router.get("/scrape-history")
async def scrape_history(limit: int = Query(default=50)):
    safe_limit = max(1, min(int(limit or 50), 200))
    return {"history": read_scrape_history(context.EXCEL_DIR, limit=safe_limit)}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if not websocket_admitted(websocket):
        await websocket.close(code=1008)
        return
    manager = scan_state.manager
    await manager.connect(websocket)
    async def reject_start(message):
        await websocket.send_json({"type": "log", "message": message, "level": "ERROR"})
        await websocket.send_json({"type": "status", "data": {"total": 0, "processed": 0, "success": 0, "error": 1, "done": True, "phase": "failed", "message": message}})
    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {"action": data}
            if not isinstance(payload, dict):
                await reject_start("Lệnh quét không hợp lệ")
                continue

            action = payload.get("action")
            # Riviu Data storage runs one scan per platform; a command names its platform.
            platform = payload.get("platform") if payload.get("platform") in PLATFORMS else None
            # Checked per command (not once at connect), so a logout or password change applies at once.
            if action in SCAN_COMMANDS and security.request_role(websocket) != "admin":
                # Only to this socket: a viewer must not stop or start anything, nor spam others.
                await websocket.send_json({"type": "log", "message": VIEWER_REFUSED, "level": "WARN"})
                continue
            if action == "start":
                lane = scan_state.lane(platform or "tiktok")
                if scan_state.start_blocked(platform or "tiktok"):
                    await websocket.send_json(lane.manager.session_message())
                    await websocket.send_json({"type": "log", "message": "Đang xử lý dữ liệu hoặc cập nhật ứng dụng. Vui lòng chờ hoàn tất."})
                    continue
                # Who started it, for Riviu Data's scan_run; never taken from the client.
                identity = security.request_identity(websocket) if server_mode.ACTIVE is not None else None
                payload = {**payload, "started_by": f"riviu-reports:{identity['username']}" if identity else ""}
                try:
                    target_path, options = await scan_service.prepare_scan_start(payload)
                except (SourceRequestError, ValueError) as error:
                    await reject_start(str(error))
                    continue
                scan_service.start_scan_task(target_path, options)
                lane = scan_state.lane(options["platform"])
                await lane.manager.broadcast_status(lane.manager.last_status)
            elif action == "cancel":
                # A user cancel is final: the run is not resumed later (scan_service.user_cancel_scan).
                channel = scan_state.lane(platform).manager
                if scan_service.user_cancel_scan(platform):
                    await channel.broadcast_log("Đang hủy phiên quét hiện tại...")
                else:
                    await channel.broadcast_log("Không có phiên quét nào đang chạy.")
            elif action == "skip_retry":
                # Ends the cooldown without a retry pass; the run completes with the main-pass results.
                if scan_service.skip_retry_wait(platform):
                    await scan_state.lane(platform).manager.broadcast_log("Bỏ qua quét lại: kết thúc phiên quét với kết quả lượt chính...")
                else:
                    await websocket.send_json({"type": "log", "message": "Phiên quét không còn chờ quét lại.", "level": "WARN"})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
