"""TPMS passage clustering."""

from __future__ import annotations

from pp_dashboard.tpms.cluster import PassageClusterer, suggested_label
from pp_dashboard.tpms.oem_hints import oem_hint_from_payload


def test_cluster_merges_within_window() -> None:
    c = PassageClusterer(window_sec=10.0, seal_idle_sec=30.0)
    a, _ = c.observe("AAAA", oem_family="Ford", now=100.0)
    b, _ = c.observe("BBBB", oem_family="Ford", now=105.0)
    assert a.cluster_key == b.cluster_key
    assert a.sensor_ids == {"AAAA", "BBBB"}


def test_cluster_seals_after_idle() -> None:
    c = PassageClusterer(window_sec=5.0, seal_idle_sec=8.0)
    c.observe("AAAA", now=0.0)
    sealed = c.flush_idle(now=20.0)
    assert sealed is not None
    assert sealed.sealed
    assert "AAAA" in sealed.sensor_ids


def test_suggested_label_and_oem_make() -> None:
    assert "Ford" in suggested_label("Ford", {"ABC123"})
    hints = oem_hint_from_payload({"model": "Toyota", "type": "TPMS", "id": "1"})
    assert hints["suggested_make"] == "Toyota"
