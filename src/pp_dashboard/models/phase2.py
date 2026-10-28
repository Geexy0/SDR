"""Phase 2 request/response models — jammer, capture, replay, rolljam."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from pp_dashboard.config import settings
from pp_dashboard.models.rf import (
    CW_AMPLITUDE_MAX,
    FREQ_MAX_HZ,
    FREQ_MIN_HZ,
    SAMPLE_RATE_MAX,
    SAMPLE_RATE_MIN,
    HardwareStatusResponse,
    RfBase,
    _ensure_under_captures,
    _require_amp,
    _require_lna,
    _require_rx_vga,
    _require_tx_vga,
)

# Light-lab jam ceiling (SCOPE forbids hard/long-duration barrage).
DEFAULT_JAM_DURATION_SEC = 5.0
MAX_DURATION_SEC = 60.0
SHM_CHIRP_PATH = Path("/dev/shm/pp_dashboard_chirp.cs8")


def _clamp_duration(value: float | None) -> float | None:
    if value is None:
        return None
    if value <= 0:
        raise ValueError("max_duration_sec must be > 0 when set")
    if value > MAX_DURATION_SEC:
        raise ValueError(f"max_duration_sec must be <= {MAX_DURATION_SEC} (lab light-jam ceiling)")
    return value


class JammerMode(str, Enum):
    CW_AWGN = "CW_AWGN"
    CHIRP_BARRAGE = "CHIRP_BARRAGE"


class DurationMixin(BaseModel):
    max_duration_sec: float | None = Field(
        default=DEFAULT_JAM_DURATION_SEC,
        description="Auto-SIGINT after this many seconds (lab watchdog).",
    )

    @field_validator("max_duration_sec")
    @classmethod
    def validate_duration(cls, value: float | None) -> float | None:
        return _clamp_duration(value)


class JammerRequest(DurationMixin):
    mode: JammerMode
    frequency_hz: int = Field(..., ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    sample_rate: int = Field(10_000_000, ge=SAMPLE_RATE_MIN, le=SAMPLE_RATE_MAX)
    tx_vga: int
    amp: Literal[0, 1] = 0
    # Mode 1 — hardware CW
    amplitude: int = Field(64, ge=0, le=CW_AMPLITUDE_MAX)
    # Mode 2 — chirp
    bandwidth_hz: float = Field(2_000_000, gt=0, le=20_000_000)
    sweep_period_s: float = Field(0.001, gt=0, le=1.0)
    chirp_amplitude: float = Field(100.0, gt=0, le=127)
    chirp_path: Path = SHM_CHIRP_PATH

    @field_validator("tx_vga")
    @classmethod
    def validate_tx_vga(cls, value: int) -> int:
        return _require_tx_vga(value)

    @field_validator("amp")
    @classmethod
    def validate_amp(cls, value: int) -> int:
        # SCOPE default: AMP off. Reject enable unless explicitly 0 here —
        # keep closed; operators amend SCOPE before raising this gate.
        if value != 0:
            raise ValueError("External amp must remain off (amp=0) per active SCOPE")
        return _require_amp(value)

    @model_validator(mode="after")
    def validate_chirp(self) -> JammerRequest:
        if self.mode is JammerMode.CHIRP_BARRAGE and self.bandwidth_hz > self.sample_rate:
            raise ValueError("bandwidth_hz cannot exceed sample_rate")
        if self.chirp_path != SHM_CHIRP_PATH and not str(self.chirp_path).startswith("/dev/shm/"):
            raise ValueError("chirp_path must be under /dev/shm/")
        return self


class CaptureJobRequest(RfBase):
    """Capture IQ with automatic JSON sidecar."""

    filepath: Path | None = None
    lna: int
    vga: int
    num_samples: int = Field(..., gt=0)
    max_duration_sec: float | None = Field(
        default=None,
        description="Optional watchdog; finite -n captures usually finish earlier.",
    )
    wait: bool = Field(
        default=True,
        description="If true, block until capture exits and write sidecar before responding.",
    )

    @field_validator("lna")
    @classmethod
    def validate_lna(cls, value: int) -> int:
        return _require_lna(value)

    @field_validator("vga")
    @classmethod
    def validate_vga(cls, value: int) -> int:
        return _require_rx_vga(value)

    @field_validator("max_duration_sec")
    @classmethod
    def validate_duration(cls, value: float | None) -> float | None:
        return _clamp_duration(value)

    @field_validator("filepath")
    @classmethod
    def validate_filepath(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        resolved = _ensure_under_captures(value)
        if resolved.suffix.lower() not in {".cs8", ".c8"}:
            raise ValueError("IQ file must use .cs8 or .c8 extension")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved


class ReplayJobRequest(DurationMixin):
    filepath: Path
    frequency_hz: int | None = Field(
        default=None,
        description="Override LO; if omitted, read from sidecar when present.",
    )
    sample_rate: int | None = None
    tx_vga: int = 20
    amp: Literal[0, 1] = 0
    loop: bool = False
    max_duration_sec: float | None = Field(
        default=None,
        description="Watchdog; recommended when loop=true.",
    )

    @field_validator("tx_vga")
    @classmethod
    def validate_tx_vga(cls, value: int) -> int:
        return _require_tx_vga(value)

    @field_validator("amp")
    @classmethod
    def validate_amp(cls, value: int) -> int:
        if value != 0:
            raise ValueError("External amp must remain off (amp=0) per active SCOPE")
        return 0

    @field_validator("filepath")
    @classmethod
    def validate_filepath(cls, value: Path) -> Path:
        resolved = value.expanduser().resolve()
        captures = settings.captures_dir.resolve()
        shm = Path("/dev/shm").resolve()
        under_captures = True
        try:
            resolved.relative_to(captures)
        except ValueError:
            under_captures = False
        under_shm = True
        try:
            resolved.relative_to(shm)
        except ValueError:
            under_shm = False
        if not (under_captures or under_shm):
            raise ValueError(f"filepath must be under {captures} or /dev/shm/")
        if not resolved.exists():
            raise ValueError(f"IQ file does not exist: {resolved}")
        if resolved.suffix.lower() not in {".cs8", ".c8"}:
            raise ValueError("IQ file must use .cs8 or .c8 extension")
        return resolved

    @field_validator("max_duration_sec")
    @classmethod
    def validate_duration(cls, value: float | None) -> float | None:
        # Replay default duration None → no forced stop unless loop; clamp if set.
        if value is None:
            return None
        return _clamp_duration(value)


class RolljamPhase(str, Enum):
    CAPTURE_THEN_REPLAY = "CAPTURE_THEN_REPLAY"
    CAPTURE_JAM_REPLAY = "CAPTURE_JAM_REPLAY"


class RolljamRequest(BaseModel):
    """Listen-then-replay (optional mid-phase light jam). Single radio → sequential only."""

    phase: RolljamPhase = RolljamPhase.CAPTURE_THEN_REPLAY
    frequency_hz: int = Field(..., ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    sample_rate: int = Field(10_000_000, ge=SAMPLE_RATE_MIN, le=SAMPLE_RATE_MAX)
    lna: int
    vga: int
    num_samples: int = Field(..., gt=0)
    capture_path: Path | None = None
    tx_vga: int = 20
    amp: Literal[0, 1] = 0
    replay_loop: bool = False
    replay_max_duration_sec: float | None = Field(default=5.0)
    jam_frequency_hz: int | None = None
    jam_amplitude: int = Field(64, ge=0, le=CW_AMPLITUDE_MAX)
    jam_duration_sec: float = Field(default=2.0, gt=0, le=MAX_DURATION_SEC)

    @field_validator("lna")
    @classmethod
    def validate_lna(cls, value: int) -> int:
        return _require_lna(value)

    @field_validator("vga")
    @classmethod
    def validate_vga(cls, value: int) -> int:
        return _require_rx_vga(value)

    @field_validator("tx_vga")
    @classmethod
    def validate_tx_vga(cls, value: int) -> int:
        return _require_tx_vga(value)

    @field_validator("amp")
    @classmethod
    def validate_amp(cls, value: int) -> int:
        if value != 0:
            raise ValueError("External amp must remain off (amp=0) per active SCOPE")
        return 0

    @field_validator("replay_max_duration_sec")
    @classmethod
    def validate_replay_duration(cls, value: float | None) -> float | None:
        return _clamp_duration(value)

    @model_validator(mode="after")
    def validate_jam_freq(self) -> RolljamRequest:
        jam = self.jam_frequency_hz if self.jam_frequency_hz is not None else self.frequency_hz
        if not (FREQ_MIN_HZ <= jam <= FREQ_MAX_HZ):
            raise ValueError("jam_frequency_hz out of HackRF range")
        if self.jam_frequency_hz is None:
            return self.model_copy(update={"jam_frequency_hz": jam})
        return self


class CaptureJobResponse(BaseModel):
    status: HardwareStatusResponse
    filepath: Path
    sidecar: Path | None = None
    metadata: dict[str, Any] | None = None


class JammerJobResponse(BaseModel):
    status: HardwareStatusResponse
    mode: JammerMode
    chirp_path: Path | None = None
    max_duration_sec: float | None = None


class RolljamResponse(BaseModel):
    capture: CaptureJobResponse
    jam_status: HardwareStatusResponse | None = None
    replay_status: HardwareStatusResponse
    notes: str = (
        "Single HackRF cannot RX+TX simultaneously; rolljam runs capture → optional jam → replay."
    )
