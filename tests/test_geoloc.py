"""Geolocation / DF / CPA math + API — offline."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.geoloc import geo
from pp_dashboard.geoloc.store import GeolocStore
from pp_dashboard.main import create_app


def test_haversine_known_short_leg() -> None:
    # ~111.2 km per degree latitude
    d = geo.haversine_m(49.0, -1.0, 50.0, -1.0)
    assert 110_000 < d < 112_500


def test_destination_and_lob_intersection() -> None:
    lat0, lon0 = 49.12, -1.10
    # Two LOBs that should cross east of origin
    a = geo.LineOfBearing(lat0, lon0, bearing_deg=45.0)
    b = geo.LineOfBearing(lat0 + 0.05, lon0, bearing_deg=135.0)
    hit = geo.intersect_lobs(a, b)
    assert hit is not None
    assert hit.lat > lat0
    assert hit.lon > lon0


def test_cpa_closing_tracks() -> None:
    # Ownship at origin, target northbound offset east — CPA exists
    own = geo.TrackState(lat=49.0, lon=-1.0, speed_mps=0.0, course_deg=0.0)
    tgt = geo.TrackState(lat=48.9, lon=-0.95, speed_mps=50.0, course_deg=0.0)
    cpa = geo.closest_point_of_approach(own, tgt, horizon_s=3600.0)
    assert cpa is not None
    assert cpa.range_m > 0
    assert cpa.time_s >= 0


def test_range_ring_polygon() -> None:
    poly = geo.range_ring(49.12, -1.10, radius_m=5_000, n=16)
    assert len(poly) == 17  # closed
    assert poly[0] == poly[-1]


def test_geoloc_store_breadcrumbs_and_heatmap(tmp_path: Path) -> None:
    store = GeolocStore(tmp_path / "geoloc.sqlite3")
    store.set_operator(49.12, -1.10, accuracy_m=12.0, source="test")
    op = store.get_operator()
    assert op is not None
    assert abs(op["lat"] - 49.12) < 1e-6
    store.add_breadcrumb(
        kind="mission",
        lat=49.12,
        lon=-1.10,
        frequency_hz=433_920_000,
        rssi_db=-42.0,
        label="OOK",
    )
    store.add_breadcrumb(
        kind="tpms",
        lat=49.121,
        lon=-1.101,
        frequency_hz=433_920_000,
        rssi_db=-50.0,
        label="sensor",
        ref_id="AABBCC",
    )
    crumbs = store.list_breadcrumbs(limit=10)
    assert len(crumbs) == 2
    heat = store.heatmap_cells(cell_deg=0.01)
    assert len(heat) >= 1
    lob_id = store.add_lob(49.12, -1.10, bearing_deg=90.0, label="east")
    lobs = store.list_lobs()
    assert any(x["id"] == lob_id for x in lobs)
    store.clear_lobs()
    assert store.list_lobs() == []


@pytest.mark.asyncio
async def test_geoloc_http_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pp_dashboard import config as cfg
    from pp_dashboard.hardware.arbiter import HardwareArbiter
    from pp_dashboard.geoloc.service import GeolocService

    monkeypatch.setattr(cfg.settings, "captures_dir", tmp_path)
    HardwareArbiter.reset_instance_for_tests()
    GeolocService.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/geoloc/operator",
            json={"lat": 49.12, "lon": -1.10, "accuracy_m": 8.0, "source": "browser"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        st = await client.get("/api/geoloc/status")
        assert st.status_code == 200
        body = st.json()
        assert body["operator"]["lat"] == pytest.approx(49.12)
        crumb = await client.post(
            "/api/geoloc/breadcrumb",
            json={
                "kind": "manual",
                "lat": 49.12,
                "lon": -1.10,
                "frequency_hz": 433920000,
                "rssi_db": -40,
                "label": "walk",
            },
        )
        assert crumb.status_code == 200
        heat = await client.get("/api/geoloc/heatmap")
        assert heat.status_code == 200
        assert "cells" in heat.json()
        lob = await client.post(
            "/api/geoloc/lob",
            json={"lat": 49.12, "lon": -1.10, "bearing_deg": 45.0, "label": "NE"},
        )
        assert lob.status_code == 200
        inter = await client.post(
            "/api/geoloc/lob/intersect",
            json={
                "a": {"lat": 49.12, "lon": -1.10, "bearing_deg": 45.0},
                "b": {"lat": 49.17, "lon": -1.10, "bearing_deg": 135.0},
            },
        )
        assert inter.status_code == 200
        assert inter.json()["intersection"] is not None
        cpa = await client.post(
            "/api/geoloc/cpa",
            json={
                "own": {"lat": 49.12, "lon": -1.10, "speed_mps": 0, "course_deg": 0},
                "target": {"lat": 49.0, "lon": -1.0, "speed_mps": 100, "course_deg": 0},
            },
        )
        assert cpa.status_code == 200
        rings = await client.get("/api/geoloc/range-rings?radii_m=1000,5000")
        assert rings.status_code == 200
        assert len(rings.json()["rings"]) == 2
