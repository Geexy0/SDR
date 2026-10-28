"""WebSocket waterfall stream (compressed Uint8 FFT rows)."""

from __future__ import annotations

import asyncio
import base64
import logging
import struct
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pp_dashboard.hardware.arbiter import get_arbiter
from pp_dashboard.recon.engine import get_recon_engine

log = logging.getLogger(__name__)
router = APIRouter(tags=["waterfall"])

_MAGIC = b"WF32"
# After magic: u32 n, u32 bin_hz, f32 db_min, f32 db_max, u64 start, u64 stop (=32)
# Total header with magic = 36 bytes.
_HDR = struct.Struct(">IIffQQ")
assert _HDR.size == 32
_HEADER_LEN = 4 + _HDR.size  # 36


@router.websocket("/ws/waterfall")
async def waterfall_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    engine = await get_recon_engine()
    arbiter = await get_arbiter()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)

    async def _handler(payload: dict[str, Any]) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    session = engine.waterfall
    mux = getattr(arbiter, "multiplexer", None)
    if session:
        session.subscribe(_handler)
    if mux is not None:
        mux.subscribe(_handler)

    await websocket.send_json(
        {
            "type": "status",
            "message": "subscribed",
            "waterfall_running": bool(
                (session and session.running) or (mux is not None and getattr(mux, "running", False))
            ),
            "multiplex_rx": bool(mux is not None and getattr(mux, "running", False)),
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
            if text == "attach":
                if engine.waterfall:
                    engine.waterfall.subscribe(_handler)
                mux2 = getattr(arbiter, "multiplexer", None)
                if mux2 is not None:
                    mux2.subscribe(_handler)
                await websocket.send_json({"type": "status", "message": "attached"})
    except WebSocketDisconnect:
        log.info("waterfall ws disconnected")
    finally:
        if engine.waterfall:
            engine.waterfall.unsubscribe(_handler)
        mux3 = getattr(arbiter, "multiplexer", None)
        if mux3 is not None:
            mux3.unsubscribe(_handler)
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


async def _send_loop(websocket: WebSocket, queue: asyncio.Queue[dict[str, Any]]) -> None:
    while True:
        item = await queue.get()
        try:
            if item.get("type") == "row" and isinstance(item.get("data_u8"), (bytes, bytearray)):
                n = int(item["n"])
                header = _MAGIC + _HDR.pack(
                    n,
                    int(item["bin_hz"]),
                    float(item["db_min"]),
                    float(item["db_max"]),
                    int(item["start_hz"]),
                    int(item["stop_hz"]),
                )
                await websocket.send_bytes(header + bytes(item["data_u8"]))
            else:
                safe = {k: v for k, v in item.items() if k != "data_u8"}
                if "data_u8" in item and item["data_u8"] is not None:
                    safe["data_b64"] = base64.b64encode(item["data_u8"]).decode("ascii")
                await websocket.send_json(safe)
        except Exception:
            log.exception("waterfall ws send failed")
