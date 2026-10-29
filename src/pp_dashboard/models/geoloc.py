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
