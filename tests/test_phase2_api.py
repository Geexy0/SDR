"""Phase 2 API validation + engine unit tests (no live TX)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.main import create_app
from pp_dashboard.models.phase2 import JammerMode, JammerRequest
from pp_dashboard.models.rf import HardwareStatusResponse
from pp_dashboard.rf.engine import RfEngine


def test_jammer_rejects_amp_on() -> None:
    with pytest.raises(ValidationError):
        JammerRequest(
            mode=JammerMode.CW_AWGN,
            frequency_hz=433_920_000,
            tx_vga=10,
            amp=1,
        )


def test_jammer_rejects_long_duration() -> None:
    with pytest.raises(ValidationError):
        JammerRequest(
            mode=JammerMode.CW_AWGN,
            frequency_hz=433_920_000,
            tx_vga=10,
            max_duration_sec=120,
        )


@pytest.mark.asyncio
async def test_watchdog_stops_runner() -> None:
    import asyncio
    from types import SimpleNamespace

    HardwareArbiter.reset_instance_for_tests()
    arbiter = HardwareArbiter()
    stop = AsyncMock()
    arbiter.runner = SimpleNamespace(running=True, stop=stop, argv=["hackrf_transfer", "-c", "64"])  # type: ignore[assignment]
    arbiter.state = HardwareState.BUSY_TX

    arbiter.arm_watchdog(0.05)
    await asyncio.sleep(0.2)
    stop.assert_awaited()
    assert arbiter.state is HardwareState.HACKRF_NATIVE
    assert arbiter.runner.running is True  # mock does not flip; state does


@pytest.mark.asyncio
async def test_jammer_route_calls_engine(monkeypatch) -> None:
    HardwareArbiter.reset_instance_for_tests()
    RfEngine.reset_instance_for_tests()
    app = create_app()

    fake = AsyncMock(
        return_value=__import__("pp_dashboard.models.phase2", fromlist=["JammerJobResponse"]).JammerJobResponse(
            status=HardwareStatusResponse(state=HardwareState.BUSY_TX, hackrf_native=True),
            mode=JammerMode.CW_AWGN,
            max_duration_sec=5.0,
        )
    )
    engine = RfEngine(HardwareArbiter())
    engine.start_jammer = fake  # type: ignore[method-assign]

    async def _dep() -> RfEngine:
        return engine

    from pp_dashboard.api.routes_tx import engine_dep

    app.dependency_overrides[engine_dep] = _dep
    # Bypass TX gate in case SCOPE files missing in weird envs — still present in repo.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/tx/jammer",
            json={
                "mode": "CW_AWGN",
                "frequency_hz": 433920000,
                "tx_vga": 10,
                "amp": 0,
                "amplitude": 64,
                "max_duration_sec": 3,
            },
        )
    assert resp.status_code == 200
    fake.assert_awaited()
