"""SubprocessRunner lifecycle tests using a stand-in binary."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from pp_dashboard.config import Settings
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.hardware.subprocess_runner import SubprocessRunner
from pp_dashboard.models.rf import TelemetryEvent


@pytest.mark.asyncio
async def test_stop_sends_sigint(tmp_path: Path) -> None:
    script = tmp_path / "fake_hackrf_transfer.py"
    script.write_text(
        """\
import signal, sys, time
stop = False
def _h(sig, frame):
    global stop
    stop = True
signal.signal(signal.SIGINT, _h)
print("3.0 MiB / 1.0 sec = 3.0 MiB/second", flush=True)
while not stop:
    time.sleep(0.05)
sys.exit(0)
"""
    )
    # SubprocessRunner only allows hackrf_transfer / hackrf_sweep as argv[0].
    # Symlink a fake name onto python + script via a wrapper named hackrf_transfer.
    wrapper = tmp_path / "hackrf_transfer"
    wrapper.write_text(
        f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n"
    )
    wrapper.chmod(0o755)

    events: list[TelemetryEvent] = []

    async def on_event(event: TelemetryEvent) -> None:
        events.append(event)

    cfg = Settings(sigkill_grace_s=2.0)
    runner = SubprocessRunner(cfg=cfg, on_event=on_event)
    # Monkeypatch by putting tmp_path first on PATH
    import os

    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{tmp_path}:{old_path}"
    try:
        await runner.start(["hackrf_transfer", "-r", "x.cs8"], busy_state=HardwareState.BUSY_RX)
        await asyncio.sleep(0.2)
        assert runner.running
        code = await runner.stop(reason="test")
        assert code == 0
        assert any(e.type == "metric" for e in events)
        assert any(e.type == "process_exit" for e in events)
    finally:
        os.environ["PATH"] = old_path
        if runner.running:
            await runner.stop(reason="cleanup")
