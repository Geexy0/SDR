"""Cluster co-heard TPMS sensors into vehicle candidates (passing-car window)."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field


@dataclass
class PassageCluster:
    """Sensors heard together within a short pass-by window."""

    cluster_key: str
    sensor_ids: set[str] = field(default_factory=set)
    oem_family: str | None = None
    first_mono: float = 0.0
    last_mono: float = 0.0
    hit_count: int = 0
    lat: float | None = None
    lon: float | None = None
    sealed: bool = False


class PassageClusterer:
    """
    Rolling window: sensors heard within `window_sec` of each other merge into
    one cluster (typical drive-by: several tires transmit within a few seconds).
    """

    def __init__(self, window_sec: float = 12.0, seal_idle_sec: float = 20.0) -> None:
        self.window_sec = window_sec
        self.seal_idle_sec = seal_idle_sec
        self._open: PassageCluster | None = None
        self.recent_sealed: list[PassageCluster] = []

    def observe(
        self,
        sensor_id: str,
        *,
        oem_family: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        now: float | None = None,
    ) -> tuple[PassageCluster, bool]:
        """
        Returns (cluster, sealed_just_now).
        When a prior open cluster goes idle, it is sealed and returned first via
        a follow-up call — here we seal lazily before attaching the new hit.
        """
        t = time.monotonic() if now is None else now
        sealed_now = False
        if self._open is not None and (t - self._open.last_mono) > self.seal_idle_sec:
            self._seal_open()
            sealed_now = True

        if self._open is None:
            key = _cluster_key({sensor_id})
            self._open = PassageCluster(
                cluster_key=key,
                sensor_ids={sensor_id},
                oem_family=oem_family,
                first_mono=t,
                last_mono=t,
                hit_count=1,
                lat=lat,
                lon=lon,
            )
            return self._open, sealed_now

        # Same pass if within window of last hit
        if (t - self._open.last_mono) <= self.window_sec:
            self._open.sensor_ids.add(sensor_id)
            self._open.last_mono = t
            self._open.hit_count += 1
            if oem_family and not self._open.oem_family:
                self._open.oem_family = oem_family
            if lat is not None:
                self._open.lat = lat
            if lon is not None:
                self._open.lon = lon
            self._open.cluster_key = _cluster_key(self._open.sensor_ids)
            return self._open, sealed_now

        # Gap too large — seal previous and start fresh
        self._seal_open()
        sealed_now = True
        key = _cluster_key({sensor_id})
        self._open = PassageCluster(
            cluster_key=key,
            sensor_ids={sensor_id},
            oem_family=oem_family,
            first_mono=t,
            last_mono=t,
            hit_count=1,
            lat=lat,
            lon=lon,
        )
        return self._open, sealed_now

    def flush_idle(self, now: float | None = None) -> PassageCluster | None:
        t = time.monotonic() if now is None else now
        if self._open is None:
            return None
        if (t - self._open.last_mono) > self.seal_idle_sec:
            return self._seal_open()
        return None

    def force_seal(self) -> PassageCluster | None:
        """Seal the open cluster immediately (listen stop)."""
        return self._seal_open()

    def _seal_open(self) -> PassageCluster | None:
        c = self._open
        self._open = None
        if c is None:
            return None
        c.sealed = True
        c.cluster_key = _cluster_key(c.sensor_ids)
        self.recent_sealed.append(c)
        del self.recent_sealed[:-50]
        return c


def _cluster_key(sensor_ids: set[str]) -> str:
    joined = ",".join(sorted(sensor_ids))
    digest = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:10].upper()
    return f"PASS-{digest}"


def suggested_label(oem_family: str | None, sensor_ids: set[str]) -> str:
    short = sorted(sensor_ids)[0][-4:] if sensor_ids else "????"
    family = (oem_family or "TPMS").split("/")[0].split()[0]
    n = len(sensor_ids)
    return f"{family}-{short}" if n == 1 else f"{family}-x{n}-{short}"
