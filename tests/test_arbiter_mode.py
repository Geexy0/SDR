"""HardwareArbiter mode-switch unit tests with mocked USB/serial."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from pp_dashboard.config import Settings
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.detect import DetectionResult
from pp_dashboard.hardware.state import HardwareState


@pytest.fixture
def arbiter() -> HardwareArbiter:
    HardwareArbiter.reset_instance_for_tests()
    return HardwareArbiter(cfg=Settings(mode_switch_timeout_s=1.0))


@pytest.mark.asyncio
async def test_to_native_sends_hackrf_and_polls(arbiter: HardwareArbiter) -> None:
    mayhem = DetectionResult(
        mayhem_serial=True,
        hackrf_native=False,
        serial_port="/dev/ttyACM0",
        lsusb_ids=(),
        hackrf_info_ok=False,
    )
    native = DetectionResult(
        mayhem_serial=False,
        hackrf_native=True,
        serial_port=None,
        lsusb_ids=("1d50:6089",),
        hackrf_info_ok=True,
    )
    calls = {"n": 0}

    async def fake_detect(_cfg=None):
        calls["n"] += 1
        return native if calls["n"] > 1 else mayhem

    with (
        patch("pp_dashboard.hardware.arbiter.detect_hardware", side_effect=fake_detect),
        patch(
            "pp_dashboard.hardware.arbiter.send_hackrf_enter_native",
            new_callable=AsyncMock,
        ) as send_mock,
        patch.object(arbiter, "_poll_hackrf_info", new_callable=AsyncMock, return_value=True),
    ):
        status = await arbiter.to_native()
        send_mock.assert_awaited_once_with("/dev/ttyACM0", arbiter.cfg)
        assert status.state is HardwareState.HACKRF_NATIVE


@pytest.mark.asyncio
async def test_to_mayhem_runs_spiflash_and_polls(arbiter: HardwareArbiter) -> None:
    native = DetectionResult(
        mayhem_serial=False,
        hackrf_native=True,
        serial_port=None,
        lsusb_ids=("1d50:6089",),
        hackrf_info_ok=True,
    )
    mayhem = DetectionResult(
        mayhem_serial=True,
        hackrf_native=False,
        serial_port="/dev/ttyACM0",
        lsusb_ids=(),
        hackrf_info_ok=False,
    )
    calls = {"n": 0}

    async def fake_detect(_cfg=None):
        calls["n"] += 1
        return mayhem if calls["n"] > 1 else native

    with (
        patch("pp_dashboard.hardware.arbiter.detect_hardware", side_effect=fake_detect),
        patch.object(arbiter, "_run_spiflash_reset", new_callable=AsyncMock),
        patch.object(
            arbiter, "_poll_mayhem_serial", new_callable=AsyncMock, return_value="/dev/ttyACM0"
        ),
    ):
        status = await arbiter.to_mayhem()
        assert status.state is HardwareState.MAYHEM_SERIAL
        assert status.serial_port == "/dev/ttyACM0"


@pytest.mark.asyncio
async def test_singleton() -> None:
    HardwareArbiter.reset_instance_for_tests()
    a = await HardwareArbiter.get_instance()
    b = await HardwareArbiter.get_instance()
    assert a is b
