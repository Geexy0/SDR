"""Hybrid local Mode-S + OpenSky ADS-B session with source tagging."""

from __future__ import annotations

import asyncio
import logging
import math
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.recon.decoders import ADS_B_HZ, DecoderSession, adsb_pipeline_cmd
from pp_dashboard.recon.modes_s import CprCache, extract_frames_from_cs8
from pp_dashboard.recon.network_tracks import NetworkTrackSession

log = logging.getLogger(__name__)

LineHandler = Callable[[dict[str, Any]], Awaitable[None] | None]

DEFAULT_RX_LAT = 49.1182
DEFAULT_RX_LON = -1.0972
SOFT_SAMPLE_RATE = 2_000_000
SOFT_CHUNK_BYTES = 256_000  # ~64k complex samples @ CS8


def merge_source_tag(
    seen: dict[str, set[str]],
    icao: str,
    incoming: str,
) -> str:
    """Track per-ICAO sources; return LOCAL_RF | OPENSKY_NET | HYBRID."""
    key = icao.upper()
    bucket = seen.setdefault(key, set())
    bucket.add(incoming)
    if "LOCAL_RF" in bucket and "OPENSKY_NET" in bucket:
        return "HYBRID"
    if "LOCAL_RF" in bucket:
        return "LOCAL_RF"
    return "OPENSKY_NET"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def reception_range_polygon(
    site_lat: float,
    site_lon: float,
    local_points: list[tuple[float, float]],
    *,
    min_km: float = 15.0,
    max_km: float = 450.0,
    n_vertices: int = 64,
) -> list[tuple[float, float]]:
    """Circle around RX site at max haversine distance to LOCAL_RF hits."""
    radius = min_km
    for lat, lon in local_points:
        radius = max(radius, haversine_km(site_lat, site_lon, lat, lon))
    radius = min(max_km, radius)
    ring: list[tuple[float, float]] = []
    for i in range(n_vertices):
        bearing = 2 * math.pi * i / n_vertices
        # Equirectangular approx adequate for map ring
        dlat = (radius / 111.32) * math.cos(bearing)
        dlon = (radius / (111.32 * max(0.2, math.cos(math.radians(site_lat))))) * math.sin(
            bearing
        )
        ring.append((site_lat + dlat, site_lon + dlon))
    return ring


@dataclass
class SoftModesSSession:
    """Built-in Mode-S soft decoder on hackrf_transfer CS8 stdout @ 2 MSPS."""

    arbiter: HardwareArbiter
    frequency_hz: int = ADS_B_HZ
    lna: int = 40
    vga: int = 20
    handlers: list[LineHandler] = field(default_factory=list)
    running: bool = False
    cmd_desc: str = ""
    kind: str = "adsb"
    _proc: asyncio.subprocess.Process | None = None
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)
    _cpr: CprCache = field(default_factory=CprCache)

    def subscribe(self, handler: LineHandler) -> None:
        if handler not in self.handlers:
            self.handlers.append(handler)

    def unsubscribe(self, handler: LineHandler) -> None:
        self.handlers = [h for h in self.handlers if h is not handler]

    async def _emit(self, payload: dict[str, Any]) -> None:
        for h in list(self.handlers):
            try:
                r = h(payload)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("soft Mode-S handler failed")

    async def start(self) -> str:
        if self.running:
            raise RuntimeError("soft Mode-S already running")
        hackrf = shutil.which("hackrf_transfer")
        if not hackrf:
            raise FileNotFoundError("hackrf_transfer not found on PATH")
        await self.arbiter.cancel_watchdog()
        async with self.arbiter.lock:
            await self.arbiter.refresh_detection()
            if self.arbiter.state is not HardwareState.HACKRF_NATIVE:
                await self.arbiter._to_native_locked()  # noqa: SLF001
            if self.arbiter.runner.running:
                raise RuntimeError("SDR subprocess already running")
            cmd = [
                hackrf,
                "-r",
                "-",
                "-f",
                str(int(self.frequency_hz)),
                "-s",
                str(SOFT_SAMPLE_RATE),
                "-l",
                str(int(self.lna)),
                "-g",
                str(int(self.vga)),
                "-a",
                "0",
            ]
            self.cmd_desc = (
                f"soft_modes_s://hackrf_transfer -r - -f {self.frequency_hz} "
                f"-s {SOFT_SAMPLE_RATE} (built-in CRC-24)"
            )
            log.info("Starting soft Mode-S: %s", " ".join(cmd))
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            self.arbiter.state = HardwareState.BUSY_RX
        self.running = True
        assert self._proc.stdout
        self._tasks = [
            asyncio.create_task(self._pump_iq(self._proc.stdout)),
            asyncio.create_task(self._drain_err()),
            asyncio.create_task(self._wait()),
        ]
        return self.cmd_desc

    async def stop(self) -> None:
        self.running = False
        proc = self._proc
        self._proc = None
        if proc and proc.returncode is None:
            try:
                import os
                import signal

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
                    import signal

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

    async def _drain_err(self) -> None:
        assert self._proc and self._proc.stderr
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break

    async def _pump_iq(self, stream: asyncio.StreamReader) -> None:
        buf = bytearray()
        while self.running:
            chunk = await stream.read(SOFT_CHUNK_BYTES)
            if not chunk:
                break
            buf.extend(chunk)
            # Keep last 2 chunks for preamble straddling
            while len(buf) >= SOFT_CHUNK_BYTES:
                block = bytes(buf[:SOFT_CHUNK_BYTES])
                del buf[: SOFT_CHUNK_BYTES // 2]
                await self._decode_block(block)

    async def _decode_block(self, raw: bytes) -> None:
        if len(raw) < 4:
            return
        # CS8 interleaved → complex64
        iq8 = np.frombuffer(raw, dtype=np.int8)
        if iq8.size % 2:
            iq8 = iq8[:-1]
        i = iq8[0::2].astype(np.float32)
        q = iq8[1::2].astype(np.float32)
        z = (i + 1j * q) / 128.0
        frames = await asyncio.to_thread(
            extract_frames_from_cs8, z, sample_rate=SOFT_SAMPLE_RATE, max_frames=32
        )
        for fr in frames:
            fr = self._cpr.update({**fr, "source": "LOCAL_RF"})
            await self._emit(fr)


@dataclass
class HybridAdsbSession:
    """LOCAL_RF (dump1090 or soft) + OPENSKY_NET with HYBRID merge tags."""

    arbiter: HardwareArbiter
    frequency_hz: int = ADS_B_HZ
    lat: float = DEFAULT_RX_LAT
    lon: float = DEFAULT_RX_LON
    handlers: list[LineHandler] = field(default_factory=list)
    running: bool = False
    cmd_desc: str = ""
    kind: str = "adsb"
    _rf: DecoderSession | SoftModesSSession | None = None
    _net: NetworkTrackSession | None = None
    _seen: dict[str, set[str]] = field(default_factory=dict)
    _local_ll: list[tuple[float, float]] = field(default_factory=list)
    _range_task: asyncio.Task[None] | None = None

    def subscribe(self, handler: LineHandler) -> None:
        if handler not in self.handlers:
            self.handlers.append(handler)

    def unsubscribe(self, handler: LineHandler) -> None:
        self.handlers = [h for h in self.handlers if h is not handler]

    async def _emit(self, payload: dict[str, Any]) -> None:
        for h in list(self.handlers):
            try:
                r = h(payload)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("hybrid ADS-B handler failed")

    async def _on_rf(self, payload: dict[str, Any]) -> None:
        if str(payload.get("type") or "") != "adsb":
            await self._emit(payload)
            return
        icao = str(payload.get("icao") or "").upper()
        src = merge_source_tag(self._seen, icao, "LOCAL_RF") if icao else "LOCAL_RF"
        out = {**payload, "source": src, "network": False}
        lat, lon = out.get("lat"), out.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            self._local_ll.append((float(lat), float(lon)))
            del self._local_ll[:-200]
        await self._emit(out)

    async def _on_net(self, payload: dict[str, Any]) -> None:
        if str(payload.get("type") or "") != "adsb":
            await self._emit(payload)
            return
        icao = str(payload.get("icao") or "").upper()
        src = merge_source_tag(self._seen, icao, "OPENSKY_NET") if icao else "OPENSKY_NET"
        await self._emit({**payload, "source": src, "network": True})

    async def start(self) -> str:
        if self.running:
            raise RuntimeError("hybrid ADS-B already running")
        self.running = True
        parts: list[str] = []

        # Prefer external dump1090/readsb; else built-in soft decoder.
        rf_ok = False
        try:
            adsb_pipeline_cmd(frequency_hz=self.frequency_hz)
            rf: DecoderSession | SoftModesSSession = DecoderSession(
                kind="adsb",
                arbiter=self.arbiter,
                frequency_hz=self.frequency_hz,
            )
            rf.subscribe(self._on_rf)
            desc = await rf.start()
            self._rf = rf
            parts.append(desc)
            rf_ok = True
        except FileNotFoundError as exc:
            log.info("dump1090/readsb unavailable (%s) — soft Mode-S", exc)
            try:
                soft = SoftModesSSession(
                    arbiter=self.arbiter, frequency_hz=self.frequency_hz
                )
                soft.subscribe(self._on_rf)
                desc = await soft.start()
                self._rf = soft
                parts.append(desc)
                rf_ok = True
            except Exception as soft_exc:
                log.warning("Local RF Mode-S unavailable: %s", soft_exc)
                parts.append(f"local_rf_unavailable:{soft_exc}")

        net = NetworkTrackSession(
            kind="adsb",
            lat=self.lat,
            lon=self.lon,
            span_deg=2.0,
        )
        net.subscribe(self._on_net)
        try:
            ndesc = await net.start()
            self._net = net
            parts.append(ndesc)
        except Exception as net_exc:
            log.warning("OpenSky unavailable: %s", net_exc)
            parts.append(f"opensky_unavailable:{net_exc}")
            if not rf_ok:
                self.running = False
                raise

        self.cmd_desc = " | ".join(parts)
        self._range_task = asyncio.create_task(self._range_loop())
        return self.cmd_desc

    async def stop(self) -> None:
        self.running = False
        if self._range_task:
            self._range_task.cancel()
            await asyncio.gather(self._range_task, return_exceptions=True)
            self._range_task = None
        if self._rf:
            await self._rf.stop()
            self._rf = None
        if self._net:
            await self._net.stop()
            self._net = None

    async def _range_loop(self) -> None:
        while self.running:
            try:
                ring = reception_range_polygon(self.lat, self.lon, list(self._local_ll))
                await self._emit(
                    {
                        "type": "adsb_meta",
                        "rx_lat": self.lat,
                        "rx_lon": self.lon,
                        "range_polygon": [[a, b] for a, b in ring],
                        "local_hits": len(self._local_ll),
                        "source": "LOCAL_RF",
                    }
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("range polygon emit failed")
            await asyncio.sleep(5.0)
