"""Operator GPS, breadcrumbs, LOB DF, CPA, GNSS L1 RX monitor."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from pathlib import Path

from pp_dashboard.api.deps import arbiter_dep
from pp_dashboard.config import settings
from pp_dashboard.geoloc import foxhunt, geo, horizon, polar
from pp_dashboard.geoloc.gnss_monitor import analyze_l1_interference, sample_l1_interference
from pp_dashboard.geoloc.service import GeolocService
from pp_dashboard.geoloc.solver import (
    BearingObservation,
    DegenerateGeometryError,
    InsufficientBreadcrumbsError,
    RssiObservation,
    solve_rssi_grid,
    solve_stansfield_wls,
)
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.geoloc.live_fusion import LiveGeoHuntFusion, fit_doppler_pass
from pp_dashboard.models.geoloc import (
    BreadcrumbRequest,
    CpaRequest,
    DopplerFitRequest,
    FoxhuntRequest,
    GeoHuntBearingRequest,
    GeoHuntIngestRequest,
    GnssL1AnalyzeRequest,
    GnssSampleRequest,
    LobIntersectRequest,
    LobRequest,
    LobSolveRequest,
    LookAngleRequest,
    OperatorFixRequest,
    PolarPeakRequest,
    RssiGridSolveRequest,
)
from pp_dashboard.models.rf import TelemetryEvent
from pp_dashboard.security.paths import resolve_safe_path

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


@router.post("/lob/solve")
async def lob_solve(
    body: LobSolveRequest, svc: GeolocService = Depends(geoloc_dep)
) -> dict[str, Any]:
    op = svc.store.get_operator()
    origin_lat = body.origin_lat
    origin_lon = body.origin_lon
    if origin_lat is None and op is not None:
        origin_lat = float(op["lat"])
    if origin_lon is None and op is not None:
        origin_lon = float(op["lon"])
    obs = [
        BearingObservation(
            lat=o.lat,
            lon=o.lon,
            bearing_deg=o.bearing_deg,
            angular_std_deg=o.angular_std_deg,
            rssi_db=o.rssi_db,
            timestamp=o.timestamp,
        )
        for o in body.observations
    ]
    try:
        result = solve_stansfield_wls(obs, origin_lat=origin_lat, origin_lon=origin_lon)
    except DegenerateGeometryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return result.to_dict()


@router.post("/rssi-grid/solve")
async def rssi_grid_solve(
    body: RssiGridSolveRequest, svc: GeolocService = Depends(geoloc_dep)
) -> dict[str, Any]:
    op = svc.store.get_operator()
    origin_lat = body.origin_lat
    origin_lon = body.origin_lon
    if origin_lat is None and op is not None:
        origin_lat = float(op["lat"])
    if origin_lon is None and op is not None:
        origin_lon = float(op["lon"])
    obs = [
        RssiObservation(
            lat=o.lat, lon=o.lon, rssi_db=o.rssi_db, timestamp=o.timestamp
        )
        for o in body.observations
    ]
    try:
        result = solve_rssi_grid(
            obs,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            gamma=body.gamma,
            shadow_std_db=body.shadow_std_db,
            grid_n=body.grid_n,
            pad_m=body.pad_m,
        )
    except (InsufficientBreadcrumbsError, ValueError) as exc:
        code = (
            status.HTTP_422_UNPROCESSABLE_ENTITY
            if isinstance(exc, InsufficientBreadcrumbsError)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return result.to_dict()


@router.post("/solve")
async def geoloc_solve(
    body: dict[str, Any], svc: GeolocService = Depends(geoloc_dep)
) -> dict[str, Any]:
    """Dispatcher: ``mode=lob|rssi`` → Stansfield or RSSI-grid solver."""
    mode = str(body.get("mode") or "lob").lower()
    if mode == "lob":
        return await lob_solve(LobSolveRequest.model_validate(body), svc)
    if mode in {"rssi", "rssi-grid", "rssi_grid"}:
        return await rssi_grid_solve(RssiGridSolveRequest.model_validate(body), svc)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="mode must be 'lob' or 'rssi'",
    )


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


@router.post("/foxhunt")
async def foxhunt_homing(body: FoxhuntRequest) -> dict[str, Any]:
    samples = [
        foxhunt.FoxSample(
            lat=s.lat, lon=s.lon, rssi_db=s.rssi_db, t_mono=s.t_mono
        )
        for s in body.samples
    ]
    return foxhunt.evaluate_homing(samples, window_sec=body.window_sec).to_dict()


@router.post("/polar/peak")
async def polar_peak(body: PolarPeakRequest) -> dict[str, Any]:
    try:
        peak = polar.circular_lobe_centroid(
            [
                polar.PolarSample(bearing_deg=s.bearing_deg, rssi_db=s.rssi_db)
                for s in body.samples
            ]
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return peak.to_dict()


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


@router.post("/gnss/l1-analyze")
async def gnss_l1_analyze(
    body: GnssL1AnalyzeRequest | None = None,
    arbiter: HardwareArbiter = Depends(arbiter_dep),
    svc: GeolocService = Depends(geoloc_dep),
) -> dict[str, Any]:
    req = body or GnssL1AnalyzeRequest()
    vga = int(req.vga) - (int(req.vga) % 2)
    op = svc.store.get_operator()
    lat = req.operator_lat if req.operator_lat is not None else (float(op["lat"]) if op else None)
    lon = req.operator_lon if req.operator_lon is not None else (float(op["lon"]) if op else None)
    filepath: Path | None = None
    if req.filepath:
        try:
            filepath = resolve_safe_path(settings.captures_dir, Path(req.filepath))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not filepath.is_file():
            raise HTTPException(status_code=404, detail="filepath not found under captures")
    try:
        return await analyze_l1_interference(
            arbiter,
            duration_sec=req.duration_sec,
            lna=req.lna,
            vga=vga,
            baseline_noise_db=req.baseline_noise_db,
            filepath=filepath,
            operator_lat=lat,
            operator_lon=lon,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("gnss l1 analyze failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="gnss l1 analyze failed",
        ) from None


@router.get("/gnss/samples")
async def gnss_samples(
    limit: int = Query(default=50, ge=1, le=500),
    svc: GeolocService = Depends(geoloc_dep),
) -> dict[str, Any]:
    return {"samples": svc.store.list_gnss_samples(limit=limit)}


@router.post("/look-angle")
async def look_angle(body: LookAngleRequest) -> dict[str, Any]:
    result = horizon.look_angle(
        rx_lat=body.rx_lat,
        rx_lon=body.rx_lon,
        rx_alt_m=body.rx_alt_m,
        tx_lat=body.tx_lat,
        tx_lon=body.tx_lon,
        tx_alt_m=body.tx_alt_m,
        frequency_hz=body.frequency_hz,
    )
    return {"ok": True, **result.to_dict()}


async def _hunt_dep() -> LiveGeoHuntFusion:
    return await LiveGeoHuntFusion.get_instance()


async def _wire_hunt_telemetry(fusion: LiveGeoHuntFusion, arbiter: HardwareArbiter) -> None:
    async def _sink(event: dict[str, Any]) -> None:
        ev_type = str(event.get("type") or "metric")
        if ev_type not in {
            "status",
            "stdout",
            "stderr",
            "metric",
            "process_exit",
            "error",
            "heartbeat",
            "mission",
            "geohunt_update",
        }:
            ev_type = "metric"
        await arbiter._fanout_telemetry(  # noqa: SLF001
            TelemetryEvent(
                type=ev_type,  # type: ignore[arg-type]
                message=event.get("message"),
                metrics=event.get("metrics") if isinstance(event.get("metrics"), dict) else event,
                state=arbiter.state,
            )
        )

    fusion.on_telemetry = _sink


@router.post("/hunt/start")
async def hunt_start(
    arbiter: HardwareArbiter = Depends(arbiter_dep),
    fusion: LiveGeoHuntFusion = Depends(_hunt_dep),
) -> dict[str, Any]:
    await _wire_hunt_telemetry(fusion, arbiter)
    return await fusion.start()


@router.post("/hunt/stop")
async def hunt_stop(fusion: LiveGeoHuntFusion = Depends(_hunt_dep)) -> dict[str, Any]:
    return await fusion.stop()


@router.get("/hunt/status")
async def hunt_status(fusion: LiveGeoHuntFusion = Depends(_hunt_dep)) -> dict[str, Any]:
    return fusion.status()


@router.post("/hunt/ingest")
async def hunt_ingest(
    body: GeoHuntIngestRequest,
    fusion: LiveGeoHuntFusion = Depends(_hunt_dep),
) -> dict[str, Any]:
    return await fusion.ingest_rx_hit(
        lat=body.lat,
        lon=body.lon,
        rssi_db=body.rssi_db,
        freq_hz=body.freq_hz,
        emitter_id=body.emitter_id,
        timestamp=body.timestamp,
    )


@router.post("/hunt/bearing")
async def hunt_bearing(
    body: GeoHuntBearingRequest,
    fusion: LiveGeoHuntFusion = Depends(_hunt_dep),
) -> dict[str, Any]:
    return await fusion.ingest_bearing(
        lat=body.lat,
        lon=body.lon,
        bearing_deg=body.bearing_deg,
        emitter_id=body.emitter_id,
        freq_hz=body.freq_hz,
        rssi_db=body.rssi_db,
        angular_std_deg=body.angular_std_deg,
    )


@router.post("/doppler/fit")
async def doppler_fit(body: DopplerFitRequest) -> dict[str, Any]:
    samples = [(s.timestamp, s.measured_cfo_hz) for s in body.samples]
    try:
        return fit_doppler_pass(samples, nominal_freq_hz=body.nominal_freq_hz)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
