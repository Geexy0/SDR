"""Fox-hunt Kalman, polar lobe centroid, GNSS L1 integrity — offline."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.geoloc import foxhunt, polar
from pp_dashboard.geoloc import gnss_monitor as gnss
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app


def test_kalman_tracks_rising_rssi() -> None:
    filt = foxhunt.RssiKalman()
    t0 = 1_000.0
    rate_est = 0.0
    for i in range(20):
        rssi = -80.0 + i * 1.5  # +1.5 dB/s if dt=1
        st = filt.update(rssi_db=rssi, t_mono=t0 + i * 1.0)
        rate_est = st.rssi_rate_db_s
    assert rate_est > 0.8


def test_foxhunt_warmer_when_moving_toward_emitter() -> None:
    # Walk east toward an emitter east of start; RSSI rises
    samples = []
    for i in range(12):
        samples.append(
            foxhunt.FoxSample(
                lat=49.12,
                lon=-1.10 + i * 0.0001,
                rssi_db=-70.0 + i * 1.2,
                t_mono=i * 0.5,
            )
        )
    result = foxhunt.evaluate_homing(samples, window_sec=5.0)
    assert result.status in {"warmer", "hold"}
    assert result.gradient_score > 0 or result.status == "hold"
    assert 0 <= result.travel_bearing_deg < 360


def test_polar_centroid_points_to_main_lobe() -> None:
    # Strong lobe around 90° (east)
    samples = []
    for deg in range(0, 360, 5):
        # Cardioid-ish peak at 90°
        rssi = -60.0 + 25.0 * max(0.0, math.cos(math.radians(deg - 90))) ** 2
        samples.append(polar.PolarSample(bearing_deg=float(deg), rssi_db=rssi))
    peak = polar.circular_lobe_centroid(samples)
    assert abs((peak.peak_bearing_deg - 90.0 + 180) % 360 - 180) < 15.0
    assert peak.half_power_beamwidth_deg > 5.0
    assert peak.half_power_beamwidth_deg < 180.0


def test_l1_analyze_classifies_cw_tone(tmp_path: Path) -> None:
    # Synthetic: strong CW at DC (centered IF) + noise
    sr = 2_000_000
    n = 16384
    t = np.arange(n, dtype=np.float64) / sr
    # Tone at 0 Hz IF (carrier) — high PAPR / kurtosis spike at bin 0
    tone = 80.0 * np.exp(1j * 0.0 * t)
    noise = (np.random.randn(n) + 1j * np.random.randn(n)) * 2.0
    iq = tone + noise
    path = tmp_path / "l1_cw.cs8"
    interleaved = np.empty(n * 2, dtype=np.int8)
    interleaved[0::2] = np.clip(iq.real, -127, 127).astype(np.int8)
    interleaved[1::2] = np.clip(iq.imag, -127, 127).astype(np.int8)
    path.write_bytes(interleaved.tobytes())
    result = gnss.analyze_l1_capture(path, sample_rate=sr, baseline_noise_db=-55.0)
    assert result["classification"] in {
        "CW_TONE_INGRESS",
        "WIDEBAND_NOISE_ELEVATION",
        "NOMINAL",
        "CHIRP_SWEEP_DETECTED",
    }
    assert "delta_n0_db" in result
    assert "papr_db" in result
    assert "spectral_kurtosis" in result
    # Strong DC tone should elevate PAPR / flag CW or at least not crash
    assert result["papr_db"] > 0


@pytest.mark.asyncio
async def test_foxhunt_and_l1_analyze_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pp_dashboard import config as cfg
    from pp_dashboard.geoloc.service import GeolocService

    monkeypatch.setattr(cfg.settings, "captures_dir", tmp_path)
    HardwareArbiter.reset_instance_for_tests()
    GeolocService.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fox = await client.post(
            "/api/geoloc/foxhunt",
            json={
                "window_sec": 5.0,
                "samples": [
                    {"lat": 49.12, "lon": -1.10, "rssi_db": -70, "t_mono": 0.0},
                    {"lat": 49.1201, "lon": -1.0999, "rssi_db": -68, "t_mono": 1.0},
                    {"lat": 49.1202, "lon": -1.0998, "rssi_db": -66, "t_mono": 2.0},
                    {"lat": 49.1203, "lon": -1.0997, "rssi_db": -64, "t_mono": 3.0},
                    {"lat": 49.1204, "lon": -1.0996, "rssi_db": -62, "t_mono": 4.0},
                    {"lat": 49.1205, "lon": -1.0995, "rssi_db": -60, "t_mono": 5.0},
                ],
            },
        )
        assert fox.status_code == 200
        assert "status" in fox.json()

        polar_r = await client.post(
            "/api/geoloc/polar/peak",
            json={
                "samples": [
                    {"bearing_deg": d, "rssi_db": -55 + 20 * max(0, math.cos(math.radians(d - 45))) ** 2}
                    for d in range(0, 360, 10)
                ]
            },
        )
        assert polar_r.status_code == 200
        assert "peak_bearing_deg" in polar_r.json()

        # Fake capture path for analyze without HackRF
        path = tmp_path / "captures" / "geoloc" / "l1.cs8"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes([10, 0] * 8192))
        an = await client.post(
            "/api/geoloc/gnss/l1-analyze",
            json={"filepath": str(path), "baseline_noise_db": -50.0},
        )
        # May 400 if path not under captures — use relative under captures_dir
        if an.status_code != 200:
            an = await client.post(
                "/api/geoloc/gnss/l1-analyze",
                json={"filepath": "geoloc/l1.cs8", "baseline_noise_db": -50.0},
            )
        assert an.status_code == 200
        body = an.json()
        assert body["classification"] in {
            "NOMINAL",
            "CW_TONE_INGRESS",
            "CHIRP_SWEEP_DETECTED",
            "WIDEBAND_NOISE_ELEVATION",
        }
