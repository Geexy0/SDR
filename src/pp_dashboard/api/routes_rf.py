"""IQ capture / replay / rolljam endpoints (Phase 2)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, status

from pp_dashboard.models.phase2 import (
    CaptureJobRequest,
    CaptureJobResponse,
    ReplayJobRequest,
    RolljamRequest,
    RolljamResponse,
)
from pp_dashboard.models.rf import HardwareStatusResponse
from pp_dashboard.rf.engine import RfEngine, get_rf_engine
from pp_dashboard.security.tx_gate import TxGateError, assert_tx_allowed

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rf", tags=["rf"])

T = TypeVar("T")


async def engine_dep() -> RfEngine:
    return await get_rf_engine()


@router.post("/capture", response_model=CaptureJobResponse)
async def capture_iq(
    body: CaptureJobRequest,
    engine: RfEngine = Depends(engine_dep),
) -> CaptureJobResponse:
    return await _run(engine.capture(body))


@router.post("/replay", response_model=HardwareStatusResponse)
async def replay_iq(
    body: ReplayJobRequest,
    engine: RfEngine = Depends(engine_dep),
) -> HardwareStatusResponse:
    try:
        assert_tx_allowed(
            require_loop_ok=body.loop,
            amp=body.amp,
            max_duration_sec=body.max_duration_sec if body.loop else None,
        )
    except TxGateError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await _run(engine.replay(body))


@router.post("/rolljam", response_model=RolljamResponse)
async def rolljam(
    body: RolljamRequest,
    engine: RfEngine = Depends(engine_dep),
) -> RolljamResponse:
    try:
        assert_tx_allowed(
            require_loop_ok=body.replay_loop,
            amp=body.amp,
            max_duration_sec=body.replay_max_duration_sec if body.replay_loop else None,
        )
    except TxGateError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await _run(engine.rolljam(body))


async def _run(coro: Awaitable[T]) -> T:
    try:
        return await coro
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("RF job failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RF job failed",
        ) from None
