"""Spherical geo math — haversine, LOB intersection, CPA, range rings."""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_R_M = 6_371_000.0


@dataclass(slots=True, frozen=True)
class LineOfBearing:
    lat: float
    lon: float
    bearing_deg: float


@dataclass(slots=True, frozen=True)
class GeoPoint:
    lat: float
    lon: float


@dataclass(slots=True, frozen=True)
class TrackState:
    lat: float
    lon: float
    speed_mps: float = 0.0
    course_deg: float = 0.0


@dataclass(slots=True, frozen=True)
class CpaResult:
    time_s: float
    range_m: float
    own_lat: float
    own_lon: float
    tgt_lat: float
    tgt_lon: float


def _rad(d: float) -> float:
    return math.radians(d)


def _deg(r: float) -> float:
    return math.degrees(r)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    φ1, φ2 = _rad(lat1), _rad(lat2)
    dφ = _rad(lat2 - lat1)
    dλ = _rad(lon2 - lon1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def destination_point(
    lat: float, lon: float, *, bearing_deg: float, distance_m: float
) -> GeoPoint:
    δ = distance_m / EARTH_R_M
    θ = _rad(bearing_deg)
    φ1 = _rad(lat)
    λ1 = _rad(lon)
    sinφ1, cosφ1 = math.sin(φ1), math.cos(φ1)
    sinδ, cosδ = math.sin(δ), math.cos(δ)
    φ2 = math.asin(sinφ1 * cosδ + cosφ1 * sinδ * math.cos(θ))
    λ2 = λ1 + math.atan2(math.sin(θ) * sinδ * cosφ1, cosδ - sinφ1 * math.sin(φ2))
    return GeoPoint(lat=_deg(φ2), lon=(_deg(λ2) + 540) % 360 - 180)


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    φ1, φ2 = _rad(lat1), _rad(lat2)
    dλ = _rad(lon2 - lon1)
    y = math.sin(dλ) * math.cos(φ2)
    x = math.cos(φ1) * math.sin(φ2) - math.sin(φ1) * math.cos(φ2) * math.cos(dλ)
    return (_deg(math.atan2(y, x)) + 360.0) % 360.0


def range_ring(
    lat: float, lon: float, *, radius_m: float, n: int = 64
) -> list[tuple[float, float]]:
    n = max(8, int(n))
    pts = [
        destination_point(lat, lon, bearing_deg=i * 360.0 / n, distance_m=radius_m)
        for i in range(n)
    ]
    closed = [(p.lat, p.lon) for p in pts]
    closed.append(closed[0])
    return closed


def _to_local_m(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    """Equirectangular local ENU metres relative to (lat0, lon0)."""
    φ0 = _rad(lat0)
    x = _rad(lon - lon0) * math.cos(φ0) * EARTH_R_M
    y = _rad(lat - lat0) * EARTH_R_M
    return x, y


def _from_local_m(lat0: float, lon0: float, x: float, y: float) -> GeoPoint:
    φ0 = _rad(lat0)
    lat = lat0 + _deg(y / EARTH_R_M)
    lon = lon0 + _deg(x / (EARTH_R_M * max(1e-9, math.cos(φ0))))
    return GeoPoint(lat=lat, lon=(lon + 540) % 360 - 180)


def intersect_lobs(a: LineOfBearing, b: LineOfBearing) -> GeoPoint | None:
    """Intersect two LOBs in a local tangent plane; None if parallel / invalid."""
    ax, ay = 0.0, 0.0
    bx, by = _to_local_m(a.lat, a.lon, b.lat, b.lon)
    aθ = _rad(a.bearing_deg)
    bθ = _rad(b.bearing_deg)
    adx, ady = math.sin(aθ), math.cos(aθ)
    bdx, bdy = math.sin(bθ), math.cos(bθ)
    det = adx * bdy - ady * bdx
    if abs(det) < 1e-9:
        return None
    dx, dy = bx - ax, by - ay
    t = (dx * bdy - dy * bdx) / det
    u = (dx * ady - dy * adx) / det
    if t < 0 or u < 0:
        return None
    if t > 500_000 or u > 500_000:  # >500 km — nonsense for DF walk
        return None
    return _from_local_m(a.lat, a.lon, ax + t * adx, ay + t * ady)


def closest_point_of_approach(
    own: TrackState, target: TrackState, *, horizon_s: float = 3600.0
) -> CpaResult | None:
    """Relative CPA assuming constant velocity in local ENU."""
    ox, oy = 0.0, 0.0
    tx, ty = _to_local_m(own.lat, own.lon, target.lat, target.lon)
    oθ, tθ = _rad(own.course_deg), _rad(target.course_deg)
    ovx = own.speed_mps * math.sin(oθ)
    ovy = own.speed_mps * math.cos(oθ)
    tvx = target.speed_mps * math.sin(tθ)
    tvy = target.speed_mps * math.cos(tθ)
    rx, ry = tx - ox, ty - oy
    vx, vy = tvx - ovx, tvy - ovy
    vv = vx * vx + vy * vy
    if vv < 1e-9:
        # Relative stationary
        rng = math.hypot(rx, ry)
        return CpaResult(
            time_s=0.0,
            range_m=rng,
            own_lat=own.lat,
            own_lon=own.lon,
            tgt_lat=target.lat,
            tgt_lon=target.lon,
        )
    t_star = -((rx * vx + ry * vy) / vv)
    t_star = max(0.0, min(float(horizon_s), t_star))
    ox2, oy2 = ox + ovx * t_star, oy + ovy * t_star
    tx2, ty2 = tx + tvx * t_star, ty + tvy * t_star
    rng = math.hypot(tx2 - ox2, ty2 - oy2)
    own_p = _from_local_m(own.lat, own.lon, ox2, oy2)
    tgt_p = _from_local_m(own.lat, own.lon, tx2, ty2)
    return CpaResult(
        time_s=t_star,
        range_m=rng,
        own_lat=own_p.lat,
        own_lon=own_p.lon,
        tgt_lat=tgt_p.lat,
        tgt_lon=tgt_p.lon,
    )


def lob_ray_points(
    lat: float,
    lon: float,
    bearing_deg: float,
    *,
    length_m: float = 25_000.0,
    n: int = 2,
) -> list[tuple[float, float]]:
    """Polyline from origin along bearing (for Leaflet)."""
    if n <= 1:
        end = destination_point(lat, lon, bearing_deg=bearing_deg, distance_m=length_m)
        return [(lat, lon), (end.lat, end.lon)]
    pts = [(lat, lon)]
    for i in range(1, n + 1):
        p = destination_point(
            lat, lon, bearing_deg=bearing_deg, distance_m=length_m * i / n
        )
        pts.append((p.lat, p.lon))
    return pts
