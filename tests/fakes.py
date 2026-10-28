"""Offline hardware fakes — no real USB / serial / libhackrf."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class FakeMayhemSerial:
    """Simulated CDC-ACM Mayhem shell on /dev/ttyACM0."""

    instances: list[FakeMayhemSerial] = []

    def __init__(
        self,
        port: str = "/dev/ttyACM0",
        baudrate: int = 115200,
        timeout: float = 1.0,
        write_timeout: float = 5.0,
        **_kwargs: Any,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.is_open = True
        self.writes: list[bytes] = []
        self._rx = bytearray(b"ch> ")
        FakeMayhemSerial.instances.append(self)

    def reset_input_buffer(self) -> None:
        pass

    def reset_output_buffer(self) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False

    def write(self, data: bytes) -> int:
        if not self.is_open:
            raise OSError("FakeMayhemSerial closed")
        payload = bytes(data)
        self.writes.append(payload)
        text = payload.decode("utf-8", errors="replace").strip().lower()
        if "hackrf" in text:
            self._rx.extend(b"ok\r\nch> ")
        elif "screenframeshort" in text:
            # Minimal ASCII frame payload + ok/prompt (decode tests use real frames elsewhere)
            self._rx.extend(b"W240 H320\r\nok\r\nch> ")
        elif "applist" in text:
            self._rx.extend(
                b"lookingglass\tLooking Glass\tUtility\r\n"
                b"rx_subghz\tSubGhz RX\tReceive\r\n"
                b"ok\r\nch> "
            )
        elif text.startswith("appstart"):
            self._rx.extend(b"ok\r\nch> ")
        elif text.startswith("button"):
            self._rx.extend(b"ok\r\nch> ")
        elif "ls /freqman" in text or text.startswith("ls "):
            self._rx.extend(b"NORMANDY.TXT\r\nRECON.TXT\r\nok\r\nch> ")
        elif text in {"info", ""} or text.endswith("\r\n"):
            self._rx.extend(
                b"Mayhem Version: fake\r\nBoard: H4M\r\nDevice: HackRF\r\nok\r\nch> "
            )
        else:
            self._rx.extend(b"ok\r\nch> ")
        return len(payload)

    def read(self, size: int = 1) -> bytes:
        if not self._rx:
            return b""
        n = min(size, len(self._rx))
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    @property
    def in_waiting(self) -> int:
        return len(self._rx)

    @classmethod
    def reset(cls) -> None:
        cls.instances.clear()


@dataclass
class FakeProcess:
    argv: list[str]
    stdout_lines: list[bytes] = field(default_factory=list)
    stderr_lines: list[bytes] = field(default_factory=list)
    returncode: int | None = None
    pid: int = 4242
    signals: list[int] = field(default_factory=list)
    ignore_sigint: bool = False
    _stdout: asyncio.StreamReader | None = field(default=None, repr=False)
    _stderr: asyncio.StreamReader | None = field(default=None, repr=False)
    _exit: asyncio.Event | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._stdout = asyncio.StreamReader()
        self._stderr = asyncio.StreamReader()
        self._exit = asyncio.Event()
        for line in self.stdout_lines:
            self._stdout.feed_data(line if line.endswith(b"\n") else line + b"\n")
        for line in self.stderr_lines:
            self._stderr.feed_data(line if line.endswith(b"\n") else line + b"\n")
        # Keep pipes open until stop/exit so readers block appropriately.
        if self.returncode is not None:
            self._stdout.feed_eof()
            self._stderr.feed_eof()
            self._exit.set()

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        return self._stdout

    @property
    def stderr(self) -> asyncio.StreamReader | None:
        return self._stderr

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        if sig == signal.SIGINT and self.ignore_sigint:
            return
        if self.returncode is None:
            # Graceful SIGINT → exit 0; SIGKILL → -9 style
            self.returncode = 0 if sig == signal.SIGINT else -int(sig)
            if self._stdout:
                self._stdout.feed_eof()
            if self._stderr:
                self._stderr.feed_eof()
            assert self._exit is not None
            self._exit.set()

    def kill(self) -> None:
        self.send_signal(signal.SIGKILL)

    async def wait(self) -> int:
        assert self._exit is not None
        await self._exit.wait()
        assert self.returncode is not None
        return self.returncode


class FakeSubprocessPool:
    """Intercepts hackrf_* / rtl_433 / spiflash without touching libusb."""

    BLOCKED = frozenset(
        {
            "hackrf_info",
            "hackrf_transfer",
            "hackrf_sweep",
            "hackrf_spiflash",
            "rtl_433",
            "readsb",
            "dump1090",
            "AIS-catcher",
            "ais-catcher",
        }
    )

    def __init__(self) -> None:
        self.started: list[list[str]] = []
        self.processes: list[FakeProcess] = []
        self._real_exec: Callable[..., Any] | None = None
        self.hackrf_info_ok = True
        self.hung_transfer = False

    def install(self, monkeypatch: Any) -> None:
        import asyncio as aio

        self._real_exec = aio.create_subprocess_exec

        async def _fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
            args = [str(a) for a in argv]
            name = args[0].rsplit("/", 1)[-1] if args else ""
            # Allow non-SDR helper binaries (e.g. stdbuf wrapper) — unwrap.
            if name == "stdbuf" and len(args) >= 4:
                # stdbuf -o0 -e0 hackrf_sweep ...
                inner = args[3:]
                return await _fake_exec(*inner, **kwargs)
            self.started.append(args)
            if name not in self.BLOCKED and name not in {
                "hackrf_info",
                "hackrf_transfer",
                "hackrf_sweep",
                "hackrf_spiflash",
                "rtl_433",
            }:
                # Soft-block unknown SDR-ish names; never call real exec for tests.
                if any(b in name for b in ("hackrf", "rtl_433", "dump1090", "readsb")):
                    name = name
                else:
                    # Still don't invoke real USB tools — return instant-ok stub
                    proc = FakeProcess(argv=args, returncode=0, stdout_lines=[b"ok"])
                    self.processes.append(proc)
                    return proc

            return self._spawn(name, args)

        monkeypatch.setattr(aio, "create_subprocess_exec", _fake_exec)

        # Also block shell pipelines used by decoders
        async def _fake_shell(cmd: str, **kwargs: Any) -> FakeProcess:
            self.started.append(["/bin/sh", "-c", cmd])
            proc = FakeProcess(
                argv=["/bin/sh", "-c", cmd],
                returncode=None,
                stdout_lines=[],
                stderr_lines=[b"fake_shell"],
            )
            self.processes.append(proc)
            return proc

        monkeypatch.setattr(aio, "create_subprocess_shell", _fake_shell)

    def _spawn(self, name: str, args: list[str]) -> FakeProcess:
        if name == "hackrf_info":
            code = 0 if self.hackrf_info_ok else 1
            proc = FakeProcess(
                argv=args,
                returncode=code,
                stdout_lines=[b"hackrf_info version: fake"] if code == 0 else [b"error"],
            )
        elif name == "hackrf_spiflash":
            proc = FakeProcess(argv=args, returncode=0, stdout_lines=[b"reset ok"])
        elif name == "hackrf_transfer":
            proc = FakeProcess(
                argv=args,
                returncode=None,
                stderr_lines=[b"3.0 MiB / 1.0 sec = 3.0 MiB/second"],
                ignore_sigint=self.hung_transfer,
            )
        elif name == "hackrf_sweep":
            # Minimal binary-ish / text noise; parser tests use synthetic bytes elsewhere
            proc = FakeProcess(
                argv=args,
                returncode=None,
                stdout_lines=[],
                stderr_lines=[b"hackrf_sweep fake"],
            )
        elif name == "rtl_433":
            proc = FakeProcess(
                argv=args,
                returncode=None,
                stdout_lines=[],
            )
        else:
            proc = FakeProcess(argv=args, returncode=0, stdout_lines=[b"ok"])
        self.processes.append(proc)
        return proc
