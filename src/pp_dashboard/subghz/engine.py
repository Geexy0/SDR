"""Sub-GHz orchestration engine."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, ClassVar

from pp_dashboard.config import settings
from pp_dashboard.hardware.arbiter import HardwareArbiter, get_arbiter
from pp_dashboard.models.rf import HardwareStatusResponse, TelemetryEvent
from pp_dashboard.models.subghz import (
    AnalyzeRequest,
    BruteforceRequest,
    ReplayPacketRequest,
    SniffRequest,
    SubConvertRequest,
)
from pp_dashboard.subghz.analyze import analyze_cs8, analyze_result_dict
from pp_dashboard.subghz.db import PacketStore
from pp_dashboard.subghz.ook_synth import (
    SHM_OOK_PATH,
    SynthResult,
    keys_in_window,
    synthesize_de_bruijn_stream,
    synthesize_framed_bruteforce,
)
from pp_dashboard.subghz.sniffer import Rtl433Sniffer
from pp_dashboard.subghz.subfile import (
    cs8_pulses_to_sub,
    parse_sub_file,
    render_sub,
    sub_to_c16,
    sub_to_cs8,
    sub_to_mayhem_txt,
)

log = logging.getLogger(__name__)


class SubGhzEngine:
    _instance: ClassVar[SubGhzEngine | None] = None

    def __init__(self, arbiter: HardwareArbiter) -> None:
        self.arbiter = arbiter
        self.store = PacketStore(settings.subghz_db_path)
        self.sniffer = Rtl433Sniffer(arbiter, self.store)
        self._last_synth: SynthResult | None = None
        self._tx_started_monotonic: float | None = None

    @classmethod
    async def get_instance(cls) -> SubGhzEngine:
        if cls._instance is None:
            cls._instance = cls(await get_arbiter())
        return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        cls._instance = None

    async def start_sniff(self, req: SniffRequest) -> dict[str, Any]:
        argv = await self.sniffer.start(
            frequency_hz=req.frequency_hz,
            sample_rate=req.sample_rate,
            device=req.device,
            max_duration_sec=req.max_duration_sec,
        )
        return {
            "status": self.arbiter.status().model_dump(mode="json"),
            "argv": argv,
        }

    async def stop_sniff(self) -> HardwareStatusResponse:
        await self.sniffer.stop()
        return self.arbiter.status()

    def list_packets(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.store.list_recent(limit)
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": r.id,
                    "ts": r.ts,
                    "frequency_hz": r.frequency_hz,
                    "protocol": r.protocol,
                    "model": r.model,
                    "data": json.loads(r.data_json),
                    "raw_pulses_us": json.loads(r.raw_pulses_us)
                    if r.raw_pulses_us
                    else None,
                }
            )
        return out

    async def analyze(self, req: AnalyzeRequest) -> dict[str, Any]:
        result = await asyncio.to_thread(
            analyze_cs8,
            req.filepath,
            sample_rate=req.sample_rate,
            threshold_method=req.threshold_method,
        )
        payload = analyze_result_dict(result)
        # Persist pulse train for one-click replay
        pid = self.store.insert(
            {
                "protocol": result.timing.encoding,
                "model": "pulse_analyze",
                "type": "ook_pulses",
                "filepath": str(req.filepath),
            },
            frequency_hz=None,
            raw_pulses_us=result.pulses.pulses_us,
        )
        payload["packet_id"] = pid
        return payload

    async def bruteforce(self, req: BruteforceRequest) -> dict[str, Any]:
        def _synth() -> SynthResult:
            if req.mode == "framed":
                return synthesize_framed_bruteforce(
                    family=req.family,
                    nbits=req.nbits,
                    key_start=req.key_start,
                    key_end=req.key_end,
                    repeats=req.repeats,
                    sample_rate=req.sample_rate,
                    path=req.out_path,
                )
            return synthesize_de_bruijn_stream(
                k=req.k,
                n=req.n,
                short_us=req.short_us,
                long_us=req.long_us,
                sample_rate=req.sample_rate,
                path=req.out_path,
            )

        synth = await asyncio.to_thread(_synth)
        self._last_synth = synth
        await self.arbiter._fanout_telemetry(  # noqa: SLF001
            TelemetryEvent(
                type="metric",
                message="ook_synth_done",
                metrics={
                    "bytes": synth.bytes_written,
                    "samples": synth.num_samples,
                    "elapsed_ms": synth.elapsed_ms,
                    "keys": len(synth.timeline),
                },
            )
        )

        status = await self.arbiter.start_tx_file(
            filepath=synth.path,
            frequency_hz=req.frequency_hz,
            sample_rate=req.sample_rate,
            tx_vga=req.tx_vga,
            amp=req.amp,
            loop=req.loop_tx,
            max_duration_sec=req.max_duration_sec,
        )
        self._tx_started_monotonic = time.monotonic()
        # Progress ticker
        asyncio.create_task(self._progress_loop(synth, req.sample_rate))
        return {
            "status": status.model_dump(mode="json"),
            "synth": {
                "path": str(synth.path),
                "bytes": synth.bytes_written,
                "samples": synth.num_samples,
                "elapsed_ms": synth.elapsed_ms,
                "key_count": len(synth.timeline),
            },
        }

    async def _progress_loop(self, synth: SynthResult, sample_rate: int) -> None:
        try:
            while self.arbiter.runner.running and self._tx_started_monotonic is not None:
                elapsed = time.monotonic() - self._tx_started_monotonic
                sample_idx = int(elapsed * sample_rate) % max(synth.num_samples, 1)
                key = None
                for entry in synth.timeline:
                    if entry.start_sample <= sample_idx < entry.end_sample:
                        key = entry.key
                        break
                remaining = max(0.0, (synth.num_samples - sample_idx) / sample_rate)
                await self.arbiter._fanout_telemetry(  # noqa: SLF001
                    TelemetryEvent(
                        type="metric",
                        message="bruteforce_progress",
                        metrics={
                            "elapsed_s": elapsed,
                            "sample_idx": sample_idx,
                            "current_key": key,
                            "eta_s": remaining,
                            "bytes_total": synth.bytes_written,
                        },
                    )
                )
                await asyncio.sleep(0.25)
        except Exception:
            log.exception("bruteforce progress loop failed")

    async def stop_bruteforce(self) -> dict[str, Any]:
        center = None
        keys: list[int] = []
        if self._last_synth and self._tx_started_monotonic is not None:
            elapsed = time.monotonic() - self._tx_started_monotonic
            center = int(elapsed * self._last_synth.sample_rate)
            window = int(self._last_synth.sample_rate * 1.0)
            keys = keys_in_window(self._last_synth.timeline, center, window)
        status = await self.arbiter.stop_subprocess(reason="bruteforce_stop")
        self._tx_started_monotonic = None
        return {
            "status": status.model_dump(mode="json"),
            "candidate_keys": keys,
            "center_sample": center,
            "note": "Keys overlapping stop time ±1s window (framed mode).",
        }

    async def replay_packet(self, req: ReplayPacketRequest) -> HardwareStatusResponse:
        row = self.store.get(req.packet_id)
        if row is None:
            raise ValueError(f"packet {req.packet_id} not found")
        if not row.raw_pulses_us:
            raise ValueError("packet has no raw pulse data for replay")
        pulses = json.loads(row.raw_pulses_us)
        freq = req.frequency_hz or row.frequency_hz or 433_920_000
        from pp_dashboard.subghz.subfile import SubFile

        sub = SubFile(
            filetype="Flipper SubGhz RAW File",
            frequency=int(freq),
            raw_data=list(pulses),
        )
        out = settings.captures_dir / f"replay_{req.packet_id}.cs8"
        await asyncio.to_thread(sub_to_cs8, sub, sample_rate=req.sample_rate, out_path=out)
        return await self.arbiter.start_tx_file(
            filepath=out,
            frequency_hz=int(freq),
            sample_rate=req.sample_rate,
            tx_vga=req.tx_vga,
            amp=req.amp,
            loop=False,
            max_duration_sec=req.max_duration_sec,
        )

    async def convert_sub(self, req: SubConvertRequest) -> dict[str, Any]:
        captures = settings.captures_dir

        def _run() -> dict[str, Any]:
            if req.action == "parse":
                sub = parse_sub_file(req.input_path)
                return {
                    "filetype": sub.filetype,
                    "frequency": sub.frequency,
                    "preset": sub.preset,
                    "protocol": sub.protocol,
                    "raw_count": len(sub.raw_data),
                    "text": render_sub(sub)[:4000],
                }
            out = req.output_path
            if out is None:
                stem = req.input_path.stem
                if req.action == "sub_to_cs8":
                    out = captures / f"{stem}.cs8"
                elif req.action == "cs8_to_sub":
                    out = captures / f"{stem}.sub"
                elif req.action == "sub_to_c16":
                    out = captures / f"{stem}.C16"
                else:
                    out = captures / f"{stem}.TXT"
            out = Path(out)
            # confine outputs
            out = (captures / out.name).resolve()

            if req.action == "sub_to_cs8":
                sub = parse_sub_file(req.input_path)
                sub_to_cs8(sub, sample_rate=req.sample_rate, out_path=out)
            elif req.action == "sub_to_c16":
                sub = parse_sub_file(req.input_path)
                sub_to_c16(sub, sample_rate=req.sample_rate, out_path=out)
            elif req.action == "sub_to_txt":
                sub = parse_sub_file(req.input_path)
                sub_to_mayhem_txt(sub, out)
            elif req.action == "cs8_to_sub":
                from pp_dashboard.subghz.analyze import analyze_cs8

                analyzed = analyze_cs8(req.input_path, sample_rate=req.sample_rate)
                freq = req.frequency_hz or 433_920_000
                cs8_pulses_to_sub(
                    analyzed.pulses.pulses_us, frequency_hz=freq, out_path=out
                )
            else:
                raise ValueError(f"unknown action {req.action}")
            return {"output_path": str(out)}

        return await asyncio.to_thread(_run)


async def get_subghz_engine() -> SubGhzEngine:
    return await SubGhzEngine.get_instance()
