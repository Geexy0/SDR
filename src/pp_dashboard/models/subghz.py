"""Sub-GHz Phase API models."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from pp_dashboard.config import settings
from pp_dashboard.models.rf import (
    FREQ_MAX_HZ,
    FREQ_MIN_HZ,
    SAMPLE_RATE_MAX,
    SAMPLE_RATE_MIN,
    _ensure_under_captures,
    _require_tx_vga,
)

MAX_BF_DURATION = 60.0
SHM_OOK_PATH = Path("/dev/shm/ook_bruteforce.cs8")
ProtocolFamily = Literal[
    "PT2262",
    "EV1527",
    "CAME",
    "NICE_FLO",
    "LINEAR_MEGACODE",
    "CHAMBERLAIN",
    "GENERIC_PWM",
]


class SniffRequest(BaseModel):
    frequency_hz: int = Field(433_920_000, ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    sample_rate: int = Field(2_000_000, ge=SAMPLE_RATE_MIN, le=SAMPLE_RATE_MAX)
    device: str = "driver=hackrf"
    max_duration_sec: float | None = Field(default=30.0, gt=0, le=MAX_BF_DURATION)


class AnalyzeRequest(BaseModel):
    filepath: Path
    sample_rate: int = Field(2_000_000, ge=SAMPLE_RATE_MIN, le=SAMPLE_RATE_MAX)
    threshold_method: Literal["otsu", "median6db"] = "otsu"

    @field_validator("filepath")
    @classmethod
    def validate_path(cls, value: Path) -> Path:
        resolved = _ensure_under_captures(value)
        if not resolved.exists():
            raise ValueError(f"missing file: {resolved}")
        if resolved.suffix.lower() not in {".cs8", ".c8"}:
            raise ValueError("analyze expects .cs8/.c8")
        return resolved


class BruteforceRequest(BaseModel):
    mode: Literal["framed", "de_bruijn"]
    frequency_hz: int = Field(..., ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    sample_rate: int = Field(2_000_000, ge=SAMPLE_RATE_MIN, le=SAMPLE_RATE_MAX)
    tx_vga: int = 20
    amp: Literal[0] = 0
    max_duration_sec: float | None = Field(default=15.0, gt=0, le=MAX_BF_DURATION)
    # framed
    family: ProtocolFamily = "PT2262"
    nbits: int = Field(12, ge=1, le=24)
    key_start: int = Field(0, ge=0)
    key_end: int = Field(64, ge=0)
    repeats: int = Field(3, ge=1, le=20)
    # de bruijn
    k: Literal[2, 3] = 2
    n: int = Field(12, ge=8, le=24)
    short_us: int = 300
    long_us: int = 900
    loop_tx: bool = False
    out_path: Path = SHM_OOK_PATH

    @field_validator("tx_vga")
    @classmethod
    def vga(cls, v: int) -> int:
        return _require_tx_vga(v)

    @field_validator("out_path")
    @classmethod
    def shm_only(cls, v: Path) -> Path:
        from pp_dashboard.security.paths import resolve_under_any

        try:
            return resolve_under_any([Path("/dev/shm")], v)
        except ValueError as exc:
            raise ValueError("bruteforce output must be under /dev/shm/") from exc


class SubConvertRequest(BaseModel):
    action: Literal["sub_to_cs8", "cs8_to_sub", "sub_to_c16", "sub_to_txt", "parse"]
    input_path: Path
    output_path: Path | None = None
    sample_rate: int = Field(2_000_000, ge=SAMPLE_RATE_MIN, le=SAMPLE_RATE_MAX)
    frequency_hz: int | None = None

    @field_validator("input_path")
    @classmethod
    def validate_input(cls, value: Path) -> Path:
        # Allow captures/ or explicit .sub under captures
        captures = settings.captures_dir.resolve()
        captures.mkdir(parents=True, exist_ok=True)
        resolved = value.expanduser().resolve()
        try:
            resolved.relative_to(captures)
        except ValueError as exc:
            raise ValueError(f"input_path must be under {captures}") from exc
        if not resolved.exists():
            raise ValueError(f"missing: {resolved}")
        return resolved


class PacketListResponse(BaseModel):
    packets: list[dict[str, Any]]


class ReplayPacketRequest(BaseModel):
    packet_id: int
    frequency_hz: int | None = None
    sample_rate: int = Field(2_000_000, ge=SAMPLE_RATE_MIN, le=SAMPLE_RATE_MAX)
    tx_vga: int = 20
    amp: Literal[0] = 0
    max_duration_sec: float | None = Field(default=5.0, gt=0, le=MAX_BF_DURATION)

    @field_validator("tx_vga")
    @classmethod
    def vga(cls, v: int) -> int:
        return _require_tx_vga(v)


class BruteforceStopResponse(BaseModel):
    status: dict[str, Any]
    candidate_keys: list[int]
    center_sample: int | None
    note: str
