"""Binary WebSocket PCM pipe for Auto Voice Recon (/ws/audio)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pp_dashboard.api.ws_origin import accept_local_ws
from pp_dashboard.recon.audio_hub import AudioHub

log = logging.getLogger(__name__)
router = APIRouter(tags=["audio"])


@router.websocket("/ws/audio")
async def audio_ws(websocket: WebSocket) -> None:
    if not await accept_local_ws(websocket):
        return
    hub = AudioHub.get()
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
    meta_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=16)

    async def _on_pcm(pcm: bytes) -> None:
        try:
            queue.put_nowait(pcm)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(pcm)
            except asyncio.QueueFull:
                pass

    async def _on_meta(payload: dict) -> None:
        try:
            meta_queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                meta_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                meta_queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    hub.subscribe(_on_pcm)
    hub.subscribe_meta(_on_meta)
    await websocket.send_json(hub.hello_payload())

    async def _pump() -> None:
        while True:
            # Prefer meta, else PCM
            if not meta_queue.empty():
                meta = await meta_queue.get()
                await websocket.send_json(meta)
                continue
            try:
                pcm = await asyncio.wait_for(queue.get(), timeout=0.05)
            except TimeoutError:
                if not meta_queue.empty():
                    continue
                await asyncio.sleep(0)
                continue
            await websocket.send_bytes(pcm)

    pump = asyncio.create_task(_pump())
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            # Client may send squelch / mute JSON; ignore for now (UI-local)
            _ = msg
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        hub.unsubscribe(_on_pcm)
        hub.unsubscribe_meta(_on_meta)
