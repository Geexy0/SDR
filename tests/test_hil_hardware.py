"""Layer 4 — opt-in hardware-in-the-loop smoke (RX only, no TX).

Skipped unless ``pytest --run-hil`` is passed with a physical PortaPack/HackRF
attached. Never asserts TX paths.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.hil


def pytest_addoption_noop() -> None:
    """Option registered in conftest; placeholder for discoverability."""


def _have_hardware() -> bool:
    """Require HackRF USB ID or a working hackrf_info — not bare ttyACM alone."""
    lsusb = shutil.which("lsusb")
    if lsusb:
        try:
            out = subprocess.check_output([lsusb], text=True, timeout=5)
            if "1d50:6089" in out.lower():
                return True
        except (subprocess.SubprocessError, OSError):
            pass
    info = shutil.which("hackrf_info")
    if info:
        try:
            return subprocess.run([info], capture_output=True, timeout=5).returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False
    return False


@pytest.fixture(scope="module")
def require_hil(pytestconfig: pytest.Config) -> None:
    if not pytestconfig.getoption("--run-hil", default=False):
        pytest.skip("HIL disabled (pass --run-hil)")
    if not _have_hardware():
        pytest.skip("No PortaPack/HackRF detected on USB")


def test_hil_detect_and_mode_roundtrip(require_hil: None, tmp_path: Path) -> None:
    """MAYHEM_SERIAL → HACKRF_NATIVE → MAYHEM_SERIAL via CLI tools (RX only)."""
    hackrf_info = shutil.which("hackrf_info")
    spiflash = shutil.which("hackrf_spiflash")
    assert hackrf_info, "hackrf_info missing"
    # Prefer native first if already there
    info = subprocess.run([hackrf_info], capture_output=True, text=True, timeout=10)
    # Mode bounce: reset to Mayhem if possible, then back
    if spiflash and info.returncode == 0:
        subprocess.run([spiflash, "-R"], capture_output=True, timeout=15)
        time.sleep(2.0)
    # Re-enter native
    acm = sorted(Path("/dev").glob("ttyACM*"))
    if acm:
        import serial

        with serial.Serial(str(acm[0]), 115200, timeout=1) as ser:
            ser.write(b"hackrf\r\n")
            ser.flush()
        deadline = time.time() + 20
        while time.time() < deadline:
            if subprocess.run([hackrf_info], capture_output=True, timeout=5).returncode == 0:
                break
            time.sleep(0.5)
        else:
            pytest.fail("hackrf_info never succeeded after Mayhem→native")
    assert (
        subprocess.run([hackrf_info], capture_output=True, timeout=5).returncode == 0
    )


def test_hil_one_second_sweep_binary(require_hil: None, tmp_path: Path) -> None:
    sweep = shutil.which("hackrf_sweep")
    assert sweep
    out = tmp_path / "sweep.bin"
    proc = subprocess.Popen(
        [sweep, "-f", "430:440", "-w", "100000", "-l", "24", "-g", "20", "-B"],
        stdout=out.open("wb"),
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    proc.send_signal(2)  # SIGINT
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    assert out.stat().st_size > 0


def test_hil_rx_noise_floor_433(require_hil: None, tmp_path: Path) -> None:
    """1 s · 2 MSPS RX at 433.92 MHz — RMS power in [-90, -5] dBFS."""
    xfer = shutil.which("hackrf_transfer")
    assert xfer
    path = tmp_path / "noise.cs8"
    n = 2_000_000  # 1 second @ 2 MSPS
    proc = subprocess.run(
        [
            xfer,
            "-r",
            str(path),
            "-f",
            "433920000",
            "-s",
            "2000000",
            "-l",
            "24",
            "-g",
            "20",
            "-a",
            "0",
            "-n",
            str(n),
        ],
        capture_output=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")[:400]
    raw = np.fromfile(path, dtype=np.int8)
    assert raw.size >= 1000
    i = raw[0::2].astype(np.float64)
    q = raw[1::2].astype(np.float64)
    power = float(np.mean(i * i + q * q)) + 1e-20
    dbfs = 10.0 * math.log10(power / (127.0**2))
    assert -90.0 <= dbfs <= -5.0, f"RMS {dbfs:.1f} dBFS outside lab noise window"
