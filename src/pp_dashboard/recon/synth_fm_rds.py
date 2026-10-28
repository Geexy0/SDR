"""Wideband FM + basic 57 kHz RDS (PS/RT) → HackRF .cs8."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

SHM_FM = Path("/dev/shm/fm_rds.cs8")


@dataclass(slots=True)
class FmRdsResult:
    path: Path
    sample_rate: int
    frequency_hz: int
    duration_s: float


def _rds_basic_bits(ps: str, rt: str) -> np.ndarray:
    """Very small synthetic bit clock for demo RDS (not full group encoder).

    Produces a repeating 1187.5 baud-ish bipolar bitstream carrying PS/RT ASCII
    for lab UI verification. Full IEC 62106 groups are out of scope; this injects
    a recognizable 57 kHz subcarrier modulation pattern.
    """
    text = (ps.ljust(8)[:8] + "|" + rt[:64]).encode("ascii", errors="replace")
    bits: list[int] = []
    for b in text:
        for i in range(8):
            bits.append((b >> i) & 1)
    # Manchester-ish expansion
    out = []
    for bit in bits:
        out.extend([bit, 1 - bit])
    return np.asarray(out, dtype=np.float32) * 2.0 - 1.0


def synthesize_fm_rds(
    *,
    audio_hz: float = 440.0,
    ps: str = "LAB-FM",
    rt: str = "PP-LAB-RF-2026",
    sample_rate: int = 2_000_000,
    duration_s: float = 2.0,
    deviation_hz: float = 75_000.0,
    amplitude: float = 100.0,
    path: Path = SHM_FM,
) -> FmRdsResult:
    n = int(sample_rate * duration_s)
    t = np.arange(n, dtype=np.float64) / sample_rate
    # Mono audio tone (L+R)
    audio = 0.4 * np.sin(2 * np.pi * audio_hz * t)
    # 19 kHz pilot
    pilot = 0.08 * np.sin(2 * np.pi * 19_000 * t)
    # RDS at 57 kHz (= 3 * 19 kHz), DSB around carrier via phase-ish AM on subcarrier
    rds_bits = _rds_basic_bits(ps, rt)
    bit_rate = 1187.5 * 2  # manchester
    samples_per_bit = max(1, int(round(sample_rate / bit_rate)))
    rds_wave = np.resize(np.repeat(rds_bits, samples_per_bit), n)
    rds = 0.06 * rds_wave * np.sin(2 * np.pi * 57_000 * t)
    # Composite baseband (approx MPX without stereo L-R)
    mpx = audio + pilot + rds
    # FM: integrate mpx * deviation
    phase = 2 * np.pi * deviation_hz * np.cumsum(mpx) / sample_rate
    i = amplitude * np.cos(phase)
    q = amplitude * np.sin(phase)
    out = np.empty(n * 2, dtype=np.int8)
    out[0::2] = np.clip(np.rint(i), -128, 127).astype(np.int8)
    out[1::2] = np.clip(np.rint(q), -128, 127).astype(np.int8)
    path = Path(path)
    out.tofile(path)
    return FmRdsResult(path=path, sample_rate=sample_rate, frequency_hz=0, duration_s=duration_s)
