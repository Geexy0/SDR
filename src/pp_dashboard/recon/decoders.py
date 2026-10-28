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


def pocsag_pipeline_cmd(frequency_hz: int) -> str:
    rtl_fm = shutil.which("rx_fm") or shutil.which("rtl_fm")
    multimon = shutil.which("multimon-ng")
    if not rtl_fm or not multimon:
        raise FileNotFoundError(
            "POCSAG needs rtl_fm (or rx_fm) and multimon-ng installed"
        )
    return (
        f"{rtl_fm} -f {frequency_hz} -M fm -s 22050 -g 40 - "
        f"| {multimon} -a POCSAG512 -a POCSAG1200 -a POCSAG2400 -a FLEX -t raw -"
    )


def adsb_pipeline_cmd(*, frequency_hz: int, lna: int = 40, vga: int = 20) -> str:
    """HackRF IQ → readsb/dump1090 Mode-S decoder with SBS on localhost:30003."""
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
    # Signed interleaved 8-bit IQ at 2 MSPS is the stable HackRF ADS-B rate.
    rx = (
        f"stdbuf -oL {hackrf} -r - -f {int(frequency_hz)} -s 2000000 "
        f"-l {int(lna)} -g {int(vga)} -a 0"
    )
    name = decoder.rsplit("/", 1)[-1]
    if name.startswith("readsb"):
        dec = (
            f"{decoder} --ifile - --iformat CS8 --quiet "
            f"--net --net-sbs-port {DEFAULT_ADS_B_SBS_PORT}"
        )
    else:
        # dump1090 family: prefer cs8 ifile; --net enables SBS :30003
        dec = f"{decoder} --ifile - --iformat cs8 --net --quiet"
    return f"{rx} 2>/dev/null | {dec}"


def ais_pipeline_cmd(*, frequency_hz: int, network_feed: bool = False) -> str:
    """Prefer AIS-catcher on HackRF; optional community network feed; else rtl_ais."""
    try:
        catcher = _which("AIS-catcher", "ais-catcher")
    except FileNotFoundError:
        catcher = None
    if catcher:
        # NMEA-only on stdout for the map parser. HackRF index 0 when present.
        # -X = aiscatcher.org community feed (boats without local RF).
        if network_feed:
            return f"{catcher} -X -o 1 -q"
        # LNA/VGA within HackRF Pro legal steps; PREAMP off (SCOPE amp lock).
        return (
            f"{catcher} -d 0 -o 1 -q "
            f"-gf LNA 24 VGA 20 PREAMP off"
        )
    rtl_ais = shutil.which("rtl_ais")
    if rtl_ais:
        return f"{rtl_ais} -n"
    ais = shutil.which("aisdecoder")
    if ais:
        return f"{ais}"
    raise FileNotFoundError(
        "AIS needs AIS-catcher (HackRF) or rtl_ais (RTL-SDR) on PATH "
        f"(wanted ~{frequency_hz} Hz). Bundled binary: tools/bin/AIS-catcher"
    )


@dataclass
class DecoderSession:
    kind: DecoderKind
    arbiter: HardwareArbiter
    frequency_hz: int
    handlers: list[LineHandler] = field(default_factory=list)
    _proc: asyncio.subprocess.Process | None = None
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
        await self.arbiter.cancel_watchdog()
        async with self.arbiter.lock:
            await self.arbiter.refresh_detection()
            if self.arbiter.state is not HardwareState.HACKRF_NATIVE:
                await self.arbiter._to_native_locked()  # noqa: SLF001
            if self.arbiter.runner.running:
                raise RuntimeError("SDR subprocess already running")

            if self.kind == "pocsag":
                cmd = pocsag_pipeline_cmd(self.frequency_hz)
            elif self.kind == "adsb":
                cmd = adsb_pipeline_cmd(frequency_hz=self.frequency_hz or ADS_B_HZ)
            else:
                cmd = ais_pipeline_cmd(frequency_hz=self.frequency_hz or AIS_A_HZ)

            log.info("Starting %s: %s", self.kind, cmd)
            self._proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
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
        self._proc = None
        if proc and proc.returncode is None:
            try:
                # Kill the whole pipeline process group (hackrf_transfer | decoder).
                import os

                os.killpg(proc.pid, signal.SIGINT)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                try:
                    import os

                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    proc.kill()
                await proc.wait()
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
