"""External recon decoder process managers (POCSAG / ADS-B / AIS)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.recon.track_parse import parse_adsb_json, parse_ais_nmea, parse_sbs_line

log = logging.getLogger(__name__)

DecoderKind = Literal["pocsag", "adsb", "ais"]
LineHandler = Callable[[dict[str, Any]], Awaitable[None] | None]

ADS_B_HZ = 1_090_000_000
AIS_A_HZ = 161_975_000
DEFAULT_ADS_B_SBS_PORT = 30003


def _project_tools_bin() -> str | None:
    """…/SDR/tools/bin when running from a source checkout."""
    here = Path(__file__).resolve()
    # src/pp_dashboard/recon/decoders.py → repo root is parents[3]
    try:
        root = here.parents[3]
    except IndexError:
        return None
    tools = root / "tools" / "bin"
    return str(tools) if tools.is_dir() else None


def _which(*names: str) -> str:
    tools = _project_tools_bin()
    search_dirs: list[str] = []
    if tools:
        search_dirs.append(tools)
    path_env = os.environ.get("PATH", "")
    for name in names:
        if tools:
            candidate = Path(tools) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        path = shutil.which(name, path=os.pathsep.join([*search_dirs, path_env]))
        if path:
            return path
    raise FileNotFoundError(f"None of {names} found on PATH")


def pocsag_pipeline_argv(frequency_hz: int) -> tuple[list[str], list[str]]:
    """Return (rtl_fm argv, multimon-ng argv) — no shell."""
    rtl_fm = shutil.which("rx_fm") or shutil.which("rtl_fm")
    multimon = shutil.which("multimon-ng")
    if not rtl_fm or not multimon:
        raise FileNotFoundError(
            "POCSAG needs rtl_fm (or rx_fm) and multimon-ng installed"
        )
    producer = [rtl_fm, "-f", str(int(frequency_hz)), "-M", "fm", "-s", "22050", "-g", "40", "-"]
    consumer = [
        multimon,
        "-a",
        "POCSAG512",
        "-a",
        "POCSAG1200",
        "-a",
        "POCSAG2400",
        "-a",
        "FLEX",
        "-t",
        "raw",
        "-",
    ]
    return producer, consumer


def pocsag_pipeline_cmd(frequency_hz: int) -> str:
    producer, consumer = pocsag_pipeline_argv(frequency_hz)
    return f"{' '.join(producer)} | {' '.join(consumer)}"


def adsb_pipeline_argv(
    *, frequency_hz: int, lna: int = 40, vga: int = 20
) -> tuple[list[str], list[str]]:
    """HackRF IQ → readsb/dump1090 Mode-S decoder argv pair (no shell)."""
    hackrf = shutil.which("hackrf_transfer")
    if not hackrf:
        raise FileNotFoundError("hackrf_transfer not found on PATH")
    decoder = None
    for name in ("readsb", "dump1090", "dump1090-mutability", "dump1090-fa"):
        decoder = shutil.which(name)
        if decoder:
            break
    if not decoder:
        raise FileNotFoundError(
            "ADS-B needs readsb or dump1090 on PATH "
            "(apt: readsb). HackRF pipe: hackrf_transfer | readsb --ifile -"
        )
    producer = [
        hackrf,
        "-r",
        "-",
        "-f",
        str(int(frequency_hz)),
        "-s",
        "2000000",
        "-l",
        str(int(lna)),
        "-g",
        str(int(vga)),
        "-a",
        "0",
    ]
    name = decoder.rsplit("/", 1)[-1]
    if name.startswith("readsb"):
        consumer = [
            decoder,
            "--ifile",
            "-",
            "--iformat",
            "CS8",
            "--quiet",
            "--net",
            "--net-sbs-port",
            str(DEFAULT_ADS_B_SBS_PORT),
        ]
    else:
        consumer = [decoder, "--ifile", "-", "--iformat", "cs8", "--net", "--quiet"]
    return producer, consumer


def adsb_pipeline_cmd(*, frequency_hz: int, lna: int = 40, vga: int = 20) -> str:
    producer, consumer = adsb_pipeline_argv(
        frequency_hz=frequency_hz, lna=lna, vga=vga
    )
    return f"{' '.join(producer)} | {' '.join(consumer)}"


def ais_pipeline_argv(*, frequency_hz: int, network_feed: bool = False) -> list[str]:
    """Prefer AIS-catcher on HackRF; optional community network feed; else rtl_ais."""
    _ = frequency_hz
    try:
        catcher = _which("AIS-catcher", "ais-catcher")
    except FileNotFoundError:
        catcher = None
    if catcher:
        if network_feed:
            return [catcher, "-X", "-o", "1", "-q"]
        return [catcher, "-d", "0", "-o", "1", "-q", "-gf", "LNA", "24", "VGA", "20", "PREAMP", "off"]
    rtl_ais = shutil.which("rtl_ais")
    if rtl_ais:
        return [rtl_ais, "-n"]
    ais = shutil.which("aisdecoder")
    if ais:
        return [ais]
    raise FileNotFoundError(
        "AIS needs AIS-catcher (HackRF) or rtl_ais (RTL-SDR) on PATH "
        f"(wanted ~{frequency_hz} Hz). Bundled binary: tools/bin/AIS-catcher"
    )


def ais_pipeline_cmd(*, frequency_hz: int, network_feed: bool = False) -> str:
    return " ".join(ais_pipeline_argv(frequency_hz=frequency_hz, network_feed=network_feed))


@dataclass
class DecoderSession:
    kind: DecoderKind
    arbiter: HardwareArbiter
    frequency_hz: int
    handlers: list[LineHandler] = field(default_factory=list)
    _proc: asyncio.subprocess.Process | None = None
    _producer: asyncio.subprocess.Process | None = None
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)
    _sbs_reader: asyncio.StreamReader | None = None
    _sbs_writer: asyncio.StreamWriter | None = None
    running: bool = False
    last_lines: list[dict[str, Any]] = field(default_factory=list)
    cmd_desc: str = ""

    def subscribe(self, handler: LineHandler) -> None:
        if handler not in self.handlers:
            self.handlers.append(handler)

    def unsubscribe(self, handler: LineHandler) -> None:
        self.handlers = [h for h in self.handlers if h is not handler]

    async def _emit(self, payload: dict[str, Any]) -> None:
        self.last_lines.append(payload)
        del self.last_lines[:-200]
        for h in list(self.handlers):
            try:
                r = h(payload)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("decoder handler failed")

    async def start(self) -> str:
        if self.running:
            raise RuntimeError(f"{self.kind} already running")
        self.arbiter.assert_sdr_idle(excluding="decoder")
        await self.arbiter.cancel_watchdog()
        async with self.arbiter.lock:
            await self.arbiter.refresh_detection()
            if self.arbiter.state is not HardwareState.HACKRF_NATIVE:
                await self.arbiter._to_native_locked()  # noqa: SLF001
            if self.arbiter.runner.running:
                raise RuntimeError("SDR subprocess already running")

            if self.kind == "pocsag":
                producer_argv, consumer_argv = pocsag_pipeline_argv(self.frequency_hz)
                cmd = pocsag_pipeline_cmd(self.frequency_hz)
            elif self.kind == "adsb":
                producer_argv, consumer_argv = adsb_pipeline_argv(
                    frequency_hz=self.frequency_hz or ADS_B_HZ
                )
                cmd = adsb_pipeline_cmd(frequency_hz=self.frequency_hz or ADS_B_HZ)
            else:
                producer_argv = None
                consumer_argv = ais_pipeline_argv(
                    frequency_hz=self.frequency_hz or AIS_A_HZ
                )
                cmd = ais_pipeline_cmd(frequency_hz=self.frequency_hz or AIS_A_HZ)

            log.info("Starting %s: %s", self.kind, cmd)
            if producer_argv is None:
                self._producer = None
                self._proc = await asyncio.create_subprocess_exec(
                    *consumer_argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
            else:
                self._producer = await asyncio.create_subprocess_exec(
                    *producer_argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
                assert self._producer.stdout is not None
                self._proc = await asyncio.create_subprocess_exec(
                    *consumer_argv,
                    stdin=self._producer.stdout,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                self._producer.stdout.close()
            self.cmd_desc = cmd
            self.arbiter.state = HardwareState.BUSY_RX

        self.running = True
        assert self._proc.stdout and self._proc.stderr
        self._tasks = [
            asyncio.create_task(self._pump(self._proc.stdout, "stdout")),
            asyncio.create_task(self._pump(self._proc.stderr, "stderr")),
            asyncio.create_task(self._wait()),
        ]
        if self.kind == "adsb":
            self._tasks.append(asyncio.create_task(self._sbs_client_loop()))
        return self.cmd_desc

    async def _signal_proc(
        self, proc: asyncio.subprocess.Process | None, sig: signal.Signals
    ) -> None:
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                if sig is signal.SIGINT:
                    proc.send_signal(signal.SIGINT)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass

    async def stop(self) -> None:
        self.running = False
        if self._sbs_writer is not None:
            self._sbs_writer.close()
            try:
                await self._sbs_writer.wait_closed()
            except Exception:
                pass
            self._sbs_writer = None
            self._sbs_reader = None
        proc = self._proc
        producer = self._producer
        self._proc = None
        self._producer = None
        await self._signal_proc(producer, signal.SIGINT)
        await self._signal_proc(proc, signal.SIGINT)
        for p in (proc, producer):
            if p is None or p.returncode is not None:
                continue
            try:
                await asyncio.wait_for(p.wait(), timeout=2.0)
            except TimeoutError:
                await self._signal_proc(p, signal.SIGKILL)
                await p.wait()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        async with self.arbiter.lock:
            if self.arbiter.state is HardwareState.BUSY_RX:
                self.arbiter.state = HardwareState.HACKRF_NATIVE

    async def _wait(self) -> None:
        assert self._proc
        await self._proc.wait()
        self.running = False
        producer = self._producer
        if producer is not None and producer.returncode is None:
            await self._signal_proc(producer, signal.SIGKILL)
            try:
                await producer.wait()
            except Exception:
                pass

    async def _sbs_client_loop(self) -> None:
        """Attach to dump1090/readsb SBS port once it comes up."""
        for _ in range(40):
            if not self.running:
                return
            try:
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", DEFAULT_ADS_B_SBS_PORT
                )
                self._sbs_reader = reader
                self._sbs_writer = writer
                log.info("ADS-B SBS connected on :%s", DEFAULT_ADS_B_SBS_PORT)
                break
            except (ConnectionRefusedError, OSError):
                await asyncio.sleep(0.25)
        else:
            log.warning("ADS-B SBS port %s never accepted", DEFAULT_ADS_B_SBS_PORT)
            return
        assert self._sbs_reader is not None
        try:
            while self.running:
                line_b = await self._sbs_reader.readline()
                if not line_b:
                    break
                line = line_b.decode("utf-8", errors="replace").rstrip()
                payload = parse_sbs_line(line)
                if payload:
                    await self._emit(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SBS client failed")

    async def _pump(self, stream: asyncio.StreamReader, name: str) -> None:
        while True:
            line_b = await stream.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            payload = self._parse_line(line, name)
            if payload:
                await self._emit(payload)

    def _parse_line(self, line: str, stream: str) -> dict[str, Any] | None:
        if self.kind == "pocsag":
            if any(k in line for k in ("POCSAG", "FLEX", "Alpha:", "Numeric:")):
                return {"type": "pocsag", "stream": stream, "raw": line}
            return None
        if self.kind == "adsb":
            sbs = parse_sbs_line(line)
            if sbs:
                return sbs
            js = parse_adsb_json(line)
            if js:
                return js
            m = re.search(r"\b([0-9A-Fa-f]{6})\b", line)
            if m and ("icao" in line.lower() or "alt" in line.lower() or "Aircraft" in line):
                return {"type": "adsb", "icao": m.group(1).upper(), "raw": line}
            return None
        ais = parse_ais_nmea(line)
        if ais:
            return ais
        if "MMSI" in line or "AIS" in line.upper():
            return {"type": "ais", "raw": line, "frequency_hz": self.frequency_hz}
        return None
