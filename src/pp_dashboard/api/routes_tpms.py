"""Lab TPMS fleet HTTP API."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, status

from pp_dashboard.models.tpms import FleetVehicleUpsert, SensorLinkRequest, TpmsListenRequest
from pp_dashboard.tpms.engine import TpmsEngine, get_tpms_engine

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tpms", tags=["tpms"])
T = TypeVar("T")


async def engine_dep() -> TpmsEngine:
    return await get_tpms_engine()


@router.get("/status")
async def tpms_status(engine: TpmsEngine = Depends(engine_dep)) -> dict[str, Any]:
    return engine.status()


@router.post("/listen")
async def tpms_listen(
    body: TpmsListenRequest, engine: TpmsEngine = Depends(engine_dep)
) -> dict[str, Any]:
    return await _run(
        engine.start(
            frequency_hz=body.frequency_hz,
            sample_rate=body.sample_rate,
            max_duration_sec=body.max_duration_sec,
            receiver_lat=body.receiver_lat,
            receiver_lon=body.receiver_lon,
            auto_link_vehicles=body.auto_link_vehicles,
            tpms_only=body.tpms_only,
            hop_frequencies_hz=body.hop_frequencies_hz,
            hop_interval_sec=body.hop_interval_sec,
            gain=body.gain,
            band=body.band,
        )
    )


@router.post("/listen/stop")
async def tpms_listen_stop(engine: TpmsEngine = Depends(engine_dep)) -> dict[str, Any]:
    return await engine.stop()


@router.post("/clear")
async def tpms_clear(engine: TpmsEngine = Depends(engine_dep)) -> dict[str, Any]:
    async def _clear() -> dict[str, Any]:
        return engine.clear_fleet()

    return await _run(_clear())


@router.get("/sensors")
async def list_sensors(engine: TpmsEngine = Depends(engine_dep)) -> dict[str, Any]:
    sensors = engine.fleet.list_sensors()
    return {
        "sensors": [
            {
                "sensor_id": s.sensor_id,
                "last_ts": s.last_ts,
                "hit_count": s.hit_count,
                "last_model": s.last_model,
                "last_protocol": s.last_protocol,
                "oem_family": s.oem_family,
                "last_pressure_kpa": s.last_pressure_kpa,
                "last_temp_c": s.last_temp_c,
                "last_lat": s.last_lat,
                "last_lon": s.last_lon,
                "vehicle_id": s.vehicle_id,
            }
            for s in sensors
        ]
    }


@router.get("/passages")
async def list_passages(
    limit: int = 100, engine: TpmsEngine = Depends(engine_dep)
) -> dict[str, Any]:
    return {"passages": engine.fleet.list_passages(limit=min(limit, 500))}


@router.get("/vehicles")
async def list_vehicles(engine: TpmsEngine = Depends(engine_dep)) -> dict[str, Any]:
    vehicles = engine.fleet.list_vehicles()
    return {
        "vehicles": [
            {
                "id": v.id,
                "label": v.label,
                "make": v.make,
                "model": v.model,
                "year": v.year,
                "color": v.color,
                "notes": v.notes,
                "sensor_ids": v.sensor_ids,
                "created_ts": v.created_ts,
                "updated_ts": v.updated_ts,
            }
            for v in vehicles
        ]
    }


@router.post("/vehicles")
async def upsert_vehicle(
    body: FleetVehicleUpsert, engine: TpmsEngine = Depends(engine_dep)
) -> dict[str, Any]:
    v = engine.fleet.upsert_vehicle(
        label=body.label,
        make=body.make,
        model=body.model,
        year=body.year,
        color=body.color,
        notes=body.notes,
        sensor_ids=body.sensor_ids,
        vehicle_id=body.vehicle_id,
    )
    return {
        "vehicle": {
            "id": v.id,
            "label": v.label,
            "make": v.make,
            "model": v.model,
            "year": v.year,
            "color": v.color,
            "notes": v.notes,
            "sensor_ids": v.sensor_ids,
        }
    }


@router.post("/link")
async def link_sensor(
    body: SensorLinkRequest, engine: TpmsEngine = Depends(engine_dep)
) -> dict[str, Any]:
    s = engine.fleet.link_sensor(body.sensor_id, body.vehicle_id)
    if s is None and body.vehicle_id is not None:
        raise HTTPException(status_code=404, detail="sensor not seen yet — listen first")
    return {
        "sensor_id": body.sensor_id.upper(),
        "vehicle_id": body.vehicle_id,
        "sensor": None
        if s is None
        else {
            "sensor_id": s.sensor_id,
            "vehicle_id": s.vehicle_id,
            "hit_count": s.hit_count,
            "oem_family": s.oem_family,
        },
    }


async def _run(coro: Awaitable[T]) -> T:
    try:
        return await coro
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("tpms operation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="tpms failed"
        ) from None
