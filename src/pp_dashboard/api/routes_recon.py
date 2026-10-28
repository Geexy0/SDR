"""Recon sweep / decoder HTTP API."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from pp_dashboard.models.recon import (
    DecoderStartRequest,
    SweepStartRequest,
    VoiceReconStartRequest,
)
from pp_dashboard.recon.engine import ReconEngine, get_recon_engine
from pp_dashboard.security.tx_gate import TxGateError, assert_tx_allowed

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/recon", tags=["recon"])
T = TypeVar("T")


async def engine_dep() -> ReconEngine:
    return await get_recon_engine()


@router.get("/status")
async def recon_status(engine: ReconEngine = Depends(engine_dep)) -> dict:
    return engine.status()


@router.get("/recordings")
async def list_voice_recordings(
    limit: int = 50, engine: ReconEngine = Depends(engine_dep)
) -> dict:
    return {"recordings": engine.list_voice_recordings(limit=min(limit, 200))}


@router.get("/recordings/{recording_id}")
async def get_voice_recording(
    recording_id: int, engine: ReconEngine = Depends(engine_dep)
) -> dict:
    row = engine.get_voice_recording(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="recording not found")
    return {"recording": row}


@router.get("/recordings/{recording_id}/audio")
async def get_voice_recording_audio(
    recording_id: int, engine: ReconEngine = Depends(engine_dep)
) -> FileResponse:
    row = engine.get_voice_recording(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="recording not found")
    path = Path(row["filepath"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="wav missing on disk")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@router.post("/sweep")
async def start_sweep(
    body: SweepStartRequest, engine: ReconEngine = Depends(engine_dep)
) -> dict:
    if body.trigger_action == "tx_tone":
        try:
            assert_tx_allowed()
        except TxGateError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await _run(engine.start_sweep(body))


@router.post("/sweep/stop")
async def stop_sweep(engine: ReconEngine = Depends(engine_dep)) -> dict:
    return await engine.stop_sweep()


@router.post("/decoder")
async def start_decoder(
    body: DecoderStartRequest, engine: ReconEngine = Depends(engine_dep)
) -> dict:
    return await _run(engine.start_decoder(body))


@router.post("/decoder/stop")
async def stop_decoder(engine: ReconEngine = Depends(engine_dep)) -> dict:
    return await engine.stop_decoder()


@router.post("/voice")
async def start_voice_recon(
    body: VoiceReconStartRequest, engine: ReconEngine = Depends(engine_dep)
) -> dict:
    return await _run(engine.start_voice_recon(body))


@router.post("/voice/stop")
async def stop_voice_recon(engine: ReconEngine = Depends(engine_dep)) -> dict:
    return await engine.stop_voice_recon()


async def _run(coro: Awaitable[T]) -> T:
    try:
        return await coro
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("recon failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="recon failed"
        ) from None
