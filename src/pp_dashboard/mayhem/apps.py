"""Featured Mayhem apps surfaced by the launcher."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FeaturedApp:
    short_name: str
    label: str
    category: str
    aliases: tuple[str, ...] = ()


# Prefer official applist short names; aliases cover firmware naming drift.
FEATURED_APPS: tuple[FeaturedApp, ...] = (
    FeaturedApp("jammer", "Jammer", "Transmit", ("jammer",)),
    FeaturedApp("bletx", "BLE TX", "Transmit", ("bletx", "blespam", "ble_tx")),
    FeaturedApp("rx_subghz", "Sub-GHz RX", "Receive", ("rx_subghz", "subghzd", "subghz")),
    FeaturedApp("replay", "Replay", "Transmit", ("replay",)),
    FeaturedApp("pocsag", "POCSAG", "Receive", ("pocsag", "pocsag_tx")),
    FeaturedApp("adsb_rx", "ADS-B RX", "Receive", ("adsb_rx", "adsb")),
    FeaturedApp(
        "looking_glass",
        "Looking Glass",
        "Receive",
        ("looking_glass", "lookingglass", "looking_glass"),
    ),
)


def resolve_featured_short_name(name: str, applist_shorts: set[str]) -> str:
    """Map a featured / alias name onto a short name present in ``applist``."""
    needle = name.strip().lower()
    if needle in applist_shorts:
        return needle
    for feat in FEATURED_APPS:
        aliases = {feat.short_name.lower(), *(a.lower() for a in feat.aliases)}
        if needle in aliases:
            for candidate in (feat.short_name, *feat.aliases):
                if candidate.lower() in applist_shorts:
                    return candidate.lower() if candidate.lower() in applist_shorts else candidate
            # Fall through: return preferred short name for appstart attempt.
            return feat.short_name
    return name.strip()
