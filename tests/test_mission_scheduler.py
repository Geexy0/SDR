"""Autonomous Recon Mission Scheduler — offline tests (no RF)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from dataclasses import asdict

from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.main import create_app
from pp_dashboard.recon import scheduler as sched


def test_rank_peaks_excludes_broadcast_and_respects_topk() -> None:
    freqs = np.array(
        [
            100_000_000,  # FM broadcast — exclude
            433_920_000,
            433_950_000,  # near neighbor
            868_300_000,
            868_350_000,
            434_000_000,
        ],
        dtype=np.float64,
    )
    powers = np.array([-30.0, -45.0, -46.0, -40.0, -55.0, -50.0], dtype=np.float64)
    peaks = sched.rank_narrowband_peaks(
        freqs,
        powers,
        threshold_db=-52.0,
        top_k=3,
        min_separation_hz=200_000.0,
    )
    assert len(peaks) <= 3
    assert all(not sched.is_static_broadcast(int(p.frequency_hz)) for p in peaks)
    assert peaks[0].frequency_hz in {433_920_000, 868_300_000, 434_000_000}
    assert peaks[0].peak_rssi_db >= peaks[-1].peak_rssi_db


def test_mission_templates_cover_three_hunts() -> None:
    names = {t["id"] for t in sched.MISSION_TEMPLATES}
    assert "subghz_sensor_hunt" in names
    assert "vhf_uhf_voice_survey" in names
    assert "ism_anomaly_audit" in names
    for t in sched.MISSION_TEMPLATES:
        kinds = [s["type"] for s in t["steps"]]
        assert "STEP_SWEEP_RANK" in kinds
        assert "STEP_BURST_CAPTURE" in kinds
        assert "STEP_AUTO_ANALYZE" in kinds


def test_mission_store_persists_findings(tmp_path: Path) -> None:
    store = sched.MissionStore(tmp_path / "missions.sqlite3")
    mid = store.create_mission("test", template_id="subghz_sensor_hunt")
    store.insert_finding(
        mid,
        frequency_hz=433_920_000,
        peak_rssi_db=-42.5,
        modulation="OOK",
        baud_est=2400.0,
        cs8_path=str(tmp_path / "a.cs8"),
    )
    rows = store.list_findings(mid)
    assert len(rows) == 1
    assert rows[0]["modulation"] == "OOK"
    report = store.summary_report(mid)
    assert report["finding_count"] == 1
    assert report["findings"][0]["frequency_hz"] == 433_920_000


@pytest.mark.asyncio
async def test_scheduler_runs_pipeline_with_injected_peaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolate_hardware
) -> None:
    from pp_dashboard import config as cfg

    monkeypatch.setattr(cfg.settings, "captures_dir", tmp_path)
    (tmp_path / "missions").mkdir(exist_ok=True)
    HardwareArbiter.reset_instance_for_tests()
    arbiter = await HardwareArbiter.get_instance()
    sched.MissionScheduler.reset_instance_for_tests()
    ms = await sched.MissionScheduler.get_instance(arbiter=arbiter)

    # Inject synthetic peaks so we never touch real sweep hardware.
    async def fake_sweep(self, step):  # noqa: ANN001
        self._ranked = [
            sched.RankedPeak(frequency_hz=433_920_000, peak_rssi_db=-41.0),
            sched.RankedPeak(frequency_hz=868_300_000, peak_rssi_db=-48.0),
        ]
        await self._emit("STEP_SWEEP_RANK", {"peaks": [asdict(p) for p in self._ranked]})

    async def fake_capture(self, step):  # noqa: ANN001
        out = []
        for p in self._ranked:
            path = tmp_path / f"burst_{p.frequency_hz}.cs8"
            # Minimal IQ for analyze path
            path.write_bytes(bytes([32, 0] * 4096))
            out.append({"frequency_hz": p.frequency_hz, "cs8_path": str(path), "peak_rssi_db": p.peak_rssi_db})
        self._captures = out
        await self._emit("STEP_BURST_CAPTURE", {"captures": out})

    async def fake_analyze(self, step):  # noqa: ANN001
        for cap in self._captures:
            self.store.insert_finding(
                self._mission_id,
                frequency_hz=cap["frequency_hz"],
                peak_rssi_db=cap["peak_rssi_db"],
                modulation="OOK",
                baud_est=2400.0,
                cs8_path=cap["cs8_path"],
            )
        await self._emit("STEP_AUTO_ANALYZE", {"analyzed": len(self._captures)})

    async def fake_export(self, step):  # noqa: ANN001
        await self._emit(
            "STEP_FREQMAN_EXPORT",
            {"exported": len(self.store.list_findings(self._mission_id))},
        )

    monkeypatch.setattr(sched.MissionScheduler, "_step_sweep_rank", fake_sweep)
    monkeypatch.setattr(sched.MissionScheduler, "_step_burst_capture", fake_capture)
    monkeypatch.setattr(sched.MissionScheduler, "_step_auto_analyze", fake_analyze)
    monkeypatch.setattr(sched.MissionScheduler, "_step_freqman_export", fake_export)

    mission = {
        "name": "unit-hunt",
        "template_id": "subghz_sensor_hunt",
        "sync_freqman_sd": False,
        "steps": [
            {"type": "STEP_SWEEP_RANK", "f_start_hz": 433e6, "f_stop_hz": 435e6, "duration_sec": 1, "top_k": 2, "threshold_db": -60},
            {"type": "STEP_BURST_CAPTURE", "duration_sec": 0.2, "sample_rate": 2_000_000},
            {"type": "STEP_AUTO_ANALYZE"},
            {"type": "STEP_FREQMAN_EXPORT", "category": "RECON"},
        ],
    }
    started = await ms.start(mission)
    assert started["ok"] is True
    # Wait for background task
    for _ in range(50):
        st = ms.status()
        if st["state"] in {"completed", "aborted", "error"}:
            break
        await __import__("asyncio").sleep(0.05)
    st = ms.status()
    assert st["state"] == "completed"
    assert st["report"]["finding_count"] == 2
    assert ms.running is False


@pytest.mark.asyncio
async def test_hardware_stop_aborts_mission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolate_hardware
) -> None:
    from pp_dashboard import config as cfg
    import asyncio

    monkeypatch.setattr(cfg.settings, "captures_dir", tmp_path)
    HardwareArbiter.reset_instance_for_tests()
    arbiter = await HardwareArbiter.get_instance()
    sched.MissionScheduler.reset_instance_for_tests()
    ms = await sched.MissionScheduler.get_instance(arbiter=arbiter)
    ms.wire_arbiter(arbiter)

    async def slow_sweep(self, step):  # noqa: ANN001
        for _ in range(40):
            if self._abort.is_set():
                return
            await asyncio.sleep(0.05)
        self._ranked = []

    monkeypatch.setattr(sched.MissionScheduler, "_step_sweep_rank", slow_sweep)
    monkeypatch.setattr(
        sched.MissionScheduler,
        "_step_burst_capture",
        lambda self, step: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        sched.MissionScheduler,
        "_step_auto_analyze",
        lambda self, step: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        sched.MissionScheduler,
        "_step_freqman_export",
        lambda self, step: asyncio.sleep(0),
    )

    await ms.start(
        {
            "name": "abort-me",
            "steps": [
                {"type": "STEP_SWEEP_RANK", "f_start_hz": 400e6, "f_stop_hz": 450e6, "duration_sec": 30, "top_k": 1, "threshold_db": -90},
                {"type": "STEP_BURST_CAPTURE", "duration_sec": 0.1, "sample_rate": 2_000_000},
                {"type": "STEP_AUTO_ANALYZE"},
                {"type": "STEP_FREQMAN_EXPORT"},
            ],
        }
    )
    assert ms.running
    await arbiter.stop_subprocess(reason="api_stop")
    await asyncio.sleep(0.2)
    assert ms.running is False
    assert ms.status()["state"] in {"aborted", "completed", "error"}


@pytest.mark.asyncio
async def test_mission_http_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pp_dashboard import config as cfg

    monkeypatch.setattr(cfg.settings, "captures_dir", tmp_path)
    HardwareArbiter.reset_instance_for_tests()
    sched.MissionScheduler.reset_instance_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        tpl = await client.get("/api/recon/mission/templates")
        assert tpl.status_code == 200
        assert len(tpl.json()["templates"]) >= 3
        st = await client.get("/api/recon/mission")
        assert st.status_code == 200
        assert "state" in st.json()
