"""HTTP smoke for v0.9.0 fingerprint + geo-hunt routes (coverage + contract)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app
from pp_dashboard.mayhem.bridge import MayhemSerialBridge


def _tone_cs8(path: Path, *, cfo_hz: float = 5000.0) -> Path:
    sr = 2_000_000
    n = np.arange(40_000, dtype=np.float64)
    phase = 2.0 * math.pi * (cfo_hz / sr) * n
    env = np.ones(40_000, dtype=np.float64)
    env[:150] = np.linspace(0.05, 1.0, 150)
    z = (80 / 127.0) * env * np.exp(1j * phase)
    i = np.clip(np.rint(z.real * 127), -128, 127).astype(np.int8)
    q = np.clip(np.rint(z.imag * 127), -128, 127).astype(np.int8)
    out = np.empty(80_000, dtype=np.int8)
    out[0::2] = i
    out[1::2] = q
    path.write_bytes(out.tobytes())
    return path


@pytest.fixture
async def client_v090(isolate_hardware, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from pp_dashboard.config import settings
    from pp_dashboard.geoloc.live_fusion import LiveGeoHuntFusion
    from pp_dashboard.geoloc.service import GeolocService
    from pp_dashboard.recon.engine import ReconEngine
    from pp_dashboard.subghz.engine import SubGhzEngine
    from pp_dashboard.tpms.engine import TpmsEngine

    assert isolate_hardware is not None
    monkeypatch.setattr(settings, "captures_dir", tmp_path / "captures")
    settings.captures_dir.mkdir(parents=True, exist_ok=True)

    HardwareArbiter.reset_instance_for_tests()
    MayhemSerialBridge.reset_instance_for_tests()
    GeolocService.reset_instance_for_tests()
    LiveGeoHuntFusion.reset_instance_for_tests()
    ReconEngine._instance = None  # noqa: SLF001
    TpmsEngine._instance = None  # noqa: SLF001
    SubGhzEngine._instance = None  # noqa: SLF001

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, settings.captures_dir


@pytest.mark.asyncio
async def test_fingerprint_and_hunt_http(client_v090) -> None:
    client, captures = client_v090
    iq = _tone_cs8(captures / "fp_api.cs8", cfo_hz=8000.0)

    fp = await client.post(
        "/api/subghz/fingerprint/analyze",
        json={
            "filepath": str(iq),
            "sample_rate": 2_000_000,
            "nominal_freq_hz": 433_920_000,
            "digital_device_id": "API-1",
            "bits_hex": "DEAD",
        },
    )
    assert fp.status_code == 200, fp.text[:300]
    body = fp.json()
    assert body["physical_emitter_id"].startswith("PHY-")
    assert "fingerprint" in body

    emitters = await client.get("/api/subghz/fingerprint/emitters")
    assert emitters.status_code == 200
    assert len(emitters.json()["emitters"]) >= 1

    start = await client.post("/api/geoloc/hunt/start")
    assert start.status_code == 200
    assert start.json()["running"] is True

    st = await client.get("/api/geoloc/hunt/status")
    assert st.status_code == 200 and st.json()["running"] is True

    # Six spaced hits → geohunt solve path
    base_lat, base_lon = 49.12, -1.10
    for i in range(6):
        r = await client.post(
            "/api/geoloc/hunt/ingest",
            json={
                "lat": base_lat + i * 0.0003,
                "lon": base_lon + i * 0.00025,
                "rssi_db": -45.0 - i * 1.5,
                "freq_hz": 433_920_000,
                "emitter_id": "PHY-API",
                "timestamp": 1_700_000_000.0 + i * 10,
            },
        )
        assert r.status_code == 200, r.text[:200]

    br = await client.post(
        "/api/geoloc/hunt/bearing",
        json={
            "lat": base_lat,
            "lon": base_lon,
            "bearing_deg": 45.0,
            "emitter_id": "LOB-API",
        },
    )
    assert br.status_code == 200

    # Doppler fit
    f0 = 433_920_000.0
    c = 299_792_458.0
    v, rmin, tc = 25.0, 120.0, 40.0
    samples = []
    for t in np.linspace(10.0, 70.0, 20):
        dt = float(t) - tc
        v_rad = (v * v * dt) / math.sqrt(rmin**2 + (v * dt) ** 2)
        samples.append({"timestamp": float(t), "measured_cfo_hz": -v_rad * f0 / c})
    dop = await client.post(
        "/api/geoloc/doppler/fit",
        json={"samples": samples, "nominal_freq_hz": f0},
    )
    assert dop.status_code == 200, dop.text[:300]
    assert "velocity_m_s" in dop.json()

    stop = await client.post("/api/geoloc/hunt/stop")
    assert stop.status_code == 200
    assert stop.json()["running"] is False
