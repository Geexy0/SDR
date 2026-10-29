"""Phase 5 recon / waterfall / spoof request models."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from pp_dashboard.config import settings
from pp_dashboard.models.rf import (
    FREQ_MAX_HZ,
    FREQ_MIN_HZ,
    _require_lna,
    _require_rx_vga,
    _require_tx_vga,
)


class SweepStartRequest(BaseModel):
    start_mhz: float = Field(..., ge=1, le=6000)
    stop_mhz: float = Field(..., ge=1, le=6000)
    bin_width_hz: int = Field(100_000, ge=2445, le=5_000_000)
    lna: int = 16
    vga: int = 20
    amp: Literal[0] = 0
    max_duration_sec: float | None = Field(default=60.0, gt=0, le=120)
    target_fps: float = Field(30.0, ge=1, le=60)
    # trigger
    trigger_enabled: bool = False
    trigger_f_lo_hz: int | None = None
    trigger_f_hi_hz: int | None = None
    trigger_rssi_db: float = -40.0
    trigger_action: Literal["log", "capture", "tx_tone"] = "log"

    @field_validator("lna")
    @classmethod
    def v_lna(cls, v: int) -> int:
        return _require_lna(v)

    @field_validator("vga")
    @classmethod
    def v_vga(cls, v: int) -> int:
        return _require_rx_vga(v)

    @model_validator(mode="after")
    def span(self) -> SweepStartRequest:
        if self.stop_mhz <= self.start_mhz:
            raise ValueError("stop_mhz must be > start_mhz")
        return self


class DecoderStartRequest(BaseModel):
    kind: Literal["pocsag", "adsb", "ais"]
    frequency_hz: int = Field(..., ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    # Optional map center for network track fallback (OpenSky).
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


class VoiceReconStartRequest(BaseModel):
    """Auto scan public speakable bands; dwell while talking; AGC for range."""

    bands: list[str] | None = None
    detect_margin_db: float = Field(12.0, ge=4.0, le=40.0)
    min_detect_db: float = Field(-75.0, ge=-120.0, le=-20.0)
    quiet_hold_sec: float = Field(2.5, ge=0.5, le=15.0)
    max_dwell_sec: float = Field(90.0, ge=5.0, le=300.0)
    start_lna: int = 24
    start_vga: int = 20
    # Legacy alias: when True and local_aplay omitted, keep browser path (WS) only.
    audio_enabled: bool = True
    local_aplay: bool = False
    squelch_db: float = Field(-50.0, ge=-120.0, le=0.0)
    vox_open_hold_sec: float = Field(0.5, ge=0.2, le=5.0)

    @field_validator("start_lna")
    @classmethod
    def v_lna(cls, v: int) -> int:
        return _require_lna(v)

    @field_validator("start_vga")
    @classmethod
    def v_vga(cls, v: int) -> int:
        return _require_rx_vga(v)


class SynthesizeRequest(BaseModel):
    kind: Literal["gps_l1", "lrs_pager", "touchtunes", "fm_rds"]
    tx_vga: int = 20
    amp: Literal[0] = 0
    max_duration_sec: float | None = Field(default=15.0, gt=0, le=60)
    # gps
    ephemeris_path: Path | None = None
    lat: float = 48.8566
    lon: float = 2.3522
    alt: float = 35.0
    gps_duration_s: int = Field(20, ge=5, le=120)
    # pager
    pager_id: int = Field(1, ge=0, le=65535)
    pin: str = "1234"
    frequency_hz: int = Field(433_920_000, ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    # fm
    fm_frequency_hz: int = Field(100_000_000, ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    ps: str = "LAB-FM"
    rt: str = "PP-LAB-RF-2026"
    audio_hz: float = 440.0

    @field_validator("tx_vga")
    @classmethod
    def v_tx(cls, v: int) -> int:
        return _require_tx_vga(v)

    @field_validator("ephemeris_path")
    @classmethod
    def ephem(cls, v: Path | None) -> Path | None:
        if v is None:
            return None
        captures = settings.captures_dir.resolve()
        resolved = v.expanduser().resolve()
        try:
            resolved.relative_to(captures)
        except ValueError as exc:
            raise ValueError(f"ephemeris must be under {captures}") from exc
        return resolved


class MissionStep(BaseModel):
    """One step in an autonomous recon mission pipeline."""

    type: Literal[
        "STEP_SWEEP_RANK",
        "STEP_BURST_CAPTURE",
        "STEP_AUTO_ANALYZE",
        "STEP_FREQMAN_EXPORT",
    ]
    f_start_hz: int | None = Field(default=None, ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    f_stop_hz: int | None = Field(default=None, ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    duration_sec: float | None = Field(default=None, gt=0, le=120)
    top_k: int | None = Field(default=None, ge=1, le=64)
    threshold_db: float | None = Field(default=None, ge=-120, le=0)
    bin_width_hz: int | None = Field(default=None, ge=2445, le=5_000_000)
    sample_rate: int | None = Field(default=None, ge=2_000_000, le=20_000_000)
    lna: int | None = None
    vga: int | None = None
    category: str | None = Field(default=None, max_length=24)
    sync_sd: bool | None = None

    @field_validator("lna")
    @classmethod
    def v_lna(cls, v: int | None) -> int | None:
        return None if v is None else _require_lna(v)

    @field_validator("vga")
    @classmethod
    def v_vga(cls, v: int | None) -> int | None:
        return None if v is None else _require_rx_vga(v)


class MissionStartRequest(BaseModel):
    """Start a multi-step RX recon mission (template and/or explicit steps)."""

    name: str | None = Field(default=None, max_length=120)
    template_id: str | None = Field(default=None, max_length=64)
    sync_freqman_sd: bool = False
    steps: list[MissionStep] | None = None

    @model_validator(mode="after")
    def require_steps_or_template(self) -> MissionStartRequest:
        if not self.template_id and not self.steps:
            raise ValueError("provide template_id or steps")
        return self


class ReconStatus(BaseModel):
    waterfall_running: bool
    decoder_kind: str | None = None
    decoder_running: bool = False
    trigger_events: list[dict[str, Any]] = Field(default_factory=list)
