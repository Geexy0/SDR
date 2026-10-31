"""Offline hardware fakes — no real USB / serial / libhackrf."""

from __future__ import annotations

import asyncio
import json
import signal
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
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
            # Minimal valid MHSC-ish ASCII rows so decode_screenframeshort can try
            self._rx.extend(b"W4 H1\r\n00000000\r\nok\r\nch> ")
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
        elif text.startswith("filesize"):
            self._rx.extend(b"8\r\nok\r\nch> ")
        elif text.startswith("fread"):
            self._rx.extend(b"DEADBEEFCAFEBABE\r\nok\r\nch> ")
        elif text.startswith("fwrite") or text.startswith("fopen") or text.startswith(
            "fclose"
        ) or text.startswith("fseek") or text.startswith("ftruncate") or text.startswith(
            "fsync"
        ) or text.startswith("unlink") or text.startswith("mkdir"):
            self._rx.extend(b"ok\r\nch> ")
        elif text.startswith("ls /captures") or text.startswith("ls /capture"):
            self._rx.extend(b"field_burst.C16\r\nook_sample.SUB\r\nok\r\nch> ")
        elif "ls /freqman" in text or text.startswith("ls "):
            self._rx.extend(
                b"NORMANDY.TXT\r\nRECON.TXT\r\nfield_burst.C16\r\nok\r\nch> "
            )
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


def _sweep_binary_record(*, lo_hz: int, hi_hz: int, n_bins: int = 20) -> bytes:
    """One little-endian hackrf_sweep -B record (matches SweepBinaryParser)."""
    powers = struct.pack(f"<{n_bins}f", *([-45.0 - (i % 5) for i in range(n_bins)]))
    body = struct.pack("<QQ", lo_hz, hi_hz) + powers
    return struct.pack("<I", len(body)) + body


def _iq_cs8_blob(n_samples: int = 4096) -> bytes:
    """Interleaved int8 IQ (2 bytes/sample)."""
    # Quiet-ish tone-ish pattern; non-zero so RMS parsers stay finite.
    out = bytearray(n_samples * 2)
    for i in range(n_samples):
        out[2 * i] = (i * 3) % 64
        out[2 * i + 1] = (i * 5) % 64
    return bytes(out)


class _CloseableStream:
    """StreamReader stand-in that also exposes ``.close()`` (decoder pipelines)."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader

    def close(self) -> None:
        return None

    def feed_data(self, data: bytes) -> None:
        self._reader.feed_data(data)

    def feed_eof(self) -> None:
        self._reader.feed_eof()

    def at_eof(self) -> bool:
        return self._reader.at_eof()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._reader, name)


@dataclass
class FakeProcess:
    argv: list[str]
    stdout_lines: list[bytes] = field(default_factory=list)
    stderr_lines: list[bytes] = field(default_factory=list)
    stdout_blobs: list[bytes] = field(default_factory=list)
    returncode: int | None = None
    pid: int = 4242
    signals: list[int] = field(default_factory=list)
    ignore_sigint: bool = False
    _stdout: _CloseableStream | None = field(default=None, repr=False)
    _stderr: _CloseableStream | None = field(default=None, repr=False)
    _exit: asyncio.Event | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        raw_out = asyncio.StreamReader()
        raw_err = asyncio.StreamReader()
        self._stdout = _CloseableStream(raw_out)
        self._stderr = _CloseableStream(raw_err)
        self._exit = asyncio.Event()
        for blob in self.stdout_blobs:
            self._stdout.feed_data(blob)
        for line in self.stdout_lines:
            self._stdout.feed_data(line if line.endswith(b"\n") else line + b"\n")
        for line in self.stderr_lines:
            self._stderr.feed_data(line if line.endswith(b"\n") else line + b"\n")
        if self.returncode is not None:
            self._stdout.feed_eof()
            self._stderr.feed_eof()
            self._exit.set()

    @property
    def stdout(self) -> _CloseableStream | None:
        return self._stdout

    @property
    def stderr(self) -> _CloseableStream | None:
        return self._stderr

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        if sig == signal.SIGINT and self.ignore_sigint:
            return
        if self.returncode is None:
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

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        _ = input
        if self.returncode is None:
            await self.wait()
        out = b"".join(self.stdout_blobs) + b"".join(
            (ln if ln.endswith(b"\n") else ln + b"\n") for ln in self.stdout_lines
        )
        err = b"".join(
            (ln if ln.endswith(b"\n") else ln + b"\n") for ln in self.stderr_lines
        )
        return out, err


# Binaries that isolate_hardware's which()-mock always resolves.
MOCK_SDR_BINARIES: frozenset[str] = frozenset(
    {
        "hackrf_info",
        "hackrf_transfer",
        "hackrf_sweep",
        "hackrf_spiflash",
        "rtl_433",
        "multimon-ng",
        "rtl_fm",
        "rx_fm",
        "ais-catcher",
        "AIS-catcher",
        "readsb",
        "dump1090",
        "dump1090-fa",
        "dump1090-mutability",
        "rs41mod",
        "rs_detect",
        "sondemod",
        "rs41decoder",
        "aplay",
        "lsusb",
        "stdbuf",
    }
)


class FakeSubprocessPool:
    """Intercepts hackrf_* / rtl_433 / decoder CLIs without touching libusb."""

    BLOCKED = frozenset(
        {
            "hackrf_info",
            "hackrf_transfer",
            "hackrf_sweep",
            "hackrf_spiflash",
            "rtl_433",
            "readsb",
            "dump1090",
            "dump1090-fa",
            "dump1090-mutability",
            "AIS-catcher",
            "ais-catcher",
            "multimon-ng",
            "rtl_fm",
            "rx_fm",
            "rs41mod",
            "rs_detect",
            "sondemod",
            "rs41decoder",
            "aplay",
            "lsusb",
        }
    )

    def __init__(self) -> None:
        self.started: list[list[str]] = []
        self.processes: list[FakeProcess] = []
        self._real_exec: Callable[..., Any] | None = None
        self.hackrf_info_ok = True
        self.hung_transfer = False
        self._next_pid = 5000
        self.frames_emitted: dict[str, int] = {}

    def _pid(self) -> int:
        self._next_pid += 1
        return self._next_pid

    def install(self, monkeypatch: Any) -> None:
        import asyncio as aio
        import os as _os

        self._real_exec = aio.create_subprocess_exec

        def _fake_killpg(pid: int, sig: int) -> None:
            live = [p for p in self.processes if p.pid == pid and p.returncode is None]
            targets = live or [p for p in self.processes if p.pid == pid]
            if not targets:
                raise ProcessLookupError(f"fake killpg: no process {pid}")
            for proc in targets:
                proc.send_signal(sig)

        monkeypatch.setattr(_os, "killpg", _fake_killpg)

        async def _fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
            args = [str(a) for a in argv]
            name = args[0].rsplit("/", 1)[-1] if args else ""
            if name == "stdbuf" and len(args) >= 4:
                return await _fake_exec(*args[3:], **kwargs)
            self.started.append(args)
            if name not in self.BLOCKED:
                if any(b in name for b in ("hackrf", "rtl_433", "dump1090", "readsb", "rs41")):
                    pass
                else:
                    proc = FakeProcess(
                        argv=args, returncode=0, stdout_lines=[b"ok"], pid=self._pid()
                    )
                    self.processes.append(proc)
                    return proc
            return self._spawn(name, args)

        monkeypatch.setattr(aio, "create_subprocess_exec", _fake_exec)

        async def _fake_shell(cmd: str, **kwargs: Any) -> FakeProcess:
            self.started.append(["/bin/sh", "-c", cmd])
            lines, blobs = self._shell_payload(cmd)
            proc = FakeProcess(
                argv=["/bin/sh", "-c", cmd],
                returncode=None,
                stdout_lines=lines,
                stdout_blobs=blobs,
                stderr_lines=[b"fake_shell"],
                pid=self._pid(),
            )
            self.processes.append(proc)
            return proc

        monkeypatch.setattr(aio, "create_subprocess_shell", _fake_shell)

    def _shell_payload(self, cmd: str) -> tuple[list[bytes], list[bytes]]:
        lower = cmd.lower()
        if "multimon" in lower:
            self.frames_emitted["multimon-ng"] = self.frames_emitted.get("multimon-ng", 0) + 1
            return (
                [b"POCSAG1200: Address: 123456 Function: 3 Alpha: TESTPAGE LAB"],
                [],
            )
        if "ais-catcher" in lower or "aisdecoder" in lower:
            self.frames_emitted["ais-catcher"] = self.frames_emitted.get("ais-catcher", 0) + 1
            return (
                [
                    b"!AIVDM,1,1,,A,133m@ogP00PD;88MD5MTDww@2D7k,0*46",
                ],
                [],
            )
        if "rs41" in lower or "rs_detect" in lower or "sondemod" in lower:
            self.frames_emitted["rs41mod"] = self.frames_emitted.get("rs41mod", 0) + 1
            return (
                [
                    b"[rs41] frame=12 serial=S1234567 lat=49.1200 lon=-1.1000 alt=11420.0 "
                    b"vz=-5.2 temp=-48.1",
                ],
                [],
            )
        return ([], [])

    def _spawn(self, name: str, args: list[str]) -> FakeProcess:
        pid = self._pid()
        if name == "hackrf_info":
            code = 0 if self.hackrf_info_ok else 1
            proc = FakeProcess(
                argv=args,
                returncode=code,
                stdout_lines=[b"hackrf_info version: fake"] if code == 0 else [b"error"],
                pid=pid,
            )
        elif name == "lsusb":
            proc = FakeProcess(
                argv=args,
                returncode=0,
                stdout_lines=[b"Bus 001 Device 003: ID 1d50:6089 Great Scott Gadgets"],
                pid=pid,
            )
        elif name == "hackrf_spiflash":
            proc = FakeProcess(argv=args, returncode=0, stdout_lines=[b"reset ok"], pid=pid)
        elif name == "hackrf_transfer":
            blobs: list[bytes] = []
            # RX to stdout (`-r -`) or to a capture file (`-r path.cs8`)
            if "-r" in args:
                try:
                    r_idx = args.index("-r")
                    target = args[r_idx + 1] if r_idx + 1 < len(args) else ""
                except ValueError:
                    target = ""
                iq = _iq_cs8_blob(8192)
                if target == "-":
                    blobs.append(iq)
                elif target:
                    path = Path(target)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(iq)
                self.frames_emitted["hackrf_transfer"] = (
                    self.frames_emitted.get("hackrf_transfer", 0) + 1
                )
            # Finite `-n` captures exit after writing; otherwise stream until SIGINT.
            finite = "-n" in args
            proc = FakeProcess(
                argv=args,
                returncode=0 if finite else None,
                stdout_blobs=blobs,
                stderr_lines=[b"3.0 MiB / 1.0 sec = 3.0 MiB/second"],
                ignore_sigint=self.hung_transfer,
                pid=pid,
            )
        elif name == "hackrf_sweep":
            blobs = []
            lines: list[bytes] = []
            if "-B" in args:
                blobs = [
                    _sweep_binary_record(lo_hz=400_000_000, hi_hz=405_000_000),
                    _sweep_binary_record(lo_hz=405_000_000, hi_hz=410_000_000),
                ]
                self.frames_emitted["hackrf_sweep"] = self.frames_emitted.get(
                    "hackrf_sweep", 0
                ) + 2
            else:
                # Text mode used by /api/hardware/sweep runner
                lines = [
                    b"2026-01-01, 00:00:00, 400000000, 405000000, 1000000.0, 5, -40.0, -41.0, -42.0",
                    b"2026-01-01, 00:00:00, 405000000, 410000000, 1000000.0, 5, -43.0, -44.0, -45.0",
                ]
                self.frames_emitted["hackrf_sweep"] = self.frames_emitted.get(
                    "hackrf_sweep", 0
                ) + 2
            proc = FakeProcess(
                argv=args,
                returncode=None,
                stdout_blobs=blobs,
                stdout_lines=lines,
                stderr_lines=[b"hackrf_sweep fake"],
                pid=pid,
            )
        elif name == "rtl_433":
            ook = {
                "time": "2026-01-01T00:00:00",
                "protocol": 1,
                "model": "Generic-Remote",
                "id": 42,
                "data": "0xA5A5",
                "mod": "ASK",
                "freq": 433.92,
            }
            tpms = {
                "time": "2026-01-01T00:00:01",
                "protocol": 59,
                "model": "Schrader",
                "type": "TPMS",
                "id": "0xABC123",
                "pressure_kPa": 220.5,
                "temperature_C": 21.0,
            }
            lines = [
                json.dumps(ook).encode(),
                json.dumps(tpms).encode(),
            ]
            self.frames_emitted["rtl_433"] = self.frames_emitted.get("rtl_433", 0) + 2
            proc = FakeProcess(argv=args, returncode=None, stdout_lines=lines, pid=pid)
        elif name == "multimon-ng":
            self.frames_emitted["multimon-ng"] = self.frames_emitted.get("multimon-ng", 0) + 1
            proc = FakeProcess(
                argv=args,
                returncode=None,
                stdout_lines=[
                    b"POCSAG1200: Address: 123456 Function: 3 Alpha: TESTPAGE LAB"
                ],
                pid=pid,
            )
        elif name in {"AIS-catcher", "ais-catcher"}:
            self.frames_emitted["ais-catcher"] = self.frames_emitted.get("ais-catcher", 0) + 1
            proc = FakeProcess(
                argv=args,
                returncode=None,
                stdout_lines=[
                    b"!AIVDM,1,1,,A,133m@ogP00PD;88MD5MTDww@2D7k,0*46"
                ],
                pid=pid,
            )
        elif name in {"rs41mod", "rs_detect", "sondemod", "rs41decoder"}:
            self.frames_emitted["rs41mod"] = self.frames_emitted.get("rs41mod", 0) + 1
            proc = FakeProcess(
                argv=args,
                returncode=None,
                stdout_lines=[
                    b"[rs41] frame=12 serial=S1234567 lat=49.1200 lon=-1.1000 alt=11420.0"
                ],
                pid=pid,
            )
        elif name in {"readsb", "dump1090", "dump1090-fa", "dump1090-mutability"}:
            self.frames_emitted["readsb"] = self.frames_emitted.get("readsb", 0) + 1
            proc = FakeProcess(
                argv=args,
                returncode=None,
                stdout_lines=[
                    b"MSG,3,1,1,ABCDEF,1,2026/01/01,00:00:00.000,"
                    b"2026/01/01,00:00:00.000,,32000,,,49.12000,-1.10000,,,0,0,0,0"
                ],
                pid=pid,
            )
        elif name in {"rtl_fm", "rx_fm", "aplay"}:
            proc = FakeProcess(
                argv=args,
                returncode=None,
                stdout_blobs=[_iq_cs8_blob(512)] if name != "aplay" else [],
                pid=pid,
            )
        else:
            proc = FakeProcess(argv=args, returncode=0, stdout_lines=[b"ok"], pid=pid)
        self.processes.append(proc)
        return proc
