"""Autonomous Recon Mission Scheduler — sequential RX pipelines on HardwareArbiter."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from pp_dashboard.config import settings
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.models.rf import CaptureRequest, TelemetryEvent

log = logging.getLogger(__name__)

# Broadcast / known static carriers excluded from narrowband peak ranking.
_FM_BROADCAST = (87_500_000, 108_000_000)
_DAB_VHF = (174_000_000, 240_000_000)
_NOAA_WX = (162_400_000, 162_550_000)
_ADS_B = (1_089_000_000, 1_091_000_000)
_GSM900 = (935_000_000, 960_000_000)


@dataclass(slots=True, frozen=True)
class RankedPeak:
    frequency_hz: int
    peak_rssi_db: float


def is_static_broadcast(frequency_hz: int) -> bool:
    f = int(frequency_hz)
    for lo, hi in (_FM_BROADCAST, _DAB_VHF, _NOAA_WX, _ADS_B, _GSM900):
        if lo <= f <= hi:
            return True
    return False


def rank_narrowband_peaks(
    freqs_hz: np.ndarray,
    powers_db: np.ndarray,
    *,
    threshold_db: float,
    top_k: int,
    min_separation_hz: float = 100_000.0,
) -> list[RankedPeak]:
    """Rank local maxima above threshold, excluding static broadcast carriers."""
    f = np.asarray(freqs_hz, dtype=np.float64).ravel()
    p = np.asarray(powers_db, dtype=np.float64).ravel()
    if f.size == 0 or p.size == 0 or f.size != p.size:
        return []
    order = np.argsort(-p)
    selected: list[RankedPeak] = []
    for idx in order:
        power = float(p[idx])
        if power < threshold_db:
            break
        freq = int(round(float(f[idx])))
        if is_static_broadcast(freq):
            continue
        if any(abs(freq - s.frequency_hz) < min_separation_hz for s in selected):
            continue
        selected.append(RankedPeak(frequency_hz=freq, peak_rssi_db=power))
        if len(selected) >= max(1, int(top_k)):
            break
    return selected


MISSION_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "subghz_sensor_hunt",
        "name": "Sub-GHz Sensor Hunt 433/868",
        "description": "Hunt ISM sensors across 433–435 and 868–870 MHz, capture bursts, tag OOK/FSK.",
        "sync_freqman_sd": False,
        "steps": [
            {
                "type": "STEP_SWEEP_RANK",
                "f_start_hz": 433_000_000,
                "f_stop_hz": 435_000_000,
                "duration_sec": 12,
                "top_k": 8,
                "threshold_db": -55,
                "bin_width_hz": 100_000,
                "lna": 24,
                "vga": 20,
            },
            {
                "type": "STEP_SWEEP_RANK",
                "f_start_hz": 868_000_000,
                "f_stop_hz": 870_000_000,
                "duration_sec": 12,
                "top_k": 8,
                "threshold_db": -55,
                "bin_width_hz": 100_000,
                "lna": 24,
                "vga": 20,
            },
            {"type": "STEP_BURST_CAPTURE", "duration_sec": 0.5, "sample_rate": 2_000_000, "lna": 24, "vga": 20},
            {"type": "STEP_AUTO_ANALYZE"},
            {"type": "STEP_FREQMAN_EXPORT", "category": "RECON"},
        ],
    },
    {
        "id": "vhf_uhf_voice_survey",
        "name": "VHF/UHF Voice & Repeater Survey",
        "description": "Survey 144–148 and 430–440 MHz for NBFM voice / repeater carriers.",
        "sync_freqman_sd": False,
        "steps": [
            {
                "type": "STEP_SWEEP_RANK",
                "f_start_hz": 144_000_000,
                "f_stop_hz": 148_000_000,
                "duration_sec": 15,
                "top_k": 10,
                "threshold_db": -60,
                "bin_width_hz": 50_000,
                "lna": 24,
                "vga": 22,
            },
            {
                "type": "STEP_SWEEP_RANK",
                "f_start_hz": 430_000_000,
                "f_stop_hz": 440_000_000,
                "duration_sec": 15,
                "top_k": 10,
                "threshold_db": -60,
                "bin_width_hz": 50_000,
                "lna": 24,
                "vga": 22,
            },
            {"type": "STEP_BURST_CAPTURE", "duration_sec": 1.0, "sample_rate": 2_000_000, "lna": 24, "vga": 22},
            {"type": "STEP_AUTO_ANALYZE"},
            {"type": "STEP_FREQMAN_EXPORT", "category": "RECON"},
        ],
    },
    {
        "id": "ism_anomaly_audit",
        "name": "Full ISM Anomaly Audit",
        "description": "Wide ISM sweep 300–950 MHz; capture and classify anomalous narrowband emitters.",
        "sync_freqman_sd": False,
        "steps": [
            {
                "type": "STEP_SWEEP_RANK",
                "f_start_hz": 300_000_000,
                "f_stop_hz": 950_000_000,
                "duration_sec": 45,
                "top_k": 16,
                "threshold_db": -50,
                "bin_width_hz": 200_000,
                "lna": 32,
                "vga": 20,
            },
            {"type": "STEP_BURST_CAPTURE", "duration_sec": 0.4, "sample_rate": 2_000_000, "lna": 32, "vga": 20},
            {"type": "STEP_AUTO_ANALYZE"},
            {"type": "STEP_FREQMAN_EXPORT", "category": "RECON"},
        ],
    },
]


def template_by_id(template_id: str) -> dict[str, Any] | None:
    for t in MISSION_TEMPLATES:
        if t["id"] == template_id:
            return dict(t)
    return None


class MissionStore:
    """SQLite store for mission runs + discovered channel findings."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    template_id TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id INTEGER NOT NULL,
                    frequency_hz INTEGER NOT NULL,
                    peak_rssi_db REAL,
                    modulation TEXT,
                    baud_est REAL,
                    cs8_path TEXT,
                    wav_path TEXT,
                    notes TEXT,
                    FOREIGN KEY(mission_id) REFERENCES missions(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_mission ON findings(mission_id)"
            )
            conn.commit()

    def create_mission(self, name: str, *, template_id: str | None = None) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO missions (name, template_id, state, created_at) VALUES (?, ?, ?, ?)",
                (name, template_id, "running", ts),
            )
            conn.commit()
            return int(cur.lastrowid)

    def set_state(self, mission_id: int, state: str, *, error: str | None = None) -> None:
        finished = (
            datetime.now(timezone.utc).isoformat()
            if state in {"completed", "aborted", "error"}
            else None
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE missions SET state=?, finished_at=COALESCE(?, finished_at), error=? WHERE id=?",
                (state, finished, error, mission_id),
            )
            conn.commit()

    def insert_finding(
        self,
        mission_id: int,
        *,
        frequency_hz: int,
        peak_rssi_db: float | None = None,
        modulation: str | None = None,
        baud_est: float | None = None,
        cs8_path: str | None = None,
        wav_path: str | None = None,
        notes: str | None = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO findings (
                    mission_id, frequency_hz, peak_rssi_db, modulation, baud_est,
                    cs8_path, wav_path, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    int(frequency_hz),
                    peak_rssi_db,
                    modulation,
                    baud_est,
                    cs8_path,
                    wav_path,
                    notes,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_findings(self, mission_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM findings WHERE mission_id=?
                ORDER BY (peak_rssi_db IS NULL), peak_rssi_db DESC, id
                """,
                (mission_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def summary_report(self, mission_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            m = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
        findings = self.list_findings(mission_id)
        return {
            "mission_id": mission_id,
            "name": None if m is None else m["name"],
            "template_id": None if m is None else m["template_id"],
            "state": None if m is None else m["state"],
            "created_at": None if m is None else m["created_at"],
            "finished_at": None if m is None else m["finished_at"],
            "error": None if m is None else m["error"],
            "finding_count": len(findings),
            "findings": findings,
        }


def classify_capture_modulation(
    path: Path, *, sample_rate: int
) -> tuple[str, float, str]:
    """Tag OOK / 2FSK / NBFM_VOICE + baud estimate using workbench + pulse analyzer."""
    from pp_dashboard.subghz.analyze import analyze_cs8
    from pp_dashboard.subghz.workbench import demodulate_file

    notes: list[str] = []
    try:
        analysis = analyze_cs8(path, sample_rate=sample_rate)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"analyze_failed:{exc}")
        analysis = None
    try:
        demod = demodulate_file(path, sample_rate=sample_rate, modulation="AUTO")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"demod_failed:{exc}")
        demod = None

    if analysis is not None:
        pulse_n = len(analysis.pulses.pulses_us)
        enc = analysis.timing.encoding
        bit_us = float(analysis.timing.bit_us or 0.0)
        baud_from_pulses = (1e6 / bit_us) if bit_us > 0 else 0.0
        fsk = analysis.fsk
        if enc != "UNKNOWN" and pulse_n >= 8:
            baud = baud_from_pulses or (demod.baud_est if demod else 0.0)
            return "OOK", float(baud), f"pulse:{enc};" + ";".join(notes)
        if fsk is not None and fsk.deviation_hz >= 800.0:
            # Continuous narrow FM with sparse pulses → likely NBFM voice.
            if pulse_n < 24 and 1500.0 <= fsk.deviation_hz <= 8000.0:
                return "NBFM_VOICE", 0.0, f"dev={fsk.deviation_hz:.0f};" + ";".join(notes)
            baud = float(demod.baud_est) if demod else float(fsk.samples_per_bit and sample_rate / max(fsk.samples_per_bit, 1) or 0.0)
            return "2FSK", baud, f"dev={fsk.deviation_hz:.0f};" + ";".join(notes)

    if demod is not None:
        mod = demod.modulation
        if mod in {"OOK", "2FSK", "GFSK"}:
            tag = "2FSK" if mod == "GFSK" else mod
            return tag, float(demod.baud_est), demod.notes + (";" + ";".join(notes) if notes else "")
        if mod == "BPSK":
            return "2FSK", float(demod.baud_est), "bpsk_as_fsk;" + ";".join(notes)

    return "OOK", 0.0, ";".join(notes) or "fallback_ook"


class MissionScheduler:
    """Runs multi-step RX recon missions sequentially under the USB mutex."""

    _instance: ClassVar[MissionScheduler | None] = None
    _instance_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    def __init__(self, arbiter: HardwareArbiter, *, store: MissionStore | None = None) -> None:
        self.arbiter = arbiter
        self.store = store or MissionStore(settings.captures_dir / "missions.sqlite3")
        self._running = False
        self._radio_child = False
        self._abort = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._mission_id: int | None = None
        self._state = "idle"
        self._step_index = -1
        self._step_total = 0
        self._step_type: str | None = None
        self._error: str | None = None
        self._ranked: list[RankedPeak] = []
        self._captures: list[dict[str, Any]] = []
        self._name = ""
        self._sync_freqman_sd = False
        self._wired = False
        self._abort_lock = asyncio.Lock()
        self._aborting = False

    @classmethod
    async def get_instance(
        cls, *, arbiter: HardwareArbiter | None = None
    ) -> MissionScheduler:
        async with cls._instance_lock:
            if cls._instance is None:
                if arbiter is None:
                    arbiter = await HardwareArbiter.get_instance()
                cls._instance = cls(arbiter)
            return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        cls._instance = None

    @property
    def running(self) -> bool:
        return self._running

    def blocking_busy(self) -> bool:
        """True when mission owns the radio pipeline (blocks other SDR starts)."""
        return self._running and not self._radio_child

    def wire_arbiter(self, arbiter: HardwareArbiter | None = None) -> None:
        a = arbiter or self.arbiter
        a.register_busy_check("mission", self.blocking_busy)
        a.register_stop_callback("mission", self._on_global_stop)
        self._wired = True

    async def _on_global_stop(self) -> None:
        if self._running:
            await self.abort(reason="hardware_stop")

    async def _emit(self, step_type: str, detail: dict[str, Any] | None = None) -> None:
        metrics: dict[str, Any] = {
            "mission": True,
            "mission_id": self._mission_id,
            "name": self._name,
            "state": self._state,
            "step_index": self._step_index,
            "step_total": self._step_total,
            "step_type": step_type,
            "detail": detail or {},
        }
        await self.arbiter._fanout_telemetry(  # noqa: SLF001
            TelemetryEvent(
                type="mission",
                state=self.arbiter.state,
                message=f"mission:{step_type}",
                metrics=metrics,
            )
        )

    def status(self) -> dict[str, Any]:
        report = None
        if self._mission_id is not None:
            report = self.store.summary_report(self._mission_id)
        return {
            "ok": True,
            "state": self._state,
            "running": self._running,
            "mission_id": self._mission_id,
            "name": self._name,
            "step_index": self._step_index,
            "step_total": self._step_total,
            "step_type": self._step_type,
            "error": self._error,
            "ranked_peaks": [asdict(p) for p in self._ranked],
            "report": report,
        }

    async def start(self, mission: dict[str, Any]) -> dict[str, Any]:
        if self._running:
            raise RuntimeError("mission already running")
        self.arbiter.assert_sdr_idle(excluding="mission")

        template_id = mission.get("template_id")
        steps = list(mission.get("steps") or [])
        if not steps and template_id:
            tpl = template_by_id(str(template_id))
            if tpl is None:
                raise ValueError(f"unknown template_id: {template_id}")
            steps = list(tpl["steps"])
            mission = {**tpl, **mission, "steps": steps}
        if not steps:
            raise ValueError("mission requires steps or template_id")

        name = str(mission.get("name") or template_id or "mission")
        self._sync_freqman_sd = bool(mission.get("sync_freqman_sd", False))
        self._abort.clear()
        self._ranked = []
        self._captures = []
        self._error = None
        self._name = name
        self._step_total = len(steps)
        self._step_index = -1
        self._step_type = None
        self._state = "running"
        self._mission_id = self.store.create_mission(name, template_id=template_id)
        self._running = True
        self._radio_child = False
        self._aborting = False
        await self._emit("started", {"steps": self._step_total})
        self._task = asyncio.create_task(self._run(steps), name=f"mission-{self._mission_id}")
        return {"ok": True, "mission_id": self._mission_id, "state": self._state}

    async def abort(self, *, reason: str = "abort") -> dict[str, Any]:
        async with self._abort_lock:
            if not self._running or self._aborting:
                return self.status()
            self._aborting = True
            self._abort.set()
            self._state = "aborted"
            self._error = reason
            if self._mission_id is not None:
                self.store.set_state(self._mission_id, "aborted", error=reason)
            await self._emit("aborted", {"reason": reason})
            # Child SDR jobs: stop arbiter runner / waterfall without re-entering abort.
            try:
                self._radio_child = True
                from pp_dashboard.recon.engine import ReconEngine

                eng = ReconEngine._instance  # noqa: SLF001
                if eng is not None and eng.waterfall is not None and eng.waterfall.running:
                    await eng.stop_sweep()
                if self.arbiter.runner.running:
                    await self.arbiter.runner.stop(reason=f"mission_abort:{reason}")
            except Exception:
                log.exception("mission abort teardown failed")
            finally:
                self._radio_child = False
            task = self._task
            if task is not None and not task.done() and asyncio.current_task() is not task:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
                except (TimeoutError, asyncio.CancelledError):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self._running = False
            self._task = None
            self._aborting = False
            return self.status()

    async def _run(self, steps: list[dict[str, Any]]) -> None:
        try:
            for i, step in enumerate(steps):
                if self._abort.is_set():
                    break
                self._step_index = i
                step_type = str(step.get("type") or "")
                self._step_type = step_type
                await self._emit(step_type, {"phase": "begin"})
                if step_type == "STEP_SWEEP_RANK":
                    await self._step_sweep_rank(step)
                elif step_type == "STEP_BURST_CAPTURE":
                    await self._step_burst_capture(step)
                elif step_type == "STEP_AUTO_ANALYZE":
                    await self._step_auto_analyze(step)
                elif step_type == "STEP_FREQMAN_EXPORT":
                    await self._step_freqman_export(step)
                else:
                    raise ValueError(f"unknown mission step type: {step_type}")
                await self._emit(step_type, {"phase": "done"})
            if self._abort.is_set():
                self._state = "aborted"
                if self._mission_id is not None:
                    self.store.set_state(self._mission_id, "aborted", error=self._error)
            else:
                self._state = "completed"
                if self._mission_id is not None:
                    self.store.set_state(self._mission_id, "completed")
                await self._emit(
                    "completed",
                    {"findings": len(self.store.list_findings(self._mission_id or 0))},
                )
        except Exception as exc:
            log.exception("mission failed")
            self._state = "error"
            self._error = str(exc)
            if self._mission_id is not None:
                self.store.set_state(self._mission_id, "error", error=str(exc))
            await self._emit("error", {"error": str(exc)})
        finally:
            self._running = False
            self._radio_child = False
            self._task = None

    async def _wait_abortable(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; return True if aborted."""
        try:
            await asyncio.wait_for(self._abort.wait(), timeout=max(0.0, seconds))
            return True
        except TimeoutError:
            return False

    async def _step_sweep_rank(self, step: dict[str, Any]) -> None:
        f_start = int(step.get("f_start_hz") or step.get("start_hz") or 0)
        f_stop = int(step.get("f_stop_hz") or step.get("stop_hz") or 0)
        if f_stop <= f_start:
            raise ValueError("STEP_SWEEP_RANK requires f_start_hz < f_stop_hz")
        duration = float(step.get("duration_sec") or 10.0)
        top_k = int(step.get("top_k") or 8)
        threshold_db = float(step.get("threshold_db") or -55.0)
        bin_width = int(step.get("bin_width_hz") or 100_000)
        lna = int(step.get("lna") or 24)
        vga = int(step.get("vga") or 20)

        from pp_dashboard.models.recon import SweepStartRequest
        from pp_dashboard.recon.engine import get_recon_engine

        eng = await get_recon_engine()
        db_min, db_max = -110.0, -20.0
        maxhold: np.ndarray | None = None
        n_bins = max(1, int(np.ceil((f_stop - f_start) / float(bin_width))))

        def on_row(payload: dict[str, Any]) -> None:
            nonlocal maxhold
            if payload.get("type") != "row":
                return
            data = payload.get("data_u8")
            if data is None:
                return
            u8 = np.frombuffer(bytes(data), dtype=np.uint8)
            if u8.size == 0:
                return
            lo = float(payload.get("db_min", db_min))
            hi = float(payload.get("db_max", db_max))
            powers = lo + (u8.astype(np.float64) / 255.0) * (hi - lo)
            if maxhold is None or maxhold.size != powers.size:
                maxhold = powers.copy()
            else:
                np.maximum(maxhold, powers, out=maxhold)

        self._radio_child = True
        try:
            req = SweepStartRequest(
                start_mhz=f_start / 1e6,
                stop_mhz=f_stop / 1e6,
                bin_width_hz=bin_width,
                lna=lna,
                vga=vga,
                amp=0,
                max_duration_sec=min(max(duration + 5.0, 5.0), 120.0),
            )
            await eng.start_sweep(req)
            if eng.waterfall is not None:
                eng.waterfall.subscribe(on_row)
            aborted = await self._wait_abortable(duration)
            if eng.waterfall is not None:
                eng.waterfall.unsubscribe(on_row)
            await eng.stop_sweep()
            if aborted:
                return
        finally:
            self._radio_child = False

        if maxhold is None:
            maxhold = np.full(n_bins, -120.0, dtype=np.float64)
        freqs = f_start + (np.arange(maxhold.size, dtype=np.float64) + 0.5) * float(bin_width)
        peaks = rank_narrowband_peaks(
            freqs,
            maxhold,
            threshold_db=threshold_db,
            top_k=top_k,
            min_separation_hz=max(float(bin_width), 100_000.0),
        )
        # Merge with prior sweep steps (keep best RSSI per separation).
        merged = list(self._ranked) + peaks
        merged.sort(key=lambda p: -p.peak_rssi_db)
        deduped: list[RankedPeak] = []
        sep = max(float(bin_width), 100_000.0)
        for p in merged:
            if any(abs(p.frequency_hz - d.frequency_hz) < sep for d in deduped):
                continue
            deduped.append(p)
        self._ranked = deduped[: max(top_k * 2, top_k)]
        await self._emit(
            "STEP_SWEEP_RANK",
            {"peaks": [asdict(p) for p in peaks], "ranked_total": len(self._ranked)},
        )

    async def _step_burst_capture(self, step: dict[str, Any]) -> None:
        if not self._ranked:
            self._captures = []
            await self._emit("STEP_BURST_CAPTURE", {"captures": [], "note": "no_peaks"})
            return
        duration = float(step.get("duration_sec") or 0.5)
        sample_rate = int(step.get("sample_rate") or 2_000_000)
        if sample_rate != 2_000_000:
            sample_rate = 2_000_000
        lna = int(step.get("lna") or 24)
        vga = int(step.get("vga") or 20)
        num_samples = max(4096, int(sample_rate * duration))
        out_dir = settings.captures_dir / "missions" / f"m{self._mission_id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        captures: list[dict[str, Any]] = []

        for peak in self._ranked:
            if self._abort.is_set():
                break
            path = out_dir / f"burst_{peak.frequency_hz}_{int(time.time())}.cs8"
            self._radio_child = True
            try:
                await self.arbiter.start_capture(
                    CaptureRequest(
                        filepath=path,
                        frequency_hz=peak.frequency_hz,
                        sample_rate=sample_rate,
                        lna=lna,
                        vga=vga,
                        amp=0,
                        num_samples=num_samples,
                        max_duration_sec=max(duration + 2.0, 3.0),
                    )
                )
                while self.arbiter.runner.running:
                    if self._abort.is_set():
                        await self.arbiter.runner.stop(reason="mission_abort")
                        break
                    await asyncio.sleep(0.05)
            finally:
                self._radio_child = False
            if path.is_file() and path.stat().st_size > 0:
                captures.append(
                    {
                        "frequency_hz": peak.frequency_hz,
                        "peak_rssi_db": peak.peak_rssi_db,
                        "cs8_path": str(path),
                        "sample_rate": sample_rate,
                    }
                )
            await self._emit(
                "STEP_BURST_CAPTURE",
                {"captured": peak.frequency_hz, "path": str(path)},
            )
        self._captures = captures
        await self._emit("STEP_BURST_CAPTURE", {"captures": captures, "phase": "summary"})

    async def _step_auto_analyze(self, step: dict[str, Any]) -> None:  # noqa: ARG002
        if self._mission_id is None:
            return
        analyzed = 0
        for cap in self._captures:
            if self._abort.is_set():
                break
            path = Path(cap["cs8_path"])
            sr = int(cap.get("sample_rate") or 2_000_000)
            mod, baud, notes = classify_capture_modulation(path, sample_rate=sr)
            self.store.insert_finding(
                self._mission_id,
                frequency_hz=int(cap["frequency_hz"]),
                peak_rssi_db=float(cap.get("peak_rssi_db") or 0.0),
                modulation=mod,
                baud_est=baud,
                cs8_path=str(path),
                wav_path=None,
                notes=notes,
            )
            try:
                from pp_dashboard.geoloc.service import stamp_rf_hit

                stamp_rf_hit(
                    kind="mission",
                    frequency_hz=int(cap["frequency_hz"]),
                    rssi_db=float(cap.get("peak_rssi_db") or 0.0),
                    label=mod,
                    ref_id=str(path.name),
                    notes=notes,
                )
            except Exception:
                log.debug("mission geoloc stamp skipped", exc_info=True)
            analyzed += 1
            await self._emit(
                "STEP_AUTO_ANALYZE",
                {
                    "frequency_hz": cap["frequency_hz"],
                    "modulation": mod,
                    "baud_est": baud,
                },
            )
        await self._emit("STEP_AUTO_ANALYZE", {"analyzed": analyzed, "phase": "summary"})

    async def _step_freqman_export(self, step: dict[str, Any]) -> None:
        from pp_dashboard.mayhem.freqman_sync import freqman_store, sync_freqman_to_sd

        category = str(step.get("category") or "RECON")
        findings = (
            self.store.list_findings(self._mission_id) if self._mission_id is not None else []
        )
        store = freqman_store()
        exported = 0
        for row in findings:
            desc = f"{row.get('modulation') or 'RX'} {row['frequency_hz'] / 1e6:.3f}"
            store.bookmark(
                int(row["frequency_hz"]),
                desc,
                category=category,
                modulation=row.get("modulation"),
                source="mission",
            )
            exported += 1
        sync_result = None
        do_sync = bool(step.get("sync_sd", self._sync_freqman_sd))
        if do_sync and exported:
            try:
                from pp_dashboard.mayhem.bridge import MayhemSerialBridge

                bridge = await MayhemSerialBridge.get_instance()
                sync_result = await sync_freqman_to_sd(bridge, categories=[category])
            except Exception as exc:  # noqa: BLE001
                sync_result = {"ok": False, "error": str(exc)}
        await self._emit(
            "STEP_FREQMAN_EXPORT",
            {"exported": exported, "category": category, "sync": sync_result},
        )


async def get_mission_scheduler() -> MissionScheduler:
    return await MissionScheduler.get_instance()
