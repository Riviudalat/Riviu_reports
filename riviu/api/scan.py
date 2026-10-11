"""The `/ws` scan channel (start/cancel and progress events) and the scrape history."""

import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from riviu import scan_service, scan_state
from riviu import server_context as context
from riviu.api import security
from riviu.platforms.tiktok import read_scrape_history
from riviu.source_files import SourceRequestError


router = APIRouter()


@router.get("/scrape-history")
async def scrape_history(limit: int = Query(default=50)):
    safe_limit = max(1, min(int(limit or 50), 200))
    return {"history": read_scrape_history(context.EXCEL_DIR, limit=safe_limit)}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if not security.trusted_local_request(websocket, require_origin=True) or not security.valid_local_session(websocket):
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
            if action == "start":
                if scan_state.source_mutation_blocked():
                    await websocket.send_json({"type": "session", "data": manager.snapshot(), **manager.context})
                    await websocket.send_json({"type": "log", "message": "Đang xử lý dữ liệu hoặc cập nhật ứng dụng. Vui lòng chờ hoàn tất."})
                    continue
                try:
                    target_path, options = await scan_service.prepare_scan_start(payload)
                except (SourceRequestError, ValueError) as error:
                    await reject_start(str(error))
                    continue
                run_context = manager.begin_run(options["platform"], options["file_id"], options["sheet_name"])
                task = asyncio.create_task(scan_service.run_scraper_safely(target_path, options, run_context=run_context))
                scan_state.SCRAPE_TASK = task
                task.add_done_callback(lambda task, run_id=run_context["runId"]: scan_service.finalize_unstarted_run(task, run_id))
                await manager.broadcast_status(manager.last_status)
            elif action == "cancel":
                task = scan_state.SCRAPE_TASK
                if task and not task.done():
                    # Repeated cancel must not interrupt the pending save/cleanup.
                    if not task.cancelling():
                        task.cancel()
                    await manager.broadcast_log("Đang hủy phiên quét hiện tại...")
                else:
                    await manager.broadcast_log("Không có phiên quét nào đang chạy.")
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
