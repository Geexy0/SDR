"""Auto-detect Mayhem serial vs HackRF native USB presence."""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from pp_dashboard.config import Settings, settings

log = logging.getLogger(__name__)

LSUSB_ID_RE = re.compile(r"ID\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4})")


@dataclass(slots=True, frozen=True)
class DetectionResult:
    mayhem_serial: bool
    hackrf_native: bool
    serial_port: str | None
    lsusb_ids: tuple[str, ...]
    hackrf_info_ok: bool


def list_ttyacm_ports() -> list[str]:
    ports = sorted(glob.glob("/dev/ttyACM*"))
    return [p for p in ports if os.path.exists(p)]


def resolve_mayhem_serial_port(cfg: Settings = settings) -> str | None:
    """Prefer /dev/serial/by-id matches, then first /dev/ttyACM*."""
    by_id = Path("/dev/serial/by-id")
    if by_id.is_dir():
        candidates: list[Path] = []
        for entry in sorted(by_id.iterdir()):
            name = entry.name
            if any(marker in name for marker in cfg.mayhem_by_id_markers):
                candidates.append(entry)
        for entry in candidates:
            try:
                resolved = entry.resolve(strict=True)
            except OSError:
                continue
            if resolved.exists():
                return str(resolved)

    ports = list_ttyacm_ports()
    return ports[0] if ports else None


async def _run_capture(cmd: list[str], timeout: float = 3.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "timeout"
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


async def lsusb_ids() -> tuple[str, ...]:
    code, out, err = await _run_capture(["lsusb"], timeout=2.0)
    if code != 0:
        log.debug("lsusb failed (%s): %s", code, err.strip())
        return ()
    found: list[str] = []
    for line in out.splitlines():
        match = LSUSB_ID_RE.search(line)
        if match:
            found.append(match.group(1).lower())
    return tuple(found)


async def hackrf_info_ready(timeout: float = 3.0) -> bool:
    code, _out, _err = await _run_capture(["hackrf_info"], timeout=timeout)
    return code == 0


async def detect_hardware(cfg: Settings = settings) -> DetectionResult:
    serial_port = resolve_mayhem_serial_port(cfg)
    ids = await lsusb_ids()
    vid_pid = cfg.hackrf_vid_pid.lower()
    native_via_usb = vid_pid in ids

    # Never call hackrf_info while a Mayhem CDC port is present and USB ID is not native —
    # the dual-mode mutex means the libusb device is unavailable in serial mode.
    if native_via_usb:
        info_ok = await hackrf_info_ready()
        return DetectionResult(
            mayhem_serial=False,
            hackrf_native=True,
            serial_port=None,
            lsusb_ids=ids,
            hackrf_info_ok=info_ok,
        )

    if serial_port is not None:
        from pp_dashboard.hardware.serial_ops import probe_mayhem_prompt

        if await probe_mayhem_prompt(serial_port, cfg):
            return DetectionResult(
                mayhem_serial=True,
                hackrf_native=False,
                serial_port=serial_port,
                lsusb_ids=ids,
                hackrf_info_ok=False,
            )
        # ACM present but not a Mayhem shell — do not claim serial mode.
        return DetectionResult(
            mayhem_serial=False,
            hackrf_native=False,
            serial_port=None,
            lsusb_ids=ids,
            hackrf_info_ok=False,
        )

    info_ok = await hackrf_info_ready()
    return DetectionResult(
        mayhem_serial=False,
        hackrf_native=info_ok,
        serial_port=None,
        lsusb_ids=ids,
        hackrf_info_ok=info_ok,
    )
