"""Radio horizon / look-angle + radiosonde parse & track prediction — offline."""

from __future__ import annotations

import math

import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.geoloc import horizon
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app
from pp_dashboard.recon import radiosonde as rs


def test_radio_horizon_km_4_3_earth() -> None:
    # 4.12*(√2 + √10000) ≈ 4.12*(1.414+100) ≈ 417.8 km
    d = horizon.radio_horizon_km(h_rx_m=2.0, h_tx_m=10_000.0)
    assert 410.0 < d < 430.0


def test_look_angle_overhead_high_elevation() -> None:
    # Target nearly overhead: high elevation, short slant vs ground range
    la = horizon.look_angle(
        rx_lat=49.12,
        rx_lon=-1.10,
        rx_alt_m=50.0,
        tx_lat=49.1205,
        tx_lon=-1.1005,
        tx_alt_m=8_000.0,
        frequency_hz=1090_000_000,
    )
    assert la.elevation_deg > 60.0
    assert la.slant_range_m > 7_500.0
    assert 0.0 <= la.azimuth_deg < 360.0
    assert la.fspl_db > 80.0
    assert la.horizon_km > 0.0


def test_fspl_scales_with_range_and_freq() -> None:
    a = horizon.fspl_db(slant_range_m=1_000.0, frequency_hz=100e6)
    b = horizon.fspl_db(slant_range_m=2_000.0, frequency_hz=100e6)
    c = horizon.fspl_db(slant_range_m=1_000.0, frequency_hz=200e6)
    assert b - a == pytest.approx(6.0, abs=0.05)
    assert c - a == pytest.approx(6.0, abs=0.05)


def test_rs41_sync_word_constants() -> None:
    assert rs.RS41_SYNC == 0x10B6CA11
    assert rs.RS41_SYNC_INV == (~0x10B6CA11) & 0xFFFFFFFF
    assert rs.BAND_HZ == (400_150_000, 406_000_000)


def test_correlate_rs41_sync_finds_normal_and_inverted() -> None:
    # Pack sync as 32 bits MSB-first into a bit array with noise padding
    bits = [0] * 40 + rs.sync_bits(rs.RS41_SYNC) + [1, 0, 1]
    hit = rs.correlate_sync(bits)
    assert hit is not None
    assert hit.offset == 40
    assert hit.inverted is False

    bits_inv = [1] * 16 + rs.sync_bits(rs.RS41_SYNC_INV) + [0] * 8
    hit2 = rs.correlate_sync(bits_inv)
    assert hit2 is not None
    assert hit2.inverted is True


def test_parse_rs41mod_line() -> None:
    line = (
        "RS41 ID=S3140789 lat=49.12345 lon=-1.09876 alt=15234.5 "
        "vz=5.2 dir=270.0 sp=12.5 temp=-48.2 freq=402.500"
    )
    frame = rs.parse_sonde_text_line(line, default_freq_hz=402_500_000)
    assert frame is not None
    assert frame.serial_id == "S3140789"
    assert frame.lat == pytest.approx(49.12345)
    assert frame.lon == pytest.approx(-1.09876)
    assert frame.alt_m == pytest.approx(15234.5)
    assert frame.vz_mps == pytest.approx(5.2)
    assert frame.wind_dir_deg == pytest.approx(270.0)
    assert frame.wind_speed_mps == pytest.approx(12.5)
    assert frame.temp_c == pytest.approx(-48.2)
    assert frame.frequency_hz == 402_500_000


def test_parse_builtin_rs41_payload_block() -> None:
    payload = rs.build_test_rs41_payload(
        serial="T1234567",
        lat=48.85,
        lon=2.35,
        alt_m=5000.0,
        vz_mps=4.0,
        wind_speed_mps=8.0,
        wind_dir_deg=90.0,
        temp_c=-20.0,
        frame_n=42,
    )
    frame = rs.decode_rs41_payload(payload, frequency_hz=403_000_000)
    assert frame is not None
    assert frame.serial_id == "T1234567"
    assert abs(frame.lat - 48.85) < 1e-4
    assert abs(frame.lon - 2.35) < 1e-4
    assert abs(frame.alt_m - 5000.0) < 1.0
    assert frame.frame_n == 42


def test_track_burst_and_landing_prediction() -> None:
    store = rs.SondeTrackStore()
    # Ascending balloon; wind_dir is meteorological FROM (270° = westerly → drifts east)
    for i in range(8):
        store.ingest(
            rs.SondeFrame(
                serial_id="S1",
                lat=49.12,
                lon=-1.10 + i * 0.001,
                alt_m=1000.0 + i * 500.0,
                vz_mps=5.0,
                wind_speed_mps=10.0,
                wind_dir_deg=270.0,
                temp_c=-10.0,
                frequency_hz=402_000_000,
                t_mono=float(i),
            )
        )
    track = store.get("S1")
    assert track is not None
    assert len(track.trajectory) >= 8
    assert track.burst is not None
    assert track.burst.alt_m >= track.trajectory[-1].alt_m
    assert track.landing is not None
    assert track.landing.alt_m == pytest.approx(0.0, abs=1.0)
    # Westerly wind → landing lon east of current
    assert track.landing.lon > track.trajectory[-1].lon


@pytest.mark.asyncio
async def test_look_angle_and_radiosonde_http(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PP_CAPTURES_DIR", str(tmp_path / "captures"))
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/geoloc/look-angle",
            json={
                "rx_lat": 49.12,
                "rx_lon": -1.10,
                "rx_alt_m": 40.0,
                "tx_lat": 49.15,
                "tx_lon": -1.05,
                "tx_alt_m": 3000.0,
                "frequency_hz": 1090_000_000,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "azimuth_deg" in body
        assert "elevation_deg" in body
        assert "slant_range_m" in body
        assert "fspl_db" in body
        assert "horizon_km" in body

        # Inject radiosonde fix without hardware
        inj = await client.post(
            "/api/recon/radiosonde/inject",
            json={
                "serial_id": "S999",
                "lat": 49.13,
                "lon": -1.09,
                "alt_m": 12000.0,
                "vz_mps": 4.5,
                "wind_speed_mps": 15.0,
                "wind_dir_deg": 180.0,
                "temp_c": -40.0,
                "frequency_hz": 402_500_000,
            },
        )
        assert inj.status_code == 200
        tracks = await client.get("/api/recon/radiosonde/tracks")
        assert tracks.status_code == 200
        data = tracks.json()
        assert any(t["serial_id"] == "S999" for t in data["tracks"])
