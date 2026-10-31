"""Kalman RSSI filter + GPS-correlated warmer/colder homing vector."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from pp_dashboard.geoloc.projection import LocalTangentPlane


@dataclass(slots=True)
class FoxSample:
    lat: float
    lon: float
    rssi_db: float
    t_mono: float


@dataclass(slots=True)
class KalmanState:
    rssi_db: float
    rssi_rate_db_s: float


class RssiKalman:
    """1D constant-velocity Kalman: state = [rssi_db, rssi_rate_db_per_sec]."""

    def __init__(
        self,
        *,
        process_var_rssi: float = 0.5,
        process_var_rate: float = 0.2,
        measure_var: float = 4.0,
    ) -> None:
        self._x = 0.0
        self._v = 0.0
        self._p00 = 100.0
        self._p01 = 0.0
        self._p10 = 0.0
        self._p11 = 100.0
        self._q_r = process_var_rssi
        self._q_v = process_var_rate
        self._r = measure_var
        self._t: float | None = None
        self._initialized = False

    def update(self, *, rssi_db: float, t_mono: float) -> KalmanState:
        z = float(rssi_db)
        if not self._initialized or self._t is None:
            self._x = z
            self._v = 0.0
            self._t = float(t_mono)
            self._initialized = True
            return KalmanState(rssi_db=self._x, rssi_rate_db_s=self._v)

        dt = max(1e-3, float(t_mono) - self._t)
        self._t = float(t_mono)

        # Predict
        x_pred = self._x + self._v * dt
        v_pred = self._v
        # P' = F P F^T + Q ; F=[[1,dt],[0,1]]
        p00 = self._p00 + dt * (self._p10 + self._p01) + dt * dt * self._p11 + self._q_r * dt
        p01 = self._p01 + dt * self._p11
        p10 = self._p10 + dt * self._p11
        p11 = self._p11 + self._q_v * dt

        # Update (H=[1,0])
        s = p00 + self._r
        k0 = p00 / s
        k1 = p10 / s
        innov = z - x_pred
        self._x = x_pred + k0 * innov
        self._v = v_pred + k1 * innov
        self._p00 = (1.0 - k0) * p00
        self._p01 = (1.0 - k0) * p01
        self._p10 = p10 - k1 * p00
        self._p11 = p11 - k1 * p01
        return KalmanState(rssi_db=self._x, rssi_rate_db_s=self._v)


@dataclass(slots=True)
class HomingResult:
    status: str  # warmer | colder | hold
    gradient_score: float
    rssi_filtered: float
    rssi_rate_db_s: float
    travel_bearing_deg: float
    arrow_east_m: float
    arrow_north_m: float
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "gradient_score": self.gradient_score,
            "rssi_filtered": self.rssi_filtered,
            "rssi_rate_db_s": self.rssi_rate_db_s,
            "travel_bearing_deg": self.travel_bearing_deg,
            "arrow": {
                "east_m": self.arrow_east_m,
                "north_m": self.arrow_north_m,
                "bearing_deg": self.travel_bearing_deg,
            },
            "message": self.message,
        }


def evaluate_homing(
    samples: list[FoxSample], *, window_sec: float = 5.0
) -> HomingResult:
    if len(samples) < 2:
        return HomingResult(
            status="hold",
            gradient_score=0.0,
            rssi_filtered=samples[0].rssi_db if samples else -120.0,
            rssi_rate_db_s=0.0,
            travel_bearing_deg=0.0,
            arrow_east_m=0.0,
            arrow_north_m=0.0,
            message="need >=2 samples",
        )

    ordered = sorted(samples, key=lambda s: s.t_mono)
    t_end = ordered[-1].t_mono
    window = [s for s in ordered if s.t_mono >= t_end - max(0.5, window_sec)]
    if len(window) < 2:
        window = ordered[-2:]

    filt = RssiKalman()
    last = KalmanState(rssi_db=window[0].rssi_db, rssi_rate_db_s=0.0)
    for s in window:
        last = filt.update(rssi_db=s.rssi_db, t_mono=s.t_mono)

    origin = window[0]
    frame = LocalTangentPlane(origin.lat, origin.lon)
    e0, n0, _ = 0.0, 0.0, 0.0
    e1, n1, _ = frame.llh_to_enu(window[-1].lat, window[-1].lon, 0.0)
    de, dn = e1 - e0, n1 - n0
    dist = math.hypot(de, dn)
    travel_bearing = (math.degrees(math.atan2(de, dn)) + 360.0) % 360.0 if dist > 0.5 else 0.0

    # Correlate dRSSI/dt with motion: score = rate * (normalized progress)
    # Positive rate while moving → warmer along travel heading
    rate = last.rssi_rate_db_s
    if dist < 0.5:
        status = "hold"
        score = 0.0
        msg = "stationary"
        ae = an = 0.0
    else:
        score = float(rate)
        if score > 0.15:
            status = "warmer"
            msg = "approaching emitter along travel heading"
        elif score < -0.15:
            status = "colder"
            msg = "receding from emitter along travel heading"
        else:
            status = "hold"
            msg = "weak gradient"
        # Arrow points along travel; length scales with |score|
        scale = min(80.0, 25.0 + abs(score) * 40.0)
        ue, un = de / dist, dn / dist
        # If colder, flip arrow to suggest reverse heading
        if status == "colder":
            ue, un = -ue, -un
            travel_bearing = (travel_bearing + 180.0) % 360.0
        ae, an = ue * scale, un * scale

    return HomingResult(
        status=status,
        gradient_score=score,
        rssi_filtered=last.rssi_db,
        rssi_rate_db_s=rate,
        travel_bearing_deg=travel_bearing,
        arrow_east_m=ae,
        arrow_north_m=an,
        message=msg,
    )
