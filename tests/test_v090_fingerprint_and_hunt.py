"""v0.9.0 analytical offline tests — RF fingerprint + live geo-hunt fusion."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from pp_dashboard.geoloc.live_fusion import LiveGeoHuntFusion, fit_doppler_pass
from pp_dashboard.geoloc.projection import LocalTangentPlane
from pp_dashboard.subghz.rf_fingerprint import (
    EmitterFingerprintStore,
    analyze_and_cluster,
    extract_rf_fingerprint,
)


def _cs8_tone(
    *,
    sample_rate: int,
    cfo_hz: float,
    num_samples: int,
    amplitude: float = 80.0,
    rise_samples: int = 200,
) -> np.ndarray:
    """Complex float32 baseband CW with optional amplitude rise, then int8 IQ bytes."""
    n = np.arange(num_samples, dtype=np.float64)
    phase = 2.0 * math.pi * (cfo_hz / float(sample_rate)) * n
    env = np.ones(num_samples, dtype=np.float64)
    if rise_samples > 1:
        rise = min(rise_samples, num_samples // 4)
        env[:rise] = np.linspace(0.05, 1.0, rise, dtype=np.float64)
        # brief overshoot
        over = min(40, num_samples - rise)
        if over > 0:
            env[rise : rise + over] = 1.0 + 0.15 * np.exp(
                -np.arange(over, dtype=np.float64) / 12.0
            )
    z = (amplitude / 127.0) * env * np.exp(1j * phase)
    i = np.clip(np.rint(z.real * 127.0), -128, 127).astype(np.int8)
    q = np.clip(np.rint(z.imag * 127.0), -128, 127).astype(np.int8)
    out = np.empty(num_samples * 2, dtype=np.int8)
    out[0::2] = i
    out[1::2] = q
    return out


def test_cfo_hz_and_ppm_recovery_12k5(tmp_path: Path) -> None:
    sample_rate = 2_000_000
    nominal = 433_920_000
    iq = _cs8_tone(sample_rate=sample_rate, cfo_hz=12_500.0, num_samples=80_000)
    path = tmp_path / "cfo_12k5.cs8"
    path.write_bytes(iq.tobytes())

    fp = extract_rf_fingerprint(path, sample_rate, nominal)
    assert abs(fp["cfo_hz"] - 12_500.0) <= 50.0, fp
    # 12500 / 433920000 * 1e6 ≈ 28.807 ppm
    assert abs(fp["cfo_ppm"] - 28.81) <= 0.15, fp
    assert "rise_time_us" in fp and "phase_noise_rad_rms" in fp
    assert "clock_skew_ppm" in fp and "overshoot_db" in fp


def test_clone_alert_same_digital_id_different_cfo(tmp_path: Path) -> None:
    sample_rate = 2_000_000
    nominal = 433_920_000
    store = EmitterFingerprintStore(tmp_path / "rf_emitters.sqlite3")

    iq_a = _cs8_tone(sample_rate=sample_rate, cfo_hz=2_000.0, num_samples=60_000)
    iq_b = _cs8_tone(sample_rate=sample_rate, cfo_hz=-18_000.0, num_samples=60_000)
    path_a = tmp_path / "a.cs8"
    path_b = tmp_path / "b.cs8"
    path_a.write_bytes(iq_a.tobytes())
    path_b.write_bytes(iq_b.tobytes())

    r1 = analyze_and_cluster(
        store,
        path_a,
        sample_rate=sample_rate,
        nominal_freq_hz=nominal,
        digital_device_id="DEV-AABBCC",
        bits_hex="AABBCC01",
    )
    assert r1["clone_alert"] is False
    assert r1["physical_emitter_id"].startswith("PHY-")

    r2 = analyze_and_cluster(
        store,
        path_b,
        sample_rate=sample_rate,
        nominal_freq_hz=nominal,
        digital_device_id="DEV-AABBCC",
        bits_hex="AABBCC01",
    )
    assert r2["clone_alert"] is True, r2
    assert r2["physical_emitter_id"] != r1["physical_emitter_id"]


@pytest.mark.asyncio
async def test_live_geo_hunt_emits_update_within_15m() -> None:
    events: list[dict] = []

    async def fanout(ev: dict) -> None:
        events.append(ev)

    fusion = LiveGeoHuntFusion(on_telemetry=fanout)
    await fusion.start()

    # True emitter ~80 m east of origin
    origin_lat, origin_lon = 49.1200, -1.1000
    frame = LocalTangentPlane(origin_lat, origin_lon)
    true_e, true_n = 80.0, 10.0
    true_lat, true_lon, _ = frame.enu_to_llh(true_e, true_n, 0.0)
    p0_db = -40.0
    gamma = 2.8

    # Six walk points with >= 15 m spatial spread and path-loss RSSI
    walk_enu = [
        (0.0, 0.0),
        (25.0, 5.0),
        (50.0, -10.0),
        (70.0, 20.0),
        (90.0, 0.0),
        (40.0, 40.0),
    ]
    t0 = 1_700_000_000.0
    for i, (e, n) in enumerate(walk_enu):
        lat, lon, _ = frame.enu_to_llh(e, n, 0.0)
        d = max(math.hypot(e - true_e, n - true_n), 10.0)
        rssi = p0_db - 10.0 * gamma * math.log10(d)
        await fusion.ingest_rx_hit(
            lat=lat,
            lon=lon,
            rssi_db=rssi,
            freq_hz=433_920_000,
            emitter_id="PHY-001",
            timestamp=t0 + i * 8.0,
        )

    await fusion.flush_pending()
    await fusion.stop()

    updates = [e for e in events if e.get("type") == "geohunt_update"]
    assert updates, f"expected geohunt_update, got {events!r}"
    pin = updates[-1]["metrics"]["ml_pin"]
    pin_lat, pin_lon = float(pin["lat"]), float(pin["lon"])
    pe, pn, _ = frame.llh_to_enu(pin_lat, pin_lon, 0.0)
    err = math.hypot(pe - true_e, pn - true_n)
    assert err <= 15.0, f"ML pin error {err:.2f} m > 15 m (pin={[pin_lat, pin_lon]})"


def test_doppler_fit_radial_velocity() -> None:
    f0 = 433_920_000.0
    c = 299_792_458.0
    v = 30.0  # m/s closing then opening
    r_min = 100.0
    t_cpa = 50.0
    samples = []
    for t in np.linspace(20.0, 80.0, 25):
        dt = float(t) - t_cpa
        # Classical flyby: v_rad = v² t / sqrt(R² + (v t)²)
        v_rad = (v * v * dt) / math.sqrt(r_min**2 + (v * dt) ** 2)
        cfo = -v_rad * f0 / c
        samples.append((float(t), float(cfo)))
    fit = fit_doppler_pass(samples, nominal_freq_hz=f0)
    assert abs(fit["cpa_timestamp"] - t_cpa) < 3.0
    assert abs(abs(fit["velocity_m_s"]) - v) < 8.0
    assert abs(fit["min_range_m"] - r_min) < 40.0
