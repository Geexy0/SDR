"""Phase 2 RF engine — jammer, capture+sidecar, replay, rolljam."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from pp_dashboard.config import settings
from pp_dashboard.hardware.arbiter import HardwareArbiter, get_arbiter
from pp_dashboard.models.phase2 import (
    SHM_CHIRP_PATH,
    CaptureJobRequest,
    CaptureJobResponse,
    JammerJobResponse,
    JammerMode,
    JammerRequest,
    ReplayJobRequest,
    RolljamPhase,
    RolljamRequest,
    RolljamResponse,
)
from pp_dashboard.models.rf import (
    CaptureRequest,
    ContinuousTxRequest,
    HardwareStatusResponse,
    ReplayRequest,
)
from pp_dashboard.rf.chirp import synthesize_chirp_cs8
from pp_dashboard.rf.metadata import read_sidecar, write_capture_sidecar

log = logging.getLogger(__name__)


class RfEngine:
    """Orchestrates TX/RX workflows on top of HardwareArbiter."""

    _instance: ClassVar[RfEngine | None] = None

    def __init__(self, arbiter: HardwareArbiter) -> None:
        self.arbiter = arbiter

    @classmethod
    async def get_instance(cls) -> RfEngine:
        if cls._instance is None:
            cls._instance = cls(await get_arbiter())
        return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        cls._instance = None

    async def start_jammer(self, req: JammerRequest) -> JammerJobResponse:
        chirp_path: Path | None = None
        if req.mode is JammerMode.CW_AWGN:
            status = await self.arbiter.start_continuous_tx(
                ContinuousTxRequest(
                    frequency_hz=req.frequency_hz,
                    sample_rate=req.sample_rate,
                    amplitude=req.amplitude,
                    tx_vga=req.tx_vga,
                    amp=req.amp,
                    max_duration_sec=req.max_duration_sec,
                )
            )
        else:
            chirp_path = await self._build_chirp(req)
            status = await self.arbiter.start_tx_file(
                filepath=chirp_path,
                frequency_hz=req.frequency_hz,
                sample_rate=req.sample_rate,
                tx_vga=req.tx_vga,
                amp=req.amp,
                loop=True,
                max_duration_sec=req.max_duration_sec,
            )

        return JammerJobResponse(
            status=status,
            mode=req.mode,
            chirp_path=chirp_path,
            max_duration_sec=req.max_duration_sec,
        )

    async def _build_chirp(self, req: JammerRequest) -> Path:
        path = req.chirp_path if req.chirp_path else SHM_CHIRP_PATH

        def _write() -> Path:
            return synthesize_chirp_cs8(
                sample_rate=req.sample_rate,
                bandwidth_hz=req.bandwidth_hz,
                sweep_period_s=req.sweep_period_s,
                amplitude=req.chirp_amplitude,
                path=path,
            )

        return await asyncio.to_thread(_write)

    async def capture(self, req: CaptureJobRequest) -> CaptureJobResponse:
        path = req.filepath or self._auto_capture_path()
        status = await self.arbiter.start_capture(
            CaptureRequest(
                filepath=path,
                frequency_hz=req.frequency_hz,
                sample_rate=req.sample_rate,
                lna=req.lna,
                vga=req.vga,
                amp=req.amp,
                num_samples=req.num_samples,
                max_duration_sec=req.max_duration_sec,
            )
        )

        sidecar = None
        metadata = None
        if req.wait:
            timeout = req.max_duration_sec
            if timeout is None:
                timeout = max(5.0, (req.num_samples / req.sample_rate) * 2.0 + 3.0)
            await self.arbiter.wait_until_idle(timeout_s=timeout)
            status = self.arbiter.status()
            if path.exists():
                sidecar = write_capture_sidecar(
                    path,
                    frequency_hz=req.frequency_hz,
                    sample_rate=req.sample_rate,
                    lna=req.lna,
                    vga=req.vga,
                    amp=req.amp,
                    num_samples=req.num_samples,
                )
                metadata = read_sidecar(path)
            else:
                log.warning("Capture finished but file missing: %s", path)

        return CaptureJobResponse(
            status=status, filepath=path, sidecar=sidecar, metadata=metadata
        )

    async def replay(self, req: ReplayJobRequest) -> HardwareStatusResponse:
        meta = read_sidecar(req.filepath) or {}
        freq = req.frequency_hz
        if freq is None:
            if "frequency_hz" not in meta:
                raise ValueError("frequency_hz required when sidecar metadata is missing")
            freq = int(meta["frequency_hz"])
        rate = req.sample_rate
        if rate is None:
            rate = int(meta.get("sample_rate", settings.default_sample_rate))

        duration = req.max_duration_sec
        if req.loop and duration is None:
            duration = 5.0

        return await self.arbiter.start_replay(
            ReplayRequest(
                filepath=req.filepath,
                frequency_hz=freq,
                sample_rate=rate,
                tx_vga=req.tx_vga,
                amp=req.amp,
                loop=req.loop,
                max_duration_sec=duration,
            )
        )

    async def rolljam(self, req: RolljamRequest) -> RolljamResponse:
        capture = await self.capture(
            CaptureJobRequest(
                filepath=req.capture_path,
                frequency_hz=req.frequency_hz,
                sample_rate=req.sample_rate,
                lna=req.lna,
                vga=req.vga,
                amp=req.amp,
                num_samples=req.num_samples,
                wait=True,
            )
        )
        jam_status = None
        if req.phase is RolljamPhase.CAPTURE_JAM_REPLAY:
            jam = await self.start_jammer(
                JammerRequest(
                    mode=JammerMode.CW_AWGN,
                    frequency_hz=req.jam_frequency_hz or req.frequency_hz,
                    sample_rate=req.sample_rate,
                    tx_vga=req.tx_vga,
                    amp=req.amp,
                    amplitude=req.jam_amplitude,
                    max_duration_sec=req.jam_duration_sec,
                )
            )
            jam_status = jam.status
            await self.arbiter.wait_until_idle(timeout_s=req.jam_duration_sec + 3.0)

        replay_status = await self.replay(
            ReplayJobRequest(
                filepath=capture.filepath,
                frequency_hz=req.frequency_hz,
                sample_rate=req.sample_rate,
                tx_vga=req.tx_vga,
                amp=req.amp,
                loop=req.replay_loop,
                max_duration_sec=req.replay_max_duration_sec,
            )
        )
        return RolljamResponse(
            capture=capture,
            jam_status=jam_status,
            replay_status=replay_status,
        )

    def _auto_capture_path(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        folder = settings.captures_dir
        folder.mkdir(parents=True, exist_ok=True)
        return (folder / f"cap_{stamp}.cs8").resolve()


async def get_rf_engine() -> RfEngine:
    return await RfEngine.get_instance()
