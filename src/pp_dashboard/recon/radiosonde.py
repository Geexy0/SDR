"""Radiosonde 400.15–406 MHz GFSK tracker (RS41 / M20) — RX only."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import shutil
import signal
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pp_dashboard.geoloc.projection import LocalTangentPlane
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.state import HardwareState

log = logging.getLogger(__name__)

LineHandler = Callable[[dict[str, Any]], Awaitable[None] | None]

BAND_HZ = (400_150_000, 406_000_000)
DEFAULT_SONDE_HZ = 402_500_000
RS41_SYNC = 0x10B6CA11
RS41_SYNC_INV = (~RS41_SYNC) & 0xFFFFFFFF
DEFAULT_BURST_ALT_M = 32_000.0
DEFAULT_DESCENT_MPS = 6.0


def sync_bits(word: int, nbits: int = 32) -> list[int]:
    """MSB-first bit list for a sync word."""
    return [(word >> (nbits - 1 - i)) & 1 for i in range(nbits)]


@dataclass(slots=True)
class SyncHit:
    offset: int
    inverted: bool
    score: int


def correlate_sync(bits: list[int], *, min_score: int = 28) -> SyncHit | None:
    """Slide RS41 sync / inverted sync across a bit stream; return best hit."""
    if len(bits) < 32:
        return None
    targets = (
        (sync_bits(RS41_SYNC), False),
        (sync_bits(RS41_SYNC_INV), True),
    )
    best: SyncHit | None = None
    for i in range(0, len(bits) - 31):
        window = bits[i : i + 32]
        for pat, inv in targets:
            score = sum(1 for a, b in zip(window, pat, strict=True) if a == b)
            if score >= min_score and (best is None or score > best.score):
                best = SyncHit(offset=i, inverted=inv, score=score)
    return best


@dataclass(slots=True)
class SondeFrame:
    serial_id: str
    lat: float
    lon: float
    alt_m: float
    vz_mps: float = 0.0
    wind_speed_mps: float = 0.0
    wind_dir_deg: float = 0.0
    temp_c: float | None = None
    frequency_hz: int = DEFAULT_SONDE_HZ
    frame_n: int | None = None
    t_mono: float = 0.0
    kind: Literal["RS41", "M20", "unknown"] = "RS41"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "radiosonde",
            "serial_id": self.serial_id,
            "lat": self.lat,
            "lon": self.lon,
            "alt_m": self.alt_m,
            "vz_mps": self.vz_mps,
            "wind_speed_mps": self.wind_speed_mps,
            "wind_dir_deg": self.wind_dir_deg,
            "temp_c": self.temp_c,
            "frequency_hz": self.frequency_hz,
            "frame_n": self.frame_n,
            "kind": self.kind,
            "t_mono": self.t_mono,
        }


@dataclass(slots=True)
class GeoPoint:
    lat: float
    lon: float
    alt_m: float
    t_mono: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"lat": self.lat, "lon": self.lon, "alt_m": self.alt_m, "t_mono": self.t_mono}


@dataclass
class SondeTrack:
    serial_id: str
    kind: str = "RS41"
    frequency_hz: int = DEFAULT_SONDE_HZ
    trajectory: list[GeoPoint] = field(default_factory=list)
    last: SondeFrame | None = None
    burst: GeoPoint | None = None
    landing: GeoPoint | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "serial_id": self.serial_id,
            "kind": self.kind,
            "frequency_hz": self.frequency_hz,
            "trajectory": [p.to_dict() for p in self.trajectory[-200:]],
            "last": self.last.to_dict() if self.last else None,
            "burst": self.burst.to_dict() if self.burst else None,
            "landing": self.landing.to_dict() if self.landing else None,
        }


def _offset_ll(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    frame = LocalTangentPlane(lat, lon, 0.0)
    la, lo, _ = frame.enu_to_llh(east_m, north_m, 0.0)
    return la, lo


def predict_burst_landing(
    *,
    lat: float,
    lon: float,
    alt_m: float,
    vz_mps: float,
    wind_speed_mps: float,
    wind_dir_deg: float,
    burst_alt_m: float = DEFAULT_BURST_ALT_M,
    descent_mps: float = DEFAULT_DESCENT_MPS,
) -> tuple[GeoPoint, GeoPoint]:
    """Simple kinematic burst + wind-drift landing estimate."""
    # Wind from meteorological direction (where wind comes FROM) → velocity TO
    to_rad = math.radians((wind_dir_deg + 180.0) % 360.0)
    ve = wind_speed_mps * math.sin(to_rad)
    vn = wind_speed_mps * math.cos(to_rad)

    cur_lat, cur_lon, cur_alt = lat, lon, alt_m
    t = 0.0

    if vz_mps > 0.2 and cur_alt < burst_alt_m:
        dt = (burst_alt_m - cur_alt) / max(0.1, vz_mps)
        cur_lat, cur_lon = _offset_ll(cur_lat, cur_lon, ve * dt, vn * dt)
        cur_alt = burst_alt_m
        t += dt
    burst = GeoPoint(lat=cur_lat, lon=cur_lon, alt_m=cur_alt, t_mono=t)

    desc = abs(descent_mps) if vz_mps >= 0 else abs(vz_mps)
    desc = max(1.0, desc)
    dt_down = cur_alt / desc
    land_lat, land_lon = _offset_ll(cur_lat, cur_lon, ve * dt_down, vn * dt_down)
    landing = GeoPoint(lat=land_lat, lon=land_lon, alt_m=0.0, t_mono=t + dt_down)
    return burst, landing


class SondeTrackStore:
    def __init__(self) -> None:
        self._tracks: dict[str, SondeTrack] = {}

    def ingest(self, frame: SondeFrame) -> SondeTrack:
        track = self._tracks.get(frame.serial_id)
        if track is None:
            track = SondeTrack(
                serial_id=frame.serial_id,
                kind=frame.kind,
                frequency_hz=frame.frequency_hz,
            )
            self._tracks[frame.serial_id] = track
        track.kind = frame.kind
        track.frequency_hz = frame.frequency_hz
        track.last = frame
        track.trajectory.append(
            GeoPoint(lat=frame.lat, lon=frame.lon, alt_m=frame.alt_m, t_mono=frame.t_mono)
        )
        del track.trajectory[:-500]
        burst_alt = max(DEFAULT_BURST_ALT_M, frame.alt_m + 500.0)
        track.burst, track.landing = predict_burst_landing(
            lat=frame.lat,
            lon=frame.lon,
            alt_m=frame.alt_m,
            vz_mps=frame.vz_mps,
            wind_speed_mps=frame.wind_speed_mps,
            wind_dir_deg=frame.wind_dir_deg,
            burst_alt_m=burst_alt,
        )
        return track

    def get(self, serial_id: str) -> SondeTrack | None:
        return self._tracks.get(serial_id)

    def list_tracks(self) -> list[SondeTrack]:
        return list(self._tracks.values())

    def clear(self) -> None:
        self._tracks.clear()


_SONDE_STORE = SondeTrackStore()


def get_sonde_store() -> SondeTrackStore:
    return _SONDE_STORE


_KV_RE = re.compile(
    r"(?P<k>ID|serial|lat|lon|alt|vz|v|dir|sp|spd|temp|freq|frequency)"
    r"[=:\s]+(?P<v>-?\d+(?:\.\d+)?|[A-Za-z0-9_-]+)",
    re.I,
)


def parse_sonde_text_line(
    line: str, *, default_freq_hz: int = DEFAULT_SONDE_HZ
) -> SondeFrame | None:
    """Parse rs41mod / rs_detect-ish whitespace or key=value telemetry lines."""
    raw = line.strip()
    if not raw:
        return None
    upper = raw.upper()
    if not any(k in upper for k in ("RS41", "M20", "SONDE", "ID=", "LAT=")):
        return None
    kv: dict[str, str] = {}
    for m in _KV_RE.finditer(raw):
        kv[m.group("k").lower()] = m.group("v")
    serial = kv.get("id") or kv.get("serial")
    if not serial:
        m = re.search(r"\b([A-Z]\d{6,8})\b", raw)
        serial = m.group(1) if m else None
    try:
        lat = float(kv["lat"])
        lon = float(kv["lon"])
        alt = float(kv["alt"])
    except (KeyError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    vz = float(kv.get("vz") or kv.get("v") or 0.0)
    wind_dir = float(kv.get("dir") or 0.0)
    wind_sp = float(kv.get("sp") or kv.get("spd") or 0.0)
    temp = float(kv["temp"]) if "temp" in kv else None
    freq = default_freq_hz
    if "freq" in kv or "frequency" in kv:
        fraw = kv.get("freq") or kv.get("frequency") or ""
        try:
            fv = float(fraw)
            freq = int(fv * 1e6) if fv < 1000 else int(fv)
        except ValueError:
            pass
    kind: Literal["RS41", "M20", "unknown"] = (
        "M20" if "M20" in upper else "RS41" if "RS41" in upper else "unknown"
    )
    return SondeFrame(
        serial_id=str(serial),
        lat=lat,
        lon=lon,
        alt_m=alt,
        vz_mps=vz,
        wind_speed_mps=wind_sp,
        wind_dir_deg=wind_dir % 360.0,
        temp_c=temp,
        frequency_hz=freq,
        t_mono=time.monotonic(),
        kind=kind,
    )


# Built-in compact RS41-like payload for offline / correlator path (little-endian).
# Layout: magic(4)=0xCA11B610 | serial(8 ascii) | frame_n u16 | lat f64 | lon f64 |
#         alt f32 | vz f32 | wind_sp f32 | wind_dir f32 | temp f32 | crc16


def build_test_rs41_payload(
    *,
    serial: str,
    lat: float,
    lon: float,
    alt_m: float,
    vz_mps: float = 0.0,
    wind_speed_mps: float = 0.0,
    wind_dir_deg: float = 0.0,
    temp_c: float = 0.0,
    frame_n: int = 0,
) -> bytes:
    ser = (serial.encode("ascii") + b"\x00" * 8)[:8]
    body = struct.pack(
        "<4s8sHddfffff",
        b"\x10\xb6\xca\x11",
        ser,
        int(frame_n) & 0xFFFF,
        float(lat),
        float(lon),
        float(alt_m),
        float(vz_mps),
        float(wind_speed_mps),
        float(wind_dir_deg),
        float(temp_c),
    )
    # body is 50 bytes (4+8+2+8+8+20); crc16 follows
    crc = sum(body) & 0xFFFF
    return body + struct.pack("<H", crc)


_RS41_BODY_LEN = 50


def decode_rs41_payload(
    payload: bytes, *, frequency_hz: int = DEFAULT_SONDE_HZ
) -> SondeFrame | None:
    if len(payload) < _RS41_BODY_LEN + 2:
        return None
    magic, ser_b, frame_n, lat, lon, alt, vz, wsp, wdir, temp = struct.unpack_from(
        "<4s8sHddfffff", payload, 0
    )
    if magic not in (b"\x10\xb6\xca\x11", b"\xef\x49\x35\xee"):
        return None
    crc_stored = struct.unpack_from("<H", payload, _RS41_BODY_LEN)[0]
    if (sum(payload[:_RS41_BODY_LEN]) & 0xFFFF) != crc_stored:
        return None
    serial = ser_b.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    if not serial:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return SondeFrame(
        serial_id=serial,
        lat=float(lat),
        lon=float(lon),
        alt_m=float(alt),
        vz_mps=float(vz),
        wind_speed_mps=float(wsp),
        wind_dir_deg=float(wdir) % 360.0,
        temp_c=float(temp),
        frequency_hz=int(frequency_hz),
        frame_n=int(frame_n),
        t_mono=time.monotonic(),
        kind="RS41",
    )


def correlate_and_decode_bits(
    bits: list[int], *, frequency_hz: int = DEFAULT_SONDE_HZ
) -> SondeFrame | None:
    """Find sync, pack following bits into bytes, attempt payload decode."""
    hit = correlate_sync(bits)
    if hit is None:
        return None
    start = hit.offset + 32
    need = (_RS41_BODY_LEN + 2) * 8
    if len(bits) < start + need:
        return None
    chunk = bits[start : start + need]
    if hit.inverted:
        chunk = [1 - b for b in chunk]
    raw = bytearray()
    for i in range(0, need, 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | chunk[i + j]
        raw.append(byte)
    return decode_rs41_payload(bytes(raw), frequency_hz=frequency_hz)


def _which_sonde_tool() -> str | None:
    for name in ("rs41mod", "rs_detect", "sondemod", "rs41decoder"):
        path = shutil.which(name)
        if path:
            return path
    tools = os.environ.get("PATH", "")
    root = None
    try:
        root = __file__
        tools_bin = (
            __import__("pathlib").Path(root).resolve().parents[3] / "tools" / "bin"
        )
        if tools_bin.is_dir():
            for name in ("rs41mod", "rs_detect", "sondemod"):
                cand = tools_bin / name
                if cand.is_file() and os.access(cand, os.X_OK):
                    return str(cand)
    except Exception:
        pass
    _ = tools
    return None


def radiosonde_pipeline_argv(
    *, frequency_hz: int, lna: int = 32, vga: int = 20
) -> tuple[list[str] | None, list[str]]:
    """Prefer external RS41 decoder; else return None producer for soft-inject mode."""
    hackrf = shutil.which("hackrf_transfer")
    tool = _which_sonde_tool()
    if hackrf and tool:
        producer = [
            hackrf,
            "-r",
            "-",
            "-f",
            str(int(frequency_hz)),
            "-s",
            "480000",
            "-l",
            str(int(lna)),
            "-g",
            str(int(vga)),
            "-a",
            "0",
        ]
        name = tool.rsplit("/", 1)[-1]
        if "rs41" in name.lower():
            consumer = [tool, "-i", "-", "-b", "4800"]
        else:
            consumer = [tool, "-"]
        return producer, consumer
    # Soft mode: no external decoder — session accepts inject / text only
    return None, ["soft-radiosonde"]


@dataclass
class RadiosondeSession:
    """Decoder-like session for 400–406 MHz radiosonde RX."""

    arbiter: HardwareArbiter
    frequency_hz: int = DEFAULT_SONDE_HZ
    kind: Literal["radiosonde"] = "radiosonde"
    handlers: list[LineHandler] = field(default_factory=list)
    _proc: asyncio.subprocess.Process | None = None
    _producer: asyncio.subprocess.Process | None = None
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)
    running: bool = False
    cmd_desc: str = ""
    store: SondeTrackStore = field(default_factory=get_sonde_store)

    def subscribe(self, handler: LineHandler) -> None:
        if handler not in self.handlers:
            self.handlers.append(handler)

    async def _emit(self, payload: dict[str, Any]) -> None:
        for h in list(self.handlers):
            try:
                r = h(payload)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("radiosonde handler failed")

    async def ingest_frame(self, frame: SondeFrame) -> dict[str, Any]:
        track = self.store.ingest(frame)
        payload = frame.to_dict()
        payload["burst"] = track.burst.to_dict() if track.burst else None
        payload["landing"] = track.landing.to_dict() if track.landing else None
        payload["trajectory"] = [p.to_dict() for p in track.trajectory[-50:]]
        await self._emit(payload)
        return payload

    async def start(self) -> str:
        if self.running:
            raise RuntimeError("radiosonde already running")
        lo, hi = BAND_HZ
        freq = int(self.frequency_hz)
        if not (lo <= freq <= hi):
            freq = DEFAULT_SONDE_HZ
            self.frequency_hz = freq

        producer_argv, consumer_argv = radiosonde_pipeline_argv(frequency_hz=freq)
        if producer_argv is None:
            # Soft mode — no SDR subprocess; operator injects frames / waits for tools
            self.cmd_desc = (
                f"soft-radiosonde @ {freq} Hz (install rs41mod/rs_detect for live GFSK)"
            )
            self.running = True
            async with self.arbiter.lock:
                await self.arbiter.refresh_detection()
            return self.cmd_desc

        self.arbiter.assert_sdr_idle(excluding="decoder")
        await self.arbiter.cancel_watchdog()
        async with self.arbiter.lock:
            await self.arbiter.refresh_detection()
            if self.arbiter.state is not HardwareState.HACKRF_NATIVE:
                await self.arbiter._to_native_locked()  # noqa: SLF001
            if self.arbiter.runner.running:
                raise RuntimeError("SDR subprocess already running")
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
            self.cmd_desc = f"{' '.join(producer_argv)} | {' '.join(consumer_argv)}"
            self.arbiter.state = HardwareState.BUSY_RX

        self.running = True
        assert self._proc.stdout and self._proc.stderr
        self._tasks = [
            asyncio.create_task(self._pump(self._proc.stdout)),
            asyncio.create_task(self._pump(self._proc.stderr)),
            asyncio.create_task(self._wait()),
        ]
        return self.cmd_desc

    async def _pump(self, stream: asyncio.StreamReader) -> None:
        while self.running:
            line_b = await stream.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").rstrip()
            frame = parse_sonde_text_line(line, default_freq_hz=self.frequency_hz)
            if frame:
                await self.ingest_frame(frame)

    async def _wait(self) -> None:
        if self._proc is None:
            return
        await self._proc.wait()
        self.running = False

    async def _signal_proc(
        self, proc: asyncio.subprocess.Process | None, sig: signal.Signals
    ) -> None:
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def stop(self) -> None:
        self.running = False
        proc, producer = self._proc, self._producer
        self._proc = self._producer = None
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
