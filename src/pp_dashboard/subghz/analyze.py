"""IQ envelope + FSK/PSK analyzer with OOK protocol classification."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

EncodingName = Literal[
    "PT2262",
    "EV1527",
    "CAME_12",
    "CAME_24",
    "NICE_FLO",
    "LINEAR_MEGACODE",
    "PRINCETON",
    "UNKNOWN",
]

# Viewport downsample trigger: CS8 > 2 MiB
LARGE_CS8_BYTES = 2 * 1024 * 1024
DEFAULT_TRACE_POINTS = 4096
CONSTELLATION_SIZE = 200

PREAMBLE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("0xAAAA", "1010101010101010"),
    ("0x5555", "0101010101010101"),
    ("0x2DD4", "0010110111010100"),
)


@dataclass(slots=True)
class PulseTrain:
    pulses_us: list[int]  # signed: +high, -low durations
    sample_rate: int
    threshold: float
    method: str


@dataclass(slots=True)
class TimingEstimate:
    short_us: float | None
    long_us: float | None
    sync_gap_us: float | None
    bit_us: float | None
    encoding: EncodingName
    confidence: float
    notes: str


@dataclass(slots=True)
class FskEstimate:
    carrier_offset_hz: float
    deviation_hz: float
    baud_est: float
    samples_per_bit: float
    bits: str
    preambles: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""


@dataclass(slots=True)
class AnalyzeResult:
    pulses: PulseTrain
    timing: TimingEstimate
    envelope_peak: float
    num_samples: int
    envelope_ds: list[float] | None = None
    freq_dev_ds: list[float] | None = None
    phase_ds: list[float] | None = None
    constellation_density: list[int] | None = None
    constellation_size: int = CONSTELLATION_SIZE
    fsk: FskEstimate | None = None
    file_bytes: int = 0
    downsampled: bool = False


def load_cs8(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.int8)
    if raw.size < 2:
        raise ValueError("IQ file too short")
    if raw.size % 2:
        raw = raw[:-1]
    i = raw[0::2].astype(np.float32)
    q = raw[1::2].astype(np.float32)
    return i + 1j * q


def magnitude_envelope(iq: np.ndarray) -> np.ndarray:
    return np.abs(iq).astype(np.float32)


def otsu_threshold(mag: np.ndarray, bins: int = 256) -> float:
    """Otsu threshold on magnitude histogram."""
    hist, edges = np.histogram(mag, bins=bins)
    hist = hist.astype(np.float64)
    total = mag.size
    if total == 0:
        return 0.0
    prob = hist / total
    omega = np.cumsum(prob)
    mu = np.cumsum(prob * (edges[:-1] + edges[1:]) * 0.5)
    mu_t = mu[-1]
    sigma_b = (mu_t * omega - mu) ** 2 / (omega * (1.0 - omega) + 1e-12)
    idx = int(np.nanargmax(sigma_b))
    return float((edges[idx] + edges[idx + 1]) * 0.5)


def adaptive_threshold(mag: np.ndarray, method: str = "otsu") -> tuple[float, str]:
    if method == "median6db":
        med = float(np.median(mag))
        # +6 dB ≈ ×2 in amplitude
        thr = med * 2.0
        # floor for near-silence captures
        thr = max(thr, float(np.percentile(mag, 70)))
        return thr, "median+6dB"
    thr = otsu_threshold(mag)
    return thr, "otsu"


def extract_pulses_us(
    mag: np.ndarray,
    sample_rate: int,
    threshold: float,
) -> list[int]:
    """Return signed pulse widths in µs (+ high / − low)."""
    high = mag >= threshold
    if high.size == 0:
        return []
    # Run-length encode
    changes = np.flatnonzero(high[1:] != high[:-1]) + 1
    starts = np.concatenate(([0], changes))
    ends = np.concatenate((changes, [high.size]))
    pulses: list[int] = []
    for s, e in zip(starts, ends, strict=True):
        dur_s = (e - s) / float(sample_rate)
        us = int(round(dur_s * 1e6))
        if us <= 0:
            continue
        pulses.append(us if high[s] else -us)
    return pulses


def _cluster_two(values: np.ndarray) -> tuple[float, float] | None:
    if values.size < 4:
        return None
    # Simple 1-D 2-means
    c0, c1 = float(np.percentile(values, 25)), float(np.percentile(values, 75))
    for _ in range(8):
        d0 = np.abs(values - c0)
        d1 = np.abs(values - c1)
        m0 = values[d0 <= d1]
        m1 = values[d1 < d0]
        if m0.size == 0 or m1.size == 0:
            return None
        c0, c1 = float(m0.mean()), float(m1.mean())
    short, long_ = sorted((c0, c1))
    if long_ / max(short, 1.0) < 1.3:
        return None
    return short, long_


def classify_encoding(pulses: list[int]) -> TimingEstimate:
    highs = np.array([p for p in pulses if p > 0], dtype=np.float64)
    lows = np.array([-p for p in pulses if p < 0], dtype=np.float64)
    if highs.size < 4:
        return TimingEstimate(None, None, None, None, "UNKNOWN", 0.0, "insufficient pulses")

    hl = _cluster_two(highs)
    ll = _cluster_two(lows) if lows.size >= 4 else None
    short = hl[0] if hl else float(np.median(highs))
    long_ = hl[1] if hl else short * 3.0
    sync_gap = float(np.max(lows)) if lows.size else None
    bit = short + (ll[0] if ll else short)

    ratio = long_ / max(short, 1.0)
    encoding: EncodingName = "UNKNOWN"
    conf = 0.2
    notes = f"short≈{short:.0f}µs long≈{long_:.0f}µs ratio≈{ratio:.2f}"

    # Heuristics (µs domains typical for ISM remotes)
    if 150 <= short <= 450 and 2.5 <= ratio <= 4.5:
        encoding = "PT2262"
        conf = 0.7
        notes += "; PT2262-like tri-state timing"
    if 200 <= short <= 500 and 2.8 <= ratio <= 3.5 and sync_gap and sync_gap > 8 * short:
        encoding = "EV1527"
        conf = max(conf, 0.75)
        notes += "; EV1527-like"
    if 250 <= short <= 400 and 2.0 <= ratio <= 3.0:
        # CAME often ~320µs short / ~640 long, 12 or 24 bit
        encoding = "CAME_12" if highs.size < 40 else "CAME_24"
        conf = max(conf, 0.65)
        notes += "; CAME-like"
    if 400 <= short <= 700 and 1.8 <= ratio <= 2.5:
        encoding = "NICE_FLO"
        conf = max(conf, 0.6)
        notes += "; Nice FLO-like"
    if 800 <= short <= 1400:
        encoding = "LINEAR_MEGACODE"
        conf = max(conf, 0.55)
        notes += "; Linear MegaCode-ish wide pulses"
    if 300 <= short <= 600 and 2.8 <= ratio <= 3.6:
        encoding = "PRINCETON"
        conf = max(conf, 0.6)
        notes += "; Princeton/PT2262 family"

    return TimingEstimate(short, long_, sync_gap, bit, encoding, conf, notes)


def downsample_envelope(mag: np.ndarray, max_points: int = DEFAULT_TRACE_POINTS) -> list[float]:
    """Peak-hold downsample for canvas rendering (keeps transient edges)."""
    return downsample_trace(mag, max_points=max_points, mode="peak")


def downsample_trace(
    x: np.ndarray,
    *,
    max_points: int = DEFAULT_TRACE_POINTS,
    mode: Literal["peak", "mean", "mid"] = "peak",
) -> list[float]:
    """Downsample a 1-D trace for viewport rendering."""
    arr = np.asarray(x, dtype=np.float32).ravel()
    n = int(arr.size)
    if n == 0:
        return []
    if n <= max_points:
        return arr.tolist()
    step = n / float(max_points)
    out = np.empty(max_points, dtype=np.float32)
    for i in range(max_points):
        a = int(i * step)
        b = int((i + 1) * step)
        if b <= a:
            b = a + 1
        chunk = arr[a:b]
        if mode == "mean":
            out[i] = float(np.mean(chunk))
        elif mode == "mid":
            out[i] = float(chunk[chunk.size // 2])
        else:
            # peak-hold by abs max with sign preserved
            idx = int(np.argmax(np.abs(chunk)))
            out[i] = float(chunk[idx])
    return out.tolist()


def instantaneous_frequency(iq: np.ndarray, *, sample_rate: int) -> np.ndarray:
    """
    Instantaneous frequency (Hz) via quadrature product discriminator:
    f̂[n] = fs/(2π) · atan2(Im{z[n] z*[n-1]}, Re{z[n] z*[n-1]}).
    """
    z = np.asarray(iq, dtype=np.complex64).ravel()
    if z.size < 2:
        return np.zeros(z.size, dtype=np.float64)
    prod = z[1:] * np.conj(z[:-1])
    dphi = np.atan2(prod.imag, prod.real)
    freq = (float(sample_rate) / (2.0 * math.pi)) * dphi
    return np.concatenate([[float(freq[0])], freq.astype(np.float64)])


def instantaneous_phase(iq: np.ndarray) -> np.ndarray:
    z = np.asarray(iq, dtype=np.complex64).ravel()
    return np.atan2(z.imag, z.real).astype(np.float64)


def constellation_density(
    iq: np.ndarray, *, size: int = CONSTELLATION_SIZE
) -> list[int]:
    """Density-mapped I/Q constellation (row-major, size×size), uint8 counts clipped."""
    z = np.asarray(iq, dtype=np.complex64).ravel()
    if z.size == 0:
        return [0] * (size * size)
    # Decimate for density if huge
    if z.size > 200_000:
        step = z.size // 200_000
        z = z[::step]
    i = z.real.astype(np.float64)
    q = z.imag.astype(np.float64)
    peak = max(float(np.percentile(np.abs(z), 99)), 1e-6)
    # Map [-peak, peak] → [0, size)
    xi = np.clip(((i / peak) * 0.5 + 0.5) * size, 0, size - 1e-9).astype(np.int32)
    yi = np.clip(((q / peak) * 0.5 + 0.5) * size, 0, size - 1e-9).astype(np.int32)
    # Flip y so +Q is up
    yi = (size - 1) - yi
    hist = np.zeros((size, size), dtype=np.float64)
    np.add.at(hist, (yi, xi), 1.0)
    if hist.max() > 0:
        hist = hist / hist.max() * 255.0
    return hist.astype(np.uint8).ravel().tolist()


def estimate_fsk_params(
    freq_hz: np.ndarray,
    *,
    sample_rate: int,
) -> FskEstimate:
    f = np.asarray(freq_hz, dtype=np.float64).ravel()
    if f.size < 16:
        return FskEstimate(0.0, 0.0, 0.0, 0.0, "", notes="too short")
    # Carrier offset ≈ mean instantaneous frequency (residual LO)
    carrier = float(np.mean(f))
    centered = f - carrier
    pos = centered[centered > 0]
    neg = centered[centered < 0]
    if pos.size >= 8 and neg.size >= 8:
        hi = float(np.median(pos))
        lo = float(np.median(neg))
        deviation = 0.5 * (hi - lo)
        # Fine offset residual after mean
        residual = 0.5 * (hi + lo)
        carrier += residual
        centered = f - carrier
    else:
        deviation = float(np.std(centered))

    # Baud from zero-crossing intervals of mean-centered deviation
    signs = np.sign(centered)
    signs[signs == 0] = 1
    zc = np.flatnonzero(signs[1:] != signs[:-1]) + 1
    spb = 0.0
    baud = 0.0
    if zc.size >= 4:
        intervals = np.diff(zc).astype(np.float64)
        # Reject outliers (long same-bit runs)
        med = float(np.median(intervals))
        tight = intervals[(intervals > 0.4 * med) & (intervals < 2.5 * med)]
        if tight.size:
            spb = float(np.median(tight))
            baud = float(sample_rate) / spb if spb > 0 else 0.0

    bits = ""
    preambles: list[dict[str, Any]] = []
    if spb >= 2:
        bits = slice_fsk_bits(centered, threshold=0.0, samples_per_bit=spb)
        preambles = find_bit_preambles(bits)

    notes = (
        f"offset≈{carrier:.0f} Hz · Δf≈±{deviation:.0f} Hz · "
        f"baud≈{baud:.0f} · spb≈{spb:.1f}"
    )
    return FskEstimate(
        carrier_offset_hz=carrier,
        deviation_hz=abs(deviation),
        baud_est=baud,
        samples_per_bit=spb,
        bits=bits[:4096],
        preambles=preambles,
        notes=notes,
    )


def slice_fsk_bits(
    freq_centered: np.ndarray,
    *,
    threshold: float = 0.0,
    samples_per_bit: float,
) -> str:
    """Sample NRZ bits at mid-bit using center-frequency threshold."""
    f = np.asarray(freq_centered, dtype=np.float64).ravel()
    spb = max(float(samples_per_bit), 1.0)
    n_bits = int(f.size / spb)
    if n_bits <= 0:
        return ""
    bits: list[str] = []
    for k in range(n_bits):
        mid = int((k + 0.5) * spb)
        if mid >= f.size:
            break
        bits.append("1" if f[mid] > threshold else "0")
    return "".join(bits)


def find_bit_preambles(bits: str) -> list[dict[str, Any]]:
    """Locate known sync / preamble words in a bitstring."""
    out: list[dict[str, Any]] = []
    if not bits:
        return out
    for name, pattern in PREAMBLE_PATTERNS:
        start = 0
        while True:
            idx = bits.find(pattern, start)
            if idx < 0:
                break
            out.append({"pattern": name, "bit_offset": idx, "pattern_bits": pattern})
            start = idx + 1
            if len(out) >= 16:
                return out
    return out


def analyze_cs8(
    path: Path,
    *,
    sample_rate: int,
    threshold_method: str = "otsu",
    fsk_threshold_hz: float | None = None,
) -> AnalyzeResult:
    file_bytes = int(path.stat().st_size) if path.is_file() else 0
    downsampled = file_bytes > LARGE_CS8_BYTES
    max_points = DEFAULT_TRACE_POINTS if downsampled else min(DEFAULT_TRACE_POINTS, 8192)

    iq = load_cs8(path)
    mag = magnitude_envelope(iq)
    thr, method = adaptive_threshold(mag, threshold_method)
    pulses = extract_pulses_us(mag, sample_rate, thr)
    timing = classify_encoding(pulses)

    freq = instantaneous_frequency(iq, sample_rate=sample_rate)
    phase = instantaneous_phase(iq)
    fsk = estimate_fsk_params(freq, sample_rate=sample_rate)
    if fsk_threshold_hz is not None and fsk.samples_per_bit >= 2:
        centered = freq - fsk.carrier_offset_hz
        fsk.bits = slice_fsk_bits(
            centered,
            threshold=float(fsk_threshold_hz),
            samples_per_bit=fsk.samples_per_bit,
        )[:4096]
        fsk.preambles = find_bit_preambles(fsk.bits)

    # Mean-centered deviation for FM viewport (kHz-friendly scale kept in Hz)
    freq_centered = freq - fsk.carrier_offset_hz

    return AnalyzeResult(
        pulses=PulseTrain(pulses, sample_rate, thr, method),
        timing=timing,
        envelope_peak=float(mag.max()) if mag.size else 0.0,
        num_samples=int(iq.size),
        envelope_ds=downsample_trace(mag, max_points=max_points, mode="peak"),
        freq_dev_ds=downsample_trace(freq_centered, max_points=max_points, mode="mid"),
        phase_ds=downsample_trace(phase, max_points=max_points, mode="mid"),
        constellation_density=constellation_density(iq, size=CONSTELLATION_SIZE),
        constellation_size=CONSTELLATION_SIZE,
        fsk=fsk,
        file_bytes=file_bytes,
        downsampled=downsampled,
    )


def analyze_result_dict(result: AnalyzeResult) -> dict[str, Any]:
    fsk = result.fsk
    return {
        "num_samples": result.num_samples,
        "envelope_peak": result.envelope_peak,
        "threshold": result.pulses.threshold,
        "threshold_method": result.pulses.method,
        "sample_rate": result.pulses.sample_rate,
        "pulses_us": result.pulses.pulses_us[:5000],
        "pulse_count": len(result.pulses.pulses_us),
        "envelope_ds": result.envelope_ds or [],
        "freq_dev_ds": result.freq_dev_ds or [],
        "phase_ds": result.phase_ds or [],
        "constellation_size": result.constellation_size,
        "constellation_density": result.constellation_density or [],
        "file_bytes": result.file_bytes,
        "downsampled": result.downsampled,
        "timing": {
            "short_us": result.timing.short_us,
            "long_us": result.timing.long_us,
            "sync_gap_us": result.timing.sync_gap_us,
            "bit_us": result.timing.bit_us,
            "encoding": result.timing.encoding,
            "confidence": result.timing.confidence,
            "notes": result.timing.notes,
        },
        "fsk": None
        if fsk is None
        else {
            "carrier_offset_hz": fsk.carrier_offset_hz,
            "deviation_hz": fsk.deviation_hz,
            "deviation_khz": fsk.deviation_hz / 1000.0,
            "baud_est": fsk.baud_est,
            "samples_per_bit": fsk.samples_per_bit,
            "bits": fsk.bits,
            "preambles": fsk.preambles,
            "notes": fsk.notes,
        },
    }
