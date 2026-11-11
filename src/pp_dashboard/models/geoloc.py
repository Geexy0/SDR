"""Geolocation / DF request models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OperatorFixRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=50_000)
    alt_m: float | None = Field(default=None, ge=-500, le=20_000)
    source: str = Field(default="browser", max_length=32)


class BreadcrumbRequest(BaseModel):
    kind: str = Field(..., min_length=1, max_length=32)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    frequency_hz: int | None = Field(default=None, ge=1_000_000, le=6_000_000_000)
    rssi_db: float | None = Field(default=None, ge=-150, le=50)
    label: str | None = Field(default=None, max_length=120)
    ref_id: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=500)


class LobRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    bearing_deg: float = Field(..., ge=0, lt=360)
    label: str | None = Field(default=None, max_length=64)
    frequency_hz: int | None = Field(default=None, ge=1_000_000, le=6_000_000_000)
    length_m: float = Field(default=25_000, gt=100, le=200_000)


class LobLine(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    bearing_deg: float = Field(..., ge=0, lt=360)


class LobIntersectRequest(BaseModel):
    a: LobLine
    b: LobLine


class TrackBody(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    speed_mps: float = Field(default=0.0, ge=0, le=1000)
    course_deg: float = Field(default=0.0, ge=0, lt=360)


class CpaRequest(BaseModel):
    own: TrackBody
    target: TrackBody
    horizon_s: float = Field(default=3600.0, gt=0, le=86_400)


class GnssSampleRequest(BaseModel):
    duration_sec: float = Field(default=0.25, gt=0.05, le=2.0)
    lna: Literal[0, 8, 16, 24, 32, 40] = 24
    vga: int = Field(default=20, ge=0, le=62)


class LobSolveObservation(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    bearing_deg: float = Field(..., ge=0, lt=360)
    angular_std_deg: float = Field(default=3.0, gt=0.05, le=45.0)
    rssi_db: float | None = Field(default=None, ge=-150, le=50)
    timestamp: str | None = Field(default=None, max_length=64)


class LobSolveRequest(BaseModel):
    observations: list[LobSolveObservation] = Field(..., min_length=2)
    origin_lat: float | None = Field(default=None, ge=-90, le=90)
    origin_lon: float | None = Field(default=None, ge=-180, le=180)


class RssiGridObservation(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    rssi_db: float = Field(..., ge=-150, le=50)
    timestamp: str | None = Field(default=None, max_length=64)


class RssiGridSolveRequest(BaseModel):
    observations: list[RssiGridObservation] = Field(..., min_length=4)
    gamma: float = Field(default=2.8, ge=2.0, le=4.5)
    shadow_std_db: float = Field(default=4.0, ge=0.5, le=20.0)
    grid_n: int = Field(default=80, ge=16, le=160)
    pad_m: float = Field(default=250.0, ge=50.0, le=5_000.0)
    origin_lat: float | None = Field(default=None, ge=-90, le=90)
    origin_lon: float | None = Field(default=None, ge=-180, le=180)


class FoxSampleBody(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    rssi_db: float = Field(..., ge=-150, le=50)
    t_mono: float = Field(..., ge=0)


class FoxhuntRequest(BaseModel):
    samples: list[FoxSampleBody] = Field(..., min_length=2)
    window_sec: float = Field(default=5.0, ge=0.5, le=60.0)


class PolarSampleBody(BaseModel):
    bearing_deg: float = Field(..., ge=0, lt=360)
    rssi_db: float = Field(..., ge=-150, le=50)


class PolarPeakRequest(BaseModel):
    samples: list[PolarSampleBody] = Field(..., min_length=3)


class GnssL1AnalyzeRequest(BaseModel):
    duration_sec: float = Field(default=0.25, gt=0.05, le=2.0)
    lna: Literal[0, 8, 16, 24, 32, 40] = 24
    vga: int = Field(default=20, ge=0, le=62)
    baseline_noise_db: float | None = Field(default=None, ge=-120, le=0)
    filepath: str | None = Field(default=None, max_length=512)
    operator_lat: float | None = Field(default=None, ge=-90, le=90)
    operator_lon: float | None = Field(default=None, ge=-180, le=180)


class LookAngleRequest(BaseModel):
    rx_lat: float = Field(..., ge=-90, le=90)
    rx_lon: float = Field(..., ge=-180, le=180)
    rx_alt_m: float = Field(default=0.0, ge=-500, le=20_000)
    tx_lat: float = Field(..., ge=-90, le=90)
    tx_lon: float = Field(..., ge=-180, le=180)
    tx_alt_m: float = Field(..., ge=-500, le=80_000)
    frequency_hz: float = Field(..., ge=1e6, le=6e9)


class GeoHuntIngestRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    rssi_db: float = Field(..., ge=-150, le=50)
    freq_hz: int = Field(433_920_000, ge=1_000_000, le=6_000_000_000)
    emitter_id: str = Field(default="PHY-UNKNOWN", min_length=1, max_length=64)
    timestamp: float | None = Field(default=None, ge=0)


class GeoHuntBearingRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    bearing_deg: float = Field(..., ge=0, lt=360)
    emitter_id: str = Field(default="LOB", min_length=1, max_length=64)
    freq_hz: int = Field(default=0, ge=0, le=6_000_000_000)
    rssi_db: float | None = Field(default=None, ge=-150, le=50)
    angular_std_deg: float = Field(default=3.0, ge=0.1, le=45.0)


class DopplerSampleBody(BaseModel):
    timestamp: float
    measured_cfo_hz: float


class DopplerFitRequest(BaseModel):
    samples: list[DopplerSampleBody] = Field(..., min_length=4, max_length=512)
    nominal_freq_hz: float = Field(..., ge=1e6, le=6e9)


class RadiosondeInjectRequest(BaseModel):
    serial_id: str = Field(..., min_length=1, max_length=16)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    alt_m: float = Field(..., ge=-500, le=50_000)
    vz_mps: float = Field(default=0.0, ge=-100, le=100)
    wind_speed_mps: float = Field(default=0.0, ge=0, le=150)
    wind_dir_deg: float = Field(default=0.0, ge=0, lt=360)
    temp_c: float | None = Field(default=None, ge=-100, le=60)
    frequency_hz: int = Field(default=402_500_000, ge=400_150_000, le=406_000_000)
    kind: Literal["RS41", "M20", "unknown"] = "RS41"
