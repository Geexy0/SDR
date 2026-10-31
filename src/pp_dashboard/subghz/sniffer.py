"""rtl_433 (SoapySDR/HackRF) managed sniffer with fan-out + SQLite."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import signal
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from pp_dashboard.config import Settings, settings
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.subghz.db import PacketStore

log = logging.getLogger(__name__)

PacketHandler = Callable[[dict[str, Any], int], Awaitable[None] | None]

# HackRF via Soapy only accepts integer-MHz sample rates (1e6, 2e6, 3e6, …).
# 2.5 Msps is invalid and yields a silent dead RX (no sample stream / no decodes).
HACKRF_SOAPY_SAMPLE_RATES: tuple[int, ...] = tuple(range(1_000_000, 21_000_000, 1_000_000))
DEFAULT_HACKRF_SAMPLE_RATE = 2_000_000
DEFAULT_HACKRF_GAIN = "LNA=32,VGA=40,AMP=0"


def snap_hackrf_sample_rate(sample_rate: int) -> int:
    """Nearest valid HackRF Soapy sample rate (1–20 MHz, 1 MHz steps)."""
    sr = int(sample_rate)
    if sr in HACKRF_SOAPY_SAMPLE_RATES:
        return sr
    return min(HACKRF_SOAPY_SAMPLE_RATES, key=lambda x: abs(x - sr))


class Rtl433Sniffer:
    def __init__(
        self,
        arbiter: HardwareArbiter,
        store: PacketStore,
        cfg: Settings | None = None,
    ) -> None:
        self.arbiter = arbiter
        self.store = store
        self.cfg = cfg or settings
        self._handlers: list[PacketHandler] = []
        self._json_task: asyncio.Task[None] | None = None
        self._freq_hz: int | None = None
        self._fifo_proc: asyncio.subprocess.Process | None = None

    @property
    def running(self) -> bool:
        fifo = self._fifo_proc
        if fifo is not None and fifo.returncode is None:
            return True
        argv = self.arbiter.runner.argv
        return bool(
            self.arbiter.runner.running
            and argv
            and "rtl_433" in Path(argv[0]).name
        )

    def subscribe(self, handler: PacketHandler) -> None:
        if handler not in self._handlers:
            self._handlers.append(handler)

    def unsubscribe(self, handler: PacketHandler) -> None:
        self._handlers = [h for h in self._handlers if h is not handler]

    def resolve_binary(self) -> str:
        configured = self.cfg.rtl_433_bin
        path = shutil.which(configured) or (
            configured if Path(configured).is_file() else None
        )
        if not path:
            raise FileNotFoundError(
                "rtl_433 not found on PATH. Install package `rtl-433` "
                "(e.g. sudo apt install rtl-433) or set Settings.rtl_433_bin."
            )
        return path

    def build_argv(
        self,
        *,
        frequency_hz: int,
        sample_rate: int,
        device: str = "driver=hackrf",
        protocol_ids: list[int] | None = None,
        hop_frequencies_hz: Sequence[int] | None = None,
        hop_interval_sec: float = 20.0,
        gain: str | None = DEFAULT_HACKRF_GAIN,
        autolevel: bool = True,
    ) -> list[str]:
        binary = self.resolve_binary()
        sr = snap_hackrf_sample_rate(sample_rate)
        freqs: list[int] = []
        if hop_frequencies_hz:
            freqs = [int(f) for f in hop_frequencies_hz if int(f) > 0]
        if not freqs:
            freqs = [int(frequency_hz)]
        argv = [
            binary,
            "-d",
            device,
        ]
        if gain:
            argv.extend(["-g", gain])
        for f_hz in freqs:
            argv.extend(["-f", str(f_hz)])
        if len(freqs) > 1:
            argv.extend(["-H", str(max(5.0, float(hop_interval_sec)))])
        argv.extend(
            [
                "-s",
                str(sr),
                "-F",
                "json",
                # log → stdout (non-JSON lines); needed for Auto Level / noise heartbeats
                "-F",
                "log",
                "-M",
                "level",
                "-M",
                "protocol",
                "-M",
                "noise:5",
            ]
        )
        if autolevel:
            argv.extend(["-Y", "autolevel"])
        if protocol_ids:
            for pid in protocol_ids:
                argv.extend(["-R", str(int(pid))])
        return argv

    def build_fifo_argv(
        self,
        *,
        sample_rate: int = DEFAULT_HACKRF_SAMPLE_RATE,
        protocol_ids: list[int] | None = None,
        fifo_path: str = "/tmp/pp_subghz.cs8",
        autolevel: bool = True,
    ) -> list[str]:
        """rtl_433 reading multiplex DDC FIFO (no Soapy/HackRF USB claim)."""
        binary = self.resolve_binary()
        sr = snap_hackrf_sample_rate(sample_rate)
        argv = [
            binary,
            "-r",
            f"cs8:{fifo_path}",
            "-s",
            str(sr),
            "-F",
            "json",
            "-F",
            "log",
            "-M",
            "level",
            "-M",
            "protocol",
            "-M",
            "noise:5",
        ]
        if autolevel:
            argv.extend(["-Y", "autolevel"])
        if protocol_ids:
            for pid in protocol_ids:
                argv.extend(["-R", str(int(pid))])
        return argv

    async def start(
        self,
        *,
        frequency_hz: int,
        sample_rate: int = DEFAULT_HACKRF_SAMPLE_RATE,
        device: str = "driver=hackrf",
        max_duration_sec: float | None = None,
        protocol_ids: list[int] | None = None,
        hop_frequencies_hz: Sequence[int] | None = None,
        hop_interval_sec: float = 20.0,
        gain: str | None = DEFAULT_HACKRF_GAIN,
        autolevel: bool = True,
    ) -> list[str]:
        self._freq_hz = frequency_hz
        mux = getattr(self.arbiter, "multiplexer", None)
        use_mux = (
            mux is not None
            and getattr(mux, "running", False)
            and self.arbiter.state is HardwareState.MULTIPLEX_RX
        )

        if self._json_task and not self._json_task.done():
            self._json_task.cancel()
            await asyncio.gather(self._json_task, return_exceptions=True)

        if use_mux:
            mux.set_subghz_tune(int(frequency_hz))
            argv = self.build_fifo_argv(
                sample_rate=sample_rate,
                protocol_ids=protocol_ids,
                autolevel=autolevel,
            )
            log.info("rtl_433 via IQ mux FIFO: %s", " ".join(argv))
            self._fifo_proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            self._json_task = asyncio.create_task(self._consume_fifo_stdout())
            return argv

        argv = self.build_argv(
            frequency_hz=frequency_hz,
            sample_rate=sample_rate,
            device=device,
            protocol_ids=protocol_ids,
            hop_frequencies_hz=hop_frequencies_hz,
            hop_interval_sec=hop_interval_sec,
            gain=gain,
            autolevel=autolevel,
        )
        # Subscribe before spawn so early JSON lines are not missed.
        self._json_task = asyncio.create_task(self._consume_stdout_json())
        await asyncio.sleep(0)  # let consumer attach telemetry handler
        await self.arbiter.cancel_watchdog()
        await self.arbiter._start_sdr(  # noqa: SLF001
            argv,
            busy=HardwareState.BUSY_RX,
            max_duration_sec=max_duration_sec,
        )
        return argv

    async def stop(self) -> None:
        if self._json_task and not self._json_task.done():
            self._json_task.cancel()
            await asyncio.gather(self._json_task, return_exceptions=True)
        self._json_task = None
        proc = getattr(self, "_fifo_proc", None)
        self._fifo_proc = None
        if proc and proc.returncode is None:
            try:
                proc.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()
            return
        await self.arbiter.stop_subprocess(reason="subghz_sniff_stop")

    async def _handle_line(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        packet_id = self.store.insert(payload, frequency_hz=self._freq_hz)
        for handler in list(self._handlers):
            try:
                result = handler(payload, packet_id)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("subghz packet handler failed")

    async def _consume_fifo_stdout(self) -> None:
        proc = getattr(self, "_fifo_proc", None)
        if not proc or not proc.stdout:
            return
        try:
            while True:
                line_b = await proc.stdout.readline()
                if not line_b:
                    break
                line = line_b.decode("utf-8", errors="replace").strip()
                if line:
                    await self._handle_line(line)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("rtl_433 FIFO consumer failed")

    async def _consume_stdout_json(self) -> None:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=512)

        async def _on_event(event) -> None:
            if event.type == "stdout" and event.message:
                try:
                    queue.put_nowait(event.message)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        queue.put_nowait(event.message)
                    except asyncio.QueueFull:
                        pass

        self.arbiter.subscribe_telemetry(_on_event)
        try:
            while True:
                line = await queue.get()
                await self._handle_line(line)
        except asyncio.CancelledError:
            raise
        finally:
            self.arbiter.unsubscribe_telemetry(_on_event)
