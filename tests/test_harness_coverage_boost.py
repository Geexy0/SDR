"""Small pure-function coverage boosts for the verify_all ≥85% gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from pp_dashboard.rf.chirp import estimate_rms_dbfs, synthesize_chirp_cs8
from pp_dashboard.tpms.oem_hints import (
    extract_sensor_id,
    is_tpms_payload,
    oem_hint_from_payload,
)
from pp_dashboard.web.spa import frontend_dist_ready, spa_index, spa_static_file


def test_oem_hints_and_sensor_id() -> None:
    hint = oem_hint_from_payload({"model": "Schrader", "type": "TPMS", "id": "0xabc"})
    assert hint["oem_family"] == "Schrader"
    assert extract_sensor_id({"id": "0xabc"}) == "ABC"
    assert extract_sensor_id({}) is None
    assert is_tpms_payload({"type": "TPMS", "id": "1"}) is True
    assert is_tpms_payload({"model": "MyTPMS"}) is True
    assert is_tpms_payload({"pressure_kPa": 220, "id": "99"}) is True
    assert is_tpms_payload({"pressure_kPa": 220}) is False
    assert is_tpms_payload({"model": "weather"}) is False
    empty = oem_hint_from_payload({"model": "zzz_unknown"})
    assert empty["oem_family"] is None


def test_chirp_synth_and_rms(tmp_path: Path) -> None:
    out = tmp_path / "chirp.cs8"
    path = synthesize_chirp_cs8(
        sample_rate=2_000_000,
        bandwidth_hz=1_000_000,
        sweep_period_s=0.002,
        amplitude=64,
        path=out,
    )
    assert path.exists()
    assert estimate_rms_dbfs(path) > -40.0
    with pytest.raises(ValueError):
        synthesize_chirp_cs8(
            sample_rate=2_000_000,
            bandwidth_hz=0,
            sweep_period_s=0.001,
            path=tmp_path / "bad.cs8",
        )
    with pytest.raises(ValueError):
        synthesize_chirp_cs8(
            sample_rate=2_000_000,
            bandwidth_hz=3_000_000,
            sweep_period_s=0.001,
            path=tmp_path / "wide.cs8",
        )
    with pytest.raises(ValueError):
        synthesize_chirp_cs8(
            sample_rate=2_000_000,
            bandwidth_hz=1_000_000,
            sweep_period_s=0.0,
            path=tmp_path / "zero_t.cs8",
        )
    with pytest.raises(ValueError):
        synthesize_chirp_cs8(
            sample_rate=2_000_000,
            bandwidth_hz=1_000_000,
            sweep_period_s=0.001,
            amplitude=0,
            path=tmp_path / "amp0.cs8",
        )
    with pytest.raises(ValueError):
        synthesize_chirp_cs8(
            sample_rate=2_000_000,
            bandwidth_hz=25_000_000,
            sweep_period_s=0.001,
            path=tmp_path / "over20.cs8",
        )
    with pytest.raises(ValueError):
        synthesize_chirp_cs8(
            sample_rate=2_000_000,
            bandwidth_hz=100_000,
            sweep_period_s=1e-8,
            path=tmp_path / "short.cs8",
        )
    empty = tmp_path / "empty.cs8"
    empty.write_bytes(b"")
    assert estimate_rms_dbfs(empty) == float("-inf")
    odd = tmp_path / "odd.cs8"
    odd.write_bytes(b"\x01\x02\x03")
    assert isinstance(estimate_rms_dbfs(odd), float)


def test_spa_missing_dist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pp_dashboard.web.spa.FRONTEND_DIST", tmp_path / "no-dist")
    assert frontend_dist_ready() is False
    assert spa_static_file("x") is None
    assert spa_static_file("") is None
    assert spa_static_file("foo/../../etc/passwd") is None
    with pytest.raises(HTTPException):
        spa_index()

    # Mount returns False when dist missing
    from fastapi import FastAPI
    from pp_dashboard.web.spa import mount_spa

    assert mount_spa(FastAPI()) is False


def test_touchtunes_pin_validation(tmp_path: Path) -> None:
    from pp_dashboard.recon.synth_pager import synthesize_touchtunes_pin

    with pytest.raises(ValueError):
        synthesize_touchtunes_pin("", path=tmp_path / "p.cs8")
    frame = synthesize_touchtunes_pin("1234", path=tmp_path / "ok.cs8")
    assert frame.path.exists()
    assert frame.protocol == "TOUCHTUNES_FSK"
