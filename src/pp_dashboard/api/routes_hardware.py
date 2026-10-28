"""REST endpoints for hardware mode, status, and SDR jobs."""

from __future__ import annotations

import logging
from collections.abc import Awaitable

from fastapi import APIRouter, Depends, HTTPException, status

from pp_dashboard.api.deps import arbiter_dep
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.models.rf import (
    CaptureRequest,
    ContinuousTxRequest,
    HardwareModeRequest,
    HardwareStatusResponse,
    MultiplexRxRequest,
    ReplayRequest,
    SweepRequest,
    TargetMode,
)
from pp_dashboard.security.tx_gate import TxGateError, assert_tx_allowed

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hardware", tags=["hardware"])


@router.get("/status", response_model=HardwareStatusResponse)
async def hardware_status(arbiter: HardwareArbiter = Depends(arbiter_dep)) -> HardwareStatusResponse:
    await arbiter.refresh_detection()
    return arbiter.status()


@router.post("/detect", response_model=HardwareStatusResponse)
async def hardware_detect(arbiter: HardwareArbiter = Depends(arbiter_dep)) -> HardwareStatusResponse:
    await arbiter.refresh_detection()
    return arbiter.status()


@router.post("/mode", response_model=HardwareStatusResponse)
async def set_hardware_mode(
    body: HardwareModeRequest,
    arbiter: HardwareArbiter = Depends(arbiter_dep),
) -> HardwareStatusResponse:
    try:
        if body.mode is TargetMode.HACKRF_NATIVE:
            return await arbiter.to_native()
        return await arbiter.to_mayhem()
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("Mode switch failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Mode switch failed",
        ) from None


@router.post("/capture", response_model=HardwareStatusResponse)
async def start_capture(
    body: CaptureRequest,
    arbiter: HardwareArbiter = Depends(arbiter_dep),
) -> HardwareStatusResponse:
    return await _start(arbiter.start_capture(body))


@router.post("/replay", response_model=HardwareStatusResponse)
async def start_replay(
    body: ReplayRequest,
    arbiter: HardwareArbiter = Depends(arbiter_dep),
) -> HardwareStatusResponse:
    try:
        assert_tx_allowed(require_loop_ok=body.loop, amp=body.amp)
    except TxGateError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await _start(arbiter.start_replay(body))


@router.post("/tx/continuous", response_model=HardwareStatusResponse)
async def start_continuous_tx(
    body: ContinuousTxRequest,
    arbiter: HardwareArbiter = Depends(arbiter_dep),
) -> HardwareStatusResponse:
    try:
        assert_tx_allowed(amp=body.amp)
    except TxGateError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await _start(arbiter.start_continuous_tx(body))


@router.post("/sweep", response_model=HardwareStatusResponse)
async def start_sweep(
    body: SweepRequest,
    arbiter: HardwareArbiter = Depends(arbiter_dep),
) -> HardwareStatusResponse:
    return await _start(arbiter.start_sweep(body))


@router.post("/multiplex", response_model=HardwareStatusResponse)
async def start_multiplex(
    body: MultiplexRxRequest,
    arbiter: HardwareArbiter = Depends(arbiter_dep),
) -> HardwareStatusResponse:
    return await _start(arbiter.start_multiplex_rx(body))


@router.post("/multiplex/stop", response_model=HardwareStatusResponse)
async def stop_multiplex(arbiter: HardwareArbiter = Depends(arbiter_dep)) -> HardwareStatusResponse:
    try:
        return await arbiter.stop_multiplex_rx()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/stop", response_model=HardwareStatusResponse)
async def stop_job(arbiter: HardwareArbiter = Depends(arbiter_dep)) -> HardwareStatusResponse:
    return await arbiter.stop_subprocess()


async def _start(coro: Awaitable[HardwareStatusResponse]) -> HardwareStatusResponse:
    try:
        return await coro
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        log.exception("SDR start failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SDR start failed",
        ) from None
