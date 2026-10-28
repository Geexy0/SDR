"""Public speakable-band plan for Auto Voice Recon (RX-only).

Police / emergency *dispatch disruption* remains out of remit for TX. This RX
plan omits dedicated public-safety trunking packs; marine CH16 is included as a
public calling / distress listen channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DemodMode = Literal["nbfm", "am", "wbfm"]


@dataclass(frozen=True, slots=True)
class VoiceBand:
    id: str
    name: str
    start_mhz: float
    stop_mhz: float
    mode: DemodMode
    bin_width_hz: int = 12_500
    default_enabled: bool = True
    note: str = ""


# EU / FR-centric public voice allocations (HackRF 1 MHz … 6 GHz).
VOICE_BANDS: tuple[VoiceBand, ...] = (
    VoiceBand(
        id="cb27",
        name="CB 27 MHz",
        start_mhz=26.965,
        stop_mhz=27.405,
        mode="am",
        bin_width_hz=10_000,
        note="Citizen band AM/FM",
    ),
    VoiceBand(
        id="airband",
        name="Airband VHF",
        start_mhz=118.0,
        stop_mhz=137.0,
        mode="am",
        bin_width_hz=25_000,
        note="Aviation AM (listen-only)",
    ),
    VoiceBand(
        id="marine_vhf",
        name="Marine VHF",
        start_mhz=156.0,
        stop_mhz=162.025,
        mode="nbfm",
        bin_width_hz=12_500,
        note="Incl. CH16 distress / calling",
    ),
    VoiceBand(
        id="ham_2m",
        name="Amateur 2 m",
        start_mhz=144.0,
        stop_mhz=146.0,
        mode="nbfm",
        bin_width_hz=12_500,
        note="Region 1 2 m voice",
    ),
    VoiceBand(
        id="pmr446",
        name="PMR446",
        start_mhz=446.0,
        stop_mhz=446.2,
        mode="nbfm",
        bin_width_hz=6_250,
        note="License-free EU handhelds",
    ),
    VoiceBand(
        id="lpd433",
        name="LPD 433",
        start_mhz=433.075,
        stop_mhz=434.775,
        mode="nbfm",
        bin_width_hz=25_000,
        note="LPD/ISM voice & telemetry",
    ),
    VoiceBand(
        id="ham_70cm",
        name="Amateur 70 cm",
        start_mhz=430.0,
        stop_mhz=440.0,
        mode="nbfm",
        bin_width_hz=12_500,
        note="Region 1 70 cm voice",
    ),
)


def bands_by_id() -> dict[str, VoiceBand]:
    return {b.id: b for b in VOICE_BANDS}


def resolve_bands(enabled_ids: list[str] | None) -> list[VoiceBand]:
    catalog = bands_by_id()
    if not enabled_ids:
        return [b for b in VOICE_BANDS if b.default_enabled]
    out: list[VoiceBand] = []
    for bid in enabled_ids:
        band = catalog.get(bid)
        if band is None:
            raise ValueError(f"unknown voice band id: {bid}")
        out.append(band)
    if not out:
        raise ValueError("at least one voice band must be enabled")
    return out
