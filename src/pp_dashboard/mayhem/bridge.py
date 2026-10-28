"""Async Mayhem CDC-ACM driver with exclusive command queue."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import serial

from pp_dashboard.config import Settings, settings
from pp_dashboard.hardware.arbiter import HardwareArbiter, get_arbiter
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.mayhem.frame import ScreenFrame, decode_screenframeshort

log = logging.getLogger(__name__)

PROMPT = b"ch> "
BUTTON_MAP = {
    1: "Right",
    2: "Left",
    3: "Down",
    4: "Up",
    5: "Select",
    6: "DFU",
    7: "EncoderLeft",
    8: "EncoderRight",
}


class MayhemBridgeError(RuntimeError):
    pass


@dataclass(slots=True)
class AppInfo:
    short_name: str
    full_name: str
    category: str


class MayhemSerialBridge:
    """Exclusive async serial bridge for Mayhem shell commands."""

    _instance: ClassVar[MayhemSerialBridge | None] = None

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or settings
        self._queue: asyncio.Queue[tuple[asyncio.Future[str], str, float]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._ser: serial.Serial | None = None
        self._port: str | None = None
        self._lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls) -> MayhemSerialBridge:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        cls._instance = None

    async def close(self) -> None:
        async with self._lock:
            if self._worker and not self._worker.done():
                self._worker.cancel()
                await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
            await asyncio.to_thread(self._close_serial_blocking)

    def _close_serial_blocking(self) -> None:
        if self._ser is not None:
            try:
                if self._ser.is_open:
                    self._ser.close()
            except serial.SerialException:
                pass
        self._ser = None
        self._port = None

    async def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._queue_worker(), name="mayhem-serial-queue")

    async def _require_mayhem_mode(self) -> HardwareArbiter:
        arbiter = await get_arbiter()
        await arbiter.refresh_detection()
        if arbiter.state is not HardwareState.MAYHEM_SERIAL:
            raise MayhemBridgeError(
                f"Mayhem bridge requires MAYHEM_SERIAL (current={arbiter.state.value})"
            )
        if not arbiter.serial_port:
            raise MayhemBridgeError("No Mayhem serial port detected")
        return arbiter

    def _open_serial_blocking(self, port: str) -> None:
        if self._ser and self._ser.is_open and self._port == port:
            return
        self._close_serial_blocking()
        ser = serial.Serial(
            port=port,
            baudrate=self.cfg.serial_baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=5.0,
        )
        ser.reset_input_buffer()
        ser.write(b"\r\n")
        ser.flush()
        # Drain banner / prompt
        deadline_chunks = 20
        for _ in range(deadline_chunks):
            chunk = ser.read(1024)
            if PROMPT in chunk:
                break
        self._ser = ser
        self._port = port
        log.info("Mayhem serial bridge open on %s", port)

    async def _queue_worker(self) -> None:
        while True:
            fut, cmd, wait = await self._queue.get()
            try:
                result = await asyncio.to_thread(self._execute_blocking, cmd, wait)
                if not fut.done():
                    fut.set_result(result)
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)
            finally:
                self._queue.task_done()

    def _execute_blocking(self, cmd: str, wait: float) -> str:
        if self._ser is None or not self._ser.is_open:
            raise MayhemBridgeError("Serial port is not open")
        ser = self._ser
        ser.reset_input_buffer()
        payload = (cmd.rstrip("\r\n") + "\r\n").encode("ascii", errors="replace")
        ser.write(payload)
        ser.flush()
        buf = b""
        import time

        deadline = time.time() + wait
        while time.time() < deadline:
            chunk = ser.read(8192)
            if chunk:
                buf += chunk
                # screenframeshort ends with ok + prompt
                if cmd.startswith("screenframe"):
                    if b"\r\nok\r\n" in buf or (b"ok\r\n" in buf and PROMPT in buf):
                        # allow trailing prompt
                        time.sleep(0.05)
                        extra = ser.read(ser.in_waiting or 256)
                        if extra:
                            buf += extra
                        break
                elif PROMPT in buf:
                    time.sleep(0.02)
                    extra = ser.read(ser.in_waiting or 64)
                    if extra:
                        buf += extra
                        if PROMPT in buf:
                            break
                    else:
                        break
            else:
                time.sleep(0.01)
        text = buf.decode("utf-8", errors="replace")
        return self._strip_echo(text, cmd)

    @staticmethod
    def _strip_echo(text: str, cmd: str) -> str:
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        cleaned: list[str] = []
        for i, line in enumerate(lines):
            if i == 0 and line.strip() == cmd.strip():
                continue
            if line.strip() == "ch>":
                continue
            cleaned.append(line)
        while cleaned and cleaned[-1].strip() in ("", "ch>"):
            cleaned.pop()
        if cleaned and cleaned[-1].rstrip().endswith("ch>"):
            cleaned[-1] = cleaned[-1].rsplit("ch>", 1)[0].rstrip()
        return "\n".join(cleaned).strip()

    async def command(self, cmd: str, *, wait: float = 3.0) -> str:
        arbiter = await self._require_mayhem_mode()
        port = arbiter.serial_port
        assert port
        async with self._lock:
            await asyncio.to_thread(self._open_serial_blocking, port)
            await self._ensure_worker()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        await self._queue.put((fut, cmd, wait))
        return await fut

    async def capture_screen_short(self, *, wait: float = 8.0) -> ScreenFrame:
        raw = await self.command("screenframeshort", wait=wait)
        return decode_screenframeshort(raw)

    async def button(self, code: int) -> str:
        if code not in BUTTON_MAP:
            raise ValueError(f"button must be 1..8, got {code}")
        return await self.command(f"button {code}", wait=2.0)

    async def touch(self, x: int, y: int) -> str:
        if x < 0 or y < 0:
            raise ValueError("touch coordinates must be >= 0")
        return await self.command(f"touch {x} {y}", wait=2.0)

    async def keyboard_text(self, text: str) -> str:
        """Send UTF-8/ASCII text as Mayhem ``keyboard`` hex payload."""
        if not text:
            raise ValueError("keyboard text must be non-empty")
        hexstr = text.encode("utf-8", errors="replace").hex().upper()
        return await self.command(f"keyboard {hexstr}", wait=3.0)

    async def keyboard_hex(self, hexstr: str) -> str:
        cleaned = re.sub(r"[^0-9A-Fa-f]", "", hexstr)
        if not cleaned or len(cleaned) % 2:
            raise ValueError("keyboard hex must be even-length hex digits")
        return await self.command(f"keyboard {cleaned.upper()}", wait=3.0)

    async def applist(self) -> list[AppInfo]:
        raw = await self.command("applist", wait=8.0)
        apps: list[AppInfo] = []
        seen: set[str] = set()
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            if low.startswith("ch>") or low in {"applist", "ok"}:
                continue
            parts = [p.strip() for p in re.split(r"\s{2,}|\t", line) if p.strip()]
            if len(parts) >= 3:
                short, full, cat = parts[0], parts[1], parts[2]
            else:
                toks = line.split()
                if len(toks) < 2:
                    continue
                short = toks[0]
                cat = toks[-1] if len(toks) >= 3 else ""
                full = " ".join(toks[1:-1]) if len(toks) >= 3 else toks[1]
            key = short.lower()
            if key in seen:
                continue
            seen.add(key)
            apps.append(AppInfo(short_name=short, full_name=full, category=cat))
        return apps

    async def appstart(self, app_id: str) -> str:
        app_id = app_id.strip()
        if not app_id or any(c.isspace() for c in app_id):
            raise ValueError("app_id must be a single token short name")
        return await self.command(f"appstart {app_id}", wait=5.0)

    async def upload_bytes(self, remote_path: str, data: bytes, *, chunk_hex: int = 8) -> None:
        """Upload via fopen / fwrite hex / ftruncate / fsync / fclose (Mayhem SD)."""
        arbiter = await self._require_mayhem_mode()
        port = arbiter.serial_port
        assert port
        async with self._lock:
            await asyncio.to_thread(self._open_serial_blocking, port)
            await asyncio.to_thread(
                self._upload_bytes_blocking, remote_path, data, chunk_hex
            )

    async def download_bytes(self, remote_path: str, *, max_bytes: int = 2_000_000) -> bytes:
        """Best-effort fread hex pull from Mayhem SD."""
        arbiter = await self._require_mayhem_mode()
        port = arbiter.serial_port
        assert port
        async with self._lock:
            await asyncio.to_thread(self._open_serial_blocking, port)
            return await asyncio.to_thread(
                self._download_bytes_blocking, remote_path, max_bytes
            )

    def _cmd_direct(self, cmd: str, wait: float = 3.0) -> str:
        """Blocking command on already-open serial (caller holds ``_lock``)."""
        return self._execute_blocking(cmd, wait)

    def _upload_bytes_blocking(
        self, remote_path: str, data: bytes, chunk_hex: int = 8
    ) -> None:
        remote_path = remote_path.replace("\\", "/")
        parent = str(Path(remote_path).parent).replace("\\", "/").rstrip("/") or "/"
        if parent not in (".", "/", "/FREQMAN", "FREQMAN"):
            self._cmd_direct(f"mkdir {parent}", wait=2.0)
        self._cmd_direct("fclose", wait=1.5)
        self._cmd_direct(f"unlink {remote_path}", wait=2.0)
        resp = self._cmd_direct(f"fopen {remote_path}", wait=3.0)
        if "already open" in resp.lower():
            self._cmd_direct("fclose", wait=1.5)
            resp = self._cmd_direct(f"fopen {remote_path}", wait=3.0)
        if "error" in resp.lower():
            raise MayhemBridgeError(f"fopen failed: {resp}")
        seek = self._cmd_direct("fseek 0", wait=2.0)
        if "error" in seek.lower() or "no open file" in seek.lower():
            raise MayhemBridgeError(f"fseek failed: {seek}")
        total = len(data)
        for i in range(0, total, chunk_hex):
            chunk = data[i : i + chunk_hex]
            hexstr = chunk.hex().upper()
            r = self._cmd_direct(f"fwrite {hexstr}", wait=4.0)
            if "usage:" in r.lower() or "no open file" in r.lower() or "error" in r.lower():
                raise MayhemBridgeError(f"fwrite failed at offset {i}: {r}")
        self._cmd_direct("ftruncate", wait=2.0)
        self._cmd_direct("fsync", wait=3.0)
        self._cmd_direct("fclose", wait=2.0)
        log.info("Uploaded %s bytes → %s", total, remote_path)

    def _download_bytes_blocking(self, remote_path: str, max_bytes: int) -> bytes:
        remote_path = remote_path.replace("\\", "/")
        self._cmd_direct("fclose", wait=1.5)
        size_raw = self._cmd_direct(f"filesize {remote_path}", wait=3.0)
        size_m = re.search(r"(\d+)", size_raw.replace(",", ""))
        size = int(size_m.group(1)) if size_m else 0
        if size <= 0:
            # Try fopen + fread anyway
            size = max_bytes
        size = min(size, max_bytes)
        resp = self._cmd_direct(f"fopen {remote_path}", wait=3.0)
        if "error" in resp.lower():
            raise MayhemBridgeError(f"fopen failed: {resp}")
        self._cmd_direct("fseek 0", wait=2.0)
        out = bytearray()
        # Mayhem fread typically: ``fread N`` returns hex digits
        while len(out) < size:
            want = min(32, size - len(out))
            r = self._cmd_direct(f"fread {want}", wait=4.0)
            hex_only = re.sub(r"[^0-9A-Fa-f]", "", r)
            if len(hex_only) < 2:
                break
            if len(hex_only) % 2:
                hex_only = hex_only[:-1]
            chunk = bytes.fromhex(hex_only)
            if not chunk:
                break
            out.extend(chunk)
            if len(chunk) < want:
                break
        self._cmd_direct("fclose", wait=2.0)
        return bytes(out)


async def get_mayhem_bridge() -> MayhemSerialBridge:
    return await MayhemSerialBridge.get_instance()


async def close_mayhem_bridge() -> None:
    if MayhemSerialBridge._instance is not None:
        await MayhemSerialBridge._instance.close()
