"""Autonomous live geo-hunt fusion + Doppler CPA estimator."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np

from pp_dashboard.geoloc.foxhunt import FoxSample, RssiKalman, evaluate_homing
from pp_dashboard.geoloc.projection import LocalTangentPlane
from pp_dashboard.geoloc.solver import (
    BearingObservation,
    InsufficientBreadcrumbsError,
    RssiObservation,
    solve_rssi_grid,
    solve_stansfield_wls,
)

log = logging.getLogger(__name__)

C_LIGHT = 299_792_458.0
MIN_MOVE_M = 5.0
MIN_SOLVE_SPREAD_M = 15.0
MIN_SOLVE_K = 4

TelemetrySink = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass
class HuntBreadcrumb:
    lat: float
    lon: float
    rssi_db: float
    freq_hz: int
    emitter_id: str
    timestamp: float


@dataclass
class LiveGeoHuntFusion:
    """Event-driven spatial correlator for Sub-GHz / voice / TPMS RSSI hits."""

    on_telemetry: TelemetrySink | None = None
    min_move_m: float = MIN_MOVE_M
    min_spread_m: float = MIN_SOLVE_SPREAD_M
    min_k: int = MIN_SOLVE_K

    _instance: ClassVar[LiveGeoHuntFusion | None] = None
    _instance_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    def __post_init__(self) -> None:
        self.running = False
        self._crumbs: dict[str, list[HuntBreadcrumb]] = {}
        self._last_pos: dict[str, tuple[float, float]] = {}
        self._kalman: dict[str, RssiKalman] = {}
        self._fox_samples: dict[str, list[FoxSample]] = {}
        self._last_grid: dict[str, dict[str, Any]] = {}
        self._last_lob: dict[str, dict[str, Any]] = {}
        self._bearings: dict[str, list[BearingObservation]] = {}
        self._pending: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls) -> LiveGeoHuntFusion:
        async with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        cls._instance = None

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "running": self.running,
            "emitters": {
                eid: {
                    "breadcrumb_count": len(crumbs),
                    "last_grid": self._last_grid.get(eid),
                    "last_lob": self._last_lob.get(eid),
                }
                for eid, crumbs in self._crumbs.items()
            },
        }

    async def start(self) -> dict[str, Any]:
        self.running = True
        self._crumbs.clear()
        self._last_pos.clear()
        self._kalman.clear()
        self._fox_samples.clear()
        self._last_grid.clear()
        self._last_lob.clear()
        self._bearings.clear()
        self._pending.clear()
        # Status-only (not a geohunt_update pin event)
        await self._emit(
            {
                "type": "status",
                "message": "geo_hunt_started",
                "metrics": {"event": "started", "running": True, "geohunt": True},
            }
        )
        return self.status()

    async def stop(self) -> dict[str, Any]:
        self.running = False
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
            self._pending.clear()
        await self._emit(
            {
                "type": "status",
                "message": "geo_hunt_stopped",
                "metrics": {"event": "stopped", "running": False, "geohunt": True},
            }
        )
        return self.status()

    async def flush_pending(self) -> None:
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
            self._pending.clear()

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.on_telemetry is None:
            return
        try:
            r = self.on_telemetry(event)
            if asyncio.iscoroutine(r):
                await r
        except Exception:
            log.exception("geo-hunt telemetry sink failed")

    def _haversine_m(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371000.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlmb = math.radians(lon2 - lon1)
        a = (
            math.sin(dphi / 2) ** 2
            + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
        )
        return 2 * r * math.asin(min(1.0, math.sqrt(a)))

    def _spread_m(self, crumbs: list[HuntBreadcrumb]) -> float:
        if len(crumbs) < 2:
            return 0.0
        frame = LocalTangentPlane(crumbs[0].lat, crumbs[0].lon)
        en = np.array(
            [frame.llh_to_enu(c.lat, c.lon, 0.0)[:2] for c in crumbs], dtype=np.float64
        )
        mean = en.mean(axis=0)
        return float(np.hypot(*(en - mean).T).max())

    async def ingest_rx_hit(
        self,
        *,
        lat: float,
        lon: float,
        rssi_db: float,
        freq_hz: int,
        emitter_id: str,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        if not self.running:
            return {"ok": False, "message": "geo_hunt_not_running"}
        ts = float(timestamp if timestamp is not None else time.time())
        key = f"{emitter_id}:{int(freq_hz)}"
        async with self._lock:
            last = self._last_pos.get(key)
            if last is not None:
                moved = self._haversine_m(last[0], last[1], lat, lon)
                if moved < self.min_move_m:
                    # Still update Kalman with RSSI at same cell
                    kal = self._kalman.setdefault(key, RssiKalman())
                    kal.update(rssi_db=rssi_db, t_mono=ts)
                    return {
                        "ok": True,
                        "appended": False,
                        "moved_m": moved,
                        "emitter_key": key,
                    }

            crumb = HuntBreadcrumb(
                lat=lat,
                lon=lon,
                rssi_db=float(rssi_db),
                freq_hz=int(freq_hz),
                emitter_id=str(emitter_id),
                timestamp=ts,
            )
            bucket = self._crumbs.setdefault(key, [])
            bucket.append(crumb)
            self._last_pos[key] = (lat, lon)

            kal = self._kalman.setdefault(key, RssiKalman())
            kstate = kal.update(rssi_db=rssi_db, t_mono=ts)
            fox = self._fox_samples.setdefault(key, [])
            fox.append(FoxSample(lat=lat, lon=lon, rssi_db=rssi_db, t_mono=ts))
            del fox[:-32]
            homing = evaluate_homing(fox)

            result: dict[str, Any] = {
                "ok": True,
                "appended": True,
                "emitter_key": key,
                "breadcrumb_count": len(bucket),
                "kalman": {
                    "rssi_db": kstate.rssi_db,
                    "rssi_rate_db_s": kstate.rssi_rate_db_s,
                },
                "homing": homing.to_dict(),
            }

            spread = self._spread_m(bucket)
            if len(bucket) >= self.min_k and spread >= self.min_spread_m:
                task = asyncio.create_task(
                    self._solve_rssi(key, list(bucket), homing.to_dict())
                )
                self._pending.append(task)
                task.add_done_callback(
                    lambda t, pending=self._pending: pending.remove(t)
                    if t in pending
                    else None
                )
                result["solve_scheduled"] = True
            return result

    async def _solve_rssi(
        self, key: str, crumbs: list[HuntBreadcrumb], homing: dict[str, Any]
    ) -> None:
        obs = [
            RssiObservation(lat=c.lat, lon=c.lon, rssi_db=c.rssi_db, timestamp=str(c.timestamp))
            for c in crumbs
        ]
        try:
            grid = await asyncio.to_thread(solve_rssi_grid, obs)
        except (InsufficientBreadcrumbsError, ValueError) as exc:
            log.info("geo-hunt RSSI solve skipped: %s", exc)
            return
        payload = grid.to_dict() if hasattr(grid, "to_dict") else {}
        if not payload and hasattr(grid, "lat"):
            payload = {
                "ok": True,
                "lat": grid.lat,
                "lon": grid.lon,
                "estimated_lat": grid.lat,
                "estimated_lon": grid.lon,
                "contour_80": [[a, b] for a, b in (grid.contour_80 or [])],
                "contour_95": [[a, b] for a, b in (grid.contour_95 or [])],
                "message": grid.message,
            }
        ml = {
            "lat": payload.get("lat") or payload.get("estimated_lat"),
            "lon": payload.get("lon") or payload.get("estimated_lon"),
            "emitter_id": crumbs[0].emitter_id,
            "freq_hz": crumbs[0].freq_hz,
        }
        metrics = {
            "event": "rssi_grid",
            "emitter_key": key,
            "emitter_id": crumbs[0].emitter_id,
            "ml_pin": ml,
            "contour_80": payload.get("contour_80"),
            "contour_95": payload.get("contour_95"),
            "homing": homing,
            "breadcrumb_count": len(crumbs),
            "grid": payload,
        }
        self._last_grid[key] = metrics
        await self._emit(
            {
                "type": "geohunt_update",
                "message": "geohunt_rssi_update",
                "metrics": metrics,
            }
        )

    async def ingest_bearing(
        self,
        *,
        lat: float,
        lon: float,
        bearing_deg: float,
        emitter_id: str = "LOB",
        freq_hz: int = 0,
        rssi_db: float | None = None,
        angular_std_deg: float = 3.0,
    ) -> dict[str, Any]:
        if not self.running:
            return {"ok": False, "message": "geo_hunt_not_running"}
        key = f"{emitter_id}:{int(freq_hz)}"
        obs = BearingObservation(
            lat=lat,
            lon=lon,
            bearing_deg=bearing_deg,
            angular_std_deg=angular_std_deg,
            rssi_db=rssi_db,
        )
        async with self._lock:
            bucket = self._bearings.setdefault(key, [])
            bucket.append(obs)
            n_obs = len(bucket)
            obs_copy = list(bucket)
        if n_obs < 2:
            return {"ok": True, "n_obs": n_obs, "solved": False}
        try:
            result = await asyncio.to_thread(solve_stansfield_wls, obs_copy)
        except Exception as exc:
            return {"ok": False, "message": str(exc), "n_obs": n_obs}
        blob = result.to_dict()
        metrics = {
            "event": "lob_solve",
            "emitter_key": key,
            "emitter_id": emitter_id,
            "cep95": blob.get("cep95"),
            "ml_pin": {"lat": blob.get("lat"), "lon": blob.get("lon")},
            "stansfield": blob,
        }
        async with self._lock:
            self._last_lob[key] = metrics
        await self._emit(
            {
                "type": "geohunt_update",
                "message": "geohunt_lob_update",
                "metrics": metrics,
            }
        )
        return {"ok": True, "solved": True, "result": blob}


def fit_doppler_pass(
    samples: list[tuple[float, float]],
    *,
    nominal_freq_hz: float,
) -> dict[str, Any]:
    """
    Fit radial velocity / CPA from (timestamp, measured_cfo_hz) series.

    Uses classical constant-speed flyby:
      v_rad(t) = v² (t - t_cpa) / sqrt(R_min² + v² (t - t_cpa)²)
      Δf = -v_rad · f0 / c
    """
    if len(samples) < 4:
        raise ValueError("need >= 4 Doppler samples")
    f0 = float(nominal_freq_hz)
    if f0 <= 0:
        raise ValueError("nominal_freq_hz must be > 0")
    ts = np.array([s[0] for s in samples], dtype=np.float64)
    cfo = np.array([s[1] for s in samples], dtype=np.float64)
    bias = float(np.median(cfo))
    cfo0 = cfo - bias
    t_cpa = float(ts[int(np.argmin(np.abs(cfo0)))])
    for i in range(len(ts) - 1):
        a, b = cfo0[i], cfo0[i + 1]
        if a == 0.0:
            t_cpa = float(ts[i])
            break
        if a * b < 0:
            frac = abs(a) / (abs(a) + abs(b))
            t_cpa = float(ts[i] + frac * (ts[i + 1] - ts[i]))
            break

    # Asymptotic |v| seed from peak |CFO| → |v| ≈ |Δf| c / f0
    v_seed = max(5.0, float(np.max(np.abs(cfo0))) * C_LIGHT / f0)

    def _sse(v: float, rmin: float, tc: float) -> tuple[float, float]:
        dt = ts - tc
        v_rad = (v * v * dt) / np.sqrt(rmin * rmin + (v * dt) ** 2)
        pred = -v_rad * f0 / C_LIGHT
        b = float(np.mean(cfo - pred))
        return float(np.mean((pred + b - cfo) ** 2)), b

    best_sse = float("inf")
    v_hat, r_hat, best_t, bias_hat = v_seed, 100.0, t_cpa, bias
    v_grid = np.unique(
        np.concatenate(
            [
                np.linspace(max(5.0, v_seed * 0.4), v_seed * 1.8, 41),
                np.array([v_seed], dtype=np.float64),
            ]
        )
    )
    for tc in (t_cpa, *np.linspace(float(ts.min()), float(ts.max()), 11)):
        for v in v_grid:
            for rmin in np.linspace(30.0, 300.0, 41):
                sse, b = _sse(float(v), float(rmin), float(tc))
                if sse < best_sse:
                    best_sse = sse
                    v_hat, r_hat, best_t, bias_hat = float(v), float(rmin), float(tc), b
    for _ in range(2):
        for v in np.linspace(max(5.0, v_hat - 8), v_hat + 8, 33):
            for rmin in np.linspace(max(10.0, r_hat - 40), r_hat + 40, 33):
                for tc in np.linspace(best_t - 2.0, best_t + 2.0, 21):
                    sse, b = _sse(float(v), float(rmin), float(tc))
                    if sse < best_sse:
                        best_sse = sse
                        v_hat, r_hat, best_t, bias_hat = float(v), float(rmin), float(tc), b

    return {
        "ok": True,
        "cpa_timestamp": best_t,
        "velocity_m_s": v_hat,
        "min_range_m": r_hat,
        "cfo_bias_hz": bias_hat,
        "nominal_freq_hz": f0,
        "n_samples": len(samples),
        "residual_sse": best_sse,
    }
