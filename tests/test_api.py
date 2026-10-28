"""FastAPI route tests (no live hardware mode switches)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.main import create_app
from pp_dashboard.models.rf import HardwareStatusResponse


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.asyncio
async def test_home_page() -> None:
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        home = await client.get("/")
        assert home.status_code == 200
        assert "text/html" in home.headers["content-type"]
        # SPA Cyber-Deck or legacy HTML console
        assert ("id=\"root\"" in home.text) or ("Hardware state" in home.text)
        fav = await client.get("/favicon.svg")
        assert fav.status_code == 200


@pytest.mark.asyncio
async def test_health_and_status() -> None:
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    arbiter = HardwareArbiter()
    arbiter.state = HardwareState.MAYHEM_SERIAL
    arbiter.serial_port = "/dev/ttyACM0"
    arbiter.refresh_detection = AsyncMock(
        return_value=None  # type: ignore[arg-type]
    )
    arbiter.status = lambda: HardwareStatusResponse(  # type: ignore[method-assign]
        state=HardwareState.MAYHEM_SERIAL,
        serial_port="/dev/ttyACM0",
        mayhem_serial=True,
        hackrf_native=False,
    )

    async def _dep() -> HardwareArbiter:
        return arbiter

    from pp_dashboard.api.deps import arbiter_dep

    app.dependency_overrides[arbiter_dep] = _dep

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/healthz")
        assert health.status_code == 200
        status = await client.get("/api/hardware/status")
        assert status.status_code == 200
        body = status.json()
        assert body["state"] == "MAYHEM_SERIAL"
        assert body["serial_port"] == "/dev/ttyACM0"


@pytest.mark.asyncio
async def test_mode_endpoint_routes() -> None:
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    arbiter = HardwareArbiter()
    arbiter.to_native = AsyncMock(
        return_value=HardwareStatusResponse(state=HardwareState.HACKRF_NATIVE, hackrf_native=True)
    )
    arbiter.to_mayhem = AsyncMock(
        return_value=HardwareStatusResponse(
            state=HardwareState.MAYHEM_SERIAL, mayhem_serial=True, serial_port="/dev/ttyACM0"
        )
    )

    async def _dep() -> HardwareArbiter:
        return arbiter

    from pp_dashboard.api.deps import arbiter_dep

    app.dependency_overrides[arbiter_dep] = _dep
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/api/hardware/mode", json={"mode": "HACKRF_NATIVE"})
        assert r1.status_code == 200
        assert r1.json()["state"] == "HACKRF_NATIVE"
        r2 = await client.post("/api/hardware/mode", json={"mode": "MAYHEM_SERIAL"})
        assert r2.status_code == 200
        assert r2.json()["state"] == "MAYHEM_SERIAL"


@pytest.mark.asyncio
async def test_invalid_lna_rejected(tmp_path, monkeypatch) -> None:
    from pp_dashboard.config import settings

    monkeypatch.setattr(settings, "captures_dir", tmp_path)
    HardwareArbiter.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/hardware/capture",
            json={
                "filepath": str(tmp_path / "out.cs8"),
                "frequency_hz": 100000000,
                "sample_rate": 10000000,
                "lna": 7,
                "vga": 20,
                "amp": 0,
                "num_samples": 1000,
            },
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_tx_gate_files_readable() -> None:
    from pp_dashboard.security.tx_gate import assert_tx_allowed

    # Lab CONFIRM is active for PP-LAB-RF-2026 — gate must allow.
    assert_tx_allowed()
