"""RX-only GNSS L1 interference sample (HackRF capture → power stats)."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from pp_dashboard.config import settings
from pp_dashboard.geoloc.service import GeolocService
from pp_dashboard.hardware.arbiter import HardwareArbiter
from pp_dashboard.models.rf import CaptureRequest, TelemetryEvent

log = logging.getLogger(__name__)

GPS_L1_HZ = 1_575_420_000
DEFAULT_SAMPLE_RATE = 2_000_000
DEFAULT_DURATION_S = 0.25
# Heuristic: if peak is this many dB above mean floor → elevated / possible jam
JAM_MARGIN_DB = 18.0


def iq_power_stats(path: Path) -> dict[str, float]:
    raw = np.fromfile(path, dtype=np.int8)
    if raw.size < 64:
        return {"peak_db": -120.0, "mean_db": -120.0, "noise_floor_db": -120.0}
    # Interleaved I,Q → magnitude
    iq = raw.astype(np.float32).reshape(-1, 2)
    mag2 = iq[:, 0] ** 2 + iq[:, 1] ** 2
    # Avoid log(0)
    db = 10.0 * np.log10(mag2 + 1e-6)
    peak = float(np.percentile(db, 99))
    mean = float(np.mean(db))
    floor = float(np.percentile(db, 20))
    return {"peak_db": peak, "mean_db": mean, "noise_floor_db": floor}


async def sample_l1_interference(
    arbiter: HardwareArbiter,
    *,
    duration_sec: float = DEFAULT_DURATION_S,
    lna: int = 24,
    vga: int = 20,
) -> dict[str, Any]:
    """Capture a short L1 window and classify elevated energy (RX only)."""
    arbiter.assert_sdr_idle()
    out_dir = settings.captures_dir / "geoloc"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"gnss_l1_{int(time.time())}.cs8"
    n = max(8192, int(DEFAULT_SAMPLE_RATE * max(0.05, min(duration_sec, 2.0))))
    await arbiter.start_capture(
        CaptureRequest(
            filepath=path,
            frequency_hz=GPS_L1_HZ,
            sample_rate=DEFAULT_SAMPLE_RATE,
            lna=lna,
            vga=vga,
            amp=0,
            num_samples=n,
            max_duration_sec=max(duration_sec + 2.0, 3.0),
        )
    )
    while arbiter.runner.running:
        await asyncio.sleep(0.05)

    stats = iq_power_stats(path) if path.is_file() else {
        "peak_db": -120.0,
        "mean_db": -120.0,
        "noise_floor_db": -120.0,
    }
    jammed = (stats["peak_db"] - stats["noise_floor_db"]) >= JAM_MARGIN_DB and stats[
        "peak_db"
    ] > -35.0
    notes = "elevated_l1_energy" if jammed else "nominal"
    svc = await GeolocService.get_instance()
    sample_id = svc.store.add_gnss_sample(
        center_hz=GPS_L1_HZ,
        peak_db=stats["peak_db"],
        mean_db=stats["mean_db"],
        noise_floor_db=stats["noise_floor_db"],
        jammed=jammed,
        notes=notes,
    )
    result = {
        "ok": True,
        "sample_id": sample_id,
        "center_hz": GPS_L1_HZ,
        "filepath": str(path),
        "jammed": jammed,
        "notes": notes,
        **stats,
    }
    await arbiter._fanout_telemetry(  # noqa: SLF001
        TelemetryEvent(
            type="status",
            state=arbiter.state,
            message="gnss_l1_sample",
            metrics=result,
        )
    )
    return result
