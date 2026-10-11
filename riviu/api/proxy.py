"""The saved proxy list and the proxy connectivity test."""

import asyncio

from fastapi import APIRouter

from riviu import server_context as context
from riviu.proxy_utils import load_proxy_list_text, parse_proxy_text, save_proxy_list_text, test_proxy_text


router = APIRouter()


@router.get("/proxy-list")
async def get_proxy_list():
    text = load_proxy_list_text(context.EXCEL_DIR)
    configs = parse_proxy_text(text)
    return {
        "text": text,
        "count": len(configs),
    }


@router.post("/proxy-list")
async def save_proxy_list(data: dict):
    text = str(data.get("text") or "")
    save_proxy_list_text(context.EXCEL_DIR, text)
    configs = parse_proxy_text(text)
    return {
        "ok": True,
        "count": len(configs),
        "message": f"Đã lưu {len(configs)} proxy" if configs else "Đã lưu danh sách (chưa đọc được proxy hợp lệ)",
    }


@router.post("/proxy-test")
async def test_proxy_list(data: dict | None = None):
    payload = data or {}
    text = str(payload.get("text") or load_proxy_list_text(context.EXCEL_DIR))
    if payload.get("save"):
        save_proxy_list_text(context.EXCEL_DIR, text)
    return await asyncio.to_thread(test_proxy_text, text)
