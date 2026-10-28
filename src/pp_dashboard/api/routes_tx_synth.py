"""Active signal synthesis / lab spoofing TX API."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, status

from pp_dashboard.models.recon import SynthesizeRequest
from pp_dashboard.recon.engine import ReconEngine, get_recon_engine
from pp_dashboard.security.tx_gate import TxGateError, assert_tx_allowed

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tx", tags=["tx-synth"])
T = TypeVar("T")


async def engine_dep() -> ReconEngine:
    return await get_recon_engine()


@router.post("/synthesize")
async def synthesize(
    body: SynthesizeRequest, engine: ReconEngine = Depends(engine_dep)
) -> dict:
    try:
        assert_tx_allowed(require_loop_ok=True)
    except TxGateError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await _run(engine.synthesize(body))


async def _run(coro: Awaitable[T]) -> T:
    try:
        return await coro
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("synthesize failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="synthesize failed",
        ) from None
