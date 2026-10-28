"""Sub-GHz sniffer / analyze / bruteforce / .sub HTTP API."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, status

from pp_dashboard.models.rf import HardwareStatusResponse
from pp_dashboard.models.subghz import (
    AnalyzeRequest,
    BruteforceRequest,
    BruteforceStopResponse,
    PacketListResponse,
    ReplayPacketRequest,
    SniffRequest,
    SubConvertRequest,
)
from pp_dashboard.security.tx_gate import TxGateError, assert_tx_allowed
from pp_dashboard.subghz.engine import SubGhzEngine, get_subghz_engine

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/subghz", tags=["subghz"])
T = TypeVar("T")


async def engine_dep() -> SubGhzEngine:
    return await get_subghz_engine()


@router.post("/sniff")
async def start_sniff(
    body: SniffRequest, engine: SubGhzEngine = Depends(engine_dep)
) -> dict:
    return await _run(engine.start_sniff(body))


@router.post("/sniff/stop", response_model=HardwareStatusResponse)
async def stop_sniff(engine: SubGhzEngine = Depends(engine_dep)) -> HardwareStatusResponse:
    return await engine.stop_sniff()


@router.get("/packets", response_model=PacketListResponse)
async def packets(
    limit: int = 100, engine: SubGhzEngine = Depends(engine_dep)
) -> PacketListResponse:
    return PacketListResponse(packets=engine.list_packets(limit=min(limit, 500)))


@router.post("/analyze")
async def analyze(
    body: AnalyzeRequest, engine: SubGhzEngine = Depends(engine_dep)
) -> dict:
    return await _run(engine.analyze(body))


@router.post("/bruteforce")
async def bruteforce(
    body: BruteforceRequest, engine: SubGhzEngine = Depends(engine_dep)
) -> dict:
    try:
        assert_tx_allowed(require_loop_ok=body.loop_tx)
    except TxGateError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await _run(engine.bruteforce(body))


@router.post("/bruteforce/stop", response_model=BruteforceStopResponse)
async def bruteforce_stop(
    engine: SubGhzEngine = Depends(engine_dep),
) -> BruteforceStopResponse:
    data = await engine.stop_bruteforce()
    return BruteforceStopResponse(**data)


@router.post("/replay", response_model=HardwareStatusResponse)
async def replay_packet(
    body: ReplayPacketRequest, engine: SubGhzEngine = Depends(engine_dep)
) -> HardwareStatusResponse:
    try:
        assert_tx_allowed()
    except TxGateError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await _run(engine.replay_packet(body))


@router.post("/sub-file")
async def sub_file(
    body: SubConvertRequest, engine: SubGhzEngine = Depends(engine_dep)
) -> dict:
    return await _run(engine.convert_sub(body))


async def _run(coro: Awaitable[T]) -> T:
    try:
        return await coro
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("subghz operation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="subghz operation failed",
        ) from None
