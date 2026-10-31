"""360° polar antenna sweep — circular centroid of main lobe."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PolarSample:
    bearing_deg: float
    rssi_db: float


@dataclass(slots=True)
class PolarPeak:
    peak_bearing_deg: float
    half_power_beamwidth_deg: float
    peak_rssi_db: float
    median_rssi_db: float
    n_samples: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_bearing_deg": self.peak_bearing_deg,
            "half_power_beamwidth_deg": self.half_power_beamwidth_deg,
            "angular_std_deg": max(1.0, self.half_power_beamwidth_deg / 2.355),
            "peak_rssi_db": self.peak_rssi_db,
            "median_rssi_db": self.median_rssi_db,
            "n_samples": self.n_samples,
        }


def circular_lobe_centroid(samples: list[PolarSample]) -> PolarPeak:
    if len(samples) < 3:
        raise ValueError("need >=3 polar samples")
    rssis = [float(s.rssi_db) for s in samples]
    median = float(sorted(rssis)[len(rssis) // 2])
    sx = sy = 0.0
    peak_rssi = max(rssis)
    for s in samples:
        w = max(0.0, float(s.rssi_db) - median) ** 2
        th = math.radians(float(s.bearing_deg) % 360.0)
        sx += w * math.sin(th)
        sy += w * math.cos(th)
    if abs(sx) < 1e-18 and abs(sy) < 1e-18:
        # Fallback: argmax RSSI
        best = max(samples, key=lambda s: s.rssi_db)
        peak_bearing = float(best.bearing_deg) % 360.0
    else:
        peak_bearing = (math.degrees(math.atan2(sx, sy)) + 360.0) % 360.0

    # Half-power beamwidth: contiguous span around peak where RSSI >= peak-3dB
    half = peak_rssi - 3.0
    # Sample densest by sorting bearings relative to peak
    rel = []
    for s in samples:
        d = ((float(s.bearing_deg) - peak_bearing + 540.0) % 360.0) - 180.0
        rel.append((d, float(s.rssi_db)))
    rel.sort(key=lambda x: x[0])
    # Find leftmost/rightmost above half within ±180
    above = [d for d, r in rel if r >= half]
    if len(above) < 2:
        hpbw = 30.0
    else:
        hpbw = max(5.0, min(180.0, above[-1] - above[0]))

    return PolarPeak(
        peak_bearing_deg=peak_bearing,
        half_power_beamwidth_deg=hpbw,
        peak_rssi_db=peak_rssi,
        median_rssi_db=median,
        n_samples=len(samples),
    )
