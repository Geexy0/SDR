"""Managed async SDR subprocess with SIGINT → SIGKILL lifecycle and telemetry fan-out."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pp_dashboard.config import Settings, settings
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.hardware.telemetry_parse import parse_hackrf_line
from pp_dashboard.models.rf import TelemetryEvent

log = logging.getLogger(__name__)

TelemetrySink = Callable[[TelemetryEvent], Awaitable[None] | None]


class SubprocessRunner:
    """Launch hackrf_transfer / hackrf_sweep with guaranteed cleanup."""

    def __init__(
        self,
        *,
        cfg: Settings = settings,
        on_event: TelemetrySink | None = None,
    ) -> None:
        self._cfg = cfg
        self._on_event = on_event
        self._proc: asyncio.subprocess.Process | None = None
        self._argv: list[str] | None = None
        self._reader_tasks: list[asyncio.Task[None]] = []
        self._wait_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def argv(self) -> list[str] | None:
        return list(self._argv) if self._argv else None

    async def _emit(self, event: TelemetryEvent) -> None:
        if self._on_event is None:
            return
        result = self._on_event(event)
        if asyncio.iscoroutine(result):
            await result

    async def start(
        self,
        argv: list[str],
        *,
        busy_state: HardwareState,
    ) -> None:
        if not argv:
            raise ValueError("argv must not be empty")
        binary = Path(argv[0]).name
        if binary not in {"hackrf_transfer", "hackrf_sweep", "rtl_433"}:
            raise ValueError(f"Unsupported SDR binary: {binary}")

        async with self._lock:
            if self.running:
                raise RuntimeError("SDR subprocess already running")

            log.info("Starting SDR subprocess: %s", " ".join(argv))
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            self._argv = list(argv)
            await self._emit(
                TelemetryEvent(
                    type="status",
                    state=busy_state,
                    message="subprocess_started",
                    argv=list(argv),
                )
            )
            assert self._proc.stdout is not None
            assert self._proc.stderr is not None
            self._reader_tasks = [
                asyncio.create_task(self._pump_stream(self._proc.stdout, "stdout", busy_state)),
                asyncio.create_task(self._pump_stream(self._proc.stderr, "stderr", busy_state)),
            ]
            self._wait_task = asyncio.create_task(self._wait_for_exit(busy_state))

    async def _pump_stream(
        self,
        stream: asyncio.StreamReader,
        stream_name: str,
        busy_state: HardwareState,
    ) -> None:
        while True:
            line_b = await stream.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").rstrip("\n")
            await self._emit(
                TelemetryEvent(
                    type="stdout" if stream_name == "stdout" else "stderr",
                    state=busy_state,
                    message=line,
                )
            )
            metrics = parse_hackrf_line(line)
            if metrics:
                await self._emit(
                    TelemetryEvent(
                        type="metric",
                        state=busy_state,
                        message=line,
                        metrics=metrics,
                    )
                )

    async def _wait_for_exit(self, busy_state: HardwareState) -> None:
        assert self._proc is not None
        code = await self._proc.wait()
        for task in self._reader_tasks:
            if not task.done():
                task.cancel()
        if self._reader_tasks:
            await asyncio.gather(*self._reader_tasks, return_exceptions=True)
        self._reader_tasks.clear()
        argv = list(self._argv) if self._argv else None
        self._proc = None
        self._argv = None
        await self._emit(
            TelemetryEvent(
                type="process_exit",
                state=busy_state,
                message="subprocess_exited",
                exit_code=code,
                argv=argv,
            )
        )

    async def stop(self, *, reason: str = "stop") -> int | None:
        """SIGINT, wait grace, then SIGKILL. Returns exit code if known."""
        async with self._lock:
            proc = self._proc
            if proc is None:
                return None

            if proc.returncode is None:
                log.info("Stopping SDR subprocess (%s) pid=%s", reason, proc.pid)
                await self._emit(
                    TelemetryEvent(
                        type="status",
                        message=f"subprocess_stopping:{reason}",
                        argv=list(self._argv) if self._argv else None,
                    )
                )
                try:
                    proc.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
                else:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=self._cfg.sigkill_grace_s)
                    except TimeoutError:
                        log.warning(
                            "SIGINT grace %.1fs elapsed; sending SIGKILL to pid=%s",
                            self._cfg.sigkill_grace_s,
                            proc.pid,
                        )
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                        await proc.wait()

            if self._wait_task is not None:
                await asyncio.gather(self._wait_task, return_exceptions=True)
            self._wait_task = None
            code = proc.returncode
            self._proc = None
            self._argv = None
            return code

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "argv": self.argv,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
