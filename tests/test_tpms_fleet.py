"""TPMS OEM hints + fleet store."""

from __future__ import annotations

from pathlib import Path

from pp_dashboard.tpms.oem_hints import (
    extract_sensor_id,
    is_tpms_payload,
    oem_hint_from_payload,
)
from pp_dashboard.tpms.store import TpmsFleetStore


def test_tpms_payload_and_oem() -> None:
    payload = {
        "model": "Schrader",
        "type": "TPMS",
        "id": "0xce0ffee",
        "pressure_kPa": 240.0,
        "temperature_C": 21.0,
    }
    assert is_tpms_payload(payload)
    assert extract_sensor_id(payload) == "CE0FFEE"
    hints = oem_hint_from_payload(payload)
    assert hints["oem_family"] == "Schrader"


def test_fleet_store_link(tmp_path: Path) -> None:
    store = TpmsFleetStore(tmp_path / "fleet.sqlite3")
    s = store.record_hit(
        sensor_id="AABBCC",
        payload={"model": "Toyota", "type": "TPMS", "id": "AABBCC", "pressure_kPa": 220},
        oem_family="Toyota/Lexus (Pacific)",
        frequency_hz=433_920_000,
        lat=48.85,
        lon=2.35,
    )
    assert s.hit_count == 1
    v = store.upsert_vehicle(
        label="Lab DUT Fiesta",
        make="Ford",
        model="Fiesta",
        year=2018,
        sensor_ids=["AABBCC"],
    )
    assert v.id >= 1
    linked = store.get_sensor("AABBCC")
    assert linked is not None
    assert linked.vehicle_id == v.id
    assert any(x.id == v.id for x in store.list_vehicles())


def test_fleet_clear_all(tmp_path: Path) -> None:
    store = TpmsFleetStore(tmp_path / "fleet.sqlite3")
    store.record_hit(
        sensor_id="DEAD",
        payload={"model": "Schrader", "type": "TPMS", "id": "DEAD", "pressure_kPa": 200},
        oem_family="Schrader",
        frequency_hz=433_920_000,
        lat=None,
        lon=None,
    )
    store.upsert_vehicle(label="X", sensor_ids=["DEAD"])
    cleared = store.clear_all()
    assert cleared["sensors_deleted"] >= 1
    assert store.list_sensors() == []
    assert store.list_vehicles() == []
