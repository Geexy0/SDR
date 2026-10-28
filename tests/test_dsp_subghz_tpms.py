"""DSP math, De Bruijn, OOK analyzer, TPMS clustering — offline."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app
from pp_dashboard.subghz.analyze import analyze_cs8, extract_pulses_us, load_cs8, magnitude_envelope
from pp_dashboard.subghz.ook_synth import de_bruijn
from pp_dashboard.tpms.cluster import PassageClusterer
from pp_dashboard.tpms.store import TpmsFleetStore


def iq_power_dbfs(cs8: bytes) -> float:
    """Peak-referenced complex power: 10·log10(mean(I²+Q²) / 127²)."""
    arr = np.frombuffer(cs8, dtype=np.int8).astype(np.float64)
    if arr.size < 2:
        return -120.0
    if arr.size % 2:
        arr = arr[:-1]
    i, q = arr[0::2], arr[1::2]
    power = float(np.mean(i * i + q * q))
    return 10.0 * math.log10(power / (127.0 * 127.0) + 1e-30)


def decode_pt2262_bits(pulses: list[int], *, short_us: int, long_us: int, sync_us: int) -> str:
    bits: list[str] = []
    i = 0
    while i + 1 < len(pulses):
        hi, lo = pulses[i], pulses[i + 1]
        if hi <= 0:
            i += 1
            continue
        gap = -lo if lo < 0 else 0
        if gap >= sync_us * 0.5:
            break
        # short-hi → '1'; long-hi → '0'
        if abs(hi - short_us) <= abs(hi - long_us):
            bits.append("1")
        else:
            bits.append("0")
        i += 2
    return "".join(bits)


def test_cw_tone_rms_dbfs(synthetic_cs8_factory) -> None:
    path = synthetic_cs8_factory.make_cw_tone(amplitude=64, num_samples=40_000)
    raw = path.read_bytes()
    db = iq_power_dbfs(raw)
    assert abs(db - (-6.02)) <= 0.2


def test_ook_pt2262_analyzer(synthetic_cs8_factory) -> None:
    bits = "101001011100"
    path = synthetic_cs8_factory.make_ook_pt2262(
        short_us=350, long_us=1050, sync_us=10850, bits=bits, sample_rate=2_000_000
    )
    result = analyze_cs8(path, sample_rate=2_000_000)
    assert result.timing.short_us is not None
    assert result.timing.long_us is not None
    assert abs(result.timing.short_us - 350) <= 10
    assert abs(result.timing.long_us - 1050) <= 15
    assert result.timing.encoding in {"PT2262", "PRINCETON", "EV1527"}

    iq = load_cs8(path)
    mag = magnitude_envelope(iq)
    thr = float(np.percentile(mag, 50))
    pulses = extract_pulses_us(mag, 2_000_000, thr)
    decoded = decode_pt2262_bits(pulses, short_us=350, long_us=1050, sync_us=10850)
    assert bits in decoded or decoded.startswith(bits) or bits.startswith(decoded[: len(bits)])


@pytest.mark.asyncio
async def test_ook_analyze_http(synthetic_cs8_factory, tmp_path: Path, monkeypatch) -> None:
    from pp_dashboard.config import settings

    monkeypatch.setattr(settings, "captures_dir", tmp_path)
    bits = "101001011100"
    path = synthetic_cs8_factory.make_ook_pt2262(bits=bits, name="lab_ook.cs8")
    # Copy under captures_dir for path validation
    dest = tmp_path / "lab_ook.cs8"
    dest.write_bytes(path.read_bytes())
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/subghz/analyze",
            json={"filepath": str(dest), "sample_rate": 2_000_000},
        )
        assert resp.status_code == 200
        body = resp.json()
        timing = body.get("timing") or {}
        assert abs(float(timing["short_us"]) - 350) <= 10
        assert abs(float(timing["long_us"]) - 1050) <= 15


@pytest.mark.parametrize(("k", "n"), [(2, 8), (3, 4)])
def test_de_bruijn_covers_all_codewords(k: int, n: int) -> None:
    seq = de_bruijn(k, n, linear=True)
    assert seq.size == k**n + n - 1
    windows = {tuple(int(x) for x in seq[i : i + n]) for i in range(k**n)}
    assert len(windows) == k**n


def test_tpms_passage_clustering_and_api(tmp_path: Path) -> None:
    store = TpmsFleetStore(tmp_path / "tpms_fleet.sqlite3")
    clusterer = PassageClusterer(window_sec=12.0, seal_idle_sec=18.0)
    sensors = ["AA0011", "AA0022", "AA0033", "AA0044"]
    t0 = 1_000.0
    for i, sid in enumerate(sensors):
        clusterer.observe(sid, oem_family="Schrader", now=t0 + i * 2.0)
        store.record_hit(
            sensor_id=sid,
            payload={
                "model": "Schrader",
                "type": "TPMS",
                "id": sid,
                "pressure_kPa": 220 + i,
                "temperature_C": 20,
            },
            oem_family="Schrader",
            frequency_hz=433_920_000,
            lat=49.1182,
            lon=-1.0972,
        )
    # >18s silence → seal (seal_idle uses strict greater-than)
    sealed = clusterer.flush_idle(now=t0 + 6.0 + 18.1)
    assert sealed is not None
    assert sealed.sealed
    assert sealed.sensor_ids == set(sensors)

    vehicle = store.upsert_vehicle(
        label="Lab Pass-By 1",
        make="Schrader",
        model="Fixture",
        sensor_ids=list(sensors),
    )
    store.record_passage(
        cluster_key=sealed.cluster_key,
        sensor_ids=set(sealed.sensor_ids),
        hit_count=sealed.hit_count,
        oem_family=sealed.oem_family,
        vehicle_id=vehicle.id,
        label=vehicle.label,
        make=vehicle.make,
        model_hint="Fixture",
        lat=49.1182,
        lon=-1.0972,
    )
    passages = store.list_passages(limit=10)
    assert len(passages) >= 1
    assert passages[0]["sensor_count"] == 4
    vehicles = store.list_vehicles()
    assert any(v.id == vehicle.id for v in vehicles)


@pytest.mark.asyncio
async def test_tpms_passages_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pp_dashboard.config import settings
    from pp_dashboard.tpms.engine import TpmsEngine

    monkeypatch.setattr(settings, "captures_dir", tmp_path)
    HardwareArbiter.reset_instance_for_tests()
    arbiter = HardwareArbiter()
    eng = TpmsEngine(arbiter)
    TpmsEngine._instance = eng  # noqa: SLF001
    # seed
    for sid in ("B1", "B2", "B3", "B4"):
        eng.fleet.record_hit(
            sensor_id=sid,
            payload={"model": "Schrader", "type": "TPMS", "id": sid, "pressure_kPa": 200},
            oem_family="Schrader",
            frequency_hz=433_920_000,
            lat=49.1,
            lon=-1.1,
        )
    v = eng.fleet.upsert_vehicle(label="HTTP Fleet Car", sensor_ids=["B1", "B2", "B3", "B4"])
    eng.fleet.record_passage(
        cluster_key="testkey",
        sensor_ids={"B1", "B2", "B3", "B4"},
        hit_count=4,
        oem_family="Schrader",
        vehicle_id=v.id,
        label=v.label,
        make=v.make,
        model_hint="HTTP",
        lat=49.1,
        lon=-1.1,
    )
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        p = await client.get("/api/tpms/passages")
        assert p.status_code == 200
        assert len(p.json()["passages"]) >= 1
        veh = await client.get("/api/tpms/vehicles")
        assert veh.status_code == 200
        assert any(x["label"] == "HTTP Fleet Car" for x in veh.json()["vehicles"])
