"""Two-pass Stansfield WLS multi-LOB solver + RSSI path-loss grid ML estimator."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pp_dashboard.geoloc.projection import LocalTangentPlane

CHI2_2_95 = 5.991  # χ²_{2, 0.95}
GDOP_DET_MIN = 1e-6
MIN_RANGE_M = 10.0
GRID_N_DEFAULT = 80
PAD_M_DEFAULT = 250.0
MIN_BREADCRUMB_SPREAD_M = 1.0


class DegenerateGeometryError(ValueError):
    """Parallel / divergent LOBs or singular AtᵀWA (GDOP failure)."""


class InsufficientBreadcrumbsError(ValueError):
    """Too few or spatially co-located RSSI breadcrumbs."""


@dataclass(slots=True)
class BearingObservation:
    lat: float
    lon: float
    bearing_deg: float
    angular_std_deg: float = 3.0
    rssi_db: float | None = None
    timestamp: str | None = None


@dataclass(slots=True)
class RssiObservation:
    lat: float
    lon: float
    rssi_db: float
    timestamp: str | None = None


@dataclass(slots=True)
class CepEllipse:
    semi_major_m: float
    semi_minor_m: float
    orientation_deg: float
    polygon: list[tuple[float, float]]  # closed lat/lon ring


@dataclass(slots=True)
class StansfieldResult:
    ok: bool
    gdop_rejected: bool = False
    lat: float | None = None
    lon: float | None = None
    east_m: float | None = None
    north_m: float | None = None
    covariance: list[list[float]] | None = None
    cep95: CepEllipse | None = None
    n_obs: int = 0
    message: str = ""

    residuals_deg: list[float] = field(default_factory=list)
    gdop: float | None = None

    def to_dict(self) -> dict[str, Any]:
        cep_blob = None
        geojson_polygon = None
        if self.cep95 is not None:
            cep_blob = {
                "semi_major_m": self.cep95.semi_major_m,
                "semi_minor_m": self.cep95.semi_minor_m,
                "orientation_deg": self.cep95.orientation_deg,
                "polygon": [[la, lo] for la, lo in self.cep95.polygon],
            }
            # GeoJSON ring order is [lon, lat]
            geojson_polygon = [[lo, la] for la, lo in self.cep95.polygon]
        out: dict[str, Any] = {
            "ok": self.ok,
            "gdop_rejected": self.gdop_rejected,
            "n_obs": self.n_obs,
            "message": self.message,
            "lat": self.lat,
            "lon": self.lon,
            "estimated_lat": self.lat,
            "estimated_lon": self.lon,
            "east_m": self.east_m,
            "north_m": self.north_m,
            "covariance": self.covariance,
            "cep95": cep_blob,
            "cep95_ellipse": cep_blob,
            "geojson_polygon": geojson_polygon,
            "gdop": self.gdop,
            "residuals": self.residuals_deg,
            "residuals_deg": self.residuals_deg,
        }
        return out


@dataclass(slots=True)
class RssiGridResult:
    ok: bool
    lat: float | None = None
    lon: float | None = None
    east_m: float | None = None
    north_m: float | None = None
    p0_db: float | None = None
    gamma: float = 2.8
    likelihood_peak: float = 0.0
    contour_80: list[tuple[float, float]] = field(default_factory=list)
    contour_95: list[tuple[float, float]] = field(default_factory=list)
    path_loss_curve: list[dict[str, float]] = field(default_factory=list)
    grid_extent: dict[str, float] | None = None
    message: str = ""

    likelihood_min: float = 0.0
    residual_rmse_db: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "lat": self.lat,
            "lon": self.lon,
            "estimated_lat": self.lat,
            "estimated_lon": self.lon,
            "east_m": self.east_m,
            "north_m": self.north_m,
            "p0_db": self.p0_db,
            "gamma": self.gamma,
            "likelihood_peak": self.likelihood_peak,
            "likelihood_min": self.likelihood_min,
            "residual_rmse_db": self.residual_rmse_db,
            "contour_80": [[la, lo] for la, lo in self.contour_80],
            "contour_95": [[la, lo] for la, lo in self.contour_95],
            "path_loss_curve": self.path_loss_curve,
            "residuals": self.path_loss_curve,
            "grid_extent": self.grid_extent,
            "message": self.message,
        }


def _snr_linear(rssi_db: float | None) -> float:
    if rssi_db is None:
        return 1.0
    # Map RSSI (−120…0) to a positive relative SNR weight
    return float(10.0 ** ((float(rssi_db) + 60.0) / 20.0))


def _build_ab(
    sensors_en: np.ndarray, bearings_rad: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Stansfield linear system: A p = b with p=[e,n]."""
    # Unit along LOB: (sin θ, cos θ); normal n=(cos θ, -sin θ)
    # n · p = n · s
    cos_t = np.cos(bearings_rad)
    sin_t = np.sin(bearings_rad)
    a = np.column_stack([cos_t, -sin_t])
    b = cos_t * sensors_en[:, 0] - sin_t * sensors_en[:, 1]
    return a, b


def _wls_solve(a: np.ndarray, b: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Return p_hat, cov, det(AtWA).

    Absolute det gates belong at the call site (pass-1 geometry). Here we only
    fail when AtᵀWA is numerically non-invertible — pass-2 weights shrink with
    σ²·d² and must not be confused with parallel LOBs.
    """
    w_sqrt = np.sqrt(np.clip(w, 1e-18, None))
    aw = a * w_sqrt[:, None]
    bw = b * w_sqrt
    ata = aw.T @ aw
    det = float(np.linalg.det(ata))
    try:
        cov = np.linalg.inv(ata)
        p = cov @ (aw.T @ bw)
    except np.linalg.LinAlgError:
        return np.zeros(2), np.eye(2) * 1e9, det
    if not np.isfinite(p).all() or not np.isfinite(cov).all():
        return np.zeros(2), np.eye(2) * 1e9, det
    return p, cov, det


def _cep95_ellipse(
    frame: LocalTangentPlane, east: float, north: float, cov: np.ndarray
) -> CepEllipse:
    # Symmetrize
    c = 0.5 * (cov + cov.T)
    eigvals, eigvecs = np.linalg.eigh(c)
    order = np.argsort(eigvals)[::-1]
    λ1 = max(float(eigvals[order[0]]), 1e-6)
    λ2 = max(float(eigvals[order[1]]), 1e-6)
    v1 = eigvecs[:, order[0]]
    semi_major = math.sqrt(CHI2_2_95 * λ1)
    semi_minor = math.sqrt(CHI2_2_95 * λ2)
    # Orientation: angle of major axis from East toward North → convert to bearing from North
    # v1 is in ENU (east, north); bearing from north CW = atan2(east, north)
    orientation_deg = (math.degrees(math.atan2(float(v1[0]), float(v1[1]))) + 360.0) % 360.0
    # Build ellipse polygon in ENU then project
    pts: list[tuple[float, float]] = []
    for i in range(64):
        ang = 2.0 * math.pi * i / 64.0
        # local ellipse coords along eigenvectors
        local = semi_major * math.cos(ang) * v1 + semi_minor * math.sin(ang) * eigvecs[:, order[1]]
        la, lo, _ = frame.enu_to_llh(east + float(local[0]), north + float(local[1]), 0.0)
        pts.append((la, lo))
    pts.append(pts[0])
    return CepEllipse(
        semi_major_m=semi_major,
        semi_minor_m=semi_minor,
        orientation_deg=orientation_deg,
        polygon=pts,
    )


def solve_stansfield_wls(
    observations: list[BearingObservation],
    *,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
) -> StansfieldResult:
    if len(observations) < 2:
        return StansfieldResult(
            ok=False, n_obs=len(observations), message="need M >= 2 bearings"
        )
    o_lat = origin_lat if origin_lat is not None else float(np.mean([o.lat for o in observations]))
    o_lon = origin_lon if origin_lon is not None else float(np.mean([o.lon for o in observations]))
    frame = LocalTangentPlane(o_lat, o_lon)

    sensors = np.zeros((len(observations), 2), dtype=np.float64)
    bearings = np.zeros(len(observations), dtype=np.float64)
    sigma = np.zeros(len(observations), dtype=np.float64)
    snr = np.zeros(len(observations), dtype=np.float64)
    for i, o in enumerate(observations):
        e, n, _ = frame.llh_to_enu(o.lat, o.lon, 0.0)
        sensors[i] = (e, n)
        bearings[i] = math.radians(float(o.bearing_deg) % 360.0)
        sigma[i] = max(math.radians(max(float(o.angular_std_deg), 0.1)), 1e-6)
        snr[i] = _snr_linear(o.rssi_db)

    a, b = _build_ab(sensors, bearings)
    # Pass 1: w = 1 / σ_θ²
    w1 = 1.0 / (sigma**2)
    p1, _cov1, det1 = _wls_solve(a, b, w1)
    if abs(det1) < GDOP_DET_MIN:
        raise DegenerateGeometryError(
            "GDOP reject: degenerate geometry — nearly parallel / divergent bearings "
            f"(det AtWA={det1:.3e} < {GDOP_DET_MIN})"
        )

    # Pass 2: relative SNR × 1/(d̂² σ_θ²) — SNR is mean-normalized so equal-RSSI
    # geometries keep geometric CEP scale (σ_xy ≈ d·σ_θ).
    d_hat = np.hypot(p1[0] - sensors[:, 0], p1[1] - sensors[:, 1])
    d_hat = np.maximum(d_hat, MIN_RANGE_M)
    snr_mean = float(np.mean(snr)) if snr.size else 1.0
    snr_rel = snr / max(snr_mean, 1e-12)
    w2 = snr_rel / ((d_hat**2) * (sigma**2))
    # Geometry already validated in pass-1. Pass-2 weights shrink with σ²·d², so
    # absolute det(AtWA) is not a stable GDOP gate — only reject non-finite solves.
    p2, cov2, _det2 = _wls_solve(a, b, w2)
    if not np.isfinite(p2).all() or not np.isfinite(cov2).all():
        raise DegenerateGeometryError(
            "GDOP reject after pass-2 reweight: non-finite solution (degenerate geometry)"
        )
    if float(np.linalg.cond(cov2)) > 1e12:
        raise DegenerateGeometryError(
            "GDOP reject after pass-2 reweight: ill-conditioned covariance"
        )

    lat, lon, _ = frame.enu_to_llh(float(p2[0]), float(p2[1]), 0.0)
    cep = _cep95_ellipse(frame, float(p2[0]), float(p2[1]), cov2)
    # Bearing residuals (signed, shortest arc)
    residuals: list[float] = []
    for i, o in enumerate(observations):
        de = float(p2[0]) - float(sensors[i, 0])
        dn = float(p2[1]) - float(sensors[i, 1])
        pred = (math.degrees(math.atan2(de, dn)) + 360.0) % 360.0
        err = ((pred - float(o.bearing_deg) + 180.0) % 360.0) - 180.0
        residuals.append(float(err))
    gdop = float(math.sqrt(max(0.0, float(cov2[0, 0]) + float(cov2[1, 1]))))
    return StansfieldResult(
        ok=True,
        gdop_rejected=False,
        lat=lat,
        lon=lon,
        east_m=float(p2[0]),
        north_m=float(p2[1]),
        covariance=[[float(cov2[0, 0]), float(cov2[0, 1])], [float(cov2[1, 0]), float(cov2[1, 1])]],
        cep95=cep,
        n_obs=len(observations),
        message="stansfield_wls_ok",
        residuals_deg=residuals,
        gdop=gdop,
    )


def _marching_squares_contour(
    xs: np.ndarray,
    ys: np.ndarray,
    z: np.ndarray,
    level: float,
    frame: LocalTangentPlane,
) -> list[tuple[float, float]]:
    """Extract a single closed-ish contour ring at ``level`` via marching squares."""
    # Collect segment midpoints as a polyline approximation of the level set hull
    ny, nx = z.shape
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []

    def interp(p0: float, p1: float, v0: float, v1: float) -> float:
        if abs(v1 - v0) < 1e-15:
            return 0.5
        return (level - v0) / (v1 - v0)

    for j in range(ny - 1):
        for i in range(nx - 1):
            v = (z[j, i], z[j, i + 1], z[j + 1, i + 1], z[j + 1, i])
            idx = sum(1 << k for k, val in enumerate(v) if val >= level)
            if idx in (0, 15):
                continue
            # Cell corners in ENU
            x0, x1 = float(xs[i]), float(xs[i + 1])
            y0, y1 = float(ys[j]), float(ys[j + 1])
            # Edge midpoints by linear interpolation
            edges = {
                0: (x0 + (x1 - x0) * interp(x0, x1, v[0], v[1]), y0),  # bottom
                1: (x1, y0 + (y1 - y0) * interp(y0, y1, v[1], v[2])),  # right
                2: (x0 + (x1 - x0) * interp(x0, x1, v[3], v[2]), y1),  # top
                3: (x0, y0 + (y1 - y0) * interp(y0, y1, v[0], v[3])),  # left
            }
            # Standard MS edge pairs for each case (simplified)
            table: dict[int, tuple[int, int]] = {
                1: (3, 0),
                2: (0, 1),
                3: (3, 1),
                4: (1, 2),
                5: (3, 0),  # ambiguous — pick one
                6: (0, 2),
                7: (3, 2),
                8: (2, 3),
                9: (0, 2),
                10: (0, 1),
                11: (1, 2),
                12: (1, 3),
                13: (0, 1),
                14: (0, 3),
            }
            pair = table.get(idx)
            if pair is None:
                continue
            a, b = edges[pair[0]], edges[pair[1]]
            segments.append((a, b))

    if not segments:
        return []

    # Greedy chain into a ring
    pts_en = [segments[0][0], segments[0][1]]
    used = {0}
    for _ in range(len(segments) - 1):
        tail = pts_en[-1]
        best_i = None
        best_d = 1e18
        flip = False
        for i, (a, b) in enumerate(segments):
            if i in used:
                continue
            da = (a[0] - tail[0]) ** 2 + (a[1] - tail[1]) ** 2
            db = (b[0] - tail[0]) ** 2 + (b[1] - tail[1]) ** 2
            if da < best_d:
                best_d, best_i, flip = da, i, False
            if db < best_d:
                best_d, best_i, flip = db, i, True
        if best_i is None:
            break
        used.add(best_i)
        a, b = segments[best_i]
        pts_en.append(b if not flip else a)

    ring: list[tuple[float, float]] = []
    for e, n in pts_en:
        la, lo, _ = frame.enu_to_llh(e, n, 0.0)
        ring.append((la, lo))
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def _iso_probability_contour(
    xs: np.ndarray,
    ys: np.ndarray,
    pdf: np.ndarray,
    mass: float,
    frame: LocalTangentPlane,
) -> list[tuple[float, float]]:
    """Highest-density region contour covering ``mass`` fraction of grid probability."""
    flat = pdf.ravel()
    order = np.argsort(flat)[::-1]
    csum = np.cumsum(flat[order])
    total = float(csum[-1]) if csum.size else 0.0
    if total <= 0:
        return []
    target = mass * total
    k = int(np.searchsorted(csum, target, side="left"))
    k = min(max(k, 0), order.size - 1)
    level = float(flat[order[k]])
    return _marching_squares_contour(xs, ys, pdf, level, frame)


def solve_rssi_grid(
    observations: list[RssiObservation],
    *,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
    gamma: float = 2.8,
    shadow_std_db: float = 4.0,
    grid_n: int = GRID_N_DEFAULT,
    pad_m: float = PAD_M_DEFAULT,
) -> RssiGridResult:
    if len(observations) < 4:
        raise InsufficientBreadcrumbsError("K >= 4 RSSI breadcrumbs required")
    if not (2.0 <= float(gamma) <= 4.5):
        raise ValueError("gamma must be in [2.0, 4.5]")
    sigma = max(float(shadow_std_db), 0.5)
    o_lat = origin_lat if origin_lat is not None else float(np.mean([o.lat for o in observations]))
    o_lon = origin_lon if origin_lon is not None else float(np.mean([o.lon for o in observations]))
    frame = LocalTangentPlane(o_lat, o_lon)

    sens = np.zeros((len(observations), 2), dtype=np.float64)
    pk = np.zeros(len(observations), dtype=np.float64)
    for i, o in enumerate(observations):
        e, n, _ = frame.llh_to_enu(o.lat, o.lon, 0.0)
        sens[i] = (e, n)
        pk[i] = float(o.rssi_db)

    # Reject co-located breadcrumbs (zero spatial spread → singular path-loss fit)
    spread = float(
        np.hypot(sens[:, 0] - sens[:, 0].mean(), sens[:, 1] - sens[:, 1].mean()).max()
    )
    if spread < MIN_BREADCRUMB_SPREAD_M:
        raise InsufficientBreadcrumbsError(
            f"colocated breadcrumbs: spatial spread {spread:.3f} m < {MIN_BREADCRUMB_SPREAD_M} m"
        )

    emin, emax = float(sens[:, 0].min()) - pad_m, float(sens[:, 0].max()) + pad_m
    nmin, nmax = float(sens[:, 1].min()) - pad_m, float(sens[:, 1].max()) + pad_m
    n = max(16, int(grid_n))
    xs = np.linspace(emin, emax, n)
    ys = np.linspace(nmin, nmax, n)
    ee, nn = np.meshgrid(xs, ys)  # shape (n, n)

    # Vectorized distances to each sensor: (n, n, K)
    d = np.sqrt(
        (ee[..., None] - sens[None, None, :, 0]) ** 2
        + (nn[..., None] - sens[None, None, :, 1]) ** 2
    )
    d = np.maximum(d, MIN_RANGE_M)
    logd = np.log10(d)
    # Closed-form P0(g) = mean_k (P_k + 10 γ log10(d_k))
    p0 = np.mean(pk[None, None, :] + 10.0 * gamma * logd, axis=2)
    pred = p0[..., None] - 10.0 * gamma * logd
    resid2 = (pk[None, None, :] - pred) ** 2
    # Likelihood ∝ exp(-0.5/σ² Σ resid²)
    neg_ll = 0.5 * np.sum(resid2, axis=2) / (sigma**2)
    neg_ll = neg_ll - np.min(neg_ll)  # stabilize
    like = np.exp(-neg_ll)
    like_norm = like / max(float(like.max()), 1e-30)

    if not np.isfinite(like_norm).all():
        raise ValueError("RSSI likelihood grid contains NaN/Inf")
    j_peak, i_peak = np.unravel_index(int(np.argmax(like_norm)), like_norm.shape)
    e_hat = float(xs[i_peak])
    n_hat = float(ys[j_peak])
    lat, lon, _ = frame.enu_to_llh(e_hat, n_hat, 0.0)
    p0_hat = float(p0[j_peak, i_peak])

    # Path-loss curve at ML position
    d_ml = np.hypot(e_hat - sens[:, 0], n_hat - sens[:, 1])
    d_ml = np.maximum(d_ml, MIN_RANGE_M)
    pred_ml = p0_hat - 10.0 * gamma * np.log10(d_ml)
    curve = [
        {
            "distance_m": float(d_ml[i]),
            "log10_distance": float(math.log10(d_ml[i])),
            "rssi_db": float(pk[i]),
            "predicted_db": float(pred_ml[i]),
            "residual_db": float(pk[i] - pred_ml[i]),
        }
        for i in range(len(observations))
    ]
    rmse = float(np.sqrt(np.mean((pk - pred_ml) ** 2)))

    # Probability mass for HDR contours
    pdf = like_norm / max(float(like_norm.sum()), 1e-30)
    contour_80 = _iso_probability_contour(xs, ys, pdf, 0.80, frame)
    contour_95 = _iso_probability_contour(xs, ys, pdf, 0.95, frame)

    return RssiGridResult(
        ok=True,
        lat=lat,
        lon=lon,
        east_m=e_hat,
        north_m=n_hat,
        p0_db=p0_hat,
        gamma=float(gamma),
        likelihood_peak=float(like_norm[j_peak, i_peak]),
        likelihood_min=float(np.min(like_norm)),
        residual_rmse_db=rmse,
        contour_80=contour_80,
        contour_95=contour_95,
        path_loss_curve=curve,
        grid_extent={"e_min": emin, "e_max": emax, "n_min": nmin, "n_max": nmax},
        message="rssi_grid_ok",
    )
