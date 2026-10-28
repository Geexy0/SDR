"""Lightweight Mode-S / ADS-B soft decoder for HackRF CS8 @ 2 MSPS.

Used when dump1090/readsb is not available. Extracts DF11/DF17 identity
frames with CRC-24; CPR lat/lon decoding is best-effort when odd/even pair.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# Mode-S CRC-24 generator (ICAO Annex 10 / dump1090). Alternate 32-bit form
# 0xFFFA0480 is equivalent when shifted; we use the compact 24-bit poly.
MODE_S_CRC_POLY = 0xFFF409


def crc24_modes(data: bytes) -> int:
    """Compute Mode-S CRC-24 over message bytes (excluding CRC field)."""
    crc = 0
    for byte in data:
        crc ^= int(byte) << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= MODE_S_CRC_POLY
    return crc & 0xFFFFFF


def verify_frame_crc(frame: bytes) -> bool:
    if len(frame) not in (7, 14):
        return False
    body, got = frame[:-3], int.from_bytes(frame[-3:], "big")
    return crc24_modes(body) == got


def icao_from_frame(frame: bytes) -> str | None:
    if len(frame) < 4:
        return None
    return f"{frame[1]:02X}{frame[2]:02X}{frame[3]:02X}"


def decode_df17_identity(frame: bytes) -> dict[str, Any] | None:
    if len(frame) < 14 or not verify_frame_crc(frame):
        return None
    df = frame[0] >> 3
    if df not in (11, 17, 18):
        return None
    icao = icao_from_frame(frame)
    if not icao:
        return None
    out: dict[str, Any] = {
        "type": "adsb",
        "df": df,
        "icao": icao,
        "source": "LOCAL_RF",
        "raw_hex": frame.hex().upper(),
    }
    if df == 17 and len(frame) >= 11:
        tc = (frame[4] >> 3) & 0x1F
        out["tc"] = tc
        if 1 <= tc <= 4:
            # Aircraft identification
            out["callsign"] = _decode_callsign(frame[5:11])
        elif 9 <= tc <= 18 or 20 <= tc <= 22:
            out["cpr_me"] = (frame[6] >> 2) & 1
            out["cpr_lat"] = ((frame[6] & 3) << 15) | (frame[7] << 7) | (frame[8] >> 1)
            out["cpr_lon"] = ((frame[8] & 1) << 16) | (frame[9] << 8) | frame[10]
            alt_code = (frame[5] << 4) | (frame[6] >> 4)
            out["alt"] = _decode_ac12(alt_code)
    return out


def _decode_callsign(six: bytes) -> str:
    charset = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ#####_###############0123456789######"
    bits = 0
    for b in six:
        bits = (bits << 8) | b
    chars: list[str] = []
    for i in range(8):
        shift = 42 - 6 * i
        idx = (bits >> shift) & 0x3F
        ch = charset[idx] if idx < len(charset) else "#"
        if ch not in "#_":
            chars.append(ch)
    return "".join(chars).strip() or ""


def _decode_ac12(ac12: int) -> float | None:
    """Rough Mode-S AC12 altitude (feet) — Gillham / metric simplified."""
    if ac12 == 0:
        return None
    # Q-bit
    if ac12 & 0x10:
        n = ((ac12 & 0x0FE0) >> 1) | (ac12 & 0x0F)
        return float(n * 25 - 1000)
    return None


def _mag_cs8(iq: np.ndarray) -> np.ndarray:
    z = np.asarray(iq, dtype=np.complex64).ravel()
    return np.abs(z).astype(np.float32)


def _preamble_score(mag: np.ndarray, i: int) -> float:
    """dump1090-style preamble quality at sample i (@ 2 Msps)."""
    # Expected high: 0,2,7,9 ; low: 1,3,4,5,6,8,10..15ish
    highs = (0, 2, 7, 9)
    lows = (1, 3, 4, 5, 6, 8)
    if i + 16 >= mag.size:
        return -1.0
    h = sum(float(mag[i + o]) for o in highs) / 4.0
    l = sum(float(mag[i + o]) for o in lows) / 6.0
    if h < l * 1.3:
        return -1.0
    return h - l


def _demod_bits(mag: np.ndarray, start: int, n_bits: int) -> bytes | None:
    """Sample Mode-S PPM bits after 16-sample preamble @ 2 Msps (2 samples/bit)."""
    bits: list[int] = []
    # First data bit starts at preamble_end = start+16
    base = start + 16
    for b in range(n_bits):
        i0 = base + 2 * b
        i1 = i0 + 1
        if i1 >= mag.size:
            return None
        bits.append(1 if mag[i0] > mag[i1] else 0)
    # Pack MSB first
    out = bytearray((n_bits + 7) // 8)
    for i, bit in enumerate(bits):
        if bit:
            out[i // 8] |= 0x80 >> (i % 8)
    return bytes(out)


def extract_frames_from_cs8(
    iq: np.ndarray,
    *,
    sample_rate: int = 2_000_000,
    max_frames: int = 64,
) -> list[dict[str, Any]]:
    """Scan magnitude for Mode-S preambles; return decoded identity-capable frames."""
    if sample_rate != 2_000_000:
        # Current soft demod assumes 2 samples/bit
        log.debug("modes_s: non-2Msps sample rate %s — skipping soft decode", sample_rate)
        return []
    mag = _mag_cs8(iq)
    if mag.size < 512:
        return []
    thr = float(np.percentile(mag, 85))
    out: list[dict[str, Any]] = []
    i = 0
    step = 1
    while i < mag.size - 300 and len(out) < max_frames:
        if mag[i] < thr:
            i += 1
            continue
        score = _preamble_score(mag, i)
        if score < 0:
            i += 1
            continue
        # Try long then short frame
        for nbits, flen in ((112, 14), (56, 7)):
            raw = _demod_bits(mag, i, nbits)
            if raw is None or len(raw) < flen:
                continue
            frame = raw[:flen]
            if not verify_frame_crc(frame):
                continue
            decoded = decode_df17_identity(frame)
            if decoded is None:
                # Still emit ICAO if DF11
                icao = icao_from_frame(frame)
                if icao:
                    decoded = {
                        "type": "adsb",
                        "df": frame[0] >> 3,
                        "icao": icao,
                        "source": "LOCAL_RF",
                        "raw_hex": frame.hex().upper(),
                    }
            if decoded:
                out.append(decoded)
                break
        i += 16  # skip ahead past preamble
        _ = step
    return out


class CprCache:
    """Pair odd/even CPR airborne positions (NL=15 global decode)."""

    def __init__(self) -> None:
        self._even: dict[str, tuple[int, int]] = {}
        self._odd: dict[str, tuple[int, int]] = {}

    def update(self, track: dict[str, Any]) -> dict[str, Any]:
        icao = str(track.get("icao") or "")
        if "cpr_lat" not in track or "cpr_lon" not in track:
            return track
        me = int(track.get("cpr_me") or 0)
        lat_cpr = int(track["cpr_lat"])
        lon_cpr = int(track["cpr_lon"])
        if me == 0:
            self._even[icao] = (lat_cpr, lon_cpr)
        else:
            self._odd[icao] = (lat_cpr, lon_cpr)
        if icao not in self._even or icao not in self._odd:
            return track
        try:
            lat, lon = _cpr_airborne(self._even[icao], self._odd[icao])
        except Exception:
            return track
        track = {**track, "lat": lat, "lon": lon}
        return track


def _cpr_airborne(
    even: tuple[int, int], odd: tuple[int, int]
) -> tuple[float, float]:
    """Global CPR decode (airborne, NZ=15) — compact implementation."""
    lat_cpr_even, lon_cpr_even = even
    lat_cpr_odd, lon_cpr_odd = odd
    air_dlat0 = 360.0 / 60.0
    air_dlat1 = 360.0 / 59.0
    j = math.floor(
        59 * lat_cpr_even / 131072.0 - 60 * lat_cpr_odd / 131072.0 + 0.5
    )
    lat_even = air_dlat0 * ((j % 60) + lat_cpr_even / 131072.0)
    lat_odd = air_dlat1 * ((j % 59) + lat_cpr_odd / 131072.0)
    if lat_even >= 270:
        lat_even -= 360
    if lat_odd >= 270:
        lat_odd -= 360
    # Use even latitude for NL
    lat = lat_even
    nl = _cpr_nl(lat)
    if nl == 0:
        raise ValueError("nl0")
    ni = max(nl - 0, 1)
    m = math.floor(
        lon_cpr_even * (nl - 1) / 131072.0 - lon_cpr_odd * nl / 131072.0 + 0.5
    )
    dlon = 360.0 / ni
    lon = dlon * ((m % ni) + lon_cpr_even / 131072.0)
    if lon >= 180:
        lon -= 360
    return lat, lon


def _cpr_nl(lat: float) -> int:
    """Number of longitude zones (lookup)."""
    if abs(lat) >= 87:
        return 1 if abs(lat) < 87 else 1
    table = (
        (87.0, 1),
        (86.535, 2),
        (85.5, 3),
        (84.0, 4),
        (82.0, 5),
        (80.0, 6),
        (75.0, 7),
        (70.0, 8),
        (65.0, 9),
        (60.0, 10),
        (55.0, 11),
        (50.0, 12),
        (45.0, 13),
        (40.0, 14),
        (35.0, 15),
        (30.0, 16),
        (27.0, 17),
        (24.0, 18),
        (21.0, 19),
        (18.0, 20),
        (15.0, 21),
        (12.0, 22),
        (9.0, 23),
        (6.0, 24),
        (3.0, 25),
        (0.0, 26),
    )
    a = abs(lat)
    for lim, nl in table:
        if a >= lim:
            return nl
    return 1
