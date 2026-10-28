"""Linear FM chirp IQ synthesizer → interleaved int8 (.cs8)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

# Baseband chirp: s(t) = A * exp(j 2π (f0 t + (B/(2T)) t^2))
# with f0 = -B/2 so the instantaneous frequency sweeps [-B/2, +B/2] around LO.


def synthesize_chirp_cs8(
    *,
    sample_rate: int,
    bandwidth_hz: float,
    sweep_period_s: float,
    amplitude: float = 100.0,
    path: Path,
) -> Path:
    """Pre-compute one loopable chirp period and write interleaved int8 I/Q.

    Args:
        sample_rate: fs in Hz (2e6..20e6).
        bandwidth_hz: total sweep span B (Hz), must be <= sample_rate and <= 20e6.
        sweep_period_s: chirp period T in seconds.
        amplitude: peak magnitude before int8 quantize (0..127).
        path: destination .cs8 path (typically under /dev/shm).
    """
    if bandwidth_hz <= 0:
        raise ValueError("bandwidth_hz must be > 0")
    if bandwidth_hz > 20_000_000:
        raise ValueError("bandwidth_hz must be <= 20 MHz")
    if bandwidth_hz > sample_rate:
        raise ValueError("bandwidth_hz cannot exceed sample_rate")
    if sweep_period_s <= 0:
        raise ValueError("sweep_period_s must be > 0")
    if not 0 < amplitude <= 127:
        raise ValueError("amplitude must be in (0, 127]")

    n = int(round(sample_rate * sweep_period_s))
    if n < 16:
        raise ValueError("chirp too short; increase sweep_period_s or sample_rate")
    # Cap RAM: ~2 bytes/sample; keep under ~64 MiB for /dev/shm courtesy.
    if n > 32_000_000:
        raise ValueError("chirp buffer too large; reduce sweep_period_s or sample_rate")

    t = np.arange(n, dtype=np.float64) / float(sample_rate)
    f0 = -0.5 * bandwidth_hz
    # Instantaneous phase: 2π (f0 t + (B/(2T)) t^2)
    phase = 2.0 * np.pi * (f0 * t + (bandwidth_hz / (2.0 * sweep_period_s)) * (t**2))
    sig = amplitude * np.exp(1j * phase)
    i = np.clip(np.rint(sig.real), -128, 127).astype(np.int8)
    q = np.clip(np.rint(sig.imag), -128, 127).astype(np.int8)
    interleaved = np.empty(n * 2, dtype=np.int8)
    interleaved[0::2] = i
    interleaved[1::2] = q

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(interleaved.tobytes())
    return path


def estimate_rms_dbfs(cs8_path: Path) -> float:
    """RMS power of interleaved int8 IQ relative to full-scale peak (127)."""
    raw = np.fromfile(cs8_path, dtype=np.int8)
    if raw.size < 2:
        return float("-inf")
    if raw.size % 2:
        raw = raw[:-1]
    i = raw[0::2].astype(np.float64)
    q = raw[1::2].astype(np.float64)
    power = np.mean(i * i + q * q)
    full_scale = 127.0 * 127.0
    if power <= 0:
        return float("-inf")
    return float(10.0 * np.log10(power / full_scale))
