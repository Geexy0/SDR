"""Auto Voice Recon — scan public speakable bands, dwell while talking, AGC for range."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from pp_dashboard.config import settings
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.recon.audio_hub import AUDIO_SAMPLE_RATE, AudioHub
from pp_dashboard.recon.ctcss import detect_ctcss_hz
from pp_dashboard.recon.sweep_parser import SweepBinaryParser, SweepRecord
from pp_dashboard.recon.voice_bands import VoiceBand, resolve_bands
from pp_dashboard.recon.voice_recordings import VoiceRecordingStore, VoxSpooler

log = logging.getLogger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]
Phase = Literal["idle", "scanning", "dwelling", "agc_adjust", "stopped"]

LNA_STEPS = (0, 8, 16, 24, 32, 40)
VGA_STEPS = tuple(range(0, 63, 2))


def _snap_lna(db: int) -> int:
    return min(LNA_STEPS, key=lambda s: abs(s - db))


def _snap_vga(db: int) -> int:
    return min(VGA_STEPS, key=lambda s: abs(s - int(db)))


def _iq_rms_dbfs(chunk: bytes) -> float:
    """8-bit interleaved signed IQ → approximate RMS dBFS."""
    if len(chunk) < 4:
        return -120.0
    n = (len(chunk) // 2) * 2
    arr = np.frombuffer(chunk[:n], dtype=np.int8).astype(np.float32)
    i = arr[0::2]
    q = arr[1::2]
    power = float(np.mean(i * i + q * q))
    if power <= 1e-12:
        return -120.0
    # Full-scale peak ~ 127^2 * 2
    return 10.0 * math.log10(power / (127.0 * 127.0 * 2.0))


def _demod_iq(
    chunk: bytes, *, mode: str, sample_rate: int, audio_hz: int = 48_000
) -> tuple[bytes, np.ndarray | None]:
    """
    Lightweight NBFM/AM demod.

    Returns (pcm_s16le@audio_hz, discriminator_float@audio_hz).
    Discriminator is pre-normalization / pre-speech-HPF so CTCSS PL remains.
    """
    n = (len(chunk) // 2) * 2
    if n < 64:
        return b"", None
    arr = np.frombuffer(chunk[:n], dtype=np.int8).astype(np.float32) / 128.0
    i = arr[0::2]
    q = arr[1::2]
    if mode == "am":
        audio = np.sqrt(i * i + q * q)
        audio = audio - float(np.mean(audio))
    else:
        phase = np.arctan2(q, i)
        audio = np.diff(np.unwrap(phase), prepend=phase[0])
    decim = max(int(sample_rate // audio_hz), 1)
    if decim > 1:
        usable = (audio.size // decim) * decim
        if usable <= 0:
            return b"", None
        # Stride for CTCSS (preserve tone phase); mean for speech envelope stability
        disc = audio[:usable:decim].astype(np.float64, copy=True)
        audio = audio[:usable].reshape(-1, decim).mean(axis=1)
    else:
        disc = audio.astype(np.float64, copy=True)
    # Soft high-pass for speaker — keep disc intact for CTCSS
    if mode != "am" and audio.size > 16:
        # Moving-average subtract ≈ HPF (~300 Hz-ish at 48 kHz)
        win = min(64, audio.size // 4)
        kernel = np.ones(win, dtype=np.float32) / float(win)
        slow = np.convolve(audio, kernel, mode="same")
        audio = audio - slow
    peak = float(np.max(np.abs(audio))) + 1e-9
    audio = np.clip(audio / peak * 0.7, -1.0, 1.0)
    pcm = (audio * 32767.0).astype(np.int16).tobytes()
    return pcm, disc


def _demod_iq_to_pcm16(
    chunk: bytes, *, mode: str, sample_rate: int, audio_hz: int = 48_000
) -> bytes:
    pcm, _ = _demod_iq(chunk, mode=mode, sample_rate=sample_rate, audio_hz=audio_hz)
    return pcm


@dataclass
class AutoVoiceReconSession:
    arbiter: HardwareArbiter
    band_ids: list[str] | None = None
    detect_margin_db: float = 12.0
    min_detect_db: float = -75.0
    quiet_hold_sec: float = 2.5
    max_dwell_sec: float = 90.0
    scan_settle_sec: float = 1.2
    start_lna: int = 24
    start_vga: int = 20
    amp: int = 0  # always off
    audio_enabled: bool = True  # browser WS always; this gates optional local aplay
    local_aplay: bool = False
    squelch_db: float = -50.0
    vox_open_hold_sec: float = 0.5

    handlers: list[EventHandler] = field(default_factory=list)
    running: bool = False
    phase: Phase = "idle"
    current_band: str | None = None
    listen_hz: int | None = None
    listen_rssi_db: float | None = None
    ctcss_hz: float | None = None
    lna: int = 24
    vga: int = 20
    hits: list[dict[str, Any]] = field(default_factory=list)

    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _proc: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _audio_proc: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _bands: list[VoiceBand] = field(default_factory=list, init=False, repr=False)
    _empty_cycles: int = field(default=0, init=False, repr=False)
    _hub: AudioHub = field(default_factory=AudioHub.get, init=False, repr=False)
    _recordings: VoiceRecordingStore | None = field(default=None, init=False, repr=False)
    _vox: VoxSpooler | None = field(default=None, init=False, repr=False)
    _disc_buf: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.float64), init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.lna = _snap_lna(self.start_lna)
        self.vga = _snap_vga(self.start_vga)
        self.amp = 0
        self._bands = resolve_bands(self.band_ids)
        # local_aplay defaults from audio_enabled for backward compat when unset path used
        if self.local_aplay is False and self.audio_enabled is True:
            # Prefer browser; aplay only when explicitly requested via local_aplay
            pass
        voice_dir = settings.captures_dir / "voice"
        db = settings.captures_dir / "voice_recordings.sqlite3"
        self._recordings = VoiceRecordingStore(db, voice_dir)
        self._vox = VoxSpooler(
            self._recordings,
            sample_rate=AUDIO_SAMPLE_RATE,
            open_hold_sec=self.vox_open_hold_sec,
        )

    def subscribe(self, handler: EventHandler) -> None:
        if handler not in self.handlers:
            self.handlers.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        self.handlers = [h for h in self.handlers if h is not handler]

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "phase": self.phase,
            "band": self.current_band,
            "listen_hz": self.listen_hz,
            "listen_rssi_db": self.listen_rssi_db,
            "ctcss_hz": self.ctcss_hz,
            "squelch_db": self.squelch_db,
            "local_aplay": self.local_aplay,
            "vox_recording": bool(self._vox and self._vox.recording),
            "lna": self.lna,
            "vga": self.vga,
            "amp": 0,
            "audio_hz": AUDIO_SAMPLE_RATE,
            "bands": [
                {
                    "id": b.id,
                    "name": b.name,
                    "start_mhz": b.start_mhz,
                    "stop_mhz": b.stop_mhz,
                    "mode": b.mode,
                }
                for b in self._bands
            ],
            "recent_hits": self.hits[-20:],
        }

    async def _emit(self, event: dict[str, Any]) -> None:
        event = {**event, "ts": time.time(), **self.status()}
        for h in list(self.handlers):
            try:
                r = h(event)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("voice recon handler failed")

    def _mux(self) -> Any | None:
        mux = getattr(self.arbiter, "multiplexer", None)
        if mux is not None and getattr(mux, "running", False):
            return mux
        return None

    async def start(self) -> dict[str, Any]:
        if self.running:
            raise RuntimeError("auto voice recon already running")
        mux = self._mux()
        await self.arbiter.cancel_watchdog()
        if mux is None:
            async with self.arbiter.lock:
                await self.arbiter.refresh_detection()
                if self.arbiter.state is not HardwareState.HACKRF_NATIVE:
                    await self.arbiter._to_native_locked()  # noqa: SLF001
                if self.arbiter.runner.running:
                    raise RuntimeError("SDR subprocess already running")
                if (
                    self.arbiter.state in {HardwareState.BUSY_RX, HardwareState.BUSY_TX}
                    and not self.arbiter.runner.running
                ):
                    self.arbiter.state = HardwareState.HACKRF_NATIVE
                self.arbiter.state = HardwareState.BUSY_RX
        # else: share MULTIPLEX_RX — do not steal the USB bus

        self.running = True
        self.phase = "scanning"
        self._empty_cycles = 0
        self._task = asyncio.create_task(self._run_loop(), name="auto_voice_recon")
        await self._emit(
            {
                "type": "voice_recon",
                "event": "started",
                "multiplex": mux is not None,
            }
        )
        return self.status()

    async def stop(self) -> dict[str, Any]:
        self.running = False
        self.phase = "stopped"
        await self._kill_proc()
        await self._kill_audio()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._mux() is None:
            async with self.arbiter.lock:
                if self.arbiter.state is HardwareState.BUSY_RX:
                    self.arbiter.state = HardwareState.HACKRF_NATIVE
        await self._emit({"type": "voice_recon", "event": "stopped"})
        self.phase = "idle"
        self.current_band = None
        self.listen_hz = None
        return self.status()

    async def _run_loop(self) -> None:
        try:
            while self.running:
                cycle_hit = False
                for band in self._bands:
                    if not self.running:
                        break
                    hit = await self._scan_band(band)
                    if hit is not None:
                        cycle_hit = True
                        self._empty_cycles = 0
                        await self._dwell(band, hit["frequency_hz"], hit["rssi_db"])
                if not self.running:
                    break
                if not cycle_hit:
                    self._empty_cycles += 1
                    await self._agc_boost_for_range()
                else:
                    # Mild boost if we heard something but want more range next pass.
                    if self._empty_cycles == 0 and self.lna < 32:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("auto voice recon crashed")
            await self._emit({"type": "voice_recon", "event": "error"})
        finally:
            await self._kill_proc()
            await self._kill_audio()
            self.running = False
            if self._mux() is None:
                async with self.arbiter.lock:
                    if (
                        self.arbiter.state is HardwareState.BUSY_RX
                        and not self.arbiter.runner.running
                    ):
                        self.arbiter.state = HardwareState.HACKRF_NATIVE

    async def _agc_boost_for_range(self) -> None:
        """No activity this cycle — raise sensitivity (AMP stays off)."""
        self.phase = "agc_adjust"
        before = (self.lna, self.vga)
        if self.lna < 40:
            idx = LNA_STEPS.index(_snap_lna(self.lna))
            self.lna = LNA_STEPS[min(idx + 1, len(LNA_STEPS) - 1)]
        elif self.vga < 50:
            self.vga = _snap_vga(self.vga + 6)
        await self._emit(
            {
                "type": "voice_recon",
                "event": "agc_boost",
                "from_lna": before[0],
                "from_vga": before[1],
                "empty_cycles": self._empty_cycles,
            }
        )
        await asyncio.sleep(0.15)

    async def _agc_during_dwell(self, rssi_db: float) -> None:
        """Keep voice in a usable window without clipping."""
        if rssi_db > -18.0:
            if self.vga >= 4:
                self.vga = _snap_vga(self.vga - 4)
            elif self.lna > 0:
                idx = LNA_STEPS.index(_snap_lna(self.lna))
                self.lna = LNA_STEPS[max(idx - 1, 0)]
            await self._emit({"type": "voice_recon", "event": "agc_cut", "rssi_db": rssi_db})
        elif rssi_db < -55.0:
            if self.lna < 40:
                idx = LNA_STEPS.index(_snap_lna(self.lna))
                self.lna = LNA_STEPS[min(idx + 1, len(LNA_STEPS) - 1)]
            elif self.vga < 50:
                self.vga = _snap_vga(self.vga + 4)
            await self._emit({"type": "voice_recon", "event": "agc_raise", "rssi_db": rssi_db})

    async def _scan_band(self, band: VoiceBand) -> dict[str, Any] | None:
        self.phase = "scanning"
        self.current_band = band.id
        self.listen_hz = None
        await self._emit({"type": "voice_recon", "event": "scan_band", "band": band.id})

        mux = self._mux()
        if mux is not None:
            return await self._scan_band_via_mux(band, mux)

        argv = self._sweep_argv(band)
        records = await self._run_sweep_collect(argv, settle_sec=self.scan_settle_sec)
        if not records:
            return None

        powers: list[float] = []
        peaks: list[tuple[int, float]] = []
        for rec in records:
            step = (rec.hi_hz - rec.lo_hz) / max(rec.power_db.size, 1)
            for i, p in enumerate(rec.power_db):
                f = int(rec.lo_hz + i * step)
                db = float(p)
                powers.append(db)
                peaks.append((f, db))

        if not powers:
            return None
        noise = float(np.percentile(np.asarray(powers, dtype=np.float64), 40))
        threshold = max(self.min_detect_db, noise + self.detect_margin_db)
        candidates = [(f, db) for f, db in peaks if db >= threshold]
        if not candidates:
            return None
        # Strongest peak in-band
        freq_hz, rssi = max(candidates, key=lambda t: t[1])
        # Snap toward channel center for NBFM-ish spacings
        spacing = band.bin_width_hz
        freq_hz = int(round(freq_hz / spacing) * spacing)
        hit = {
            "frequency_hz": freq_hz,
            "rssi_db": rssi,
            "noise_db": noise,
            "threshold_db": threshold,
            "band": band.id,
        }
        self.hits.append(hit)
        del self.hits[:-100]
        await self._emit({"type": "voice_recon", "event": "detected", **hit})
        return hit

    async def _scan_band_via_mux(self, band: VoiceBand, mux: Any) -> dict[str, Any] | None:
        """Use live 10 MHz FFT from IQMultiplexer — no second USB claim."""
        from pp_dashboard.hardware.iq_multiplexer import FFT_SIZE

        band_lo = int(band.start_mhz * 1e6)
        band_hi = int(band.stop_mhz * 1e6)
        if band_hi < mux.start_hz or band_lo > mux.stop_hz:
            await self._emit(
                {
                    "type": "voice_recon",
                    "event": "scan_skip",
                    "band": band.id,
                    "reason": "outside_multiplex_window",
                }
            )
            return None
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2)

        async def _on_row(payload: dict[str, Any]) -> None:
            if payload.get("type") == "row":
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass

        mux.subscribe(_on_row)
        try:
            row = await asyncio.wait_for(q.get(), timeout=1.5)
        except TimeoutError:
            return None
        finally:
            mux.unsubscribe(_on_row)

        data = row.get("data_u8")
        if not isinstance(data, (bytes, bytearray)) or len(data) < 8:
            return None
        u8 = np.frombuffer(data, dtype=np.uint8)
        db_min = float(row.get("db_min", -110))
        db_max = float(row.get("db_max", -20))
        powers = db_min + (u8.astype(np.float64) / 255.0) * (db_max - db_min)
        start_hz = int(row.get("start_hz", mux.start_hz))
        stop_hz = int(row.get("stop_hz", mux.stop_hz))
        n = int(row.get("n", powers.size))
        bin_hz = (stop_hz - start_hz) / max(n, 1)
        peaks: list[tuple[int, float]] = []
        for i, db in enumerate(powers):
            f = int(start_hz + i * bin_hz)
            if band_lo <= f <= band_hi:
                peaks.append((f, float(db)))
        if not peaks:
            return None
        noise = float(np.percentile(np.asarray([p for _, p in peaks], dtype=np.float64), 40))
        threshold = max(self.min_detect_db, noise + self.detect_margin_db)
        candidates = [(f, db) for f, db in peaks if db >= threshold]
        if not candidates:
            return None
        freq_hz, rssi = max(candidates, key=lambda t: t[1])
        spacing = band.bin_width_hz
        freq_hz = int(round(freq_hz / spacing) * spacing)
        hit = {
            "frequency_hz": freq_hz,
            "rssi_db": rssi,
            "noise_db": noise,
            "threshold_db": threshold,
            "band": band.id,
            "via": "iq_multiplexer",
        }
        _ = FFT_SIZE
        self.hits.append(hit)
        del self.hits[:-100]
        await self._emit({"type": "voice_recon", "event": "detected", **hit})
        return hit

    def _sweep_argv(self, band: VoiceBand) -> list[str]:
        sweep = [
            "hackrf_sweep",
            "-f",
            f"{band.start_mhz:g}:{band.stop_mhz:g}",
            "-w",
            str(band.bin_width_hz),
            "-l",
            str(self.lna),
            "-g",
            str(self.vga),
            "-a",
            "0",
            "-B",
        ]
        if Path("/usr/bin/stdbuf").is_file():
            return ["stdbuf", "-o0", "-e0", *sweep]
        return sweep

    async def _run_sweep_collect(
        self, argv: list[str], *, settle_sec: float
    ) -> list[SweepRecord]:
        await self._kill_proc()
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        parser = SweepBinaryParser()
        collected: list[SweepRecord] = []
        assert self._proc.stdout and self._proc.stderr
        stderr_task = asyncio.create_task(self._drain(self._proc.stderr))
        deadline = time.monotonic() + settle_sec
        try:
            while self.running and time.monotonic() < deadline:
                try:
                    chunk = await asyncio.wait_for(self._proc.stdout.read(65536), timeout=0.4)
                except TimeoutError:
                    continue
                if not chunk:
                    break
                for rec in parser.feed(chunk):
                    collected.append(rec)
        finally:
            await self._kill_proc()
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
        return collected

    async def _dwell(self, band: VoiceBand, freq_hz: int, scan_rssi: float) -> None:
        self.phase = "dwelling"
        self.current_band = band.id
        self.listen_hz = freq_hz
        self.listen_rssi_db = scan_rssi
        await self._emit(
            {
                "type": "voice_recon",
                "event": "dwell_start",
                "frequency_hz": freq_hz,
                "rssi_db": scan_rssi,
                "mode": band.mode,
            }
        )

        mux = self._mux()
        iq_rate = 2_000_000
        stderr_task: asyncio.Task[None] | None = None
        fifo_fd: int | None = None
        await self._kill_proc()
        if mux is not None:
            from pp_dashboard.hardware.iq_multiplexer import VOICE_FIFO_PATH, VOICE_OUT_RATE

            mux.set_voice_tune(int(freq_hz))
            iq_rate = int(VOICE_OUT_RATE)
            # Open FIFO for read (non-blocking); mux writer is O_NONBLOCK too.
            for _ in range(20):
                try:
                    fifo_fd = os.open(VOICE_FIFO_PATH, os.O_RDONLY | os.O_NONBLOCK)
                    break
                except OSError:
                    await asyncio.sleep(0.05)
            if fifo_fd is None:
                log.warning("voice FIFO open failed — falling back to dedicated RX")
                mux = None
        if mux is None:
            argv = [
                "hackrf_transfer",
                "-r",
                "-",
                "-f",
                str(int(freq_hz)),
                "-s",
                "2000000",
                "-l",
                str(self.lna),
                "-g",
                str(self.vga),
                "-a",
                "0",
            ]
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            assert self._proc.stdout and self._proc.stderr
            stderr_task = asyncio.create_task(self._drain(self._proc.stderr))
        if self.local_aplay:
            await self._start_audio_monitor(band.mode)

        self.ctcss_hz = None
        self._disc_buf = np.zeros(0, dtype=np.float64)
        if self._vox:
            self._vox.configure(band_name=band.id, freq_hz=freq_hz)
        await self._hub.set_session(
            active=True, listen_hz=freq_hz, band=band.id, rssi_db=scan_rssi, ctcss_hz=None
        )

        quiet_since: float | None = None
        started = time.monotonic()
        last_agc = 0.0
        last_tick = 0.0
        last_ctcss = 0.0
        rx_lna, rx_vga = self.lna, self.vga
        try:
            while self.running:
                if time.monotonic() - started > self.max_dwell_sec:
                    await self._emit(
                        {
                            "type": "voice_recon",
                            "event": "dwell_timeout",
                            "frequency_hz": freq_hz,
                        }
                    )
                    break
                chunk = b""
                if fifo_fd is not None:
                    try:
                        chunk = await asyncio.to_thread(os.read, fifo_fd, 65536)
                    except BlockingIOError:
                        chunk = b""
                        await asyncio.sleep(0.02)
                    except OSError:
                        chunk = b""
                        await asyncio.sleep(0.05)
                else:
                    assert self._proc and self._proc.stdout
                    try:
                        chunk = await asyncio.wait_for(self._proc.stdout.read(65536), timeout=0.5)
                    except TimeoutError:
                        chunk = b""
                    if not chunk and self._proc.returncode is not None:
                        break
                if not chunk:
                    continue
                rssi = _iq_rms_dbfs(chunk)
                self.listen_rssi_db = rssi
                talking = rssi > self.squelch_db
                now = time.monotonic()
                pcm, disc = _demod_iq(chunk, mode=band.mode, sample_rate=iq_rate)
                if pcm:
                    await self._hub.publish_pcm(pcm)
                    if self._audio_proc and self._audio_proc.stdin:
                        try:
                            self._audio_proc.stdin.write(pcm)
                            await self._audio_proc.stdin.drain()
                        except Exception:
                            await self._kill_audio()
                if disc is not None and disc.size and band.mode != "am":
                    self._disc_buf = np.concatenate([self._disc_buf, disc])[-12_000:]
                    if now - last_ctcss >= 0.75 and self._disc_buf.size >= 2048:
                        last_ctcss = now
                        found = detect_ctcss_hz(self._disc_buf, sample_rate=float(AUDIO_SAMPLE_RATE))
                        if found != self.ctcss_hz:
                            self.ctcss_hz = found
                            if self._vox:
                                self._vox.note_ctcss(found)
                            await self._hub.set_session(
                                active=True,
                                listen_hz=freq_hz,
                                band=band.id,
                                rssi_db=rssi,
                                ctcss_hz=found,
                            )
                            await self._emit(
                                {
                                    "type": "voice_recon",
                                    "event": "ctcss",
                                    "frequency_hz": freq_hz,
                                    "ctcss_hz": found,
                                }
                            )
                if self._vox:
                    sealed = self._vox.feed(
                        rssi_db=rssi, squelch_db=self.squelch_db, pcm=pcm or b"", now=now
                    )
                    if sealed is not None:
                        await self._emit(
                            {
                                "type": "voice_recon",
                                "event": "vox_saved",
                                "recording": sealed,
                            }
                        )
                if talking:
                    quiet_since = None
                    if now - last_agc > 1.5 and fifo_fd is None:
                        prev = (self.lna, self.vga)
                        await self._agc_during_dwell(rssi)
                        if (self.lna, self.vga) != prev:
                            await self._restart_dwell_rx(freq_hz)
                            rx_lna, rx_vga = self.lna, self.vga
                            assert self._proc and self._proc.stdout
                        last_agc = now
                    _ = (rx_lna, rx_vga)
                else:
                    if quiet_since is None:
                        quiet_since = now
                    elif now - quiet_since >= self.quiet_hold_sec:
                        await self._emit(
                            {
                                "type": "voice_recon",
                                "event": "dwell_end",
                                "frequency_hz": freq_hz,
                                "reason": "quiet",
                                "rssi_db": rssi,
                                "ctcss_hz": self.ctcss_hz,
                            }
                        )
                        break
                if now - last_tick >= 0.5:
                    last_tick = now
                    await self._hub.set_session(
                        active=True,
                        listen_hz=freq_hz,
                        band=band.id,
                        rssi_db=rssi,
                        ctcss_hz=self.ctcss_hz,
                    )
                    await self._emit(
                        {
                            "type": "voice_recon",
                            "event": "dwell_tick",
                            "frequency_hz": freq_hz,
                            "rssi_db": rssi,
                            "talking": talking,
                            "ctcss_hz": self.ctcss_hz,
                            "vox_recording": bool(self._vox and self._vox.recording),
                        }
                    )
        finally:
            if self._vox:
                sealed = self._vox.force_finalize()
                if sealed is not None:
                    await self._emit(
                        {
                            "type": "voice_recon",
                            "event": "vox_saved",
                            "recording": sealed,
                        }
                    )
            await self._hub.set_session(active=False, ctcss_hz=None)
            if stderr_task is not None:
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
            if fifo_fd is not None:
                try:
                    os.close(fifo_fd)
                except OSError:
                    pass
            await self._kill_audio()
            await self._kill_proc()
            self.listen_hz = None
            self.ctcss_hz = None
            self.phase = "scanning"

    async def _restart_dwell_rx(self, freq_hz: int) -> None:
        await self._kill_proc()
        argv = [
            "hackrf_transfer",
            "-r",
            "-",
            "-f",
            str(int(freq_hz)),
            "-s",
            "2000000",
            "-l",
            str(self.lna),
            "-g",
            str(self.vga),
            "-a",
            "0",
        ]
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    async def _start_audio_monitor(self, mode: str) -> None:
        """Optional local aplay sink (browser WS is primary)."""
        _ = mode
        await self._kill_audio()
        if not self.local_aplay:
            return
        aplay = shutil.which("aplay")
        if not aplay:
            log.warning("local_aplay requested but aplay not on PATH")
            return
        self._audio_proc = await asyncio.create_subprocess_exec(
            aplay,
            "-q",
            "-f",
            "S16_LE",
            "-c",
            "1",
            "-r",
            "48000",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _kill_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if not proc or proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.send_signal(signal.SIGINT)
            except ProcessLookupError:
                return
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
            await proc.wait()

    async def _kill_audio(self) -> None:
        proc = self._audio_proc
        self._audio_proc = None
        if not proc or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    @staticmethod
    async def _drain(stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
