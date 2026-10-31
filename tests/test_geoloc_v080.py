"""Analytical offline suite for v0.8.0 WLS LOB / CEP95 / RSSI-grid geoloc.

Runs 100% offline with deterministic synthetic ENU geometry — no network, no SDR.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pp_dashboard.geoloc import geo, projection as proj
from pp_dashboard.geoloc import solver
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app

ORIGIN_LAT = 49.1182
ORIGIN_LON = -1.0972
ORIGIN = (ORIGIN_LAT, ORIGIN_LON)


def _point_in_ring(lat: float, lon: float, ring: list[tuple[float, float]]) -> bool:
    """Ray-cast containment for a closed (lat, lon) ring."""
    if len(ring) < 4:
        return False
    inside = False
    n = len(ring) - 1  # last == first
    for i in range(n):
        y1, x1 = ring[i]
        y2, x2 = ring[i + 1]
        if ((y1 > lat) != (y2 > lat)) and (
            lon < (x2 - x1) * (lat - y1) / (y2 - y1 + 1e-30) + x1
        ):
            inside = not inside
    return inside


def _obs_at(
    frame: proj.LocalTangentPlane,
    east: float,
    north: float,
    bearing_deg: float,
    *,
    angular_std_deg: float = 2.0,
    rssi_db: float = -45.0,
) -> solver.BearingObservation:
    lat, lon, _ = frame.enu_to_llh(east, north, 0.0)
    return solver.BearingObservation(
        lat=lat,
        lon=lon,
        bearing_deg=bearing_deg % 360.0,
        angular_std_deg=angular_std_deg,
        rssi_db=rssi_db,
    )


class TestENUProjection:
    def test_enu_origin_identity(self) -> None:
        frame = proj.LocalTangentPlane(ORIGIN_LAT, ORIGIN_LON)
        e, n, u = frame.llh_to_enu(ORIGIN_LAT, ORIGIN_LON, 0.0)
        assert e == pytest.approx(0.0, abs=1e-9)
        assert n == pytest.approx(0.0, abs=1e-9)
        assert abs(u) < 1e-6
        lat, lon, _ = frame.enu_to_llh(0.0, 0.0, 0.0)
        assert lat == pytest.approx(ORIGIN_LAT, abs=1e-9)
        assert lon == pytest.approx(ORIGIN_LON, abs=1e-9)

    def test_enu_roundtrip_and_metric_scale(self) -> None:
        frame = proj.LocalTangentPlane(ORIGIN_LAT, ORIGIN_LON)
        east_m, north_m = 1000.0, -750.0
        lat, lon, _ = frame.enu_to_llh(east_m, north_m, 0.0)
        e2, n2, _ = frame.llh_to_enu(lat, lon, 0.0)
        assert math.hypot(e2 - east_m, n2 - north_m) < 1e-3
        enu_dist = math.hypot(east_m, north_m)
        assert enu_dist == pytest.approx(1250.0, abs=1e-9)
        # WGS84 ground range via ECEF chord→arc (matches ENU metric; spherical
        # haversine on R=6371 km is ~0.2% off at this baseline).
        a = proj.llh_to_ecef(ORIGIN_LAT, ORIGIN_LON, 0.0)
        b = proj.llh_to_ecef(lat, lon, 0.0)
        chord = math.dist(a, b)
        r_mean = 0.5 * (math.dist(a, (0.0, 0.0, 0.0)) + math.dist(b, (0.0, 0.0, 0.0)))
        wgs84_m = 2.0 * r_mean * math.asin(min(1.0, chord / (2.0 * r_mean)))
        assert abs(wgs84_m - enu_dist) < 0.5
        # Spherical haversine stays within a few metres (sanity, not the 1 mm bar)
        assert abs(geo.haversine_m(ORIGIN_LAT, ORIGIN_LON, lat, lon) - enu_dist) < 5.0


class TestStansfieldWLSSolver:
    def test_orthogonal_two_ray_exact_intersection_and_cep95(self) -> None:
        frame = proj.LocalTangentPlane(ORIGIN_LAT, ORIGIN_LON)
        true_e, true_n = 500.0, 500.0
        obs = [
            _obs_at(frame, 0.0, 500.0, 90.0, angular_std_deg=2.0),
            _obs_at(frame, 500.0, 0.0, 0.0, angular_std_deg=2.0),
        ]
        result = solver.solve_stansfield_wls(
            obs, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON
        )
        assert result.ok is True
        assert result.east_m == pytest.approx(true_e, abs=0.5)
        assert result.north_m == pytest.approx(true_n, abs=0.5)
        assert result.cep95 is not None
        assert result.cep95.semi_major_m == pytest.approx(42.72, rel=0.05)
        assert result.cep95.semi_minor_m == pytest.approx(42.72, rel=0.05)
        poly = result.cep95.polygon
        assert len(poly) in {64, 65}
        assert poly[0] == poly[-1]
        assert result.lat is not None and result.lon is not None
        assert _point_in_ring(result.lat, result.lon, poly)

    def test_cep95_scales_linearly_with_angular_uncertainty(self) -> None:
        frame = proj.LocalTangentPlane(ORIGIN_LAT, ORIGIN_LON)
        base = [
            _obs_at(frame, 0.0, 500.0, 90.0, angular_std_deg=2.0),
            _obs_at(frame, 500.0, 0.0, 0.0, angular_std_deg=2.0),
        ]
        doubled = [
            _obs_at(frame, 0.0, 500.0, 90.0, angular_std_deg=4.0),
            _obs_at(frame, 500.0, 0.0, 0.0, angular_std_deg=4.0),
        ]
        r2 = solver.solve_stansfield_wls(
            base, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON
        )
        r4 = solver.solve_stansfield_wls(
            doubled, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON
        )
        assert r2.cep95 and r4.cep95
        ratio_maj = r4.cep95.semi_major_m / r2.cep95.semi_major_m
        ratio_min = r4.cep95.semi_minor_m / r2.cep95.semi_minor_m
        assert ratio_maj == pytest.approx(2.0, rel=1e-3)
        assert ratio_min == pytest.approx(2.0, rel=1e-3)

    def test_overdetermined_noisy_five_lob_wls_convergence(self) -> None:
        frame = proj.LocalTangentPlane(ORIGIN_LAT, ORIGIN_LON)
        true_e, true_n = 300.0, -200.0
        radius = 800.0
        # 5 observers on a semi-circle (south → east → north → west → SW)
        angles = np.linspace(-math.pi / 2, math.pi / 2, 5)
        perturbs = [-1.5, +1.0, -0.5, +1.5, -0.5]
        obs: list[solver.BearingObservation] = []
        for ang, dth in zip(angles, perturbs, strict=True):
            se = true_e + radius * math.cos(ang)
            sn = true_n + radius * math.sin(ang)
            true_brg = math.degrees(math.atan2(true_e - se, true_n - sn)) % 360.0
            obs.append(
                _obs_at(
                    frame,
                    se,
                    sn,
                    true_brg + dth,
                    angular_std_deg=2.0,
                    rssi_db=-42.0,
                )
            )
        # 6th outlier — low SNR / high angular variance
        se, sn = true_e + 900.0, true_n + 50.0
        true_brg = math.degrees(math.atan2(true_e - se, true_n - sn)) % 360.0
        obs.append(
            _obs_at(
                frame,
                se,
                sn,
                true_brg + 25.0,
                angular_std_deg=15.0,
                rssi_db=-85.0,
            )
        )
        result = solver.solve_stansfield_wls(
            obs, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON
        )
        assert result.ok is True
        err = math.hypot(result.east_m - true_e, result.north_m - true_n)  # type: ignore[operator]
        assert err < 18.0

    def test_parallel_and_divergent_bearings_rejected_cleanly(self) -> None:
        frame = proj.LocalTangentPlane(ORIGIN_LAT, ORIGIN_LON)
        obs = [
            _obs_at(frame, 0.0, 0.0, 45.0, angular_std_deg=3.0),
            _obs_at(frame, 100.0, 0.0, 45.0, angular_std_deg=3.0),
        ]
        with pytest.raises(solver.DegenerateGeometryError, match="GDOP|degenerate"):
            solver.solve_stansfield_wls(
                obs, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON
            )


class TestRSSIGridSolver:
    def test_synthetic_breadcrumb_ring_recovers_emitter_and_p0(self) -> None:
        frame = proj.LocalTangentPlane(ORIGIN_LAT, ORIGIN_LON)
        true_e, true_n = 200.0, -150.0
        p0_true = -20.0
        gamma = 3.0
        # 8 breadcrumbs on a ring at distances 45…280 m around the emitter
        dists = [45.0, 70.0, 95.0, 120.0, 160.0, 200.0, 240.0, 280.0]
        crumbs: list[solver.RssiObservation] = []
        for i, dist in enumerate(dists):
            ang = i * (2.0 * math.pi / 8.0)
            se = true_e + dist * math.cos(ang)
            sn = true_n + dist * math.sin(ang)
            rssi = p0_true - 10.0 * gamma * math.log10(dist)
            lat, lon, _ = frame.enu_to_llh(se, sn, 0.0)
            crumbs.append(solver.RssiObservation(lat=lat, lon=lon, rssi_db=rssi))

        result = solver.solve_rssi_grid(
            crumbs,
            origin_lat=ORIGIN_LAT,
            origin_lon=ORIGIN_LON,
            gamma=gamma,
            shadow_std_db=4.0,
            grid_n=80,
            pad_m=80.0,
        )
        assert result.ok is True
        err = math.hypot(result.east_m - true_e, result.north_m - true_n)  # type: ignore[operator]
        assert err < 15.0
        assert result.p0_db == pytest.approx(-20.0, abs=1.5)
        assert result.likelihood_peak == pytest.approx(1.0, abs=1e-12)
        assert result.likelihood_min >= 0.0
        assert result.likelihood_min <= 1.0
        assert result.residual_rmse_db is not None
        assert result.residual_rmse_db < 0.5
        for pt in result.path_loss_curve:
            assert math.isfinite(pt["residual_db"])

    def test_insufficient_or_colocated_breadcrumbs_rejected(self) -> None:
        crumbs3 = [
            solver.RssiObservation(ORIGIN_LAT, ORIGIN_LON, -40.0),
            solver.RssiObservation(ORIGIN_LAT + 0.001, ORIGIN_LON, -42.0),
            solver.RssiObservation(ORIGIN_LAT, ORIGIN_LON + 0.001, -44.0),
        ]
        with pytest.raises(solver.InsufficientBreadcrumbsError, match="K >= 4"):
            solver.solve_rssi_grid(
                crumbs3, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON
            )

        colocated = [
            solver.RssiObservation(ORIGIN_LAT, ORIGIN_LON, -40.0 - i)
            for i in range(4)
        ]
        with pytest.raises(solver.InsufficientBreadcrumbsError, match="colocated"):
            solver.solve_rssi_grid(
                colocated, origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON
            )


class TestGeolocAPIRoutes:
    @pytest.fixture()
    def client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        from pp_dashboard import config as cfg
        from pp_dashboard.geoloc.service import GeolocService

        monkeypatch.setattr(cfg.settings, "captures_dir", tmp_path)
        HardwareArbiter.reset_instance_for_tests()
        GeolocService.reset_instance_for_tests()
        return TestClient(create_app())

    def test_lob_solve_schema_and_orthogonal_fix(self, client: TestClient) -> None:
        frame = proj.LocalTangentPlane(ORIGIN_LAT, ORIGIN_LON)
        o1 = _obs_at(frame, 0.0, 500.0, 90.0)
        o2 = _obs_at(frame, 500.0, 0.0, 0.0)
        resp = client.post(
            "/api/geoloc/lob/solve",
            json={
                "origin_lat": ORIGIN_LAT,
                "origin_lon": ORIGIN_LON,
                "observations": [
                    {
                        "lat": o1.lat,
                        "lon": o1.lon,
                        "bearing_deg": o1.bearing_deg,
                        "angular_std_deg": 2.0,
                        "rssi_db": -45.0,
                    },
                    {
                        "lat": o2.lat,
                        "lon": o2.lon,
                        "bearing_deg": o2.bearing_deg,
                        "angular_std_deg": 2.0,
                        "rssi_db": -45.0,
                    },
                ],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["estimated_lat"] is not None
        assert body["estimated_lon"] is not None
        assert body["cep95_ellipse"] is not None
        assert body["geojson_polygon"] is not None
        assert len(body["geojson_polygon"]) in {64, 65}
        # GeoJSON order [lon, lat]
        lon0, lat0 = body["geojson_polygon"][0]
        assert -180.0 <= lon0 <= 180.0
        assert -90.0 <= lat0 <= 90.0
        assert body["gdop"] is not None
        assert isinstance(body["residuals"], list)
        assert math.isfinite(body["estimated_lat"])
        assert math.isfinite(body["estimated_lon"])
        assert "NaN" not in resp.text

    def test_parallel_lob_returns_422_gdop(self, client: TestClient) -> None:
        frame = proj.LocalTangentPlane(ORIGIN_LAT, ORIGIN_LON)
        o1 = _obs_at(frame, 0.0, 0.0, 45.0)
        o2 = _obs_at(frame, 100.0, 0.0, 45.0)
        resp = client.post(
            "/api/geoloc/lob/solve",
            json={
                "origin_lat": ORIGIN_LAT,
                "origin_lon": ORIGIN_LON,
                "observations": [
                    {
                        "lat": o1.lat,
                        "lon": o1.lon,
                        "bearing_deg": 45.0,
                        "angular_std_deg": 3.0,
                        "rssi_db": -40.0,
                    },
                    {
                        "lat": o2.lat,
                        "lon": o2.lon,
                        "bearing_deg": 45.0,
                        "angular_std_deg": 3.0,
                        "rssi_db": -42.0,
                    },
                ],
            },
        )
        assert resp.status_code == 422
        detail = str(resp.json().get("detail", "")).upper()
        assert "GDOP" in detail or "DEGENERATE" in detail
        assert "NaN" not in resp.text

    def test_rssi_grid_solve_schema(self, client: TestClient) -> None:
        frame = proj.LocalTangentPlane(ORIGIN_LAT, ORIGIN_LON)
        true_e, true_n = 200.0, -150.0
        p0, gamma = -20.0, 3.0
        observations = []
        for de, dn in [(50.0, 0.0), (0.0, 80.0), (-100.0, 0.0), (0.0, -120.0), (150.0, 150.0)]:
            se, sn = true_e + de, true_n + dn
            dist = math.hypot(de, dn)
            lat, lon, _ = frame.enu_to_llh(se, sn, 0.0)
            observations.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "rssi_db": p0 - 10.0 * gamma * math.log10(dist),
                }
            )
        resp = client.post(
            "/api/geoloc/rssi-grid/solve",
            json={
                "origin_lat": ORIGIN_LAT,
                "origin_lon": ORIGIN_LON,
                "gamma": gamma,
                "shadow_std_db": 4.0,
                "grid_n": 80,
                "observations": observations,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["estimated_lat"] is not None
        assert body["estimated_lon"] is not None
        assert body["p0_db"] == pytest.approx(-20.0, abs=2.0)
        assert body["residuals"] is not None

    def test_colocated_rssi_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/geoloc/rssi-grid/solve",
            json={
                "observations": [
                    {"lat": ORIGIN_LAT, "lon": ORIGIN_LON, "rssi_db": -40.0 - i}
                    for i in range(4)
                ]
            },
        )
        assert resp.status_code == 422
        assert "colocated" in str(resp.json().get("detail", "")).lower()

    def test_geoloc_rx_accessible_when_tx_gate_locked(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        confirm = tmp_path / "CONFIRM.md"
        confirm.write_text(
            "# CONFIRM\n\n**Status:** `NOT CONFIRMED`\n\nENGAGEMENT: PP-LAB-RF-2026\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "pp_dashboard.security.tx_gate.CONFIRM_PATH", confirm
        )
        from pp_dashboard.security.tx_gate import TxGateError, assert_tx_allowed

        with pytest.raises(TxGateError):
            assert_tx_allowed()

        # RX / analysis geoloc endpoints must still work
        frame = proj.LocalTangentPlane(ORIGIN_LAT, ORIGIN_LON)
        o1 = _obs_at(frame, 0.0, 500.0, 90.0)
        o2 = _obs_at(frame, 500.0, 0.0, 0.0)
        lob = client.post(
            "/api/geoloc/lob/solve",
            json={
                "origin_lat": ORIGIN_LAT,
                "origin_lon": ORIGIN_LON,
                "observations": [
                    {
                        "lat": o1.lat,
                        "lon": o1.lon,
                        "bearing_deg": 90.0,
                        "angular_std_deg": 2.0,
                        "rssi_db": -45.0,
                    },
                    {
                        "lat": o2.lat,
                        "lon": o2.lon,
                        "bearing_deg": 0.0,
                        "angular_std_deg": 2.0,
                        "rssi_db": -45.0,
                    },
                ],
            },
        )
        assert lob.status_code == 200
        st = client.get("/api/geoloc/status")
        assert st.status_code == 200
