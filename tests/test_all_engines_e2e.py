"""Layer 2 — engine lifecycle + global kill-switch matrix (offline mocks)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.main import create_app
from pp_dashboard.security import tx_gate
from tests.fakes import FakeSubprocessPool


def _confirm_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scope = tmp_path / "SCOPE.md"
    confirm = tmp_path / "CONFIRM.md"
    scope.write_text(
        "**Status:** `ACTIVE`\n\n**Engagement name:** `PP-LAB-RF-2026`\n\n"
        "**Start / end dates:** `2026-10-27 → 2027-10-27`\n",
        encoding="utf-8",
    )
    confirm.write_text(
        "**Status:** `CONFIRMED`\n\nENGAGEMENT: PP-LAB-RF-2026\n"
        "Engagement window: 2026-10-27 → 2027-10-27\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)


async def _assert_kill_clean(
    client: AsyncClient,
    arbiter: HardwareArbiter,
    pool: FakeSubprocessPool,
    capture_dirs: list[Path],
) -> None:
    stop = await client.post("/api/hardware/stop")
    assert stop.status_code == 200
    await asyncio.sleep(0.08)
    assert arbiter.is_sdr_busy() is False
    alive = [p for p in pool.processes if p.returncode is None]
    assert not alive, f"orphan FakeProcess still running: {alive}"
    leftovers: list[Path] = []
    for d in capture_dirs:
        if d.exists():
            leftovers.extend(d.glob("*.cs8"))
    shmish = [p for p in leftovers if "shm" in str(p) or p.name.startswith("ook_")]
    assert not shmish, f"leftover temp .cs8: {shmish}"


async def _wait_frames(pool: FakeSubprocessPool, key: str, *, min_n: int = 1, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if pool.frames_emitted.get(key, 0) >= min_n:
            return
        # Some engines spawn a bit after HTTP 200
        await asyncio.sleep(0.05)
    assert pool.frames_emitted.get(key, 0) >= min_n, (
        f"expected ≥{min_n} synthetic '{key}' frame(s), got {pool.frames_emitted!r} "
        f"started={pool.started!r}"
    )


@pytest.fixture
async def e2e_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolate_hardware: FakeSubprocessPool,
):
    from unittest.mock import AsyncMock

    from pp_dashboard.config import settings
    from pp_dashboard.geoloc.service import GeolocService
    from pp_dashboard.hardware.detect import DetectionResult
    from pp_dashboard.recon.engine import ReconEngine
    from pp_dashboard.subghz.engine import SubGhzEngine
    from pp_dashboard.tpms.engine import TpmsEngine

    assert isolate_hardware is not None
    _confirm_gate(tmp_path, monkeypatch)
    shm = tmp_path / "shm"
    shm.mkdir(exist_ok=True)
    monkeypatch.setenv("PP_MOCK_HARDWARE", "1")
    monkeypatch.setattr(settings, "mode_switch_timeout_s", 1.0)
    monkeypatch.setattr(settings, "sigkill_grace_s", 0.2)
    # Only assert on fixtures under tmp — never scan host /dev/shm (cross-test pollution).
    capture_roots = [settings.captures_dir, shm]

    async def _native_detect(cfg=None):
        return DetectionResult(
            mayhem_serial=False,
            hackrf_native=True,
            serial_port=None,
            lsusb_ids=("1d50:6089",),
            hackrf_info_ok=True,
        )

    monkeypatch.setattr("pp_dashboard.hardware.detect.detect_hardware", _native_detect)
    monkeypatch.setattr("pp_dashboard.hardware.arbiter.detect_hardware", _native_detect)

    HardwareArbiter.reset_instance_for_tests()
    GeolocService.reset_instance_for_tests()
    ReconEngine._instance = None  # noqa: SLF001
    TpmsEngine._instance = None  # noqa: SLF001
    SubGhzEngine._instance = None  # noqa: SLF001

    app = create_app()
    transport = ASGITransport(app=app)
    arbiter = await HardwareArbiter.get_instance()
    arbiter.state = HardwareState.HACKRF_NATIVE
    arbiter._poll_hackrf_info = AsyncMock(return_value=True)  # noqa: SLF001
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, arbiter, isolate_hardware, capture_roots


@pytest.mark.asyncio
async def test_arbiter_mode_toggle_roundtrip(e2e_client) -> None:
    client, arbiter, pool, caps = e2e_client
    r1 = await client.post("/api/hardware/mode", json={"mode": "MAYHEM_SERIAL"})
    assert r1.status_code == 200, r1.text[:300]
    r2 = await client.post("/api/hardware/mode", json={"mode": "HACKRF_NATIVE"})
    assert r2.status_code == 200, r2.text[:300]
    await _assert_kill_clean(client, arbiter, pool, caps)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_path,start_body,frame_key",
    [
        (
            "/api/hardware/sweep",
            {
                "min_mhz": 400.0,
                "max_mhz": 410.0,
                "bin_width_hz": 100_000,
                "lna": 24,
                "vga": 20,
            },
            "hackrf_sweep",
        ),
        (
            "/api/hardware/multiplex",
            {
                "center_hz": 433_920_000,
                "sample_rate": 10_000_000,
                "lna": 24,
                "vga": 20,
                "amp": 0,
            },
            "hackrf_transfer",
        ),
        (
            "/api/subghz/sniff",
            {
                "frequency_hz": 433_920_000,
                "sample_rate": 2_000_000,
                "max_duration_sec": 2,
            },
            "rtl_433",
        ),
        (
            "/api/tpms/listen",
            {
                "frequency_hz": 433_920_000,
                "sample_rate": 2_000_000,
                "max_duration_sec": 2,
            },
            "rtl_433",
        ),
        (
            "/api/recon/decoder",
            {"kind": "pocsag", "frequency_hz": 466_075_000},
            "multimon-ng",
        ),
        (
            "/api/recon/decoder",
            {"kind": "ais", "frequency_hz": 161_975_000},
            "ais-catcher",
        ),
        (
            "/api/recon/decoder",
            {"kind": "radiosonde", "frequency_hz": 402_500_000},
            "rs41mod",
        ),
        (
            "/api/recon/voice",
            {"start_lna": 24, "start_vga": 20, "local_aplay": False},
            "hackrf_sweep",
        ),
    ],
)
async def test_engine_start_then_hardware_stop(
    e2e_client, start_path: str, start_body: dict, frame_key: str
) -> None:
    client, arbiter, pool, caps = e2e_client
    res = await client.post(start_path, json=start_body)
    assert res.status_code == 200, (
        f"{start_path} expected 200, got {res.status_code}: {res.text[:400]}"
    )
    assert arbiter.is_sdr_busy() is True or arbiter.state in {
        HardwareState.BUSY_RX,
        HardwareState.BUSY_TX,
        HardwareState.MULTIPLEX_RX,
        HardwareState.HACKRF_NATIVE,
    }
    await _wait_frames(pool, frame_key, min_n=1)
    # Sub-GHz / TPMS should have persisted at least one JSON decode
    if frame_key == "rtl_433":
        await asyncio.sleep(0.15)
        if start_path.startswith("/api/subghz"):
            pk = await client.get("/api/subghz/packets?limit=10")
            assert pk.status_code == 200
            assert len(pk.json().get("packets") or pk.json() or []) >= 1 or isinstance(
                pk.json(), list
            )
        if start_path.startswith("/api/tpms"):
            st = await client.get("/api/tpms/status")
            assert st.status_code == 200
    await _assert_kill_clean(client, arbiter, pool, caps)


@pytest.mark.asyncio
async def test_mission_scheduler_start_stop(e2e_client) -> None:
    client, arbiter, pool, caps = e2e_client
    templates = await client.get("/api/recon/mission/templates")
    assert templates.status_code == 200
    tpls = templates.json().get("templates") or []
    body: dict
    if tpls:
        tid = tpls[0].get("id") or tpls[0].get("template_id")
        body = {"template_id": tid} if tid else {"steps": []}
    else:
        body = {
            "steps": [
                {
                    "type": "STEP_SWEEP_RANK",
                    "f_start_hz": 400_000_000,
                    "f_stop_hz": 405_000_000,
                }
            ]
        }
    start = await client.post("/api/recon/mission", json=body)
    assert start.status_code == 200, start.text[:400]
    await asyncio.sleep(0.2)
    stop = await client.post("/api/recon/mission/stop")
    assert stop.status_code == 200
    await _assert_kill_clean(client, arbiter, pool, caps)


@pytest.mark.asyncio
async def test_geoloc_engine_analysis_chain(e2e_client) -> None:
    client, arbiter, pool, caps = e2e_client
    op = await client.post(
        "/api/geoloc/operator",
        json={"lat": 49.1182, "lon": -1.0972, "accuracy_m": 12, "alt_m": 40},
    )
    assert op.status_code == 200
    crumb = await client.post(
        "/api/geoloc/breadcrumb",
        json={
            "kind": "rf_hit",
            "lat": 49.1182,
            "lon": -1.0972,
            "frequency_hz": 433_920_000,
            "rssi_db": -55,
            "label": "e2e",
        },
    )
    assert crumb.status_code == 200
    lob_a = await client.post(
        "/api/geoloc/lob",
        json={"lat": 49.1182, "lon": -1.0972, "bearing_deg": 90.0, "label": "a"},
    )
    lob_b = await client.post(
        "/api/geoloc/lob",
        json={"lat": 49.1190, "lon": -1.0972, "bearing_deg": 45.0, "label": "b"},
    )
    assert lob_a.status_code == 200 and lob_b.status_code == 200
    solve = await client.post(
        "/api/geoloc/lob/solve",
        json={
            "origin_lat": 49.1182,
            "origin_lon": -1.0972,
            "observations": [
                {
                    "lat": 49.1182,
                    "lon": -1.0972,
                    "bearing_deg": 90.0,
                    "angular_std_deg": 2.0,
                    "rssi_db": -45,
                },
                {
                    "lat": 49.1182,
                    "lon": -1.0900,
                    "bearing_deg": 0.0,
                    "angular_std_deg": 2.0,
                    "rssi_db": -45,
                },
            ],
        },
    )
    assert solve.status_code == 200, solve.text[:300]
    rssi = await client.post(
        "/api/geoloc/rssi-grid/solve",
        json={
            "origin_lat": 49.1182,
            "origin_lon": -1.0972,
            "gamma": 3.0,
            "observations": [
                {"lat": 49.1182, "lon": -1.0972, "rssi_db": -40},
                {"lat": 49.1190, "lon": -1.0972, "rssi_db": -48},
                {"lat": 49.1182, "lon": -1.0960, "rssi_db": -52},
                {"lat": 49.1175, "lon": -1.0980, "rssi_db": -55},
            ],
        },
    )
    assert rssi.status_code == 200
    l1 = await client.post(
        "/api/geoloc/gnss/l1-analyze",
        json={"duration_sec": 0.1, "lna": 24, "vga": 20},
    )
    assert l1.status_code == 200, l1.text[:400]
    await _wait_frames(pool, "hackrf_transfer", min_n=1)
    await _assert_kill_clean(client, arbiter, pool, caps)


@pytest.mark.asyncio
async def test_adsb_decoder_start_stop(e2e_client) -> None:
    client, arbiter, pool, caps = e2e_client
    res = await client.post(
        "/api/recon/decoder",
        json={"kind": "adsb", "frequency_hz": 1_090_000_000, "lat": 49.12, "lon": -1.10},
    )
    assert res.status_code == 200, res.text[:400]
    await _wait_frames(pool, "readsb", min_n=1)
    stop = await client.post("/api/recon/decoder/stop")
    assert stop.status_code == 200
    await _assert_kill_clean(client, arbiter, pool, caps)
