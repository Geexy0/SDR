"""Strict RF parameter validation for HackRF Pro."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from pp_dashboard.config import settings
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.security.paths import resolve_safe_path, resolve_under_any


def _ensure_under_captures(path: Path) -> Path:
    return resolve_safe_path(settings.captures_dir, path)


def _ensure_tx_iq_path(path: Path) -> Path:
    """Allow TX IQ from captures/ or /dev/shm (chirp RAM buffers)."""
    captures = settings.captures_dir
    captures.mkdir(parents=True, exist_ok=True)
    return resolve_under_any([captures, Path("/dev/shm")], path)

FREQ_MIN_HZ = 1_000_000
FREQ_MAX_HZ = 6_000_000_000
SAMPLE_RATE_MIN = 2_000_000
SAMPLE_RATE_MAX = 20_000_000
LNA_STEPS = frozenset({0, 8, 16, 24, 32, 40})
RX_VGA_MAX = 62
TX_VGA_MAX = 47
CW_AMPLITUDE_MAX = 127


def _require_lna(value: int) -> int:
    if value not in LNA_STEPS:
        raise ValueError(f"LNA gain must be one of {sorted(LNA_STEPS)}, got {value}")
    return value


def _require_rx_vga(value: int) -> int:
    if value < 0 or value > RX_VGA_MAX or value % 2 != 0:
        raise ValueError(
            f"RX VGA gain must be 0..{RX_VGA_MAX} in 2 dB steps, got {value}"
        )
    return value


def _require_tx_vga(value: int) -> int:
    if value < 0 or value > TX_VGA_MAX:
        raise ValueError(f"TX VGA gain must be 0..{TX_VGA_MAX} in 1 dB steps, got {value}")
    return value


def _require_amp(value: int) -> int:
    if value not in (0, 1):
        raise ValueError(f"External amp (-a) must be 0 or 1, got {value}")
    return value


class TargetMode(str, Enum):
    MAYHEM_SERIAL = "MAYHEM_SERIAL"
    HACKRF_NATIVE = "HACKRF_NATIVE"


class HardwareModeRequest(BaseModel):
    mode: TargetMode


class HardwareStatusResponse(BaseModel):
    state: HardwareState
    serial_port: str | None = None
    hackrf_native: bool = False
    mayhem_serial: bool = False
    subprocess_running: bool = False
    subprocess_argv: list[str] | None = None
    last_error: str | None = None


class RfBase(BaseModel):
    frequency_hz: int = Field(..., ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    sample_rate: int = Field(10_000_000, ge=SAMPLE_RATE_MIN, le=SAMPLE_RATE_MAX)
    amp: Literal[0, 1] = 0

    @field_validator("amp")
    @classmethod
    def validate_amp(cls, value: int) -> int:
        return _require_amp(value)


class CaptureRequest(RfBase):
    """IQ capture via hackrf_transfer -r."""

    filepath: Path
    lna: int
    vga: int
    num_samples: int = Field(..., gt=0)
    max_duration_sec: float | None = None

    @field_validator("filepath")
    @classmethod
    def validate_filepath(cls, value: Path) -> Path:
        resolved = _ensure_under_captures(value)
        if resolved.suffix.lower() not in {".cs8", ".c8"}:
            raise ValueError("IQ file must use .cs8 or .c8 extension")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    @field_validator("lna")
    @classmethod
    def validate_lna(cls, value: int) -> int:
        return _require_lna(value)

    @field_validator("vga")
    @classmethod
    def validate_vga(cls, value: int) -> int:
        return _require_rx_vga(value)


class ReplayRequest(RfBase):
    """IQ replay via hackrf_transfer -t [-R]."""

    filepath: Path
    tx_vga: int
    loop: bool = False
    max_duration_sec: float | None = None
    # PP-LAB-RF-2026: TX amp hard-off (RX CaptureRequest may still use amp 0|1).
    amp: Literal[0] = 0

    @field_validator("tx_vga")
    @classmethod
    def validate_tx_vga(cls, value: int) -> int:
        return _require_tx_vga(value)

    @field_validator("amp")
    @classmethod
    def validate_tx_amp(cls, value: int) -> int:
        if value != 0:
            raise ValueError("External amp must remain off (amp=0) per active SCOPE")
        return 0

    @field_validator("filepath")
    @classmethod
    def validate_filepath(cls, value: Path) -> Path:
        resolved = _ensure_tx_iq_path(value)
        if not resolved.exists():
            raise ValueError(f"IQ file does not exist: {resolved}")
        if resolved.suffix.lower() not in {".cs8", ".c8"}:
            raise ValueError("IQ file must use .cs8 or .c8 extension")
        return resolved

    @model_validator(mode="after")
    def require_watchdog_when_loop(self) -> ReplayRequest:
        if self.loop and self.max_duration_sec is None:
            self.max_duration_sec = 60.0
        return self


class ContinuousTxRequest(RfBase):
    """CW / AWGN via hackrf_transfer -c."""

    amplitude: int = Field(..., ge=0, le=CW_AMPLITUDE_MAX)
    tx_vga: int
    max_duration_sec: float | None = None
    amp: Literal[0] = 0

    @field_validator("tx_vga")
    @classmethod
    def validate_tx_vga(cls, value: int) -> int:
        return _require_tx_vga(value)

    @field_validator("amp")
    @classmethod
    def validate_tx_amp(cls, value: int) -> int:
        if value != 0:
            raise ValueError("External amp must remain off (amp=0) per active SCOPE")
        return 0


class SweepRequest(BaseModel):
    """Wideband sweep via hackrf_sweep."""

    min_mhz: float = Field(..., gt=0)
    max_mhz: float = Field(..., gt=0)
    bin_width_hz: int = Field(..., gt=0)
    lna: int
    vga: int

    @field_validator("lna")
    @classmethod
    def validate_lna(cls, value: int) -> int:
        return _require_lna(value)

    @field_validator("vga")
    @classmethod
    def validate_vga(cls, value: int) -> int:
        return _require_rx_vga(value)

    @model_validator(mode="after")
    def validate_range(self) -> SweepRequest:
        if self.max_mhz <= self.min_mhz:
            raise ValueError("max_mhz must be greater than min_mhz")
        # Sweep CLI uses MHz; keep within HackRF Pro span.
        if self.min_mhz < 1 or self.max_mhz > 6000:
            raise ValueError("sweep range must stay within 1..6000 MHz")
        return self


class MultiplexRxRequest(BaseModel):
    """Wideband IQ multiplexer — single 10 MSPS RX feeding FFT + DDC FIFOs."""

    center_hz: int = Field(435_000_000, ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    sample_rate: Literal[10_000_000] = 10_000_000
    lna: int = 24
    vga: int = 20
    amp: Literal[0] = 0
    subghz_tune_hz: int | None = Field(default=None, ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    voice_tune_hz: int | None = Field(default=None, ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    max_duration_sec: float | None = Field(default=None, gt=0, le=3600)

    @field_validator("lna")
    @classmethod
    def validate_lna(cls, value: int) -> int:
        return _require_lna(value)

    @field_validator("vga")
    @classmethod
    def validate_vga(cls, value: int) -> int:
        return _require_rx_vga(value)


class TelemetryEvent(BaseModel):
    type: Literal[
        "status",
        "stdout",
        "stderr",
        "metric",
        "process_exit",
        "error",
        "heartbeat",
    ]
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    state: HardwareState | None = None
    message: str | None = None
    metrics: dict[str, Any] | None = None
    exit_code: int | None = None
    argv: list[str] | None = None
