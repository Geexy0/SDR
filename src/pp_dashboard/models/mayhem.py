"""Mayhem Phase 3 API models."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from pp_dashboard.mayhem.bridge import BUTTON_MAP


class MayhemInputKind(str, Enum):
    BUTTON = "button"
    TOUCH = "touch"
    KEYBOARD = "keyboard"


class MayhemInputRequest(BaseModel):
    kind: MayhemInputKind
    button: int | None = Field(default=None, ge=1, le=8)
    x: int | None = None
    y: int | None = None
    text: str | None = None
    hex: str | None = Field(default=None, description="Optional raw Mayhem keyboard hex")

    @model_validator(mode="after")
    def validate_payload(self) -> MayhemInputRequest:
        if self.kind is MayhemInputKind.BUTTON:
            if self.button is None or self.button not in BUTTON_MAP:
                raise ValueError("button requires button=1..8")
        elif self.kind is MayhemInputKind.TOUCH:
            if self.x is None or self.y is None:
                raise ValueError("touch requires x and y")
            if self.x < 0 or self.y < 0:
                raise ValueError("touch coordinates must be >= 0")
        elif self.kind is MayhemInputKind.KEYBOARD:
            if not self.text and not self.hex:
                raise ValueError("keyboard requires text or hex")
        return self


class MayhemInputResponse(BaseModel):
    ok: bool = True
    kind: MayhemInputKind
    response: str
    button_label: str | None = None


class MayhemAppStartRequest(BaseModel):
    app_id: str = Field(..., min_length=1, max_length=64)


class MayhemAppInfo(BaseModel):
    short_name: str
    full_name: str
    category: str
    featured: bool = False


class MayhemAppListResponse(BaseModel):
    apps: list[MayhemAppInfo]
    featured: list[str]


class MayhemAppStartResponse(BaseModel):
    ok: bool = True
    app_id: str
    resolved_id: str
    response: str


class ScreenStreamControl(BaseModel):
    action: Literal["ping", "stop"] = "ping"


class FreqmanBookmarkRequest(BaseModel):
    frequency_hz: int = Field(..., ge=1_000_000, le=6_000_000_000)
    description: str = Field(default="", max_length=64)
    category: str = Field(default="RECON", max_length=24)
    modulation: str | None = Field(default=None, max_length=8)
    bandwidth: str | None = Field(default=None, max_length=12)
    source: str = Field(default="dashboard", max_length=32)


class FreqmanSyncRequest(BaseModel):
    categories: list[str] | None = Field(
        default=None,
        description="FREQMAN categories to push (default RECON + NORMANDY)",
    )
    pull_captures: bool = Field(
        default=True,
        description="Also pull new .C16 / .SUB from PortaPack SD into captures/",
    )
