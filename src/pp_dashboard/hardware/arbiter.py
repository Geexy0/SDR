"""Singleton HardwareArbiter — dual-mode USB mutex + SDR process manager."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, ClassVar

from pp_dashboard.config import Settings, settings
from pp_dashboard.hardware.detect import DetectionResult, detect_hardware, resolve_mayhem_serial_port
from pp_dashboard.hardware.serial_ops import poll_until, probe_mayhem_prompt, send_hackrf_enter_native
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.hardware.subprocess_runner import SubprocessRunner
from pp_dashboard.models.rf import (
    CaptureRequest,
    ContinuousTxRequest,
    HardwareStatusResponse,
    MultiplexRxRequest,
    ReplayRequest,
    SweepRequest,
    TelemetryEvent,
)

log = logging.getLogger(__name__)

TelemetryHandler = Callable[[TelemetryEvent], Awaitable[None] | None]


class HardwareArbiter:
    """Owns the HackRF/Mayhem mode mutex and long-running SDR subprocesses."""

    _instance: ClassVar[HardwareArbiter | None] = None
    _instance_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or settings
        self.lock = asyncio.Lock()
        self.state = HardwareState.DISCONNECTED
        self.serial_port: str | None = None
        self.last_error: str | None = None
        self._detection: DetectionResult | None = None
        self._telemetry_handlers: list[TelemetryHandler] = []
        self.runner = SubprocessRunner(cfg=self.cfg, on_event=self._fanout_telemetry)
        self._idle_after_busy: HardwareState = HardwareState.HACKRF_NATIVE
        self._watchdog_task: asyncio.Task[None] | None = None
        self.multiplexer: Any | None = None

    @classmethod
    async def get_instance(cls, cfg: Settings | None = None) -> HardwareArbiter:
        async with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(cfg=cfg)
            return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        cls._instance = None

    def subscribe_telemetry(self, handler: TelemetryHandler) -> None:
        if handler not in self._telemetry_handlers:
            self._telemetry_handlers.append(handler)

    def unsubscribe_telemetry(self, handler: TelemetryHandler) -> None:
        self._telemetry_handlers = [h for h in self._telemetry_handlers if h is not handler]

    async def _fanout_telemetry(self, event: TelemetryEvent) -> None:
        if event.type == "process_exit":
            # Return to native idle after TX/RX unless we disconnected.
            if self.state in {
                HardwareState.BUSY_TX,
                HardwareState.BUSY_RX,
                HardwareState.MULTIPLEX_RX,
            }:
                self.state = self._idle_after_busy
                event = event.model_copy(update={"state": self.state})

        for handler in list(self._telemetry_handlers):
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("Telemetry handler failed")

    async def _emit_status(self, message: str) -> None:
        await self._fanout_telemetry(
            TelemetryEvent(type="status", state=self.state, message=message)
        )

    async def refresh_detection(self) -> DetectionResult:
        detection = await detect_hardware(self.cfg)
        self._detection = detection
        self.serial_port = detection.serial_port
        # Do NOT auto-clear BUSY here — waterfall / IQ mux own RX via their own
        # subprocesses (not runner). Clearing would race _watch_arbiter_stop.
        if self.state in {
            HardwareState.BUSY_TX,
            HardwareState.BUSY_RX,
            HardwareState.MULTIPLEX_RX,
            HardwareState.SWITCHING,
        }:
            return detection
        if detection.hackrf_native:
            self.state = HardwareState.HACKRF_NATIVE
        elif detection.mayhem_serial:
            self.state = HardwareState.MAYHEM_SERIAL
        else:
            self.state = HardwareState.DISCONNECTED
        return detection

    def status(self) -> HardwareStatusResponse:
        det = self._detection
        return HardwareStatusResponse(
            state=self.state,
            serial_port=self.serial_port,
            hackrf_native=bool(det.hackrf_native) if det else False,
            mayhem_serial=bool(det.mayhem_serial) if det else False,
            subprocess_running=self.runner.running,
            subprocess_argv=self.runner.argv,
            last_error=self.last_error,
        )

    async def to_native(self) -> HardwareStatusResponse:
        async with self.lock:
            return await self._to_native_locked()

    async def _to_native_locked(self) -> HardwareStatusResponse:
        if self.runner.running:
            raise RuntimeError("Cannot switch mode while an SDR subprocess is running")
        if self.multiplexer is not None and getattr(self.multiplexer, "running", False):
            raise RuntimeError("Cannot switch mode while IQ multiplexer is running")

        await self.refresh_detection()
        if self.state == HardwareState.HACKRF_NATIVE:
            return self.status()

        # Recover orphaned BUSY (waterfall / mux use their own proc, not runner).
        if (
            self.state
            in {HardwareState.BUSY_TX, HardwareState.BUSY_RX, HardwareState.MULTIPLEX_RX}
            and not self.runner.running
            and self.multiplexer is None
        ):
            self.state = (
                HardwareState.HACKRF_NATIVE
                if (self._detection and self._detection.hackrf_native)
                else HardwareState.DISCONNECTED
            )
        if self.state == HardwareState.HACKRF_NATIVE:
            return self.status()
        if self.state not in {HardwareState.MAYHEM_SERIAL, HardwareState.DISCONNECTED}:
            raise RuntimeError(f"Cannot switch to native from state {self.state.value}")

        port = self.serial_port or resolve_mayhem_serial_port(self.cfg)
        if not port:
            # Already native or no serial — probe hackrf_info directly.
            ok = await self._poll_hackrf_info()
            if ok:
                self.state = HardwareState.HACKRF_NATIVE
                self.last_error = None
                await self.refresh_detection()
                return self.status()
            self.last_error = "No Mayhem serial port found and hackrf_info not ready"
            raise RuntimeError(self.last_error)

        previous = self.state
        self.state = HardwareState.SWITCHING
        await self._emit_status("switching_to_native")
        try:
            # Release Mayhem CLI bridge so CDC can leave serial mode cleanly.
            from pp_dashboard.mayhem.bridge import close_mayhem_bridge

            await close_mayhem_bridge()
            await send_hackrf_enter_native(port, self.cfg)
            ok = await self._poll_hackrf_info()
            if not ok:
                self.last_error = (
                    f"Timed out after {self.cfg.mode_switch_timeout_s}s waiting for hackrf_info"
                )
                self.state = previous
                raise TimeoutError(self.last_error)
            self.serial_port = None
            self.state = HardwareState.HACKRF_NATIVE
            self.last_error = None
            await self.refresh_detection()
            await self._emit_status("native_ready")
            return self.status()
        except Exception as exc:
            self.last_error = str(exc)
            if self.state == HardwareState.SWITCHING:
                self.state = previous
            raise

    async def to_mayhem(self) -> HardwareStatusResponse:
        async with self.lock:
            return await self._to_mayhem_locked()

    async def _to_mayhem_locked(self) -> HardwareStatusResponse:
        if self.runner.running:
            await self.runner.stop(reason="mode_switch_to_mayhem")

        await self.refresh_detection()
        if self.state == HardwareState.MAYHEM_SERIAL and self.serial_port:
            if await probe_mayhem_prompt(self.serial_port, self.cfg):
                return self.status()

        previous = self.state
        self.state = HardwareState.SWITCHING
        await self._emit_status("switching_to_mayhem")
        try:
            await self._run_spiflash_reset()
            port = await self._poll_mayhem_serial()
            if not port:
                self.last_error = (
                    f"Timed out after {self.cfg.mode_switch_timeout_s}s waiting for Mayhem serial"
                )
                self.state = previous
                raise TimeoutError(self.last_error)
            self.serial_port = port
            self.state = HardwareState.MAYHEM_SERIAL
            self.last_error = None
            await self.refresh_detection()
            await self._emit_status("mayhem_ready")
            return self.status()
        except Exception as exc:
            self.last_error = str(exc)
            if self.state == HardwareState.SWITCHING:
                self.state = previous
            raise

    async def _poll_hackrf_info(self) -> bool:
        from pp_dashboard.hardware.detect import hackrf_info_ready

        async def _ready() -> bool:
            return await hackrf_info_ready(timeout=1.5)

        return await poll_until(
            _ready,
            timeout_s=self.cfg.mode_switch_timeout_s,
            initial_delay_s=0.1,
            max_delay_s=0.8,
        )

    async def _run_spiflash_reset(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "hackrf_spiflash",
            "-R",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError("hackrf_spiflash -R timed out") from None
        if proc.returncode not in (0, None):
            # Some firmware builds return non-zero after reset as the USB drops; treat
            # as soft failure and rely on serial re-enumeration polling.
            log.warning(
                "hackrf_spiflash -R exited %s: %s",
                proc.returncode,
                err.decode("utf-8", errors="replace").strip(),
            )

    async def _poll_mayhem_serial(self) -> str | None:
        async def _probe() -> bool:
            port = resolve_mayhem_serial_port(self.cfg)
            if not port:
                return False
            if await probe_mayhem_prompt(port, self.cfg):
                self.serial_port = port
                return True
            return False

        ok = await poll_until(
            _probe,
            timeout_s=self.cfg.mode_switch_timeout_s,
            initial_delay_s=0.15,
            max_delay_s=0.8,
        )
        return self.serial_port if ok else None

    async def ensure_native(self) -> None:
        async with self.lock:
            await self.refresh_detection()
            if self.state != HardwareState.HACKRF_NATIVE:
                await self._to_native_locked()
            if self.state != HardwareState.HACKRF_NATIVE:
                raise RuntimeError("Hardware is not in HACKRF_NATIVE mode")

    async def start_capture(self, req: CaptureRequest) -> HardwareStatusResponse:
        argv = [
            "hackrf_transfer",
            "-r",
            str(req.filepath),
            "-f",
            str(req.frequency_hz),
            "-s",
            str(req.sample_rate),
            "-l",
            str(req.lna),
            "-g",
            str(req.vga),
            "-a",
            str(req.amp),
            "-n",
            str(req.num_samples),
        ]
        return await self._start_sdr(
            argv,
            busy=HardwareState.BUSY_RX,
            max_duration_sec=req.max_duration_sec,
        )

    async def start_replay(self, req: ReplayRequest) -> HardwareStatusResponse:
        from pp_dashboard.security.tx_gate import tx_argv_amp

        amp_s = tx_argv_amp(req.amp)
        argv = [
            "hackrf_transfer",
            "-t",
            str(req.filepath),
            "-f",
            str(req.frequency_hz),
            "-s",
            str(req.sample_rate),
            "-x",
            str(req.tx_vga),
            "-a",
            amp_s,
        ]
        if req.loop:
            argv.append("-R")
        max_dur = req.max_duration_sec
        if req.loop and max_dur is None:
            max_dur = 60.0
        return await self._start_sdr(
            argv,
            busy=HardwareState.BUSY_TX,
            max_duration_sec=max_dur,
        )

    async def start_continuous_tx(self, req: ContinuousTxRequest) -> HardwareStatusResponse:
        from pp_dashboard.security.tx_gate import tx_argv_amp

        amp_s = tx_argv_amp(req.amp)
        argv = [
            "hackrf_transfer",
            "-c",
            str(req.amplitude),
            "-f",
            str(req.frequency_hz),
            "-s",
            str(req.sample_rate),
            "-x",
            str(req.tx_vga),
            "-a",
            amp_s,
        ]
        return await self._start_sdr(
            argv,
            busy=HardwareState.BUSY_TX,
            max_duration_sec=req.max_duration_sec,
        )

    async def start_tx_file(
        self,
        *,
        filepath: Path,
        frequency_hz: int,
        sample_rate: int,
        tx_vga: int,
        amp: int,
        loop: bool = False,
        max_duration_sec: float | None = None,
    ) -> HardwareStatusResponse:
        """Transmit a precomputed IQ file (used by chirp barrage)."""
        from pp_dashboard.security.tx_gate import tx_argv_amp

        amp_s = tx_argv_amp(amp)
        argv = [
            "hackrf_transfer",
            "-t",
            str(filepath),
            "-f",
            str(frequency_hz),
            "-s",
            str(sample_rate),
            "-x",
            str(tx_vga),
            "-a",
            amp_s,
        ]
        if loop:
            argv.append("-R")
        max_dur = max_duration_sec
        if loop and max_dur is None:
            max_dur = 60.0
        return await self._start_sdr(
            argv,
            busy=HardwareState.BUSY_TX,
            max_duration_sec=max_dur,
        )

    async def start_sweep(self, req: SweepRequest) -> HardwareStatusResponse:
        argv = [
            "hackrf_sweep",
            "-f",
            f"{req.min_mhz}:{req.max_mhz}",
            "-w",
            str(req.bin_width_hz),
            "-l",
            str(req.lna),
            "-g",
            str(req.vga),
        ]
        return await self._start_sdr(argv, busy=HardwareState.BUSY_RX)

    async def _start_sdr(
        self,
        argv: list[str],
        *,
        busy: HardwareState,
        max_duration_sec: float | None = None,
    ) -> HardwareStatusResponse:
        # Cancel outside the mutex — watchdog may be contending for the same lock.
        await self.cancel_watchdog()
        async with self.lock:
            await self.refresh_detection()
            if self.state != HardwareState.HACKRF_NATIVE:
                await self._to_native_locked()
            if self.runner.running:
                raise RuntimeError("SDR subprocess already running")
            self._idle_after_busy = HardwareState.HACKRF_NATIVE
            await self.runner.start(argv, busy_state=busy)
            self.state = busy
            await self._emit_status(f"entered_{busy.value}")
            if max_duration_sec is not None:
                self.arm_watchdog(max_duration_sec)
            return self.status()

    def arm_watchdog(self, max_duration_sec: float) -> None:
        """Schedule SIGINT stop after max_duration_sec (safety watchdog)."""
        if max_duration_sec <= 0:
            raise ValueError("max_duration_sec must be > 0")

        async def _fire() -> None:
            try:
                await asyncio.sleep(max_duration_sec)
                log.warning("Duration watchdog fired after %.3fs — stopping SDR", max_duration_sec)
                await self._fanout_telemetry(
                    TelemetryEvent(
                        type="status",
                        state=self.state,
                        message=f"watchdog_fired:{max_duration_sec}",
                    )
                )
                # Inline stop (do not call stop_subprocess — that cancels this task).
                mux = self.multiplexer
                if mux is not None and getattr(mux, "running", False):
                    await mux.stop(reason="watchdog")
                async with self.lock:
                    if self.runner.running:
                        await self.runner.stop(reason="watchdog")
                    if self.state in {
                        HardwareState.BUSY_TX,
                        HardwareState.BUSY_RX,
                        HardwareState.MULTIPLEX_RX,
                    }:
                        self.state = self._idle_after_busy
                    await self._emit_status("subprocess_stopped:watchdog")
            except asyncio.CancelledError:
                return
            finally:
                if self._watchdog_task is asyncio.current_task():
                    self._watchdog_task = None

        self._watchdog_task = asyncio.create_task(_fire(), name="sdr-duration-watchdog")

    async def cancel_watchdog(self) -> None:
        task = self._watchdog_task
        self._watchdog_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def wait_until_idle(self, timeout_s: float | None = None) -> None:
        """Wait until no SDR subprocess is running."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout_s is None else loop.time() + timeout_s
        while self.runner.running:
            if deadline is not None and loop.time() >= deadline:
                raise TimeoutError("Timed out waiting for SDR subprocess to finish")
            await asyncio.sleep(0.05)

    async def start_multiplex_rx(self, req: MultiplexRxRequest) -> HardwareStatusResponse:
        """Start wideband IQ multiplexer (10 MSPS → FFT + DDC FIFOs)."""
        from pp_dashboard.hardware.iq_multiplexer import IQMultiplexer

        if self.multiplexer is not None and getattr(self.multiplexer, "running", False):
            raise RuntimeError("IQ multiplexer already running")
        if self.runner.running:
            raise RuntimeError("stop other SDR job before MULTIPLEX_RX")
        mux = IQMultiplexer(
            arbiter=self,
            center_hz=req.center_hz,
            sample_rate=req.sample_rate,
            lna=req.lna,
            vga=req.vga,
            amp=req.amp,
            subghz_tune_hz=req.subghz_tune_hz,
            voice_tune_hz=req.voice_tune_hz,
        )
        argv = await mux.start()
        await self._fanout_telemetry(
            TelemetryEvent(
                type="status",
                state=self.state,
                message="multiplex_rx_started",
                metrics={
                    "argv": argv,
                    "center_hz": req.center_hz,
                    "sample_rate": req.sample_rate,
                    "subghz_fifo": "/tmp/pp_subghz.cs8",
                    "voice_fifo": "/tmp/pp_voice.cs8",
                },
            )
        )
        if req.max_duration_sec is not None:
            self.arm_watchdog(req.max_duration_sec)
        return self.status()

    async def stop_multiplex_rx(self, *, reason: str = "api_stop") -> HardwareStatusResponse:
        await self.cancel_watchdog()
        mux = self.multiplexer
        if mux is not None:
            await mux.stop(reason=reason)
        await self._emit_status(f"multiplex_rx_stopped:{reason}")
        return self.status()

    async def _stop_sidecar_sessions(self, *, reason: str) -> None:
        """Stop recon/TPMS owners outside the arbiter mutex (avoids lock deadlock)."""
        try:
            from pp_dashboard.recon.engine import ReconEngine

            eng = ReconEngine._instance  # noqa: SLF001
            if eng is not None:
                if eng.waterfall is not None and eng.waterfall.running:
                    await eng.waterfall.stop(reason=reason)
                if eng.decoder is not None and eng.decoder.running:
                    await eng.decoder.stop()
                if eng.voice_recon is not None and eng.voice_recon.running:
                    await eng.voice_recon.stop()
                hybrid = getattr(eng, "hybrid_adsb", None)
                if hybrid is not None and getattr(hybrid, "running", False):
                    await hybrid.stop()
        except Exception:
            log.exception("sidecar recon stop failed (%s)", reason)
        try:
            from pp_dashboard.tpms.engine import TpmsEngine

            tpms = TpmsEngine._instance  # noqa: SLF001
            if tpms is not None and getattr(tpms, "running", False):
                await tpms.stop()
        except Exception:
            log.exception("sidecar tpms stop failed (%s)", reason)

    async def stop_subprocess(self, *, reason: str = "api_stop") -> HardwareStatusResponse:
        # Sidecar teardown first, outside the mutex — sidecars may need the lock.
        await self.cancel_watchdog()
        await self._stop_sidecar_sessions(reason=reason)
        mux = self.multiplexer
        if mux is not None and getattr(mux, "running", False):
            await mux.stop(reason=reason)
        async with self.lock:
            if self.runner.running:
                await self.runner.stop(reason=reason)
            if self.state in {
                HardwareState.BUSY_TX,
                HardwareState.BUSY_RX,
                HardwareState.MULTIPLEX_RX,
            }:
                self.state = self._idle_after_busy
            await self._emit_status(f"subprocess_stopped:{reason}")
            return self.status()


async def get_arbiter() -> HardwareArbiter:
    return await HardwareArbiter.get_instance()
