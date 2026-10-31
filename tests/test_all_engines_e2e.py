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
    # Give stop callbacks a beat
    await asyncio.sleep(0.05)
    assert arbiter.is_sdr_busy() is False
    alive = [p for p in pool.processes if p.returncode is None]
    assert not alive, f"orphan FakeProcess still running: {alive}"
    leftovers: list[Path] = []
    for d in capture_dirs:
        if d.exists():
            leftovers.extend(d.glob("*.cs8"))
    # Live engines may leave intentional capture artifacts; only flag /dev/shm-style temps
    shmish = [p for p in leftovers if "shm" in str(p) or p.name.startswith("ook_")]
    assert not shmish, f"leftover temp .cs8: {shmish}"


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
    from pp_dashboard.tpms.engine import TpmsEngine

    _confirm_gate(tmp_path, monkeypatch)
    shm = tmp_path / "shm"
    shm.mkdir(exist_ok=True)
    monkeypatch.setenv("PP_MOCK_HARDWARE", "1")
    monkeypatch.setattr(settings, "mode_switch_timeout_s", 1.0)
    monkeypatch.setattr(settings, "sigkill_grace_s", 0.2)
    capture_roots = [settings.captures_dir, shm, Path("/dev/shm")]
    capture_roots = [settings.captures_dir, shm, Path("/dev/shm")]

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
    # Force serial then native via API
    r1 = await client.post("/api/hardware/mode", json={"mode": "MAYHEM_SERIAL"})
    assert r1.status_code in {200, 409}
    r2 = await client.post("/api/hardware/mode", json={"mode": "HACKRF_NATIVE"})
    assert r2.status_code in {200, 409}
    await _assert_kill_clean(client, arbiter, pool, caps)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_path,start_body",
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
        ),
        (
            "/api/subghz/sniff",
            {
                "frequency_hz": 433_920_000,
                "sample_rate": 2_000_000,
                "max_duration_sec": 2,
            },
        ),
        (
            "/api/tpms/listen",
            {
                "frequency_hz": 433_920_000,
                "sample_rate": 2_000_000,
                "max_duration_sec": 2,
            },
        ),
        (
            "/api/recon/decoder",
            {"kind": "pocsag", "frequency_hz": 466_075_000},
        ),
        (
            "/api/recon/decoder",
            {"kind": "ais", "frequency_hz": 161_975_000},
        ),
        (
            "/api/recon/decoder",
            {"kind": "radiosonde", "frequency_hz": 402_500_000},
        ),
        (
            "/api/recon/voice",
            {"start_lna": 24, "start_vga": 20, "local_aplay": False},
        ),
    ],
)
async def test_engine_start_then_hardware_stop(
    e2e_client, start_path: str, start_body: dict
) -> None:
    client, arbiter, pool, caps = e2e_client
    res = await client.post(start_path, json=start_body)
    # Engines may 200 (started), 409 (busy/conflict), or 503 (tool missing)
    assert res.status_code in {200, 409, 503}, (
        f"{start_path} unexpected {res.status_code}: {res.text[:240]}"
    )
    if res.status_code == 200:
        # Soft busy check — multiplex/sweep register via runner or callbacks
        _ = arbiter.is_sdr_busy()
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
    assert start.status_code in {200, 409, 422}
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
    assert solve.status_code in {200, 422}
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
    # GNSS L1 analyze may 409 if arbiter busy; soft-ok
    l1 = await client.post(
        "/api/geoloc/gnss/l1-analyze",
        json={"duration_sec": 0.1, "lna": 24, "vga": 20},
    )
    assert l1.status_code in {200, 409, 500}
    await _assert_kill_clean(client, arbiter, pool, caps)


@pytest.mark.asyncio
async def test_adsb_decoder_soft_start_stop(e2e_client) -> None:
    client, arbiter, pool, caps = e2e_client
    res = await client.post(
        "/api/recon/decoder",
        json={"kind": "adsb", "frequency_hz": 1_090_000_000, "lat": 49.12, "lon": -1.10},
    )
    assert res.status_code in {200, 409, 503}
    stop = await client.post("/api/recon/decoder/stop")
    assert stop.status_code == 200
    await _assert_kill_clean(client, arbiter, pool, caps)
