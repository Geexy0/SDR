"""Hybrid ADS-B source tagging + reception range polygon."""

from __future__ import annotations

from pp_dashboard.recon.hybrid_adsb import (
    DEFAULT_RX_LAT,
    DEFAULT_RX_LON,
    merge_source_tag,
    reception_range_polygon,
)


def test_default_rx_site_normandy() -> None:
    assert abs(DEFAULT_RX_LAT - 49.1182) < 1e-4
    assert abs(DEFAULT_RX_LON - (-1.0972)) < 1e-4


def test_merge_source_tag() -> None:
    seen: dict[str, set[str]] = {}
    a = merge_source_tag(seen, "ABC123", "LOCAL_RF")
    assert a == "LOCAL_RF"
    b = merge_source_tag(seen, "ABC123", "OPENSKY_NET")
    assert b == "HYBRID"
    c = merge_source_tag(seen, "DEF456", "OPENSKY_NET")
    assert c == "OPENSKY_NET"


def test_reception_range_polygon_grows_with_hits() -> None:
    # ~111 km north of site → ~1° lat
    pts = [(DEFAULT_RX_LAT + 1.0, DEFAULT_RX_LON)]
    ring = reception_range_polygon(DEFAULT_RX_LAT, DEFAULT_RX_LON, pts, min_km=5.0)
    assert len(ring) >= 16
    # First vertex should be roughly at max range (~111 km)
    lat0, lon0 = ring[0]
    assert abs(lat0 - (DEFAULT_RX_LAT + 1.0)) < 0.15 or abs(lat0 - DEFAULT_RX_LAT) > 0.5
