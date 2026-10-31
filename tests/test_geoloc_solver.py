"""Probabilistic RF emitter localization — projection, Stansfield WLS, RSSI grid."""

from __future__ import annotations

import math

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.geoloc import projection as proj
from pp_dashboard.geoloc import solver
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app


ORIGIN = (49.12, -1.10)


def test_enu_roundtrip_near_origin() -> None:
    frame = proj.LocalTangentPlane(ORIGIN[0], ORIGIN[1])
    e, n, u = frame.llh_to_enu(49.125, -1.095, 0.0)
    assert abs(u) < 2.0  # near-horizontal
    lat, lon, alt = frame.enu_to_llh(e, n, u)
    assert lat == pytest.approx(49.125, abs=1e-6)
    assert lon == pytest.approx(-1.095, abs=1e-6)
    assert alt == pytest.approx(0.0, abs=1.0)


def test_enu_east_positive() -> None:
    frame = proj.LocalTangentPlane(ORIGIN[0], ORIGIN[1])
    e, n, _ = frame.llh_to_enu(ORIGIN[0], ORIGIN[1] + 0.01, 0.0)
    assert e > 0
    assert abs(n) < abs(e) * 0.2


def test_stansfield_recovers_known_emitter() -> None:
    frame = proj.LocalTangentPlane(ORIGIN[0], ORIGIN[1])
    # Emitter 2 km east, 1 km north of origin
    true_e, true_n = 2000.0, 1000.0
    true_lat, true_lon, _ = frame.enu_to_llh(true_e, true_n, 0.0)

    sensors = [
        (0.0, 0.0),
        (500.0, -800.0),
        (-600.0, 400.0),
        (1200.0, -200.0),
    ]
    obs: list[solver.BearingObservation] = []
    for se, sn in sensors:
        slat, slon, _ = frame.enu_to_llh(se, sn, 0.0)
        bearing = math.degrees(math.atan2(true_e - se, true_n - sn)) % 360.0
        obs.append(
            solver.BearingObservation(
                lat=slat,
                lon=slon,
                bearing_deg=bearing,
                angular_std_deg=2.0,
                rssi_db=-45.0,
            )
        )
    result = solver.solve_stansfield_wls(obs, origin_lat=ORIGIN[0], origin_lon=ORIGIN[1])
    assert result.ok is True
    assert result.gdop_rejected is False
    assert result.lat == pytest.approx(true_lat, abs=5e-5)
    assert result.lon == pytest.approx(true_lon, abs=5e-5)
    assert result.cep95.semi_major_m > 0
    assert result.cep95.semi_minor_m > 0
    assert len(result.cep95.polygon) >= 64
    assert result.cep95.polygon[0] == result.cep95.polygon[-1]


def test_stansfield_rejects_parallel_bearings() -> None:
    frame = proj.LocalTangentPlane(49.12, -1.10)
    # Exact parallel LOBs (same bearing, separated east) → singular AtᵀWA
    la0, lo0, _ = frame.enu_to_llh(0.0, 0.0, 0.0)
    la1, lo1, _ = frame.enu_to_llh(100.0, 0.0, 0.0)
    obs = [
        solver.BearingObservation(la0, lo0, 45.0, 3.0, -40.0),
        solver.BearingObservation(la1, lo1, 45.0, 3.0, -42.0),
    ]
    with pytest.raises(solver.DegenerateGeometryError, match="GDOP"):
        solver.solve_stansfield_wls(obs, origin_lat=49.12, origin_lon=-1.10)


def test_rssi_grid_localizes_from_path_loss() -> None:
    frame = proj.LocalTangentPlane(ORIGIN[0], ORIGIN[1])
    true_e, true_n = 400.0, -200.0
    true_lat, true_lon, _ = frame.enu_to_llh(true_e, true_n, 0.0)
    p0 = -30.0
    gamma = 2.8
    crumbs: list[solver.RssiObservation] = []
    for se, sn in [
        (0.0, 0.0),
        (800.0, 0.0),
        (0.0, -600.0),
        (700.0, -500.0),
        (200.0, 300.0),
        (-100.0, -100.0),
    ]:
        d = max(10.0, math.hypot(true_e - se, true_n - sn))
        rssi = p0 - 10.0 * gamma * math.log10(d)
        slat, slon, _ = frame.enu_to_llh(se, sn, 0.0)
        crumbs.append(solver.RssiObservation(lat=slat, lon=slon, rssi_db=rssi))
    result = solver.solve_rssi_grid(
        crumbs,
        origin_lat=ORIGIN[0],
        origin_lon=ORIGIN[1],
        gamma=gamma,
        shadow_std_db=4.0,
        grid_n=80,
        pad_m=250.0,
    )
    assert result.ok is True
    assert result.lat == pytest.approx(true_lat, abs=2e-4)
    assert result.lon == pytest.approx(true_lon, abs=2e-4)
    assert 0.0 <= result.likelihood_peak <= 1.0
    assert len(result.contour_80) >= 3
    assert len(result.contour_95) >= 3
    assert len(result.path_loss_curve) == len(crumbs)
    # Residuals should be tiny on noiseless synthetic data
    assert max(abs(p["residual_db"]) for p in result.path_loss_curve) < 0.5


def test_rssi_grid_requires_four_points() -> None:
    crumbs = [
        solver.RssiObservation(49.12, -1.10, -40.0),
        solver.RssiObservation(49.121, -1.101, -42.0),
        solver.RssiObservation(49.122, -1.102, -44.0),
    ]
    with pytest.raises(solver.InsufficientBreadcrumbsError, match="K >= 4"):
        solver.solve_rssi_grid(crumbs, origin_lat=49.12, origin_lon=-1.10)


@pytest.mark.asyncio
async def test_solve_http_endpoints(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pp_dashboard import config as cfg
    from pp_dashboard.geoloc.service import GeolocService

    monkeypatch.setattr(cfg.settings, "captures_dir", tmp_path)
    HardwareArbiter.reset_instance_for_tests()
    GeolocService.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        lob = await client.post(
            "/api/geoloc/lob/solve",
            json={
                "observations": [
                    {
                        "lat": 49.12,
                        "lon": -1.10,
                        "bearing_deg": 45.0,
                        "angular_std_deg": 3.0,
                        "rssi_db": -40,
                    },
                    {
                        "lat": 49.13,
                        "lon": -1.10,
                        "bearing_deg": 135.0,
                        "angular_std_deg": 3.0,
                        "rssi_db": -42,
                    },
                    {
                        "lat": 49.12,
                        "lon": -1.09,
                        "bearing_deg": 315.0,
                        "angular_std_deg": 4.0,
                        "rssi_db": -45,
                    },
                ]
            },
        )
        assert lob.status_code == 200
        body = lob.json()
        assert "lat" in body or body.get("gdop_rejected") is True

        rssi = await client.post(
            "/api/geoloc/rssi-grid/solve",
            json={
                "gamma": 2.8,
                "observations": [
                    {"lat": 49.1200, "lon": -1.1000, "rssi_db": -40},
                    {"lat": 49.1210, "lon": -1.1010, "rssi_db": -45},
                    {"lat": 49.1190, "lon": -1.0990, "rssi_db": -48},
                    {"lat": 49.1220, "lon": -1.0980, "rssi_db": -50},
                ],
            },
        )
        assert rssi.status_code == 200
        rbody = rssi.json()
        assert rbody["ok"] is True
        assert "path_loss_curve" in rbody
        assert "contour_95" in rbody
