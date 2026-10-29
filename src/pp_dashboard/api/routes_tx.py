"""Active TX / jammer endpoints (Phase 2)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable

from fastapi import APIRouter, Depends, HTTPException, status

from pp_dashboard.models.phase2 import JammerJobResponse, JammerRequest
from pp_dashboard.rf.engine import RfEngine, get_rf_engine
from pp_dashboard.security.tx_gate import TxGateError, assert_tx_allowed

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tx", tags=["tx"])


async def engine_dep() -> RfEngine:
    return await get_rf_engine()


@router.post("/jammer", response_model=JammerJobResponse)
async def start_jammer(
    body: JammerRequest,
    engine: RfEngine = Depends(engine_dep),
) -> JammerJobResponse:
    try:
        assert_tx_allowed(
            require_loop_ok=True,
            amp=body.amp,
            max_duration_sec=body.max_duration_sec,
        )
    except TxGateError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await _run(engine.start_jammer(body))


async def _run(coro: Awaitable[JammerJobResponse]) -> JammerJobResponse:
    try:
        return await coro
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("Jammer start failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Jammer start failed",
        ) from None
