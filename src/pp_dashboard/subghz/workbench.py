"""Universal Digital Packet Workbench — baud estimate, bit-slice, sync, CRC reveng."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from scipy import signal as sp_signal

from pp_dashboard.subghz.analyze import instantaneous_frequency, load_cs8

Modulation = Literal["AUTO", "OOK", "2FSK", "GFSK", "BPSK"]
LineCode = Literal[
    "NRZ",
    "NRZ_INV",
    "MANCHESTER_IEEE",
    "MANCHESTER_THOMAS",
    "DIFF_MANCHESTER",
    "PWM",
]
ColumnClass = Literal["static", "incrementing", "high_entropy", "mixed"]

KNOWN_SYNCS: tuple[str, ...] = (
    "0010110111010100",  # 0x2DD4
    "1101001110010001",  # 0xD391
    "1010101001010101",  # 0xAA55
    "0101010110101010",  # 0x55AA
)


@dataclass(slots=True)
class SyncPacket:
    bit_offset: int
    sync_bits: str
    sync_hex: str
    payload_bits: str
    payload_bytes: bytes
    hamming: int = 0


@dataclass(slots=True)
class DemodResult:
    modulation: Modulation
    line_code: LineCode
    sample_rate: int
    baud_est: float
    samples_per_symbol: float
    bits: str
    bits_inv: str
    sync_word: str | None
    packets: list[SyncPacket] = field(default_factory=list)
    column_classes: list[ColumnClass] = field(default_factory=list)
    crc_matches: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""


def am_envelope(iq: np.ndarray) -> np.ndarray:
    z = np.asarray(iq, dtype=np.complex64).ravel()
    i = z.real.astype(np.float32)
    q = z.imag.astype(np.float32)
    return np.sqrt(i * i + q * q + 1e-20).astype(np.float32)


def butter_lowpass(
    x: np.ndarray, *, sample_rate: float, cutoff_hz: float, order: int = 4
) -> np.ndarray:
    """Fourth-order Butterworth LPF (SOS) around detected signal bandwidth."""
    arr = np.asarray(x, dtype=np.float64).ravel()
    if arr.size < 16:
        return arr.astype(np.float32)
    nyq = 0.5 * float(sample_rate)
    wn = min(max(cutoff_hz / nyq, 1e-4), 0.99)
    sos = sp_signal.butter(order, wn, btype="low", output="sos")
    return sp_signal.sosfiltfilt(sos, arr).astype(np.float32)


def estimate_signal_bandwidth_hz(iq: np.ndarray, *, sample_rate: int) -> float:
    """Rough occupied BW from spectral centroid / 90% energy."""
    z = np.asarray(iq, dtype=np.complex64).ravel()
    n = min(z.size, 65536)
    if n < 64:
        return float(sample_rate) * 0.1
    win = np.hanning(n).astype(np.float32)
    spec = np.fft.fftshift(np.fft.fft(z[:n] * win))
    power = (np.abs(spec) ** 2).astype(np.float64)
    power = np.maximum(power, 1e-20)
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / sample_rate))
    cum = np.cumsum(power)
    total = cum[-1]
    lo = int(np.searchsorted(cum, 0.05 * total))
    hi = int(np.searchsorted(cum, 0.95 * total))
    bw = abs(float(freqs[min(hi, n - 1)] - freqs[max(lo, 0)]))
    return max(bw, float(sample_rate) * 0.02)


def estimate_baud_fft(x: np.ndarray, *, sample_rate: int) -> float:
    """Baud from FFT spectral line on squared first-difference (transition rate)."""
    arr = np.asarray(x, dtype=np.float64).ravel()
    if arr.size < 64:
        return 0.0
    # Center / normalize
    arr = arr - np.mean(arr)
    dx = np.diff(arr)
    sq = dx * dx
    sq = sq - np.mean(sq)
    n = min(sq.size, 131072)
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(sq[:n] * win))
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    # Ignore DC / very low; look for baud in [50, fs/4]
    lo = max(1, int(50.0 * n / sample_rate))
    hi = max(lo + 2, int((sample_rate / 4.0) * n / sample_rate))
    hi = min(hi, spec.size - 1)
    band = spec[lo:hi]
    if band.size == 0:
        return 0.0
    peak_i = int(np.argmax(band)) + lo
    baud = float(freqs[peak_i])
    # Prefer first harmonic if a half-rate peak dominates
    half = baud * 0.5
    if half >= 50:
        half_i = int(round(half * n / sample_rate))
        if lo <= half_i < hi and spec[half_i] > 0.6 * spec[peak_i]:
            baud = half
    return max(baud, 0.0)


def _choose_baseband(
    iq: np.ndarray, *, sample_rate: int, modulation: Modulation
) -> tuple[np.ndarray, Modulation, str]:
    """Return (baseband_float, resolved_mod, note)."""
    env = am_envelope(iq)
    freq = instantaneous_frequency(iq, sample_rate=sample_rate).astype(np.float32)
    bw = estimate_signal_bandwidth_hz(iq, sample_rate=sample_rate)
    cutoff = min(max(bw * 0.75, 500.0), sample_rate * 0.45)
    env_f = butter_lowpass(env, sample_rate=sample_rate, cutoff_hz=cutoff, order=4)
    freq_f = butter_lowpass(freq, sample_rate=sample_rate, cutoff_hz=cutoff, order=4)

    if modulation == "OOK":
        return env_f, "OOK", f"AM envelope · LPF {cutoff:.0f} Hz"
    if modulation in {"2FSK", "GFSK"}:
        return freq_f, modulation, f"FM discriminator · LPF {cutoff:.0f} Hz"
    if modulation == "BPSK":
        # Coarse Costas-like: use I after derotation by mean phase ramp
        z = np.asarray(iq, dtype=np.complex64).ravel()
        phase = np.unwrap(np.angle(z))
        derot = z * np.exp(-1j * phase)
        base = butter_lowpass(
            derot.real.astype(np.float32),
            sample_rate=sample_rate,
            cutoff_hz=cutoff,
            order=4,
        )
        return base, "BPSK", f"BPSK I · LPF {cutoff:.0f} Hz"

    # AUTO: pick the channel with higher normalized transition energy
    e_env = float(np.std(np.diff(env_f)))
    e_fm = float(np.std(np.diff(freq_f)))
    if e_env >= e_fm * 0.85:
        return env_f, "OOK", f"AUTO→OOK · LPF {cutoff:.0f} Hz"
    return freq_f, "2FSK", f"AUTO→2FSK · LPF {cutoff:.0f} Hz"


def mueller_muller_recover(
    x: np.ndarray, *, samples_per_symbol: float
) -> np.ndarray:
    """Vectorized Mueller & Müller timing recovery → one sample per symbol."""
    arr = np.asarray(x, dtype=np.float64).ravel()
    if arr.size < 8 or samples_per_symbol < 1.5:
        return arr
    spb = float(samples_per_symbol)
    # Normalize
    s = arr / (np.std(arr) + 1e-12)
    out: list[float] = []
    mu = 0.0
    i = spb
    prev = 0.0
    gain = 0.3
    while i + 1 < s.size:
        idx = int(i)
        frac = i - idx
        # Linear interp
        sample = (1.0 - frac) * s[idx] + frac * s[min(idx + 1, s.size - 1)]
        out.append(sample)
        # MM TED: e = x[n] x[n-1/2] - x[n-1] x[n+1/2] ≈ x[n]*(x[n]-x[n-1]) soft form
        err = sample * (sample - prev)
        mu += -gain * err
        mu = float(np.clip(mu, -0.5, 0.5))
        prev = sample
        i += spb + mu
    return np.asarray(out, dtype=np.float64)


def slice_symbols(symbols: np.ndarray, *, threshold: float | None = None) -> str:
    s = np.asarray(symbols, dtype=np.float64).ravel()
    if s.size == 0:
        return ""
    thr = float(np.median(s)) if threshold is None else float(threshold)
    # For AM: above thr → 1; for FM centered: above 0 → 1
    bits = np.where(s >= thr, "1", "0")
    return "".join(bits.tolist())


def decode_manchester(coded: str, *, convention: Literal["ieee", "thomas"] = "ieee") -> str:
    """Decode Manchester pairs → data bits."""
    clean = "".join(c for c in coded if c in "01")
    if len(clean) % 2:
        clean = clean[:-1]
    out: list[str] = []
    for i in range(0, len(clean), 2):
        pair = clean[i : i + 2]
        if convention == "ieee":
            # 0→01, 1→10
            if pair == "10":
                out.append("1")
            elif pair == "01":
                out.append("0")
            else:
                out.append("?")
        else:
            # Thomas: 0→10, 1→01
            if pair == "01":
                out.append("1")
            elif pair == "10":
                out.append("0")
            else:
                out.append("?")
    return "".join(b for b in out if b in "01")


def decode_diff_manchester(coded: str) -> str:
    clean = "".join(c for c in coded if c in "01")
    if len(clean) < 2:
        return ""
    out: list[str] = []
    prev = clean[0]
    for i in range(1, len(clean), 2):
        pair = clean[i : i + 2] if i + 1 < len(clean) else clean[i] + prev
        # Transition at start → 0, no transition → 1 (common Diff Man)
        bit = "0" if pair[0] != prev else "1"
        out.append(bit)
        prev = pair[-1]
    return "".join(out)


def decode_pwm(symbols: np.ndarray, *, short_frac: float = 0.75) -> str:
    """PWM: short high → 0, long high → 1 (relative to median high width)."""
    s = np.asarray(symbols, dtype=np.float64).ravel()
    if s.size < 4:
        return ""
    thr = float(np.median(s))
    hard = (s >= thr).astype(np.int8)
    runs: list[int] = []
    i = 0
    while i < hard.size:
        if hard[i] == 0:
            i += 1
            continue
        j = i
        while j < hard.size and hard[j] == 1:
            j += 1
        runs.append(j - i)
        i = j
    if not runs:
        return ""
    med = float(np.median(runs))
    return "".join("0" if w < med * short_frac else "1" for w in runs)


def _bits_to_bytes(bits: str) -> bytes:
    clean = "".join(c for c in bits if c in "01")
    pad = (-len(clean)) % 8
    if pad:
        clean += "0" * pad
    return bytes(int(clean[i : i + 8], 2) for i in range(0, len(clean), 8) if len(clean[i : i + 8]) == 8)


def _bits_to_hex(bits: str) -> str:
    clean = "".join(c for c in bits if c in "01")
    if not clean:
        return "0x0"
    pad = (-len(clean)) % 4
    if pad:
        clean = "0" * pad + clean
    val = int(clean, 2)
    width = max(2, (len(clean) + 3) // 4)
    return f"0x{val:0{width}X}"


def _hamming(a: str, b: str) -> int:
    n = min(len(a), len(b))
    return sum(1 for i in range(n) if a[i] != b[i]) + abs(len(a) - len(b))


def find_sync_packets(
    bits: str,
    *,
    sync_candidates: list[str] | None = None,
    hamming_tol: int = 1,
    max_payload_bits: int = 256,
) -> list[SyncPacket]:
    """Find alternating preamble then sync; split bursts on sync with Hamming tolerance."""
    clean = "".join(c for c in bits if c in "01")
    candidates = list(sync_candidates or KNOWN_SYNCS)
    # Also auto-extract 16/32-bit words after preamble runs
    packets: list[SyncPacket] = []
    i = 0
    while i < len(clean) - 24:
        # Look for alternating run ≥ 8 bits
        run = 0
        while i + run + 1 < len(clean) and clean[i + run] != clean[i + run + 1]:
            run += 1
        if run < 8:
            i += 1
            continue
        sync_start = i + run + 1
        if sync_start + 16 > len(clean):
            break
        window16 = clean[sync_start : sync_start + 16]
        window32 = clean[sync_start : sync_start + 32] if sync_start + 32 <= len(clean) else ""
        best: tuple[str, int, int] | None = None  # sync, ham, width
        for cand in candidates:
            w = len(cand)
            win = window16 if w <= 16 else window32
            if len(win) < w:
                continue
            ham = _hamming(win[:w], cand)
            if ham <= hamming_tol and (best is None or ham < best[1]):
                best = (cand, ham, w)
        if best is None:
            # Treat following 16 bits as candidate sync
            cand = window16
            best = (cand, 0, 16)
            candidates.append(cand)
        sync_bits, ham, width = best
        payload_start = sync_start + width
        # Payload until next long alternating preamble or max length
        payload_end = min(len(clean), payload_start + max_payload_bits)
        # Truncate at next preamble (≥12 alternating)
        j = payload_start + 8
        while j < payload_end - 12:
            alt = 0
            while j + alt + 1 < payload_end and clean[j + alt] != clean[j + alt + 1]:
                alt += 1
            if alt >= 12:
                payload_end = j
                break
            j += 1
        payload_bits = clean[payload_start:payload_end]
        packets.append(
            SyncPacket(
                bit_offset=sync_start,
                sync_bits=sync_bits,
                sync_hex=_bits_to_hex(sync_bits),
                payload_bits=payload_bits,
                payload_bytes=_bits_to_bytes(payload_bits),
                hamming=ham,
            )
        )
        i = payload_end
    return packets


def classify_columns(rows: list[bytes]) -> list[ColumnClass]:
    if not rows:
        return []
    width = max(len(r) for r in rows)
    padded = [r + bytes(width - len(r)) for r in rows]
    classes: list[ColumnClass] = []
    for col in range(width):
        vals = [r[col] for r in padded]
        uniq = len(set(vals))
        if uniq == 1:
            classes.append("static")
            continue
        # Incrementing?
        diffs = [(vals[i + 1] - vals[i]) % 256 for i in range(len(vals) - 1)]
        if diffs and all(d in (0, 1) for d in diffs) and any(d == 1 for d in diffs):
            classes.append("incrementing")
            continue
        # Entropy proxy
        counts = np.bincount(np.asarray(vals, dtype=np.int64), minlength=256).astype(np.float64)
        p = counts[counts > 0] / len(vals)
        ent = float(-np.sum(p * np.log2(p + 1e-20)))
        classes.append("high_entropy" if ent > 2.5 or uniq >= max(3, len(vals) // 2) else "mixed")
    return classes


def crc8(data: bytes, *, poly: int = 0x07, init: int = 0x00) -> int:
    crc = init & 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc16_ccitt(data: bytes, *, init: int = 0xFFFF) -> int:
    crc = init & 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc16_ibm(data: bytes, *, init: int = 0x0000) -> int:
    crc = init & 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def xor_sum(data: bytes) -> int:
    acc = 0
    for b in data:
        acc ^= b
    return acc & 0xFF


def crc_reveng(frame: bytes, *, payload_len: int | None = None) -> list[dict[str, Any]]:
    """Test standard CRC/checksum polys against trailing bytes."""
    if len(frame) < 2:
        return []
    if payload_len is None:
        # Try payload = all but last 1 or 2 bytes
        candidates_len = [len(frame) - 1, len(frame) - 2]
    else:
        candidates_len = [payload_len]
    out: list[dict[str, Any]] = []
    for plen in candidates_len:
        if plen <= 0 or plen >= len(frame):
            continue
        payload = frame[:plen]
        trailer = frame[plen:]
        checks = [
            ("CRC-8", bytes([crc8(payload)]), trailer[:1]),
            ("CRC-16-CCITT", crc16_ccitt(payload).to_bytes(2, "big"), trailer[:2]),
            ("CRC-16-IBM", crc16_ibm(payload).to_bytes(2, "little"), trailer[:2]),
            ("XOR sum", bytes([xor_sum(payload)]), trailer[:1]),
        ]
        for name, expect, got in checks:
            if not got:
                continue
            ok = expect[: len(got)] == got
            out.append(
                {
                    "name": name,
                    "ok": ok,
                    "payload_len": plen,
                    "expect": expect.hex(),
                    "got": got.hex(),
                }
            )
    return out


def apply_line_code(symbols: np.ndarray, bits_raw: str, line_code: LineCode) -> str:
    if line_code == "NRZ":
        return bits_raw
    if line_code == "NRZ_INV":
        return "".join("1" if b == "0" else "0" for b in bits_raw)
    if line_code == "MANCHESTER_IEEE":
        return decode_manchester(bits_raw, convention="ieee")
    if line_code == "MANCHESTER_THOMAS":
        return decode_manchester(bits_raw, convention="thomas")
    if line_code == "DIFF_MANCHESTER":
        return decode_diff_manchester(bits_raw)
    if line_code == "PWM":
        return decode_pwm(symbols)
    return bits_raw


def demodulate(
    iq: np.ndarray,
    *,
    sample_rate: int,
    modulation: Modulation = "AUTO",
    line_code: LineCode = "NRZ",
    baud_override: float | None = None,
) -> DemodResult:
    base, resolved_mod, note = _choose_baseband(
        iq, sample_rate=sample_rate, modulation=modulation
    )
    baud = float(baud_override) if baud_override and baud_override > 0 else estimate_baud_fft(
        base, sample_rate=sample_rate
    )
    if baud <= 0:
        baud = float(sample_rate) / 16.0
    spb = float(sample_rate) / baud
    symbols = mueller_muller_recover(base, samples_per_symbol=spb)
    # Threshold: for OOK use mid of percentiles; for FM use 0 after centering
    if resolved_mod == "OOK":
        thr = float(np.percentile(symbols, 40) + np.percentile(symbols, 70)) * 0.5
        bits_raw = slice_symbols(symbols, threshold=thr)
    else:
        symbols = symbols - np.mean(symbols)
        bits_raw = slice_symbols(symbols, threshold=0.0)

    bits = apply_line_code(symbols, bits_raw, line_code)
    bits_inv = "".join("1" if b == "0" else "0" for b in bits)
    packets = find_sync_packets(bits)
    if not packets:
        packets = find_sync_packets(bits_inv)

    rows = [p.payload_bytes for p in packets if p.payload_bytes]
    # Align to min length for matrix
    if rows:
        min_len = min(len(r) for r in rows)
        rows = [r[:min_len] for r in rows if len(r) >= min_len]
    col_classes = classify_columns(rows)

    crc_matches: list[dict[str, Any]] = []
    for p in packets[:8]:
        if len(p.payload_bytes) >= 2:
            crc_matches.extend(crc_reveng(p.payload_bytes))

    sync = packets[0].sync_hex if packets else None
    return DemodResult(
        modulation=resolved_mod,
        line_code=line_code,
        sample_rate=sample_rate,
        baud_est=baud,
        samples_per_symbol=spb,
        bits=bits,
        bits_inv=bits_inv,
        sync_word=sync,
        packets=packets,
        column_classes=col_classes,
        crc_matches=[m for m in crc_matches if m.get("ok")],
        notes=f"{note} · baud≈{baud:.1f} · spb≈{spb:.2f}",
    )


def demodulate_file(
    path: Path,
    *,
    sample_rate: int,
    modulation: Modulation = "AUTO",
    line_code: LineCode = "NRZ",
    baud_override: float | None = None,
) -> DemodResult:
    iq = load_cs8(path)
    return demodulate(
        iq,
        sample_rate=sample_rate,
        modulation=modulation,
        line_code=line_code,
        baud_override=baud_override,
    )


def result_to_dict(result: DemodResult) -> dict[str, Any]:
    return {
        "modulation": result.modulation,
        "line_code": result.line_code,
        "sample_rate": result.sample_rate,
        "baud_est": result.baud_est,
        "samples_per_symbol": result.samples_per_symbol,
        "bits": result.bits[:4096],
        "bits_inv": result.bits_inv[:4096],
        "sync_word": result.sync_word,
        "packets": [
            {
                "bit_offset": p.bit_offset,
                "sync_bits": p.sync_bits,
                "sync_hex": p.sync_hex,
                "payload_bits": p.payload_bits[:512],
                "payload_hex": p.payload_bytes.hex(),
                "payload_bytes": list(p.payload_bytes[:64]),
                "hamming": p.hamming,
            }
            for p in result.packets[:64]
        ],
        "column_classes": list(result.column_classes),
        "crc_matches": result.crc_matches[:16],
        "notes": result.notes,
    }
