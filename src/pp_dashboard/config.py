"""Runtime configuration for the hardware control plane."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


HACKRF_VID_PID = "1d50:6089"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAPTURES_DIR = REPO_ROOT / "captures"
MAYHEM_SERIAL_BAUD = 115200
DEFAULT_SAMPLE_RATE = 10_000_000
MODE_SWITCH_TIMEOUT_S = 5.0
SUBPROCESS_SIGKILL_GRACE_S = 2.0
SERIAL_PROBE_TIMEOUT_S = 1.5

# Prefer Mayhem / PortaPack / Great Scott Gadgets ACM links under by-id.
MAYHEM_BY_ID_MARKERS: tuple[str, ...] = (
    "PortaPack",
    "Mayhem",
    "HackRF",
    "Great_Scott_Gadgets",
)


@dataclass(slots=True)
class Settings:
    hackrf_vid_pid: str = HACKRF_VID_PID
    serial_baud: int = MAYHEM_SERIAL_BAUD
    mode_switch_timeout_s: float = MODE_SWITCH_TIMEOUT_S
    sigkill_grace_s: float = SUBPROCESS_SIGKILL_GRACE_S
    serial_probe_timeout_s: float = SERIAL_PROBE_TIMEOUT_S
    default_sample_rate: int = DEFAULT_SAMPLE_RATE
    mayhem_by_id_markers: tuple[str, ...] = field(default_factory=lambda: MAYHEM_BY_ID_MARKERS)
    captures_dir: Path = field(default_factory=lambda: DEFAULT_CAPTURES_DIR)
    subghz_db_path: Path = field(default_factory=lambda: DEFAULT_CAPTURES_DIR / "subghz_packets.sqlite3")
    rtl_433_bin: str = "rtl_433"
    host: str = "127.0.0.1"
    port: int = 8765


settings = Settings()
