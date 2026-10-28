"""gps-sdr-sim wrapper → /dev/shm/gpssim.cs8 + hackrf_transfer TX."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from pp_dashboard.hardware.arbiter import HardwareArbiter

log = logging.getLogger(__name__)

GPS_L1_HZ = 1_575_420_000
GPS_CS8 = Path("/dev/shm/gpssim.cs8")
DEFAULT_FS = 2_600_000


async def run_gps_sdr_sim(
    *,
    ephemeris: Path,
    lat: float,
    lon: float,
    alt: float,
    out: Path = GPS_CS8,
    duration_s: int = 30,
) -> Path:
    binary = shutil.which("gps-sdr-sim")
    if not binary:
        raise FileNotFoundError(
            "gps-sdr-sim not found. Build/install https://github.com/osqzss/gps-sdr-sim"
        )
    if not ephemeris.is_file():
        raise FileNotFoundError(f"ephemeris file missing: {ephemeris}")
    out = Path(out)
    argv = [
        binary,
        "-e",
        str(ephemeris),
        "-l",
        f"{lat},{lon},{alt}",
        "-b",
        "8",
        "-d",
        str(duration_s),
        "-o",
        str(out),
    ]
    log.info("gps-sdr-sim: %s", " ".join(argv))
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"gps-sdr-sim failed: {err.decode('utf-8', errors='replace')}")
    if not out.exists():
        raise RuntimeError("gps-sdr-sim produced no output file")
    return out


async def transmit_gps_cs8(
    arbiter: HardwareArbiter,
    *,
    path: Path,
    tx_vga: int,
    max_duration_sec: float | None = 30.0,
) -> None:
    await arbiter.start_tx_file(
        filepath=path,
        frequency_hz=GPS_L1_HZ,
        sample_rate=DEFAULT_FS,
        tx_vga=tx_vga,
        amp=0,
        loop=True,
        max_duration_sec=max_duration_sec,
    )
