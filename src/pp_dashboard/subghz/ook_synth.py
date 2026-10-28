"""Vectorized OOK/PWM synthesizer + De Bruijn bruteforce to .cs8."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

SHM_OOK_PATH = Path("/dev/shm/ook_bruteforce.cs8")
DEFAULT_DELTA_F = 50_000.0  # Hz — offset LO to dodge HackRF DC spike


ProtocolFamily = Literal[
    "PT2262",
    "EV1527",
    "CAME",
    "NICE_FLO",
    "LINEAR_MEGACODE",
    "CHAMBERLAIN",
    "GENERIC_PWM",
]


@dataclass(slots=True)
class KeyTimelineEntry:
    key: int
    start_sample: int
    end_sample: int


@dataclass(slots=True)
class SynthResult:
    path: Path
    sample_rate: int
    num_samples: int
    bytes_written: int
    timeline: list[KeyTimelineEntry]
    elapsed_ms: float


def de_bruijn(k: int, n: int, *, linear: bool = False) -> np.ndarray:
    """FKM preferential-arrangement De Bruijn sequence B(k, n) as int8 symbols 0..k-1.

    Cyclic length is ``k**n``. With ``linear=True``, append the first ``n-1``
    symbols for a covering string of length ``k**n + n - 1``.
    """
    if k not in (2, 3):
        raise ValueError("k must be 2 or 3")
    if not 2 <= n <= 24:
        raise ValueError("n must be in [2, 24]")
    a = [0] * (k * n)
    seq: list[int] = []

    def db(t: int, p: int) -> None:
        if t > n:
            if n % p == 0:
                seq.extend(a[1 : p + 1])
        else:
            a[t] = a[t - p]
            db(t + 1, p)
            for j in range(a[t - p] + 1, k):
                a[t] = j
                db(t + 1, t)

    db(1, 1)
    cyclic = np.asarray(seq, dtype=np.int8)
    if not linear or cyclic.size == 0 or n <= 1:
        return cyclic
    return np.concatenate([cyclic, cyclic[: n - 1]])


def _protocol_timings(family: ProtocolFamily) -> tuple[int, int, int, int, int]:
    """Return (short_us, long_us, sync_us, guard_us, preamble_bits)."""
    table: dict[ProtocolFamily, tuple[int, int, int, int, int]] = {
        "PT2262": (350, 1050, 10850, 2000, 0),
        "EV1527": (320, 960, 9000, 2000, 0),
        "CAME": (320, 640, 11520, 3000, 12),
        "NICE_FLO": (500, 1000, 15000, 4000, 0),
        "LINEAR_MEGACODE": (1000, 2000, 25000, 5000, 0),
        "CHAMBERLAIN": (250, 500, 10000, 3000, 0),
        "GENERIC_PWM": (300, 900, 9000, 2000, 0),
    }
    return table[family]


def _bits_from_key(key: int, nbits: int) -> np.ndarray:
    bits = np.empty(nbits, dtype=np.uint8)
    for i in range(nbits - 1, -1, -1):
        bits[nbits - 1 - i] = (key >> i) & 1
    return bits


def _encode_pwm_bits(
    bits: np.ndarray,
    *,
    short_us: int,
    long_us: int,
    tri_state: bool,
) -> list[tuple[bool, int]]:
    """Bit → list of (is_high, duration_us)."""
    segs: list[tuple[bool, int]] = []
    for b in bits.tolist():
        if tri_state:
            # PT2262: 0 → short-high long-low; 1 → long-high short-low; F often unused here
            if b == 0:
                segs.append((True, short_us))
                segs.append((False, long_us))
            else:
                segs.append((True, long_us))
                segs.append((False, short_us))
        else:
            # simple PWM: 1 = long high / short low; 0 = short high / long low
            if b:
                segs.append((True, long_us))
                segs.append((False, short_us))
            else:
                segs.append((True, short_us))
                segs.append((False, long_us))
    return segs


def _segments_to_envelope(
    segments: list[tuple[bool, int]],
    sample_rate: int,
) -> np.ndarray:
    """Build float32 0/1 envelope from µs segments (vectorized per segment)."""
    if not segments:
        return np.zeros(0, dtype=np.float32)
    counts = np.array(
        [max(1, int(round(us * sample_rate / 1_000_000.0))) for _, us in segments],
        dtype=np.int64,
    )
    levels = np.array([1.0 if hi else 0.0 for hi, _ in segments], dtype=np.float32)
    total = int(counts.sum())
    env = np.empty(total, dtype=np.float32)
    pos = 0
    for level, n in zip(levels, counts, strict=True):
        env[pos : pos + n] = level
        pos += n
    return env


def envelope_to_cs8(
    envelope: np.ndarray,
    *,
    sample_rate: int,
    amplitude: float = 100.0,
    delta_f_hz: float = DEFAULT_DELTA_F,
) -> np.ndarray:
    """Multiply baseband CW at ±Δf by OOK envelope → interleaved int8 I/Q.

    Uses a one-period cosine/sine LUT + integer phase index (avoids 50M+
    ``cos``/``sin`` calls) so ~100 MiB ``.cs8`` writes stay well under 500 ms.
    """
    n = int(envelope.size)
    if n == 0:
        return np.zeros(0, dtype=np.int8)

    # Period in samples for Δf tone (e.g. 2e6 / 5e4 = 40).
    period = max(4, int(round(sample_rate / float(delta_f_hz))))
    ph = (2.0 * np.pi * np.arange(period, dtype=np.float32)) / np.float32(period)
    cos_lut = (amplitude * np.cos(ph)).astype(np.float32)
    sin_lut = (amplitude * np.sin(ph)).astype(np.float32)

    env = np.asarray(envelope, dtype=np.float32)
    # Chunk to keep index buffers cache-friendly on huge envelopes.
    out = np.empty(n * 2, dtype=np.int8)
    chunk = 1 << 20  # 1M samples
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        idx = np.arange(start, end, dtype=np.int64) % period
        i = np.rint(cos_lut[idx] * env[start:end])
        q = np.rint(sin_lut[idx] * env[start:end])
        np.clip(i, -128, 127, out=i)
        np.clip(q, -128, 127, out=q)
        out[start * 2 : end * 2 : 2] = i.astype(np.int8, copy=False)
        out[start * 2 + 1 : end * 2 : 2] = q.astype(np.int8, copy=False)
    return out


def synthesize_framed_bruteforce(
    *,
    family: ProtocolFamily,
    nbits: int,
    key_start: int,
    key_end: int,
    repeats: int,
    sample_rate: int,
    path: Path = SHM_OOK_PATH,
    amplitude: float = 100.0,
    delta_f_hz: float = DEFAULT_DELTA_F,
) -> SynthResult:
    import time

    if nbits < 1 or nbits > 32:
        raise ValueError("nbits must be 1..32")
    if key_end < key_start:
        raise ValueError("key_end must be >= key_start")
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    short_us, long_us, sync_us, guard_us, preamble_bits = _protocol_timings(family)
    tri = family in {"PT2262", "EV1527"}

    t0 = time.perf_counter()
    chunks: list[np.ndarray] = []
    timeline: list[KeyTimelineEntry] = []
    sample_cursor = 0

    for key in range(key_start, key_end + 1):
        bits = _bits_from_key(key, nbits)
        segs: list[tuple[bool, int]] = []
        if preamble_bits:
            for _ in range(preamble_bits):
                segs.append((True, short_us))
                segs.append((False, short_us))
        # sync / header gap (low)
        segs.append((False, sync_us))
        segs.extend(_encode_pwm_bits(bits, short_us=short_us, long_us=long_us, tri_state=tri))
        segs.append((False, guard_us))
        frame_env = _segments_to_envelope(segs, sample_rate)
        if repeats > 1:
            frame_env = np.tile(frame_env, repeats)
        start = sample_cursor
        end = sample_cursor + frame_env.size
        timeline.append(KeyTimelineEntry(key=key, start_sample=start, end_sample=end))
        sample_cursor = end
        chunks.append(frame_env)

    if not chunks:
        raise ValueError("empty key range")
    envelope = np.concatenate(chunks)
    iq = envelope_to_cs8(
        envelope, sample_rate=sample_rate, amplitude=amplitude, delta_f_hz=delta_f_hz
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    iq.tofile(path)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return SynthResult(
        path=path,
        sample_rate=sample_rate,
        num_samples=envelope.size,
        bytes_written=iq.nbytes,
        timeline=timeline,
        elapsed_ms=elapsed_ms,
    )


def synthesize_de_bruijn_stream(
    *,
    k: int,
    n: int,
    short_us: int = 300,
    long_us: int = 900,
    sample_rate: int,
    path: Path = SHM_OOK_PATH,
    amplitude: float = 100.0,
    delta_f_hz: float = DEFAULT_DELTA_F,
    symbol_guard_us: int = 0,
) -> SynthResult:
    """Encode continuous De Bruijn symbols as PWM into a single loopable .cs8."""
    import time

    t0 = time.perf_counter()
    symbols = de_bruijn(k, n)
    segs: list[tuple[bool, int]] = []
    # Map symbol 0/1/(2) → PWM patterns
    for sym in symbols.tolist():
        if k == 2:
            bit = int(sym)
            if bit:
                segs.append((True, long_us))
                segs.append((False, short_us))
            else:
                segs.append((True, short_us))
                segs.append((False, long_us))
        else:
            # trit: 0 short, 1 long, 2 = float-ish (short-short)
            if sym == 0:
                segs.append((True, short_us))
                segs.append((False, long_us))
            elif sym == 1:
                segs.append((True, long_us))
                segs.append((False, short_us))
            else:
                segs.append((True, short_us))
                segs.append((False, short_us))
        if symbol_guard_us:
            segs.append((False, symbol_guard_us))

    envelope = _segments_to_envelope(segs, sample_rate)
    iq = envelope_to_cs8(
        envelope, sample_rate=sample_rate, amplitude=amplitude, delta_f_hz=delta_f_hz
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    iq.tofile(path)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    # Timeline approximates each overlapping n-window key as sequential symbol index
    timeline = [
        KeyTimelineEntry(key=i, start_sample=0, end_sample=envelope.size)
        for i in range(min(16, symbols.size))
    ]
    return SynthResult(
        path=path,
        sample_rate=sample_rate,
        num_samples=envelope.size,
        bytes_written=iq.nbytes,
        timeline=timeline,
        elapsed_ms=elapsed_ms,
    )


def key_at_sample(timeline: list[KeyTimelineEntry], sample_idx: int) -> int | None:
    for entry in timeline:
        if entry.start_sample <= sample_idx < entry.end_sample:
            return entry.key
    return None


def keys_in_window(
    timeline: list[KeyTimelineEntry],
    center_sample: int,
    window_samples: int,
) -> list[int]:
    lo = center_sample - window_samples
    hi = center_sample + window_samples
    hits: list[int] = []
    for entry in timeline:
        if entry.end_sample < lo or entry.start_sample > hi:
            continue
        hits.append(entry.key)
    return hits
