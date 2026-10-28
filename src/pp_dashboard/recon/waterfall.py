"""Managed hackrf_sweep -B session with reactive RSSI triggers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from pp_dashboard.config import settings
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.models.rf import CaptureRequest
from pp_dashboard.recon.sweep_parser import (
    SweepBinaryParser,
    SweepRecord,
    assemble_row,
    powers_to_uint8,
)

log = logging.getLogger(__name__)

RowHandler = Callable[[dict[str, Any]], Awaitable[None] | None]
TriggerAction = Literal["log", "capture", "tx_tone"]


@dataclass(slots=True)
class TriggerConfig:
    enabled: bool = False
    f_lo_hz: int = 0
    f_hi_hz: int = 0
    rssi_db: float = -40.0
    action: TriggerAction = "log"
    cooldown_s: float = 2.0
    capture_samples: int = 2_000_000
    lna: int = 16
    vga: int = 20
    tx_vga: int = 10
    tx_amplitude: int = 64


@dataclass
class WaterfallSession:
    arbiter: HardwareArbiter
    start_mhz: float
    stop_mhz: float
    bin_width_hz: int
    lna: int
    vga: int
    amp: int = 0
    max_duration_sec: float | None = 60.0
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    db_min: float = -110.0
    db_max: float = -20.0
    target_fps: float = 30.0

    def __post_init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr_drain: asyncio.Task[None] | None = None
        self._handlers: list[RowHandler] = []
        self._pending: list[SweepRecord] = []
        self._last_push = 0.0
        self._last_trigger = 0.0
        self._events: list[dict[str, Any]] = []
        self._parser = SweepBinaryParser()
        self.running = False

    def subscribe(self, handler: RowHandler) -> None:
        if handler not in self._handlers:
            self._handlers.append(handler)

    def unsubscribe(self, handler: RowHandler) -> None:
        self._handlers = [h for h in self._handlers if h is not handler]

    @property
    def start_hz(self) -> int:
        return int(self.start_mhz * 1e6)

    @property
    def stop_hz(self) -> int:
        return int(self.stop_mhz * 1e6)

    def build_argv(self) -> list[str]:
        # hackrf_sweep rejects "430.0:440.0" — :g drops trailing .0
        sweep = [
            "hackrf_sweep",
            "-f",
            f"{self.start_mhz:g}:{self.stop_mhz:g}",
            "-w",
            str(self.bin_width_hz),
            "-l",
            str(self.lna),
            "-g",
            str(self.vga),
            "-a",
            str(self.amp),
            "-B",
        ]
        # Pipes are fully buffered for C stdio — without stdbuf, WF32 rows stall
        # in the stdout buffer until the process exits.
        if Path("/usr/bin/stdbuf").is_file():
            return ["stdbuf", "-o0", "-e0", *sweep]
        return sweep

    async def start(self) -> list[str]:
        if self.running:
            raise RuntimeError("waterfall already running")
        await self.arbiter.cancel_watchdog()
        async with self.arbiter.lock:
            await self.arbiter.refresh_detection()
            # Orphan BUSY_RX from a prior sweep that died without stop().
            if (
                self.arbiter.state in {HardwareState.BUSY_RX, HardwareState.BUSY_TX}
                and not self.arbiter.runner.running
            ):
                self.arbiter.state = HardwareState.HACKRF_NATIVE
            if self.arbiter.state is not HardwareState.HACKRF_NATIVE:
                await self.arbiter._to_native_locked()  # noqa: SLF001
            if self.arbiter.runner.running:
                raise RuntimeError("SDR subprocess already running")
            argv = self.build_argv()
            log.info("Starting waterfall: %s", " ".join(argv))
            # stderr MUST be drained — hackrf_sweep floods progress lines and will
            # block the process if the PIPE buffer fills (~64 KiB).
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            self.arbiter.state = HardwareState.BUSY_RX
            self.arbiter._idle_after_busy = HardwareState.HACKRF_NATIVE  # noqa: SLF001
        self.running = True
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_drain = asyncio.create_task(self._drain_stderr())
        if self.max_duration_sec:
            self.arbiter.arm_watchdog(self.max_duration_sec)
            # Watchdog stops arbiter.runner — also stop our proc via monitor
            asyncio.create_task(self._watch_arbiter_stop())
        return self.build_argv()

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if not proc or not proc.stderr:
            return
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace").strip()
                if text:
                    for line in text.splitlines()[-3:]:
                        log.debug("hackrf_sweep: %s", line[:200])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("waterfall stderr drain failed")

    async def _watch_arbiter_stop(self) -> None:
        while self.running:
            await asyncio.sleep(0.2)
            if self.arbiter.state not in {HardwareState.BUSY_RX, HardwareState.BUSY_TX}:
                # External stop/watchdog may have cleared state without our stop()
                if self._proc and self._proc.returncode is None:
                    await self.stop(reason="arbiter_state_cleared")
                break

    async def stop(self, *, reason: str = "stop") -> None:
        self.running = False
        await self.arbiter.cancel_watchdog()
        proc = self._proc
        self._proc = None
        if proc and proc.returncode is None:
            try:
                proc.send_signal(__import__("signal").SIGINT)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        if self._reader:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        if self._stderr_drain:
            self._stderr_drain.cancel()
            await asyncio.gather(self._stderr_drain, return_exceptions=True)
            self._stderr_drain = None
        async with self.arbiter.lock:
            if self.arbiter.state is HardwareState.BUSY_RX:
                self.arbiter.state = HardwareState.HACKRF_NATIVE
        log.info("Waterfall stopped (%s)", reason)

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while self.running:
                chunk = await self._proc.stdout.read(65536)
                if not chunk:
                    break
                records = self._parser.feed(chunk)
                if records:
                    self._pending.extend(records)
                    await self._maybe_emit_row()
                    await self._check_triggers(records)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("waterfall read loop failed")
        finally:
            self.running = False
            async with self.arbiter.lock:
                if self.arbiter.state is HardwareState.BUSY_RX and not self.arbiter.runner.running:
                    self.arbiter.state = HardwareState.HACKRF_NATIVE
            await self.arbiter._emit_status("waterfall_reader_exit")  # noqa: SLF001

    async def _maybe_emit_row(self) -> None:
        now = time.monotonic()
        min_interval = 1.0 / max(self.target_fps, 1.0)
        if now - self._last_push < min_interval:
            return
        if not self._pending:
            return
        batch = self._pending
        self._pending = []
        row, n = assemble_row(
            batch,
            start_hz=self.start_hz,
            stop_hz=self.stop_hz,
            bin_hz=float(self.bin_width_hz),
        )
        payload = {
            "type": "row",
            "start_hz": self.start_hz,
            "stop_hz": self.stop_hz,
            "bin_hz": self.bin_width_hz,
            "n": n,
            "encoding": "db_u8",
            "db_min": self.db_min,
            "db_max": self.db_max,
            "data_b64": None,
            "data_u8": powers_to_uint8(row, self.db_min, self.db_max),
            "peak_db": float(row.max()) if row.size else -120.0,
            "ts": time.time(),
        }
        self._last_push = now
        for handler in list(self._handlers):
            try:
                result = handler(payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("waterfall handler failed")

    async def _check_triggers(self, records: list[SweepRecord]) -> None:
        cfg = self.trigger
        if not cfg.enabled or cfg.f_hi_hz <= cfg.f_lo_hz:
            return
        now = time.monotonic()
        if now - self._last_trigger < cfg.cooldown_s:
            return
        hit_f: int | None = None
        hit_db: float | None = None
        for rec in records:
            if rec.hi_hz < cfg.f_lo_hz or rec.lo_hz > cfg.f_hi_hz:
                continue
            step = (rec.hi_hz - rec.lo_hz) / max(rec.power_db.size, 1)
            for i, p in enumerate(rec.power_db):
                f = int(rec.lo_hz + i * step)
                if cfg.f_lo_hz <= f <= cfg.f_hi_hz and float(p) >= cfg.rssi_db:
                    hit_f, hit_db = f, float(p)
                    break
            if hit_f is not None:
                break
        if hit_f is None:
            return
        self._last_trigger = now
        event = {
            "type": "trigger",
            "frequency_hz": hit_f,
            "rssi_db": hit_db,
            "action": cfg.action,
            "ts": time.time(),
        }
        self._events.append(event)
        for handler in list(self._handlers):
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        await self._run_trigger_action(hit_f, cfg)

    async def _run_trigger_action(self, freq_hz: int, cfg: TriggerConfig) -> None:
        if cfg.action == "log":
            return
        # Stop sweep to free the radio for capture/TX response.
        await self.stop(reason="reactive_trigger")
        if cfg.action == "capture":
            path = settings.captures_dir / f"trigger_{freq_hz}_{int(time.time())}.cs8"
            path.parent.mkdir(parents=True, exist_ok=True)
            await self.arbiter.start_capture(
                CaptureRequest(
                    filepath=path,
                    frequency_hz=freq_hz,
                    sample_rate=10_000_000,
                    lna=cfg.lna,
                    vga=cfg.vga,
                    amp=0,
                    num_samples=cfg.capture_samples,
                    max_duration_sec=5.0,
                )
            )
        elif cfg.action == "tx_tone":
            from pp_dashboard.models.rf import ContinuousTxRequest
            from pp_dashboard.security.tx_gate import TxGateError, assert_tx_allowed

            # Re-evaluate SCOPE/CONFIRM at fire time (not only at sweep start).
            try:
                assert_tx_allowed(amp=0)
            except TxGateError:
                log.warning("reactive tx_tone blocked by TX gate at fire time")
                return
            await self.arbiter.start_continuous_tx(
                ContinuousTxRequest(
                    frequency_hz=freq_hz,
                    sample_rate=10_000_000,
                    amplitude=cfg.tx_amplitude,
                    tx_vga=cfg.tx_vga,
                    amp=0,
                    max_duration_sec=2.0,
                )
            )

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._events[-limit:]
