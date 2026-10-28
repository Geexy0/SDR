"""Async-friendly Mayhem CDC-ACM serial helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import serial

from pp_dashboard.config import Settings, settings

log = logging.getLogger(__name__)

Predicate = Callable[[], bool | Awaitable[bool]]


class SerialError(RuntimeError):
    pass


def _open_serial(port: str, cfg: Settings) -> serial.Serial:
    return serial.Serial(
        port=port,
        baudrate=cfg.serial_baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=cfg.serial_probe_timeout_s,
        write_timeout=5.0,
    )


async def send_hackrf_enter_native(port: str, cfg: Settings = settings) -> None:
    """Send `hackrf\\r\\n` then close the port so the device can re-enumerate as native."""

    def _blocking() -> None:
        ser = _open_serial(port, cfg)
        try:
            ser.reset_input_buffer()
            ser.write(b"hackrf\r\n")
            ser.flush()
        finally:
            ser.close()

    await asyncio.to_thread(_blocking)
    log.info("Sent hackrf mode switch on %s", port)


async def probe_mayhem_prompt(port: str, cfg: Settings = settings) -> bool:
    """Return True if the serial port answers a CRLF probe (Mayhem shell)."""

    def _blocking() -> bool:
        try:
            ser = _open_serial(port, cfg)
        except serial.SerialException as exc:
            log.debug("Serial open failed on %s: %s", port, exc)
            return False
        try:
            ser.reset_input_buffer()
            ser.write(b"\r\n")
            ser.flush()
            chunks: list[bytes] = []
            for _ in range(15):
                data = ser.read(256)
                if data:
                    chunks.append(data)
                    if b"ch>" in b"".join(chunks):
                        return True
            return False
        except serial.SerialException as exc:
            log.debug("Serial probe failed on %s: %s", port, exc)
            return False
        finally:
            try:
                ser.close()
            except serial.SerialException:
                pass

    return await asyncio.to_thread(_blocking)


async def poll_until(
    predicate: Predicate,
    *,
    timeout_s: float,
    initial_delay_s: float = 0.05,
    max_delay_s: float = 0.8,
) -> bool:
    """Poll a sync/async predicate with exponential backoff until True or timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    delay = initial_delay_s
    while loop.time() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            ok = bool(await result)
        else:
            ok = bool(result)
        if ok:
            return True
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(delay, remaining, max_delay_s))
        delay = min(delay * 2, max_delay_s)
    return False
