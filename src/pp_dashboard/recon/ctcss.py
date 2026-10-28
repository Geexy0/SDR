"""Standard CTCSS / PL tone table + Goertzel detector (sub-audible)."""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

# EIA / Motorola standard 50 CTCSS tones (Hz), 67.0 … 254.1
CTCSS_TONES_HZ: tuple[float, ...] = (
    67.0,
    69.3,
    71.9,
    74.4,
    77.0,
    79.7,
    82.5,
    85.4,
    88.5,
    91.5,
    94.8,
    97.4,
    100.0,
    103.5,
    107.2,
    110.9,
    114.8,
    118.8,
    123.0,
    127.3,
    131.8,
    136.5,
    141.3,
    146.2,
    151.4,
    156.7,
    159.8,
    162.2,
    165.5,
    167.9,
    171.3,
    173.8,
    177.3,
    179.9,
    183.5,
    186.2,
    189.9,
    192.8,
    196.6,
    199.5,
    203.5,
    206.5,
    210.7,
    218.1,
    225.7,
    229.1,
    233.6,
    241.8,
    250.3,
    254.1,
)


def goertzel_power(samples: np.ndarray, sample_rate: float, freq_hz: float) -> float:
    """Normalized Goertzel magnitude-squared for one bin frequency."""
    x = np.asarray(samples, dtype=np.float64).ravel()
    n = x.size
    if n < 8 or sample_rate <= 0 or freq_hz <= 0:
        return 0.0
    k = int(0.5 + (n * freq_hz) / sample_rate)
    w = (2.0 * math.pi * k) / n
    coeff = 2.0 * math.cos(w)
    s0 = 0.0
    s1 = 0.0
    s2 = 0.0
    for sample in x:
        s0 = sample + coeff * s1 - s2
        s2 = s1
        s1 = s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return float(power) / (n * n)


def detect_ctcss_hz(
    samples: np.ndarray | Sequence[float],
    *,
    sample_rate: float = 48_000.0,
    tones: Sequence[float] = CTCSS_TONES_HZ,
    min_snr: float = 6.0,
    window: int = 4096,
) -> float | None:
    """
    Return best-matching CTCSS tone (Hz) or None.

    Expects raw FM discriminator (or low-passed audio) with PL still present —
    call before speech high-pass.
    """
    x = np.asarray(samples, dtype=np.float64).ravel()
    if x.size < window // 2:
        return None
    if x.size > window:
        x = x[-window:]
    # Remove DC
    x = x - float(np.mean(x))
    powers = [goertzel_power(x, sample_rate, float(f)) for f in tones]
    if not powers:
        return None
    best_i = int(np.argmax(powers))
    best = powers[best_i]
    # Median of other bins as noise proxy
    others = [p for i, p in enumerate(powers) if i != best_i]
    noise = float(np.median(others)) if others else 0.0
    if best <= 1e-18:
        return None
    snr = best / max(noise, 1e-18)
    if snr < min_snr:
        return None
    return float(tones[best_i])
