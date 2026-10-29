"""Operator GPS, breadcrumbs, LOB DF, CPA, GNSS L1 RX monitor."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from pp_dashboard.api.deps import arbiter_dep
from pp_dashboard.geoloc import geo
from pp_dashboard.geoloc.gnss_monitor import sample_l1_interference
from pp_dashboard.geoloc.service import GeolocService
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.models.geoloc import (
    BreadcrumbRequest,
    CpaRequest,
    GnssSampleRequest,
    LobIntersectRequest,
    LobRequest,
    OperatorFixRequest,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/geoloc", tags=["geoloc"])


async def geoloc_dep() -> GeolocService:
    return await GeolocService.get_instance()


@router.get("/status")
async def geoloc_status(svc: GeolocService = Depends(geoloc_dep)) -> dict[str, Any]:
    return svc.status()


@router.post("/operator")
async def set_operator(
    body: OperatorFixRequest, svc: GeolocService = Depends(geoloc_dep)
) -> dict[str, Any]:
    try:
        return svc.set_operator(
            body.lat,
            body.lon,
            accuracy_m=body.accuracy_m,
            alt_m=body.alt_m,
            source=body.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/breadcrumb")
async def add_breadcrumb(
    body: BreadcrumbRequest, svc: GeolocService = Depends(geoloc_dep)
) -> dict[str, Any]:
    bid = svc.store.add_breadcrumb(
        kind=body.kind,
        lat=body.lat,
        lon=body.lon,
        frequency_hz=body.frequency_hz,
        rssi_db=body.rssi_db,
        label=body.label,
        ref_id=body.ref_id,
        notes=body.notes,
    )
    return {"ok": True, "id": bid}


@router.get("/breadcrumbs")
async def list_breadcrumbs(
    kind: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    svc: GeolocService = Depends(geoloc_dep),
) -> dict[str, Any]:
    return {"breadcrumbs": svc.store.list_breadcrumbs(kind=kind, limit=limit)}


@router.get("/heatmap")
async def heatmap(
    cell_deg: float = Query(default=0.002, gt=0.0001, le=0.1),
    svc: GeolocService = Depends(geoloc_dep),
) -> dict[str, Any]:
    return {"cells": svc.store.heatmap_cells(cell_deg=cell_deg)}


@router.post("/lob")
async def add_lob(body: LobRequest, svc: GeolocService = Depends(geoloc_dep)) -> dict[str, Any]:
    lob_id = svc.store.add_lob(
        body.lat,
        body.lon,
        bearing_deg=body.bearing_deg,
        label=body.label,
        frequency_hz=body.frequency_hz,
    )
    ray = geo.lob_ray_points(
        body.lat, body.lon, body.bearing_deg, length_m=body.length_m
    )
    return {"ok": True, "id": lob_id, "ray": ray}


@router.get("/lobs")
async def list_lobs(svc: GeolocService = Depends(geoloc_dep)) -> dict[str, Any]:
    rows = svc.store.list_lobs()
    out = []
    for r in rows:
        ray = geo.lob_ray_points(
            float(r["lat"]), float(r["lon"]), float(r["bearing_deg"])
        )
        out.append({**r, "ray": ray})
    return {"lobs": out}


@router.delete("/lobs")
async def clear_lobs(svc: GeolocService = Depends(geoloc_dep)) -> dict[str, Any]:
    svc.store.clear_lobs()
    return {"ok": True}


@router.post("/lob/intersect")
async def lob_intersect(body: LobIntersectRequest) -> dict[str, Any]:
    hit = geo.intersect_lobs(
        geo.LineOfBearing(body.a.lat, body.a.lon, body.a.bearing_deg),
        geo.LineOfBearing(body.b.lat, body.b.lon, body.b.bearing_deg),
    )
    if hit is None:
        return {"ok": True, "intersection": None}
    return {"ok": True, "intersection": {"lat": hit.lat, "lon": hit.lon}}


@router.post("/cpa")
async def cpa(body: CpaRequest) -> dict[str, Any]:
    result = geo.closest_point_of_approach(
        geo.TrackState(
            body.own.lat, body.own.lon, body.own.speed_mps, body.own.course_deg
        ),
        geo.TrackState(
            body.target.lat,
            body.target.lon,
            body.target.speed_mps,
            body.target.course_deg,
        ),
        horizon_s=body.horizon_s,
    )
    if result is None:
        return {"ok": True, "cpa": None}
    return {
        "ok": True,
        "cpa": {
            "time_s": result.time_s,
            "range_m": result.range_m,
            "own": {"lat": result.own_lat, "lon": result.own_lon},
            "target": {"lat": result.tgt_lat, "lon": result.tgt_lon},
        },
    }


@router.get("/range-rings")
async def range_rings(
    radii_m: str = Query(default="1000,5000,15000"),
    n: int = Query(default=64, ge=8, le=256),
    svc: GeolocService = Depends(geoloc_dep),
) -> dict[str, Any]:
    op = svc.store.get_operator()
    if op is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="set operator GPS first (POST /api/geoloc/operator)",
        )
    radii: list[float] = []
    for part in radii_m.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            radii.append(float(part))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"bad radius: {part}"
            ) from exc
    if not radii:
        raise HTTPException(status_code=400, detail="no radii")
    rings = [
        {
            "radius_m": r,
            "polygon": geo.range_ring(float(op["lat"]), float(op["lon"]), radius_m=r, n=n),
        }
        for r in radii
        if r > 0
    ]
    return {"ok": True, "operator": op, "rings": rings}


@router.post("/gnss/l1-sample")
async def gnss_l1_sample(
    body: GnssSampleRequest | None = None,
    arbiter: HardwareArbiter = Depends(arbiter_dep),
) -> dict[str, Any]:
    req = body or GnssSampleRequest()
    # Snap VGA to even steps
    vga = int(req.vga) - (int(req.vga) % 2)
    try:
        return await sample_l1_interference(
            arbiter, duration_sec=req.duration_sec, lna=req.lna, vga=vga
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("gnss l1 sample failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="gnss l1 sample failed",
        ) from None


@router.get("/gnss/samples")
async def gnss_samples(
    limit: int = Query(default=50, ge=1, le=500),
    svc: GeolocService = Depends(geoloc_dep),
) -> dict[str, Any]:
    return {"samples": svc.store.list_gnss_samples(limit=limit)}
