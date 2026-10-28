"""Parse ADS-B (SBS) and AIS (AIVDM) lines into map track dicts."""

from __future__ import annotations

import json
import math
import re
from typing import Any


_SBS_RE = re.compile(r"^MSG,")
_AIVDM_RE = re.compile(r"^!AIVD[MO]")


def parse_sbs_line(line: str) -> dict[str, Any] | None:
    """BaseStation SBS-1 CSV → track metrics (lat/lon when present)."""
    if not _SBS_RE.match(line):
        return None
    parts = line.split(",")
    if len(parts) < 17:
        return None
    icao = (parts[4] or "").strip().upper()
    if not icao:
        return None
    callsign = (parts[10] or "").strip() or None
    alt = _num(parts[11])
    lat = _num(parts[14])
    lon = _num(parts[15])
    out: dict[str, Any] = {"type": "adsb", "icao": icao, "source": "LOCAL_RF"}
    if callsign:
        out["callsign"] = callsign
    if alt is not None:
        out["alt"] = alt
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
        out["lat"] = lat
        out["lon"] = lon
    return out


def parse_adsb_json(line: str) -> dict[str, Any] | None:
    if not (line.startswith("{") and line.endswith("}")):
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    icao = str(data.get("hex") or data.get("icao") or data.get("icao24") or "").upper()
    lat = _num(data.get("lat") if "lat" in data else data.get("latitude"))
    lon = _num(data.get("lon") if "lon" in data else data.get("longitude"))
    call = data.get("flight") or data.get("callsign") or data.get("r")
    out: dict[str, Any] = {"type": "adsb", **data}
    if icao:
        out["icao"] = icao
    if call:
        out["callsign"] = str(call).strip()
    if lat is not None and lon is not None:
        out["lat"] = lat
        out["lon"] = lon
    return out


def parse_ais_nmea(line: str) -> dict[str, Any] | None:
    """Decode single-fragment AIVDM type 1/2/3 position reports."""
    raw = line.strip()
    if not raw:
        return None
    if "MMSI" in raw:
        mm = re.search(r"MMSI[=:\s]+(\d{9})", raw)
        lat_m = re.search(r"lat[=:\s]+(-?\d+(?:\.\d+)?)", raw, re.I)
        lon_m = re.search(r"lon[=:\s]+(-?\d+(?:\.\d+)?)", raw, re.I)
        if mm:
            out: dict[str, Any] = {"type": "ais", "mmsi": mm.group(1), "raw": raw}
            if lat_m and lon_m:
                out["lat"] = float(lat_m.group(1))
                out["lon"] = float(lon_m.group(1))
            return out
    if not _AIVDM_RE.match(raw):
        return None
    try:
        body = raw.split("*", 1)[0]
        fields = body.split(",")
        # !AIVDM,frag_count,frag_num,seq,channel,payload,fill
        if len(fields) < 7:
            return None
        if int(fields[1]) != 1 or int(fields[2]) != 1:
            return None  # multi-fragment not handled
        payload = fields[5]
        fill = int(fields[6] or "0")
        bits = _payload_to_bits(payload, fill)
        if len(bits) < 137:
            return None
        msg_type = _bits_uint(bits, 0, 6)
        if msg_type not in (1, 2, 3):
            return {"type": "ais", "msg_type": msg_type, "raw": raw}
        mmsi = f"{_bits_uint(bits, 8, 30):09d}"
        lon_raw = _bits_int(bits, 61, 28)
        lat_raw = _bits_int(bits, 89, 27)
        if lon_raw == 0x6791AC0 or lat_raw == 0x3412140:
            return {"type": "ais", "mmsi": mmsi, "msg_type": msg_type, "raw": raw}
        lon = lon_raw / 600000.0
        lat = lat_raw / 600000.0
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return {"type": "ais", "mmsi": mmsi, "msg_type": msg_type, "raw": raw}
        sog = _bits_uint(bits, 50, 10)
        cog = _bits_uint(bits, 116, 12)
        return {
            "type": "ais",
            "mmsi": mmsi,
            "msg_type": msg_type,
            "lat": lat,
            "lon": lon,
            "sog": None if sog == 1023 else sog / 10.0,
            "cog": None if cog == 3600 else cog / 10.0,
            "name": mmsi,
            "raw": raw,
        }
    except (ValueError, IndexError):
        return None


def _num(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _payload_to_bits(payload: str, fill_bits: int) -> str:
    bits: list[str] = []
    for ch in payload:
        v = ord(ch) - 48
        if v > 40:
            v -= 8
        bits.append(f"{v & 0x3F:06b}")
    joined = "".join(bits)
    if fill_bits > 0:
        joined = joined[:-fill_bits]
    return joined


def _bits_uint(bits: str, start: int, width: int) -> int:
    return int(bits[start : start + width], 2)


def _bits_int(bits: str, start: int, width: int) -> int:
    u = _bits_uint(bits, start, width)
    sign = 1 << (width - 1)
    if u & sign:
        u -= 1 << width
    return u
