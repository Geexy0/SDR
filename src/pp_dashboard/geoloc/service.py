"""Singleton geolocation facade — operator GPS + auto breadcrumb stamping."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, ClassVar

from pp_dashboard.config import settings
from pp_dashboard.geoloc.store import GeolocStore

log = logging.getLogger(__name__)


class GeolocService:
    _instance: ClassVar[GeolocService | None] = None
    _instance_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    def __init__(self, store: GeolocStore | None = None) -> None:
        self.store = store or GeolocStore(settings.captures_dir / "geoloc.sqlite3")

    @classmethod
    async def get_instance(cls) -> GeolocService:
        async with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        cls._instance = None

    def status(self) -> dict[str, Any]:
        op = self.store.get_operator()
        return {
            "ok": True,
            "operator": op,
            "breadcrumb_count": len(self.store.list_breadcrumbs(limit=5000)),
            "lob_count": len(self.store.list_lobs()),
            "gnss_latest": (self.store.list_gnss_samples(limit=1) or [None])[0],
        }

    def set_operator(
        self,
        lat: float,
        lon: float,
        *,
        accuracy_m: float | None = None,
        alt_m: float | None = None,
        source: str = "manual",
        push_tpms: bool = True,
    ) -> dict[str, Any]:
        row = self.store.set_operator(
            lat, lon, accuracy_m=accuracy_m, alt_m=alt_m, source=source
        )
        if push_tpms:
            try:
                from pp_dashboard.tpms.engine import TpmsEngine

                eng = TpmsEngine._instance  # noqa: SLF001
                if eng is not None:
                    eng.receiver_lat = float(lat)
                    eng.receiver_lon = float(lon)
            except Exception:
                log.debug("tpms operator push skipped", exc_info=True)
        return {"ok": True, "operator": row}

    def stamp(
        self,
        *,
        kind: str,
        frequency_hz: int | None = None,
        rssi_db: float | None = None,
        label: str | None = None,
        ref_id: str | None = None,
        notes: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
    ) -> dict[str, Any] | None:
        """Record a breadcrumb at operator GPS (or explicit lat/lon)."""
        if lat is None or lon is None:
            op = self.store.get_operator()
            if op is None:
                return None
            lat = float(op["lat"])
            lon = float(op["lon"])
        bid = self.store.add_breadcrumb(
            kind=kind,
            lat=float(lat),
            lon=float(lon),
            frequency_hz=frequency_hz,
            rssi_db=rssi_db,
            label=label,
            ref_id=ref_id,
            notes=notes,
        )
        return {"id": bid, "lat": lat, "lon": lon, "kind": kind}


def stamp_rf_hit(**kwargs: Any) -> dict[str, Any] | None:
    """Best-effort stamp from any RX path (sync-safe if service already created)."""
    svc = GeolocService._instance
    if svc is None:
        try:
            path = settings.captures_dir / "geoloc.sqlite3"
            svc = GeolocService(GeolocStore(path))
            GeolocService._instance = svc
        except Exception:
            log.debug("geoloc stamp bootstrap failed", exc_info=True)
            return None
    try:
        return svc.stamp(**kwargs)
    except Exception:
        log.debug("geoloc stamp failed", exc_info=True)
        return None
