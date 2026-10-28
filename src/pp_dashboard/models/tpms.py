"""TPMS fleet API models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from pp_dashboard.models.rf import FREQ_MAX_HZ, FREQ_MIN_HZ


class TpmsListenRequest(BaseModel):
    frequency_hz: int = Field(433_920_000, ge=FREQ_MIN_HZ, le=FREQ_MAX_HZ)
    # HackRF Soapy only accepts integer-MHz rates; 2e6 is the stable TPMS default.
    sample_rate: int = Field(2_000_000, ge=1_000_000, le=20_000_000)
    max_duration_sec: float | None = Field(default=None, gt=0, le=3600)
    # Map pin for last-seen placement (receiver location)
    receiver_lat: float | None = Field(default=None, ge=-90, le=90)
    receiver_lon: float | None = Field(default=None, ge=-180, le=180)
    auto_link_vehicles: bool = True
    tpms_only: bool = True
    # Optional hop list (e.g. EU 433.92 + US 315). Empty → frequency_hz only.
    hop_frequencies_hz: list[int] = Field(default_factory=list)
    hop_interval_sec: float = Field(20.0, ge=5.0, le=600.0)
    # SoapyHackRF gain string; AMP off unless SCOPE amends TX (RX amp ok at 0).
    gain: str = Field(default="LNA=32,VGA=40,AMP=0", max_length=80)
    band: str = Field(
        default="eu433",
        description="eu433 | us315 | hop_both — convenience for hop list",
    )


class FleetVehicleUpsert(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    year: int | None = Field(default=None, ge=1980, le=2100)
    color: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=2000)
    sensor_ids: list[str] = Field(default_factory=list)
    vehicle_id: int | None = None


class SensorLinkRequest(BaseModel):
    sensor_id: str = Field(..., min_length=1, max_length=64)
    vehicle_id: int | None = None


class TpmsStatusResponse(BaseModel):
    status: dict[str, Any]
