"""Fan-out demodulated PCM (s16le mono @ 48 kHz) to WebSocket subscribers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

log = logging.getLogger(__name__)

PcmHandler = Callable[[bytes], Awaitable[None] | None]

AUDIO_SAMPLE_RATE = 48_000
AUDIO_CHANNELS = 1
AUDIO_FORMAT = "s16le"


class AudioHub:
    """Singleton-ish broadcaster for Auto Voice Recon PCM."""

    _instance: ClassVar[AudioHub | None] = None

    def __init__(self) -> None:
        self._handlers: list[PcmHandler] = []
        self._meta_handlers: list[Callable[[dict[str, Any]], Awaitable[None] | None]] = []
        self.active = False
        self.sample_rate = AUDIO_SAMPLE_RATE
        self.ctcss_hz: float | None = None
        self.listen_hz: int | None = None
        self.band: str | None = None
        self.rssi_db: float | None = None

    @classmethod
    def get(cls) -> AudioHub:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None

    def subscribe(self, handler: PcmHandler) -> None:
        if handler not in self._handlers:
            self._handlers.append(handler)

    def unsubscribe(self, handler: PcmHandler) -> None:
        self._handlers = [h for h in self._handlers if h is not handler]

    def subscribe_meta(self, handler: Callable[[dict[str, Any]], Awaitable[None] | None]) -> None:
        if handler not in self._meta_handlers:
            self._meta_handlers.append(handler)

    def unsubscribe_meta(
        self, handler: Callable[[dict[str, Any]], Awaitable[None] | None]
    ) -> None:
        self._meta_handlers = [h for h in self._meta_handlers if h is not handler]

    def hello_payload(self) -> dict[str, Any]:
        return {
            "type": "audio_hello",
            "sample_rate": self.sample_rate,
            "channels": AUDIO_CHANNELS,
            "format": AUDIO_FORMAT,
            "active": self.active,
            "ctcss_hz": self.ctcss_hz,
            "listen_hz": self.listen_hz,
            "band": self.band,
            "rssi_db": self.rssi_db,
        }

    async def set_session(
        self,
        *,
        active: bool,
        listen_hz: int | None = None,
        band: str | None = None,
        rssi_db: float | None = None,
        ctcss_hz: float | None = None,
    ) -> None:
        self.active = active
        if listen_hz is not None:
            self.listen_hz = listen_hz
        if band is not None:
            self.band = band
        if rssi_db is not None:
            self.rssi_db = rssi_db
        self.ctcss_hz = ctcss_hz
        if not active:
            self.listen_hz = listen_hz
            self.band = band
        await self._emit_meta(self.hello_payload())

    async def publish_pcm(self, pcm: bytes) -> None:
        if not pcm:
            return
        for h in list(self._handlers):
            try:
                r = h(pcm)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("audio hub pcm handler failed")

    async def publish_meta(self, payload: dict[str, Any]) -> None:
        await self._emit_meta(payload)

    async def _emit_meta(self, payload: dict[str, Any]) -> None:
        for h in list(self._meta_handlers):
            try:
                r = h(payload)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("audio hub meta handler failed")
