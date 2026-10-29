"""TPMS RX + passing-car cluster / map orchestration (RX remit)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, ClassVar

from pp_dashboard.config import settings
from pp_dashboard.hardware.arbiter import HardwareArbiter, get_arbiter
from pp_dashboard.models.rf import TelemetryEvent
from pp_dashboard.subghz.db import PacketStore
from pp_dashboard.subghz.sniffer import DEFAULT_HACKRF_GAIN, DEFAULT_HACKRF_SAMPLE_RATE, Rtl433Sniffer
from pp_dashboard.tpms.cluster import PassageClusterer, suggested_label
from pp_dashboard.tpms.oem_hints import (
    TPMS_PROTOCOL_IDS,
    extract_sensor_id,
    is_tpms_payload,
    oem_hint_from_payload,
)
from pp_dashboard.tpms.store import TpmsFleetStore

log = logging.getLogger(__name__)

DEFAULT_TPMS_HZ = 433_920_000
US_TPMS_HZ = 315_000_000
_NOISE_RE = re.compile(
    r"Estimated noise level is\s+(-?\d+(?:\.\d+)?)\s*dB", re.IGNORECASE
)


def resolve_tpms_frequencies(
    *,
    band: str,
    frequency_hz: int,
    hop_frequencies_hz: list[int] | None,
) -> tuple[int, list[int]]:
    """Return (primary_hz, hop_list). hop_list empty → single frequency."""
    b = (band or "eu433").strip().lower()
    hops = [int(x) for x in (hop_frequencies_hz or []) if int(x) > 0]
    if hops:
        return hops[0], hops
    if b in {"us315", "us", "315"}:
        return US_TPMS_HZ, []
    if b in {"hop", "hop_both", "both", "eu_us"}:
        return DEFAULT_TPMS_HZ, [DEFAULT_TPMS_HZ, US_TPMS_HZ]
    return int(frequency_hz) or DEFAULT_TPMS_HZ, []


class TpmsEngine:
    _instance: ClassVar[TpmsEngine | None] = None

    def __init__(self, arbiter: HardwareArbiter) -> None:
        self.arbiter = arbiter
        self.packet_store = PacketStore(settings.subghz_db_path)
        self.fleet = TpmsFleetStore(settings.captures_dir / "tpms_fleet.sqlite3")
        self.sniffer = Rtl433Sniffer(arbiter, self.packet_store)
        self.clusterer = PassageClusterer(window_sec=12.0, seal_idle_sec=18.0)
        self.running = False
        self.frequency_hz = DEFAULT_TPMS_HZ
        self.hop_frequencies_hz: list[int] = []
        self.receiver_lat: float | None = None
        self.receiver_lon: float | None = None
        self.auto_link_vehicles = True
        self.decode_count = 0
        self.last_noise_db: float | None = None
        self.last_rx_line: str | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._stderr_bound = False
        self.sniffer.subscribe(self._on_packet)

    @classmethod
    async def get_instance(cls) -> TpmsEngine:
        if cls._instance is None:
            cls._instance = cls(await get_arbiter())
        return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        cls._instance = None

    def status(self) -> dict[str, Any]:
        open_c = self.clusterer._open  # noqa: SLF001
        return {
            "running": self.running,
            "frequency_hz": self.frequency_hz,
            "hop_frequencies_hz": list(self.hop_frequencies_hz),
            "receiver_lat": self.receiver_lat,
            "receiver_lon": self.receiver_lon,
            "auto_link_vehicles": self.auto_link_vehicles,
            "decode_count": self.decode_count,
            "last_noise_db": self.last_noise_db,
            "last_rx_line": self.last_rx_line,
            "sensor_count": len(self.fleet.list_sensors(limit=10_000)),
            "vehicle_count": len(self.fleet.list_vehicles()),
            "passage_count": len(self.fleet.list_passages(limit=10_000)),
            "open_cluster": None
            if open_c is None
            else {
                "cluster_key": open_c.cluster_key,
                "sensor_ids": sorted(open_c.sensor_ids),
                "oem_family": open_c.oem_family,
                "hit_count": open_c.hit_count,
            },
            "scope_note": (
                "RX remit's always-on lane: TPMS passing-car ID mapping. "
                "SCOPE+CONFIRM gates TX/jam only. Operator remains responsible for privacy law."
            ),
            "hardware": self.arbiter.status().model_dump(mode="json"),
        }

    async def start(
        self,
        *,
        frequency_hz: int = DEFAULT_TPMS_HZ,
        sample_rate: int = DEFAULT_HACKRF_SAMPLE_RATE,
        max_duration_sec: float | None = None,
        receiver_lat: float | None = None,
        receiver_lon: float | None = None,
        auto_link_vehicles: bool = True,
        tpms_only: bool = True,
        hop_frequencies_hz: list[int] | None = None,
        hop_interval_sec: float = 20.0,
        gain: str = DEFAULT_HACKRF_GAIN,
        band: str = "eu433",
    ) -> dict[str, Any]:
        if self.running:
            raise RuntimeError("TPMS listen already running")
        primary, hops = resolve_tpms_frequencies(
            band=band,
            frequency_hz=frequency_hz,
            hop_frequencies_hz=hop_frequencies_hz,
        )
        self.frequency_hz = primary
        self.hop_frequencies_hz = hops
        if receiver_lat is None or receiver_lon is None:
            try:
                from pp_dashboard.geoloc.service import GeolocService

                svc = GeolocService._instance  # noqa: SLF001
                op = svc.store.get_operator() if svc is not None else None
                if op is not None:
                    receiver_lat = float(op["lat"])
                    receiver_lon = float(op["lon"])
            except Exception:
                pass
        self.receiver_lat = receiver_lat
        self.receiver_lon = receiver_lon
        self.auto_link_vehicles = auto_link_vehicles
        self.decode_count = 0
        self.last_noise_db = None
        self.last_rx_line = None
        self.clusterer = PassageClusterer(window_sec=12.0, seal_idle_sec=18.0)
        self._ensure_stderr_watch()
        argv = await self.sniffer.start(
            frequency_hz=primary,
            sample_rate=sample_rate,
            max_duration_sec=max_duration_sec,
            protocol_ids=list(TPMS_PROTOCOL_IDS) if tpms_only else None,
            hop_frequencies_hz=hops or None,
            hop_interval_sec=hop_interval_sec,
            gain=gain,
            autolevel=True,
        )
        self.running = True
        self._flush_task = asyncio.create_task(self._flush_loop(), name="tpms_cluster_flush")
        await self.arbiter._fanout_telemetry(  # noqa: SLF001
            TelemetryEvent(
                type="status",
                state=self.arbiter.state,
                message="tpms_started",
                metrics={
                    "frequency_hz": primary,
                    "hop_frequencies_hz": hops,
                    "argv": argv,
                    "receiver_lat": receiver_lat,
                    "receiver_lon": receiver_lon,
                },
            )
        )
        return {"argv": argv, "status": self.status()}

    async def stop(self) -> dict[str, Any]:
        self.running = False
        if self._flush_task:
            self._flush_task.cancel()
            await asyncio.gather(self._flush_task, return_exceptions=True)
            self._flush_task = None
        sealed = self.clusterer.force_seal()
        if sealed is not None:
            await self._commit_passage(sealed)
        await self.sniffer.stop()
        await self.arbiter._fanout_telemetry(  # noqa: SLF001
            TelemetryEvent(
                type="status",
                state=self.arbiter.state,
                message="tpms_stopped",
            )
        )
        return {"status": self.status()}

    def clear_fleet(self) -> dict[str, Any]:
        if self.running:
            raise RuntimeError("stop TPMS listen before clearing fleet")
        deleted = self.fleet.clear_all()
        self.clusterer = PassageClusterer(window_sec=12.0, seal_idle_sec=18.0)
        self.decode_count = 0
        return {"cleared": deleted, "status": self.status()}

    def _ensure_stderr_watch(self) -> None:
        if self._stderr_bound:
            return
        self.arbiter.subscribe_telemetry(self._on_telemetry)
        self._stderr_bound = True

    async def _on_telemetry(self, event: TelemetryEvent) -> None:
        if not self.running:
            return
        if event.type not in {"stderr", "stdout"} or not event.message:
            return
        line = event.message
        # rtl_433 "-F log" emits Auto Level on stdout; noise JSON also possible
        m = _NOISE_RE.search(line)
        noise_from_json: float | None = None
        if line.lstrip().startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and payload.get("noise") is not None:
                try:
                    noise_from_json = float(payload["noise"])
                except (TypeError, ValueError):
                    noise_from_json = None
        if m or noise_from_json is not None:
            if m:
                try:
                    self.last_noise_db = float(m.group(1))
                except ValueError:
                    pass
            if noise_from_json is not None:
                self.last_noise_db = noise_from_json
            self.last_rx_line = line.strip()[:160]
            await self.arbiter._fanout_telemetry(  # noqa: SLF001
                TelemetryEvent(
                    type="metric",
                    state=self.arbiter.state,
                    message="tpms_rx_health",
                    metrics={
                        "last_noise_db": self.last_noise_db,
                        "decode_count": self.decode_count,
                    },
                )
            )

    async def _flush_loop(self) -> None:
        try:
            while self.running:
                await asyncio.sleep(2.0)
                sealed = self.clusterer.flush_idle()
                if sealed is not None:
                    await self._commit_passage(sealed)
        except asyncio.CancelledError:
            raise

    async def _on_packet(self, payload: dict[str, Any], packet_id: int) -> None:
        if not is_tpms_payload(payload):
            return
        sensor_id = extract_sensor_id(payload)
        if not sensor_id:
            return
        self.decode_count += 1
        hints = oem_hint_from_payload(payload)
        oem = hints.get("oem_family")
        sighting = self.fleet.record_hit(
            sensor_id=sensor_id,
            payload=payload,
            oem_family=oem,  # type: ignore[arg-type]
            frequency_hz=self.frequency_hz,
            lat=self.receiver_lat,
            lon=self.receiver_lon,
        )
        if self.receiver_lat is not None and self.receiver_lon is not None:
            try:
                from pp_dashboard.geoloc.service import stamp_rf_hit

                stamp_rf_hit(
                    kind="tpms",
                    frequency_hz=self.frequency_hz,
                    rssi_db=None,
                    label=sighting.last_model or sensor_id,
                    ref_id=sensor_id,
                    lat=self.receiver_lat,
                    lon=self.receiver_lon,
                )
            except Exception:
                log.debug("tpms geoloc stamp skipped", exc_info=True)
        cluster, _ = self.clusterer.observe(
            sensor_id,
            oem_family=oem,  # type: ignore[arg-type]
            lat=self.receiver_lat,
            lon=self.receiver_lon,
        )

        vehicle = None
        if sighting.vehicle_id is not None:
            vehicle = self.fleet.get_vehicle(sighting.vehicle_id)

        metrics: dict[str, Any] = {
            "packet_id": packet_id,
            "sensor_id": sighting.sensor_id,
            "hit_count": sighting.hit_count,
            "model": sighting.last_model,
            "oem_family": sighting.oem_family,
            "suggested_make": hints.get("suggested_make"),
            "suggested_model": hints.get("suggested_model"),
            "oem_note": hints.get("oem_note"),
            "pressure_kpa": sighting.last_pressure_kpa,
            "temp_c": sighting.last_temp_c,
            "lat": sighting.last_lat,
            "lon": sighting.last_lon,
            "vehicle_id": sighting.vehicle_id,
            "vehicle_label": vehicle.label if vehicle else None,
            "vehicle_make": vehicle.make if vehicle else None,
            "vehicle_model": vehicle.model if vehicle else None,
            "cluster_key": cluster.cluster_key,
            "cluster_sensors": sorted(cluster.sensor_ids),
            "cluster_hits": cluster.hit_count,
            "decode_count": self.decode_count,
        }
        await self.arbiter._fanout_telemetry(  # noqa: SLF001
            TelemetryEvent(
                type="metric",
                state=self.arbiter.state,
                message="tpms",
                metrics=metrics,
            )
        )

    async def _commit_passage(self, cluster: Any) -> None:
        label = suggested_label(cluster.oem_family, cluster.sensor_ids)
        make: str | None = None
        model_hint: str | None = None
        for sid in cluster.sensor_ids:
            s = self.fleet.get_sensor(sid)
            if not s:
                continue
            hints = oem_hint_from_payload(s.last_payload)
            make = make or hints.get("suggested_make")
            model_hint = model_hint or hints.get("suggested_model")
            if not cluster.oem_family and s.oem_family:
                cluster.oem_family = s.oem_family

        vehicle_id: int | None = None
        if self.auto_link_vehicles and cluster.sensor_ids:
            vehicle = self.fleet.ensure_vehicle_for_cluster(
                sensor_ids=set(cluster.sensor_ids),
                label=label,
                make=make,
                model=model_hint,
                oem_family=cluster.oem_family,
            )
            vehicle_id = vehicle.id
            label = vehicle.label
            make = vehicle.make or make
            model_hint = vehicle.model or model_hint

        passage = self.fleet.record_passage(
            cluster_key=cluster.cluster_key,
            vehicle_id=vehicle_id,
            label=label,
            make=make,
            model_hint=model_hint,
            oem_family=cluster.oem_family,
            sensor_ids=set(cluster.sensor_ids),
            hit_count=cluster.hit_count,
            lat=cluster.lat if cluster.lat is not None else self.receiver_lat,
            lon=cluster.lon if cluster.lon is not None else self.receiver_lon,
        )
        await self.arbiter._fanout_telemetry(  # noqa: SLF001
            TelemetryEvent(
                type="metric",
                state=self.arbiter.state,
                message="tpms_passage",
                metrics=passage,
            )
        )
        log.info(
            "TPMS passage %s vehicle=%s sensors=%s",
            passage["cluster_key"],
            vehicle_id,
            passage["sensor_ids"],
        )


async def get_tpms_engine() -> TpmsEngine:
    return await TpmsEngine.get_instance()
