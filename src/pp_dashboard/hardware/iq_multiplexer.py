"""Wideband IQ multiplexer — one hackrf_transfer @ 10 MSPS → FFT + DDC taps."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.recon.sweep_parser import powers_to_uint8

if TYPE_CHECKING:
    from pp_dashboard.hardware.arbiter import HardwareArbiter

log = logging.getLogger(__name__)

RowHandler = Callable[[dict[str, Any]], Awaitable[None] | None]

DEFAULT_CENTER_HZ = 435_000_000
DEFAULT_SAMPLE_RATE = 10_000_000
FFT_SIZE = 2048
SUBGHZ_FIFO_PATH = Path("/tmp/pp_subghz.cs8")
VOICE_FIFO_PATH = Path("/tmp/pp_voice.cs8")
SUBGHZ_OUT_RATE = 2_000_000
VOICE_OUT_RATE = 250_000
TARGET_FPS = 30.0


def cs8_to_complex(raw: bytes | bytearray | memoryview) -> np.ndarray:
    iq8 = np.frombuffer(raw, dtype=np.int8)
    if iq8.size % 2:
        iq8 = iq8[:-1]
    i = iq8[0::2].astype(np.float32) / 128.0
    q = iq8[1::2].astype(np.float32) / 128.0
    return (i + 1j * q).astype(np.complex64)


def complex_to_cs8(z: np.ndarray) -> bytes:
    z = np.asarray(z, dtype=np.complex64).ravel()
    i = np.clip(np.rint(z.real * 127.0), -128, 127).astype(np.int8)
    q = np.clip(np.rint(z.imag * 127.0), -128, 127).astype(np.int8)
    out = np.empty(i.size * 2, dtype=np.int8)
    out[0::2] = i
    out[1::2] = q
    return out.tobytes()


def make_ddc_taps(*, decimation: int, taps: int = 63) -> np.ndarray:
    """Hann-windowed low-pass FIR for post-mix anti-alias (cutoff ~0.4/dec)."""
    if decimation < 2:
        raise ValueError("decimation must be >= 2")
    if taps < 7 or taps % 2 == 0:
        raise ValueError("taps must be odd and >= 7")
    cutoff = 0.4 / float(decimation)
    n = np.arange(taps, dtype=np.float64) - (taps - 1) / 2.0
    h = np.empty(taps, dtype=np.float64)
    zero = n == 0
    h[zero] = 2 * cutoff
    n_nz = n[~zero]
    h[~zero] = np.sin(2 * math.pi * cutoff * n_nz) / (math.pi * n_nz)
    h *= np.hanning(taps)
    h /= np.sum(h)
    return h.astype(np.float32)


def ddc_shift_decimate(
    iq: np.ndarray,
    *,
    shift_hz: float,
    fs_in: float,
    fs_out: float,
    taps: np.ndarray | None = None,
) -> np.ndarray:
    """Mix by -shift_hz, FIR low-pass, decimate to fs_out."""
    z = np.asarray(iq, dtype=np.complex64).ravel()
    if z.size == 0:
        return z
    ratio = fs_in / fs_out
    if abs(ratio - round(ratio)) > 1e-6:
        raise ValueError(f"fs_in/fs_out must be integer (got {ratio})")
    decim = int(round(ratio))
    if decim < 1:
        raise ValueError("invalid decimation")
    if taps is None:
        taps = make_ddc_taps(decimation=max(decim, 2))
    n = z.size
    t = np.arange(n, dtype=np.float64) / fs_in
    mixed = z * np.exp(-2j * math.pi * float(shift_hz) * t).astype(np.complex64)
    filt = np.convolve(mixed, taps.astype(np.complex64), mode="valid")
    return filt[::decim].astype(np.complex64)


def fft_power_row_db(iq: np.ndarray, *, fft_size: int = FFT_SIZE) -> np.ndarray:
    """Windowed FFT → fftshifted power spectral density in dBFS."""
    z = np.asarray(iq, dtype=np.complex64).ravel()
    if z.size < fft_size:
        pad = np.zeros(fft_size, dtype=np.complex64)
        pad[: z.size] = z
        z = pad
    else:
        z = z[:fft_size]
    window = np.hanning(fft_size).astype(np.float32)
    spec = np.fft.fftshift(np.fft.fft(z * window, n=fft_size))
    power = (np.abs(spec) ** 2).astype(np.float32)
    power = np.maximum(power, 1e-20)
    return (10.0 * np.log10(power)).astype(np.float32)


def ensure_fifo(path: Path) -> None:
    path = Path(path)
    if path.exists():
        if not path.is_fifo():
            path.unlink()
            os.mkfifo(path, 0o666)
    else:
        os.mkfifo(path, 0o666)


@dataclass
class DdcChannel:
    name: str
    fifo_path: Path
    tune_hz: int
    out_rate: int
    taps: np.ndarray
    fd: int | None = None
    enabled: bool = True


@dataclass
class _ChunkResult:
    row: dict[str, Any] | None
    fifo_blobs: list[tuple[str, bytes]]


@dataclass
class IQMultiplexer:
    """Single wideband RX → waterfall FFT + named-FIFO DDC consumers."""

    arbiter: HardwareArbiter
    center_hz: int = DEFAULT_CENTER_HZ
    sample_rate: int = DEFAULT_SAMPLE_RATE
    lna: int = 24
    vga: int = 20
    amp: int = 0
    fft_size: int = FFT_SIZE
    target_fps: float = TARGET_FPS
    db_min: float = -110.0
    db_max: float = -20.0
    subghz_tune_hz: int | None = None
    voice_tune_hz: int | None = None
    handlers: list[RowHandler] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.running = False
        self._proc: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._last_fft = 0.0
        self._channels: dict[str, DdcChannel] = {}

    @property
    def start_hz(self) -> int:
        return int(self.center_hz - self.sample_rate // 2)

    @property
    def stop_hz(self) -> int:
        return int(self.center_hz + self.sample_rate // 2)

    @property
    def bin_hz(self) -> float:
        return float(self.sample_rate) / float(self.fft_size)

    def subscribe(self, handler: RowHandler) -> None:
        if handler not in self.handlers:
            self.handlers.append(handler)

    def unsubscribe(self, handler: RowHandler) -> None:
        self.handlers = [h for h in self.handlers if h is not handler]

    def set_subghz_tune(self, freq_hz: int | None) -> None:
        self.subghz_tune_hz = int(freq_hz) if freq_hz is not None else None
        ch = self._channels.get("subghz")
        if ch and freq_hz is not None:
            ch.tune_hz = int(freq_hz)
            ch.enabled = True

    def set_voice_tune(self, freq_hz: int | None) -> None:
        self.voice_tune_hz = int(freq_hz) if freq_hz is not None else None
        ch = self._channels.get("voice")
        if ch and freq_hz is not None:
            ch.tune_hz = int(freq_hz)
            ch.enabled = True

    def build_argv(self) -> list[str]:
        return [
            "hackrf_transfer",
            "-r",
            "-",
            "-f",
            str(int(self.center_hz)),
            "-s",
            str(int(self.sample_rate)),
            "-l",
            str(int(self.lna)),
            "-g",
            str(int(self.vga)),
            "-a",
            str(int(self.amp)),
        ]

    async def start(self) -> list[str]:
        if self.running:
            raise RuntimeError("IQ multiplexer already running")
        self.sample_rate = DEFAULT_SAMPLE_RATE
        await self.arbiter.cancel_watchdog()
        async with self.arbiter.lock:
            await self.arbiter.refresh_detection()
            if (
                self.arbiter.state
                in {
                    HardwareState.BUSY_RX,
                    HardwareState.BUSY_TX,
                    HardwareState.MULTIPLEX_RX,
                }
                and not self.arbiter.runner.running
                and getattr(self.arbiter, "multiplexer", None) is None
            ):
                self.arbiter.state = HardwareState.HACKRF_NATIVE
            if self.arbiter.state is not HardwareState.HACKRF_NATIVE:
                await self.arbiter._to_native_locked()  # noqa: SLF001
            if self.arbiter.runner.running:
                raise RuntimeError("SDR subprocess already running")
            if getattr(self.arbiter, "multiplexer", None) not in (None, self):
                raise RuntimeError("another IQ multiplexer is active")

            ensure_fifo(SUBGHZ_FIFO_PATH)
            ensure_fifo(VOICE_FIFO_PATH)
            self._init_channels()

            argv = self.build_argv()
            log.info("Starting IQ multiplexer: %s", " ".join(argv))
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            self.arbiter.state = HardwareState.MULTIPLEX_RX
            self.arbiter._idle_after_busy = HardwareState.HACKRF_NATIVE  # noqa: SLF001
            self.arbiter.multiplexer = self

        self.running = True
        assert self._proc.stdout
        self._tasks = [
            asyncio.create_task(self._pump(self._proc.stdout), name="iqmux-pump"),
            asyncio.create_task(self._drain_stderr(), name="iqmux-stderr"),
            asyncio.create_task(self._wait_proc(), name="iqmux-wait"),
        ]
        return argv

    def _init_channels(self) -> None:
        sub_dec = int(self.sample_rate // SUBGHZ_OUT_RATE)
        voice_dec = int(self.sample_rate // VOICE_OUT_RATE)
        sub_tune = self.subghz_tune_hz or self.center_hz
        voice_tune = self.voice_tune_hz or self.center_hz
        self._channels = {
            "subghz": DdcChannel(
                name="subghz",
                fifo_path=SUBGHZ_FIFO_PATH,
                tune_hz=sub_tune,
                out_rate=SUBGHZ_OUT_RATE,
                taps=make_ddc_taps(decimation=sub_dec),
                fd=self._open_fifo_write(SUBGHZ_FIFO_PATH),
            ),
            "voice": DdcChannel(
                name="voice",
                fifo_path=VOICE_FIFO_PATH,
                tune_hz=voice_tune,
                out_rate=VOICE_OUT_RATE,
                taps=make_ddc_taps(decimation=voice_dec),
                fd=self._open_fifo_write(VOICE_FIFO_PATH),
            ),
        }

    @staticmethod
    def _open_fifo_write(path: Path) -> int | None:
        try:
            return os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            log.debug("FIFO %s not opened yet (%s)", path, exc)
            return None

    def _reopen_channel(self, ch: DdcChannel) -> None:
        if ch.fd is not None:
            try:
                os.close(ch.fd)
            except OSError:
                pass
            ch.fd = None
        ch.fd = self._open_fifo_write(ch.fifo_path)

    async def stop(self, *, reason: str = "stop") -> None:
        self.running = False
        proc = self._proc
        self._proc = None
        if proc and proc.returncode is None:
            try:
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
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    proc.kill()
                await proc.wait()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for ch in self._channels.values():
            if ch.fd is not None:
                try:
                    os.close(ch.fd)
                except OSError:
                    pass
                ch.fd = None
        self._channels.clear()
        async with self.arbiter.lock:
            if getattr(self.arbiter, "multiplexer", None) is self:
                self.arbiter.multiplexer = None
            if self.arbiter.state is HardwareState.MULTIPLEX_RX:
                self.arbiter.state = HardwareState.HACKRF_NATIVE
        log.info("IQ multiplexer stopped (%s)", reason)

    async def _wait_proc(self) -> None:
        assert self._proc
        await self._proc.wait()
        self.running = False

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if not proc or not proc.stderr:
            return
        stderr = proc.stderr
        try:
            while True:
                chunk = await stderr.read(4096)
                if not chunk:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("iqmux stderr drain failed")

    async def _pump(self, stream: asyncio.StreamReader) -> None:
        chunk_bytes = max(int(self.sample_rate * 0.05 * 2), self.fft_size * 2)
        try:
            while self.running:
                raw = await stream.read(chunk_bytes)
                if not raw:
                    break
                if len(raw) < 4:
                    continue
                result = await asyncio.to_thread(self._compute_chunk, raw)
                if result.row is not None:
                    await self._emit_row(result.row)
                for name, blob in result.fifo_blobs:
                    ch = self._channels.get(name)
                    if ch:
                        self._write_fifo(ch, blob)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("iqmux pump failed")
        finally:
            self.running = False

    def _compute_chunk(self, raw: bytes) -> _ChunkResult:
        iq = cs8_to_complex(raw)
        row: dict[str, Any] | None = None
        now = time.monotonic()
        min_interval = 1.0 / max(self.target_fps, 1.0)
        if now - self._last_fft >= min_interval:
            self._last_fft = now
            powers = fft_power_row_db(iq[-self.fft_size :], fft_size=self.fft_size)
            row = {
                "type": "row",
                "start_hz": self.start_hz,
                "stop_hz": self.stop_hz,
                "bin_hz": int(round(self.bin_hz)),
                "n": self.fft_size,
                "encoding": "db_u8",
                "db_min": self.db_min,
                "db_max": self.db_max,
                "data_u8": powers_to_uint8(powers, self.db_min, self.db_max),
                "peak_db": float(powers.max()) if powers.size else -120.0,
                "ts": time.time(),
                "source": "iq_multiplexer",
            }

        blobs: list[tuple[str, bytes]] = []
        for name, ch in list(self._channels.items()):
            if not ch.enabled:
                continue
            shift = float(ch.tune_hz - self.center_hz)
            try:
                out = ddc_shift_decimate(
                    iq,
                    shift_hz=shift,
                    fs_in=float(self.sample_rate),
                    fs_out=float(ch.out_rate),
                    taps=ch.taps,
                )
            except ValueError:
                continue
            if out.size == 0:
                continue
            blobs.append((name, complex_to_cs8(out)))
        return _ChunkResult(row=row, fifo_blobs=blobs)

    def _write_fifo(self, ch: DdcChannel, blob: bytes) -> None:
        if ch.fd is None:
            self._reopen_channel(ch)
        if ch.fd is None:
            return
        try:
            os.write(ch.fd, blob)
        except BlockingIOError:
            pass
        except BrokenPipeError:
            self._reopen_channel(ch)
        except OSError:
            self._reopen_channel(ch)

    async def _emit_row(self, payload: dict[str, Any]) -> None:
        for h in list(self.handlers):
            try:
                r = h(payload)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("iqmux waterfall handler failed")
