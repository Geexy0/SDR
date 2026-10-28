"""WebSocket remote GUI stream via screenframeshort."""

from __future__ import annotations

import asyncio
import base64
import logging
import struct

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pp_dashboard.hardware.arbiter import get_arbiter
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.mayhem.bridge import MayhemBridgeError, get_mayhem_bridge

log = logging.getLogger(__name__)

router = APIRouter(tags=["mayhem-screen"])

_MAGIC = b"MHSC"


@router.websocket("/ws/mayhem-screen")
async def mayhem_screen(websocket: WebSocket) -> None:
    await websocket.accept()
    arbiter = await get_arbiter()
    await arbiter.refresh_detection()
    if arbiter.state is not HardwareState.MAYHEM_SERIAL:
        await websocket.send_json(
            {
                "type": "error",
                "message": f"Requires MAYHEM_SERIAL (current={arbiter.state.value})",
            }
        )
        await websocket.close(code=1008)
        return

    bridge = await get_mayhem_bridge()
    stop = asyncio.Event()
    interval = {"s": 0.35}

    async def _reader() -> None:
        while not stop.is_set():
            try:
                msg = await websocket.receive()
            except WebSocketDisconnect:
                stop.set()
                return
            if msg.get("type") == "websocket.disconnect":
                stop.set()
                return
            text = msg.get("text")
            if not text:
                continue
            low = text.strip().lower()
            if low in {"stop", "close"}:
                stop.set()
                return
            if low.startswith("interval "):
                try:
                    val = float(text.strip().split(None, 1)[1])
                    interval["s"] = max(0.2, min(5.0, val))
                except (IndexError, ValueError):
                    pass

    reader = asyncio.create_task(_reader())
    await websocket.send_json({"type": "status", "message": "streaming", "format": "MHSC+RGBA"})

    try:
        while not stop.is_set():
            try:
                frame = await bridge.capture_screen_short(wait=10.0)
            except MayhemBridgeError as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
                break
            except Exception as exc:
                log.exception("screenframeshort failed")
                await websocket.send_json({"type": "error", "message": str(exc)})
                await asyncio.sleep(1.0)
                continue

            header = _MAGIC + struct.pack(">HH", frame.width, frame.height)
            try:
                await websocket.send_bytes(header + frame.rgba)
            except Exception:
                await websocket.send_json(
                    {
                        "type": "frame",
                        "width": frame.width,
                        "height": frame.height,
                        "encoding": "rgba-base64",
                        "data": base64.b64encode(frame.rgba).decode("ascii"),
                    }
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval["s"])
            except TimeoutError:
                pass
    except WebSocketDisconnect:
        log.info("Mayhem screen client disconnected")
    finally:
        stop.set()
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
