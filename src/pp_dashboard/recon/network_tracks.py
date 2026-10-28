"""Network track pollers used when local ADS-B/AIS SDR tools are missing."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

LineHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


def _http_get_json(url: str, timeout: float = 8.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "pp-dashboard/0.5 (lab recon map)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


@dataclass
class NetworkTrackSession:
    """Poll OpenSky (aircraft) around a lat/lon box; emit adsb metrics."""

    kind: str  # "adsb" | "ais"
    lat: float
    lon: float
    span_deg: float = 1.5
    interval_sec: float = 8.0
    handlers: list[LineHandler] = field(default_factory=list)
    running: bool = False
    _task: asyncio.Task[None] | None = None
    cmd_desc: str = ""

    def subscribe(self, handler: LineHandler) -> None:
        if handler not in self.handlers:
            self.handlers.append(handler)

    def unsubscribe(self, handler: LineHandler) -> None:
        self.handlers = [h for h in self.handlers if h is not handler]

    async def _emit(self, payload: dict[str, Any]) -> None:
        for h in list(self.handlers):
            try:
                r = h(payload)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("network track handler failed")

    async def start(self) -> str:
        if self.running:
            raise RuntimeError("network tracks already running")
        self.running = True
        if self.kind == "adsb":
            self.cmd_desc = (
                f"opensky://states lat={self.lat:.4f} lon={self.lon:.4f} "
                f"span={self.span_deg}"
            )
        else:
            self.cmd_desc = (
                f"ais-network://bbox lat={self.lat:.4f} lon={self.lon:.4f} "
                f"(no free AIS feed configured — install rtl_ais or ais-catcher)"
            )
            self.running = False
            raise FileNotFoundError(
                "AIS needs ais-catcher (HackRF) or rtl_ais (RTL-SDR) on PATH "
                "(sudo apt install rtl-ais). No free boat network feed bundled."
            )
        self._task = asyncio.create_task(self._loop())
        return self.cmd_desc

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while self.running:
            try:
                tracks = await asyncio.to_thread(self._fetch_opensky)
                for t in tracks:
                    await self._emit(t)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("OpenSky poll failed: %s", exc)
                await self._emit(
                    {
                        "type": "adsb",
                        "raw": f"opensky_error:{exc}",
                        "network": True,
                    }
                )
            await asyncio.sleep(self.interval_sec)

    def _fetch_opensky(self) -> list[dict[str, Any]]:
        lamin = self.lat - self.span_deg
        lamax = self.lat + self.span_deg
        lomin = self.lon - self.span_deg
        lomax = self.lon + self.span_deg
        qs = urllib.parse.urlencode(
            {
                "lamin": f"{lamin:.4f}",
                "lamax": f"{lamax:.4f}",
                "lomin": f"{lomin:.4f}",
                "lomax": f"{lomax:.4f}",
            }
        )
        url = f"https://opensky-network.org/api/states/all?{qs}"
        try:
            data = _http_get_json(url)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"OpenSky HTTP {exc.code}") from exc
        states = data.get("states") or []
        out: list[dict[str, Any]] = []
        for st in states:
            # [icao24, callsign, origin_country, time_position, last_contact,
            #  longitude, latitude, baro_altitude, on_ground, velocity, ...]
            if not isinstance(st, list) or len(st) < 7:
                continue
            lon, lat = st[5], st[6]
            if lon is None or lat is None:
                continue
            icao = str(st[0] or "").upper()
            call = str(st[1] or "").strip() or icao
            alt = st[7]
            out.append(
                {
                    "type": "adsb",
                    "icao": icao,
                    "callsign": call,
                    "lat": float(lat),
                    "lon": float(lon),
                    "alt": float(alt) if alt is not None else None,
                    "network": True,
                    "source": "OPENSKY_NET",
                }
            )
        return out
