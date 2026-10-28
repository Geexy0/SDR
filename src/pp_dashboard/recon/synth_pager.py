"""Restaurant pager / coaster / TouchTunes-style FSK/OOK baseband encoders → .cs8."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pp_dashboard.subghz.ook_synth import DEFAULT_DELTA_F, envelope_to_cs8

SHM_PAGER = Path("/dev/shm/pager_tx.cs8")


@dataclass(slots=True)
class PagerFrame:
    protocol: str
    frequency_hz: int
    path: Path
    samples: int


def _bits(value: int, n: int) -> list[int]:
    return [(value >> (n - 1 - i)) & 1 for i in range(n)]


def encode_ook_pwm(
    bits: list[int],
    *,
    sample_rate: int,
    short_us: int = 300,
    long_us: int = 900,
    repeats: int = 8,
    amplitude: float = 100.0,
    path: Path = SHM_PAGER,
) -> Path:
    segs: list[tuple[bool, int]] = []
    for _ in range(repeats):
        segs.append((False, 9000))  # sync gap
        for b in bits:
            if b:
                segs.append((True, long_us))
                segs.append((False, short_us))
            else:
                segs.append((True, short_us))
                segs.append((False, long_us))
        segs.append((False, 4000))
    counts = [max(1, int(round(us * sample_rate / 1e6))) for _, us in segs]
    total = sum(counts)
    env = np.empty(total, dtype=np.float32)
    pos = 0
    for (hi, _), n in zip(segs, counts, strict=True):
        env[pos : pos + n] = 1.0 if hi else 0.0
        pos += n
    iq = envelope_to_cs8(env, sample_rate=sample_rate, amplitude=amplitude)
    path = Path(path)
    iq.tofile(path)
    return path


def encode_fsk_bits(
    bits: list[int],
    *,
    sample_rate: int,
    bit_us: int = 500,
    deviation_hz: float = 4_500.0,
    amplitude: float = 100.0,
    path: Path = SHM_PAGER,
    repeats: int = 6,
) -> Path:
    """2-FSK around Δf LO offset (mark/space)."""
    samples_per_bit = max(1, int(round(bit_us * sample_rate / 1e6)))
    stream = bits * repeats
    n = samples_per_bit * len(stream)
    t = np.arange(n, dtype=np.float64) / sample_rate
    # Map bits to ±deviation around DEFAULT_DELTA_F
    freq = np.empty(n, dtype=np.float64)
    for i, b in enumerate(stream):
        f = DEFAULT_DELTA_F + (deviation_hz if b else -deviation_hz)
        freq[i * samples_per_bit : (i + 1) * samples_per_bit] = f
    phase = 2.0 * np.pi * np.cumsum(freq) / sample_rate
    i = amplitude * np.cos(phase)
    q = amplitude * np.sin(phase)
    out = np.empty(n * 2, dtype=np.int8)
    out[0::2] = np.clip(np.rint(i), -128, 127).astype(np.int8)
    out[1::2] = np.clip(np.rint(q), -128, 127).astype(np.int8)
    path = Path(path)
    out.tofile(path)
    return path


def synthesize_lrs_pager(
    pager_id: int,
    *,
    sample_rate: int = 2_000_000,
    path: Path = SHM_PAGER,
) -> PagerFrame:
    """Generic LRS-style 16-bit OOK ID frame (lab coaster remotes)."""
    pid = pager_id & 0xFFFF
    bits = [1, 0, 1, 0] + _bits(pid, 16) + [0, 1]
    encode_ook_pwm(bits, sample_rate=sample_rate, path=path, repeats=10)
    return PagerFrame("LRS_OOK", 433_920_000, path, path.stat().st_size // 2)


def synthesize_touchtunes_pin(
    pin: str,
    *,
    sample_rate: int = 2_000_000,
    path: Path = SHM_PAGER,
) -> PagerFrame:
    """TouchTunes-like PIN over 2-FSK (lab fixture encoding, not vendor crypto)."""
    digits = [int(c) for c in pin if c.isdigit()]
    if not digits or len(digits) > 8:
        raise ValueError("pin must be 1..8 decimal digits")
    bits: list[int] = [1, 1, 0, 1]  # preamble
    for d in digits:
        bits.extend(_bits(d, 4))
    bits.extend([0, 0, 1, 0])
    encode_fsk_bits(bits, sample_rate=sample_rate, path=path)
    return PagerFrame("TOUCHTUNES_FSK", 433_920_000, path, path.stat().st_size // 2)
