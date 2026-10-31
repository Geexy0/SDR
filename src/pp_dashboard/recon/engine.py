"""Phase 5 recon orchestration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from pp_dashboard.hardware.arbiter import HardwareArbiter, get_arbiter
from pp_dashboard.models.recon import (
    DecoderStartRequest,
    SweepStartRequest,
    SynthesizeRequest,
    VoiceReconStartRequest,
)
from pp_dashboard.models.rf import TelemetryEvent
from pp_dashboard.recon.decoders import AIS_A_HZ, DecoderSession
from pp_dashboard.recon.hybrid_adsb import DEFAULT_RX_LAT, DEFAULT_RX_LON, HybridAdsbSession
from pp_dashboard.recon.network_tracks import NetworkTrackSession
from pp_dashboard.recon.radiosonde import DEFAULT_SONDE_HZ, BAND_HZ, RadiosondeSession
from pp_dashboard.recon.synth_fm_rds import synthesize_fm_rds
from pp_dashboard.recon.synth_gps import GPS_L1_HZ, run_gps_sdr_sim, transmit_gps_cs8
from pp_dashboard.recon.synth_pager import synthesize_lrs_pager, synthesize_touchtunes_pin
from pp_dashboard.config import settings
from pp_dashboard.recon.voice_bands import VOICE_BANDS
from pp_dashboard.recon.voice_recordings import VoiceRecordingStore
from pp_dashboard.recon.voice_recon import AutoVoiceReconSession
from pp_dashboard.recon.waterfall import TriggerConfig, WaterfallSession

log = logging.getLogger(__name__)

# Common AIS channels
AIS_B_HZ = 162_025_000

DecoderLike = DecoderSession | NetworkTrackSession | HybridAdsbSession | RadiosondeSession


class ReconEngine:
    _instance: ClassVar[ReconEngine | None] = None

    def __init__(self, arbiter: HardwareArbiter) -> None:
        self.arbiter = arbiter
        self.waterfall: WaterfallSession | None = None
        self.decoder: DecoderLike | None = None
        self.voice_recon: AutoVoiceReconSession | None = None
        self._voice_store = VoiceRecordingStore(
            settings.captures_dir / "voice_recordings.sqlite3",
            settings.captures_dir / "voice",
        )

    @classmethod
    async def get_instance(cls) -> ReconEngine:
        if cls._instance is None:
            cls._instance = cls(await get_arbiter())
        return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        cls._instance = None

    def status(self) -> dict[str, Any]:
        return {
            "waterfall_running": bool(self.waterfall and self.waterfall.running),
            "decoder_kind": self.decoder.kind if self.decoder else None,
            "decoder_running": bool(self.decoder and self.decoder.running),
            "voice_recon": self.voice_recon.status() if self.voice_recon else {"running": False},
            "voice_bands": [
                {
                    "id": b.id,
                    "name": b.name,
                    "start_mhz": b.start_mhz,
                    "stop_mhz": b.stop_mhz,
                    "mode": b.mode,
                    "default_enabled": b.default_enabled,
                    "note": b.note,
                }
                for b in VOICE_BANDS
            ],
            "trigger_events": self.waterfall.recent_events() if self.waterfall else [],
            "hardware": self.arbiter.status().model_dump(mode="json"),
        }

    async def start_sweep(self, req: SweepStartRequest) -> dict[str, Any]:
        if self.waterfall and self.waterfall.running:
            raise RuntimeError("waterfall already running")
        if getattr(self.arbiter, "multiplexer", None) and getattr(
            self.arbiter.multiplexer, "running", False
        ):
            raise RuntimeError("stop MULTIPLEX_RX before hackrf_sweep waterfall (or use MUX FFT)")
        if self.decoder and self.decoder.running:
            raise RuntimeError("stop decoder before waterfall")
        if self.voice_recon and self.voice_recon.running:
            raise RuntimeError("stop voice recon before waterfall")
        trig = TriggerConfig(
            enabled=req.trigger_enabled,
            f_lo_hz=req.trigger_f_lo_hz or 0,
            f_hi_hz=req.trigger_f_hi_hz or 0,
            rssi_db=req.trigger_rssi_db,
            action=req.trigger_action,
        )
        session = WaterfallSession(
            arbiter=self.arbiter,
            start_mhz=req.start_mhz,
            stop_mhz=req.stop_mhz,
            bin_width_hz=req.bin_width_hz,
            lna=req.lna,
            vga=req.vga,
            amp=req.amp,
            max_duration_sec=req.max_duration_sec,
            trigger=trig,
            target_fps=req.target_fps,
        )
        # Publish session before start so /ws/waterfall attach can subscribe mid-boot.
        self.waterfall = session
        try:
            argv = await session.start()
        except Exception:
            self.waterfall = None
            raise
        return {"argv": argv, "status": self.status()}

    async def stop_sweep(self) -> dict[str, Any]:
        if self.waterfall:
            await self.waterfall.stop(reason="api_stop")
            self.waterfall = None
        return {"status": self.status()}

    async def start_voice_recon(self, req: VoiceReconStartRequest) -> dict[str, Any]:
        if self.waterfall and self.waterfall.running:
            raise RuntimeError("stop waterfall before voice recon")
        if self.decoder and self.decoder.running:
            raise RuntimeError("stop decoder before voice recon")
        if self.voice_recon and self.voice_recon.running:
            await self.voice_recon.stop()
            self.voice_recon = None

        session = AutoVoiceReconSession(
            arbiter=self.arbiter,
            band_ids=req.bands,
            detect_margin_db=req.detect_margin_db,
            min_detect_db=req.min_detect_db,
            quiet_hold_sec=req.quiet_hold_sec,
            max_dwell_sec=req.max_dwell_sec,
            start_lna=req.start_lna,
            start_vga=req.start_vga,
            audio_enabled=req.audio_enabled,
            local_aplay=req.local_aplay,
            squelch_db=req.squelch_db,
            vox_open_hold_sec=req.vox_open_hold_sec,
        )

        async def _to_telemetry(payload: dict[str, Any]) -> None:
            await self.arbiter._fanout_telemetry(  # noqa: SLF001
                TelemetryEvent(
                    type="metric",
                    state=self.arbiter.state,
                    message="voice_recon",
                    metrics=payload,
                )
            )

        session.subscribe(_to_telemetry)
        try:
            status = await session.start()
        except Exception:
            session.unsubscribe(_to_telemetry)
            raise
        self.voice_recon = session
        return {"status": self.status(), "session": status}

    async def stop_voice_recon(self) -> dict[str, Any]:
        if self.voice_recon:
            await self.voice_recon.stop()
            self.voice_recon = None
        return {"status": self.status()}

    def list_voice_recordings(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._voice_store.list_recent(limit=limit)

    def get_voice_recording(self, recording_id: int) -> dict[str, Any] | None:
        return self._voice_store.get(recording_id)

    async def start_decoder(self, req: DecoderStartRequest) -> dict[str, Any]:
        if self.waterfall and self.waterfall.running:
            raise RuntimeError("stop waterfall before decoder")
        if self.voice_recon and self.voice_recon.running:
            raise RuntimeError("stop voice recon before decoder")
        if self.decoder and self.decoder.running:
            await self.decoder.stop()
            self.decoder = None
        freq = req.frequency_hz
        if req.kind == "adsb" and (not freq or freq == 0):
            freq = 1_090_000_000
        if req.kind == "ais" and freq not in (AIS_A_HZ, AIS_B_HZ):
            freq = AIS_A_HZ
        if req.kind == "pocsag" and req.frequency_hz:
            freq = req.frequency_hz
        if req.kind == "radiosonde":
            lo, hi = BAND_HZ
            if not freq or freq < lo or freq > hi:
                freq = DEFAULT_SONDE_HZ

        async def _to_telemetry(payload: dict[str, Any]) -> None:
            kind = str(payload.get("type") or req.kind)
            await self.arbiter._fanout_telemetry(  # noqa: SLF001
                TelemetryEvent(
                    type="metric",
                    state=self.arbiter.state,
                    message=kind,
                    metrics=payload,
                )
            )

        session: DecoderLike
        if req.kind == "adsb":
            # Hybrid: local dump1090/soft Mode-S + OpenSky, tagged LOCAL_RF|OPENSKY_NET|HYBRID.
            hybrid = HybridAdsbSession(
                arbiter=self.arbiter,
                frequency_hz=freq,
                lat=float(req.lat if req.lat is not None else DEFAULT_RX_LAT),
                lon=float(req.lon if req.lon is not None else DEFAULT_RX_LON),
            )
            hybrid.subscribe(_to_telemetry)
            desc = await hybrid.start()
            session = hybrid
        elif req.kind == "radiosonde":
            sonde = RadiosondeSession(arbiter=self.arbiter, frequency_hz=freq)
            sonde.subscribe(_to_telemetry)
            desc = await sonde.start()
            session = sonde
        else:
            try:
                rf_session = DecoderSession(
                    kind=req.kind, arbiter=self.arbiter, frequency_hz=freq
                )
                rf_session.subscribe(_to_telemetry)
                desc = await rf_session.start()
                session = rf_session
            except FileNotFoundError:
                raise

        self.decoder = session
        await self.arbiter._fanout_telemetry(  # noqa: SLF001
            TelemetryEvent(
                type="status",
                state=self.arbiter.state,
                message=f"decoder_started:{req.kind}",
                metrics={"cmd": desc, "frequency_hz": freq},
            )
        )
        return {"cmd": desc, "status": self.status()}

    async def stop_decoder(self) -> dict[str, Any]:
        if self.decoder:
            await self.decoder.stop()
            self.decoder = None
            await self.arbiter._fanout_telemetry(  # noqa: SLF001
                TelemetryEvent(
                    type="status",
                    state=self.arbiter.state,
                    message="decoder_stopped",
                )
            )
        return {"status": self.status()}

    async def synthesize(self, req: SynthesizeRequest) -> dict[str, Any]:
        if req.kind == "gps_l1":
            if req.ephemeris_path is None:
                raise ValueError("ephemeris_path required for gps_l1")
            path = await run_gps_sdr_sim(
                ephemeris=req.ephemeris_path,
                lat=req.lat,
                lon=req.lon,
                alt=req.alt,
                duration_s=req.gps_duration_s,
            )
            await transmit_gps_cs8(
                self.arbiter,
                path=path,
                tx_vga=req.tx_vga,
                max_duration_sec=req.max_duration_sec,
            )
            return {
                "kind": req.kind,
                "path": str(path),
                "frequency_hz": GPS_L1_HZ,
                "status": self.arbiter.status().model_dump(mode="json"),
            }

        if req.kind == "lrs_pager":
            frame = await asyncio.to_thread(
                synthesize_lrs_pager, req.pager_id, sample_rate=2_000_000
            )
            await self.arbiter.start_tx_file(
                filepath=frame.path,
                frequency_hz=req.frequency_hz,
                sample_rate=2_000_000,
                tx_vga=req.tx_vga,
                amp=0,
                loop=True,
                max_duration_sec=req.max_duration_sec,
            )
            return {
                "kind": req.kind,
                "path": str(frame.path),
                "frequency_hz": req.frequency_hz,
                "status": self.arbiter.status().model_dump(mode="json"),
            }

        if req.kind == "touchtunes":
            frame = await asyncio.to_thread(
                synthesize_touchtunes_pin, req.pin, sample_rate=2_000_000
            )
            await self.arbiter.start_tx_file(
                filepath=frame.path,
                frequency_hz=req.frequency_hz,
                sample_rate=2_000_000,
                tx_vga=req.tx_vga,
                amp=0,
                loop=True,
                max_duration_sec=req.max_duration_sec,
            )
            return {
                "kind": req.kind,
                "path": str(frame.path),
                "frequency_hz": req.frequency_hz,
                "status": self.arbiter.status().model_dump(mode="json"),
            }

        # fm_rds
        result = await asyncio.to_thread(
            synthesize_fm_rds,
            audio_hz=req.audio_hz,
            ps=req.ps,
            rt=req.rt,
            sample_rate=2_000_000,
            duration_s=min(req.max_duration_sec or 5.0, 10.0),
        )
        await self.arbiter.start_tx_file(
            filepath=result.path,
            frequency_hz=req.fm_frequency_hz,
            sample_rate=result.sample_rate,
            tx_vga=req.tx_vga,
            amp=0,
            loop=True,
            max_duration_sec=req.max_duration_sec,
        )
        return {
            "kind": req.kind,
            "path": str(result.path),
            "frequency_hz": req.fm_frequency_hz,
            "ps": req.ps,
            "rt": req.rt,
            "status": self.arbiter.status().model_dump(mode="json"),
        }


async def get_recon_engine() -> ReconEngine:
    return await ReconEngine.get_instance()
