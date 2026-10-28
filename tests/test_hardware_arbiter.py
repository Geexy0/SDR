"""HardwareArbiter USB mutex, mode switch, kill-switch — fully mocked."""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from pp_dashboard.config import Settings
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.detect import DetectionResult
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.hardware.subprocess_runner import SubprocessRunner
from pp_dashboard.models.rf import CaptureRequest, ContinuousTxRequest
from pp_dashboard.subghz.sniffer import snap_hackrf_sample_rate
from tests.fakes import FakeMayhemSerial


@pytest.fixture
def arbiter(isolate_hardware) -> HardwareArbiter:
    HardwareArbiter.reset_instance_for_tests()
    return HardwareArbiter(cfg=Settings(mode_switch_timeout_s=1.0, sigkill_grace_s=0.15))


@pytest.mark.asyncio
async def test_mayhem_to_native_writes_hackrf(arbiter: HardwareArbiter, isolate_hardware) -> None:
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

    FakeMayhemSerial.reset()
    with (
        patch("pp_dashboard.hardware.arbiter.detect_hardware", side_effect=fake_detect),
        patch.object(arbiter, "_poll_hackrf_info", new_callable=AsyncMock, return_value=True),
    ):
        status = await arbiter.to_native()
        assert status.state is HardwareState.HACKRF_NATIVE
    assert FakeMayhemSerial.instances
    joined = b"".join(FakeMayhemSerial.instances[0].writes)
    assert b"hackrf" in joined
    assert FakeMayhemSerial.instances[0].is_open is False


@pytest.mark.asyncio
async def test_native_to_mayhem_spiflash_and_poll(arbiter: HardwareArbiter) -> None:
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
        patch.object(arbiter, "_run_spiflash_reset", new_callable=AsyncMock) as spiflash,
        patch.object(
            arbiter, "_poll_mayhem_serial", new_callable=AsyncMock, return_value="/dev/ttyACM0"
        ),
    ):
        # Pretend a transfer is running then stop it via runner mock
        arbiter.state = HardwareState.HACKRF_NATIVE
        status = await arbiter.to_mayhem()
        spiflash.assert_awaited()
        assert status.state is HardwareState.MAYHEM_SERIAL
        assert status.serial_port == "/dev/ttyACM0"


@pytest.mark.asyncio
async def test_mutex_blocks_second_job(arbiter: HardwareArbiter, isolate_hardware) -> None:
    arbiter.state = HardwareState.HACKRF_NATIVE
    isolate_hardware.hackrf_info_ok = True

    async def fake_detect(_cfg=None):
        return DetectionResult(
            mayhem_serial=False,
            hackrf_native=True,
            serial_port=None,
            lsusb_ids=("1d50:6089",),
            hackrf_info_ok=True,
        )

    with patch("pp_dashboard.hardware.arbiter.detect_hardware", side_effect=fake_detect):
        await arbiter._start_sdr(  # noqa: SLF001
            ["hackrf_transfer", "-r", "-", "-f", "100000000", "-s", "10000000"],
            busy=HardwareState.BUSY_RX,
        )
        assert arbiter.state is HardwareState.BUSY_RX
        with pytest.raises(RuntimeError, match="already running|Cannot switch mode"):
            await arbiter._start_sdr(  # noqa: SLF001
                ["hackrf_transfer", "-t", "x.cs8", "-f", "100000000", "-s", "10000000"],
                busy=HardwareState.BUSY_TX,
            )
        await arbiter.stop_subprocess(reason="test")
        assert arbiter.state is HardwareState.HACKRF_NATIVE
        assert not arbiter.runner.running


@pytest.mark.asyncio
async def test_sigkill_escalation_on_hung_transfer(
    arbiter: HardwareArbiter, isolate_hardware
) -> None:
    isolate_hardware.hung_transfer = True
    events: list = []

    async def on_event(ev) -> None:
        events.append(ev)

    runner = SubprocessRunner(cfg=Settings(sigkill_grace_s=0.1), on_event=on_event)
    await runner.start(
        ["hackrf_transfer", "-r", "-", "-f", "100000000", "-s", "2000000"],
        busy_state=HardwareState.BUSY_RX,
    )
    assert runner.running
    proc = isolate_hardware.processes[-1]
    assert proc.ignore_sigint
    code = await runner.stop(reason="hung")
    assert signal.SIGINT in proc.signals
    assert signal.SIGKILL in proc.signals or any(s == signal.SIGKILL for s in proc.signals)
    assert code is not None
    assert not runner.running


def test_gain_and_sample_rate_guardrails(tmp_path) -> None:
    with pytest.raises(ValidationError):
        CaptureRequest(
            filepath=tmp_path / "out.cs8",
            frequency_hz=100_000_000,
            lna=15,  # not 8 dB step
            vga=20,
            num_samples=1000,
        )
    with pytest.raises(ValidationError):
        CaptureRequest(
            filepath=tmp_path / "out.cs8",
            frequency_hz=100_000_000,
            lna=16,
            vga=21,  # not 2 dB step
            num_samples=1000,
        )
    with pytest.raises(ValidationError):
        ContinuousTxRequest(frequency_hz=433_920_000, amplitude=64, tx_vga=50)
    # Soapy HackRF: 2.5 Msps invalid → snap to nearest integer MHz
    assert snap_hackrf_sample_rate(2_500_000) == 2_000_000
    assert snap_hackrf_sample_rate(2_000_000) == 2_000_000
