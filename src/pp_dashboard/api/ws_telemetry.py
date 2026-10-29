"""WebSocket telemetry fan-out for SDR subprocess metrics."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pp_dashboard.api.ws_origin import accept_local_ws
from pp_dashboard.hardware.arbiter import get_arbiter
from pp_dashboard.models.rf import TelemetryEvent

log = logging.getLogger(__name__)

router = APIRouter(tags=["telemetry"])

_subscriber_count = 0
_subscriber_lock = asyncio.Lock()


@router.websocket("/ws/telemetry")
async def telemetry_socket(websocket: WebSocket) -> None:
    global _subscriber_count
    if not await accept_local_ws(websocket):
        return
    arbiter = await get_arbiter()
    queue: asyncio.Queue[TelemetryEvent] = asyncio.Queue(maxsize=256)

    async with _subscriber_lock:
        _subscriber_count += 1

    async def _handler(event: TelemetryEvent) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    arbiter.subscribe_telemetry(_handler)
    await arbiter.refresh_detection()
    await websocket.send_json(
        TelemetryEvent(
            type="status",
            state=arbiter.state,
            message="subscribed",
            metrics={**arbiter.runner.snapshot(), "subscribers": _subscriber_count},
        ).model_dump(mode="json")
    )

    async def _send_loop() -> None:
        while True:
            event = await queue.get()
            await websocket.send_json(event.model_dump(mode="json"))

    async def _heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(5.0)
            await queue.put(
                TelemetryEvent(
                    type="heartbeat",
                    state=arbiter.state,
                    metrics=arbiter.runner.snapshot(),
                )
            )

    sender = asyncio.create_task(_send_loop())
    heartbeat = asyncio.create_task(_heartbeat_loop())
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            # Ignore side-effectful WS commands (CSRF / CSWSH). Use POST /api/hardware/stop.
            _ = message.get("text")
    except WebSocketDisconnect:
        log.info("Telemetry client disconnected")
    finally:
        arbiter.unsubscribe_telemetry(_handler)
        sender.cancel()
        heartbeat.cancel()
        await asyncio.gather(sender, heartbeat, return_exceptions=True)
        async with _subscriber_lock:
            _subscriber_count = max(0, _subscriber_count - 1)
        # Disconnect must NOT stop SDR jobs — only explicit stop/cancel or HTTP /stop.
