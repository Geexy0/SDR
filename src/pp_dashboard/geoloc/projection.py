"""WGS84 ↔ local East-North-Up (ENU) tangent-plane projection."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# WGS84 ellipsoid
WGS84_A = 6_378_137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


def _rad(d: float) -> float:
    return math.radians(d)


def _deg(r: float) -> float:
    return math.degrees(r)


def llh_to_ecef(lat_deg: float, lon_deg: float, alt_m: float = 0.0) -> tuple[float, float, float]:
    φ = _rad(lat_deg)
    λ = _rad(lon_deg)
    sinφ, cosφ = math.sin(φ), math.cos(φ)
    sinλ, cosλ = math.sin(λ), math.cos(λ)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sinφ * sinφ)
    x = (n + alt_m) * cosφ * cosλ
    y = (n + alt_m) * cosφ * sinλ
    z = (n * (1.0 - WGS84_E2) + alt_m) * sinφ
    return x, y, z


def ecef_to_llh(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Olson / Bowring iterative ECEF → geodetic."""
    λ = math.atan2(y, x)
    p = math.hypot(x, y)
    φ = math.atan2(z, p * (1.0 - WGS84_E2))
    for _ in range(8):
        sinφ = math.sin(φ)
        n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sinφ * sinφ)
        alt = p / max(1e-15, math.cos(φ)) - n
        φ_new = math.atan2(z, p * (1.0 - WGS84_E2 * n / (n + alt)))
        if abs(φ_new - φ) < 1e-12:
            φ = φ_new
            break
        φ = φ_new
    sinφ = math.sin(φ)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sinφ * sinφ)
    alt = p / max(1e-15, math.cos(φ)) - n
    return _deg(φ), _deg(λ), alt


@dataclass(slots=True)
class LocalTangentPlane:
    """Session-origin ENU frame for metric triangulation / path-loss math."""

    origin_lat: float
    origin_lon: float
    origin_alt: float = 0.0
    _ox: float = field(init=False, repr=False)
    _oy: float = field(init=False, repr=False)
    _oz: float = field(init=False, repr=False)
    _r: tuple[tuple[float, float, float], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        ox, oy, oz = llh_to_ecef(self.origin_lat, self.origin_lon, self.origin_alt)
        object.__setattr__(self, "_ox", ox)
        object.__setattr__(self, "_oy", oy)
        object.__setattr__(self, "_oz", oz)
        φ = _rad(self.origin_lat)
        λ = _rad(self.origin_lon)
        sinφ, cosφ = math.sin(φ), math.cos(φ)
        sinλ, cosλ = math.sin(λ), math.cos(λ)
        # Rows: East, North, Up basis vectors in ECEF
        object.__setattr__(
            self,
            "_r",
            (
                (-sinλ, cosλ, 0.0),
                (-sinφ * cosλ, -sinφ * sinλ, cosφ),
                (cosφ * cosλ, cosφ * sinλ, sinφ),
            ),
        )

    def llh_to_enu(
        self, lat_deg: float, lon_deg: float, alt_m: float = 0.0
    ) -> tuple[float, float, float]:
        x, y, z = llh_to_ecef(lat_deg, lon_deg, alt_m)
        dx, dy, dz = x - self._ox, y - self._oy, z - self._oz
        e = self._r[0][0] * dx + self._r[0][1] * dy + self._r[0][2] * dz
        n = self._r[1][0] * dx + self._r[1][1] * dy + self._r[1][2] * dz
        u = self._r[2][0] * dx + self._r[2][1] * dy + self._r[2][2] * dz
        return e, n, u

    def enu_to_llh(
        self, east_m: float, north_m: float, up_m: float = 0.0
    ) -> tuple[float, float, float]:
        # ECEF = origin + R^T * enu  (R rows are E,N,U)
        dx = self._r[0][0] * east_m + self._r[1][0] * north_m + self._r[2][0] * up_m
        dy = self._r[0][1] * east_m + self._r[1][1] * north_m + self._r[2][1] * up_m
        dz = self._r[0][2] * east_m + self._r[1][2] * north_m + self._r[2][2] * up_m
        return ecef_to_llh(self._ox + dx, self._oy + dy, self._oz + dz)
