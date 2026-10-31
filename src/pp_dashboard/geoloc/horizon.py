"""4/3-Earth radio horizon, slant range, look-angles, FSPL."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from pp_dashboard.geoloc.projection import LocalTangentPlane, llh_to_ecef


def radio_horizon_km(*, h_rx_m: float, h_tx_m: float) -> float:
    """Theoretical radio horizon (km) with 4/3-Earth: 4.12(√h_rx + √h_tx), h in metres."""
    hr = max(0.0, float(h_rx_m))
    ht = max(0.0, float(h_tx_m))
    return 4.12 * (math.sqrt(hr) + math.sqrt(ht))


def fspl_db(*, slant_range_m: float, frequency_hz: float) -> float:
    """Free-space path loss: 20log10(R) + 20log10(f) − 147.55 (R metres, f Hz)."""
    r = max(1.0, float(slant_range_m))
    f = max(1.0, float(frequency_hz))
    return 20.0 * math.log10(r) + 20.0 * math.log10(f) - 147.55


@dataclass(slots=True)
class LookAngle:
    azimuth_deg: float
    elevation_deg: float
    slant_range_m: float
    ground_range_m: float
    fspl_db: float
    horizon_km: float
    beyond_horizon: bool
    frequency_hz: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "azimuth_deg": self.azimuth_deg,
            "elevation_deg": self.elevation_deg,
            "slant_range_m": self.slant_range_m,
            "ground_range_m": self.ground_range_m,
            "fspl_db": self.fspl_db,
            "horizon_km": self.horizon_km,
            "beyond_horizon": self.beyond_horizon,
            "frequency_hz": self.frequency_hz,
        }


def look_angle(
    *,
    rx_lat: float,
    rx_lon: float,
    rx_alt_m: float,
    tx_lat: float,
    tx_lon: float,
    tx_alt_m: float,
    frequency_hz: float,
) -> LookAngle:
    """Az/El/slant/FSPL from operator GPS to a target with known altitude."""
    rx = llh_to_ecef(rx_lat, rx_lon, rx_alt_m)
    tx = llh_to_ecef(tx_lat, tx_lon, tx_alt_m)
    dx, dy, dz = tx[0] - rx[0], tx[1] - rx[1], tx[2] - rx[2]
    slant = math.sqrt(dx * dx + dy * dy + dz * dz)

    frame = LocalTangentPlane(rx_lat, rx_lon, rx_alt_m)
    east, north, up = frame.llh_to_enu(tx_lat, tx_lon, tx_alt_m)
    ground = math.hypot(east, north)
    az = (math.degrees(math.atan2(east, north)) + 360.0) % 360.0
    el = math.degrees(math.atan2(up, max(1.0, ground)))

    horiz_km = radio_horizon_km(h_rx_m=rx_alt_m, h_tx_m=tx_alt_m)
    beyond = (ground / 1000.0) > horiz_km
    loss = fspl_db(slant_range_m=slant, frequency_hz=frequency_hz)
    return LookAngle(
        azimuth_deg=az,
        elevation_deg=el,
        slant_range_m=slant,
        ground_range_m=ground,
        fspl_db=loss,
        horizon_km=horiz_km,
        beyond_horizon=beyond,
        frequency_hz=float(frequency_hz),
    )
