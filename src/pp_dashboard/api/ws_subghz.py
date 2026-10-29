"""WebSocket live Sub-GHz packet stream."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pp_dashboard.api.ws_origin import accept_local_ws
from pp_dashboard.subghz.engine import get_subghz_engine

log = logging.getLogger(__name__)
router = APIRouter(tags=["subghz-ws"])


@router.websocket("/ws/subghz")
async def subghz_ws(websocket: WebSocket) -> None:
    if not await accept_local_ws(websocket):
        return
    engine = await get_subghz_engine()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)

    async def _handler(payload: dict[str, Any], packet_id: int) -> None:
        try:
            queue.put_nowait({"type": "packet", "id": packet_id, "data": payload})
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait({"type": "packet", "id": packet_id, "data": payload})
            except asyncio.QueueFull:
                pass

    engine.sniffer.subscribe(_handler)
    await websocket.send_json(
        {
            "type": "status",
            "message": "subscribed",
            "recent": engine.list_packets(20),
        }
    )

    sender = asyncio.create_task(_send_loop(websocket, queue))
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = (msg.get("text") or "").strip().lower()
            if text in {"stop", "close"}:
                break
            # stop_sniff removed from WS (CSRF): use POST /api/subghz/sniff/stop
    except WebSocketDisconnect:
        log.info("subghz ws disconnected")
    finally:
        engine.sniffer.unsubscribe(_handler)
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


async def _send_loop(websocket: WebSocket, queue: asyncio.Queue[dict[str, Any]]) -> None:
    while True:
        item = await queue.get()
        await websocket.send_json(item)
