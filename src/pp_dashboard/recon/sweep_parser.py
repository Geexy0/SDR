"""Parse hackrf_sweep ``-B`` binary records."""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

# record_length (u32) covers the following bytes only (not itself):
# lo_hz u64 + hi_hz u64 + float32 bins...
_HDR = struct.Struct("<IQQ")  # len, lo, hi — but len is separate first


@dataclass(slots=True)
class SweepRecord:
    lo_hz: int
    hi_hz: int
    power_db: np.ndarray  # float32


class SweepBinaryParser:
    """Incremental parser for little-endian hackrf_sweep -B stream."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[SweepRecord]:
        self._buf.extend(data)
        out: list[SweepRecord] = []
        while True:
            if len(self._buf) < 4:
                break
            rec_len = struct.unpack_from("<I", self._buf, 0)[0]
            total = 4 + rec_len
            if rec_len < 16 or len(self._buf) < total:
                break
            lo = struct.unpack_from("<Q", self._buf, 4)[0]
            hi = struct.unpack_from("<Q", self._buf, 12)[0]
            n_floats = (rec_len - 16) // 4
            if n_floats < 1:
                del self._buf[:total]
                continue
            powers = np.frombuffer(
                self._buf, dtype="<f4", count=n_floats, offset=20
            ).copy()
            out.append(SweepRecord(lo_hz=int(lo), hi_hz=int(hi), power_db=powers))
            del self._buf[:total]
        return out


def powers_to_uint8(powers: np.ndarray, db_min: float = -110.0, db_max: float = -20.0) -> bytes:
    """Compress dB floats to Uint8 for websocket bandwidth."""
    span = max(db_max - db_min, 1.0)
    scaled = (powers.astype(np.float32) - db_min) * (255.0 / span)
    return np.clip(np.rint(scaled), 0, 255).astype(np.uint8).tobytes()


def assemble_row(
    records: list[SweepRecord],
    *,
    start_hz: int,
    stop_hz: int,
    bin_hz: float,
) -> tuple[np.ndarray, int]:
    """Merge unordered 5 MHz blocks into a contiguous power row for [start, stop)."""
    if bin_hz <= 0:
        raise ValueError("bin_hz must be > 0")
    n = max(1, int(np.ceil((stop_hz - start_hz) / bin_hz)))
    row = np.full(n, -120.0, dtype=np.float32)
    counts = np.zeros(n, dtype=np.int32)
    for rec in records:
        if rec.power_db.size == 0:
            continue
        step = (rec.hi_hz - rec.lo_hz) / max(rec.power_db.size, 1)
        for i, p in enumerate(rec.power_db):
            f = rec.lo_hz + i * step
            if f < start_hz or f >= stop_hz:
                continue
            idx = int((f - start_hz) / bin_hz)
            if 0 <= idx < n:
                row[idx] += float(p)
                counts[idx] += 1
    mask = counts > 0
    row[mask] /= counts[mask]
    return row, n
