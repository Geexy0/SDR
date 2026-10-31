"""RX-only GNSS L1 interference sample + spectral integrity analyze."""

from __future__ import annotations

import asyncio
import logging
import math
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
JAM_MARGIN_DB = 18.0
CA_MAINLOBE_HZ = 1_023_000.0


def _load_iq_cs8(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.int8)
    if raw.size < 64:
        return np.zeros(0, dtype=np.complex64)
    iq = raw.astype(np.float32).reshape(-1, 2)
    return (iq[:, 0] + 1j * iq[:, 1]).astype(np.complex64)


def iq_power_stats(path: Path) -> dict[str, float]:
    z = _load_iq_cs8(path)
    if z.size < 32:
        return {"peak_db": -120.0, "mean_db": -120.0, "noise_floor_db": -120.0}
    mag2 = (z.real * z.real + z.imag * z.imag).astype(np.float64)
    db = 10.0 * np.log10(mag2 + 1e-6)
    return {
        "peak_db": float(np.percentile(db, 99)),
        "mean_db": float(np.mean(db)),
        "noise_floor_db": float(np.percentile(db, 20)),
    }


def analyze_l1_capture(
    path: Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    baseline_noise_db: float | None = None,
) -> dict[str, Any]:
    """Spectral integrity metrics + alert classification for a GNSS L1 IQ capture."""
    z = _load_iq_cs8(path)
    if z.size < 256:
        return {
            "ok": False,
            "classification": "NOMINAL",
            "delta_n0_db": 0.0,
            "papr_db": 0.0,
            "spectral_kurtosis": 0.0,
            "carrier_offset_hz": 0.0,
            "carrier_peak_db": -120.0,
            "message": "capture too short",
        }

    mag2 = np.abs(z) ** 2
    peak_p = float(np.max(mag2) + 1e-20)
    avg_p = float(np.mean(mag2) + 1e-20)
    papr_db = 10.0 * math.log10(peak_p / avg_p)

    nfft = int(2 ** np.floor(np.log2(min(z.size, 65536))))
    win = np.hanning(nfft).astype(np.float64)
    seg = z[:nfft].astype(np.complex128) * win
    spec = np.fft.fftshift(np.fft.fft(seg))
    psd = (np.abs(spec) ** 2).astype(np.float64) + 1e-20
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / float(sample_rate)))

    outer = np.abs(freqs) > CA_MAINLOBE_HZ
    if np.count_nonzero(outer) < 16:
        outer = np.abs(freqs) > 0.4 * (sample_rate / 2)
    noise_lin = float(np.median(psd[outer]))
    noise_db = 10.0 * math.log10(noise_lin)
    baseline = float(baseline_noise_db) if baseline_noise_db is not None else noise_db - 0.5
    delta_n0 = noise_db - baseline

    mu = float(np.mean(psd))
    centered = psd - mu
    m2 = float(np.mean(centered**2)) + 1e-30
    m4 = float(np.mean(centered**4))
    kurtosis = m4 / (m2 * m2) - 3.0

    dc_mask = np.abs(freqs) < 25_000.0
    if not np.any(dc_mask):
        dc_mask = np.abs(freqs) == np.min(np.abs(freqs))
    carrier_peak_lin = float(np.max(psd[dc_mask]))
    carrier_peak_db = 10.0 * math.log10(carrier_peak_lin)
    carrier_freqs = freqs[dc_mask]
    carrier_bin = int(np.argmax(psd[dc_mask]))
    carrier_offset_hz = float(carrier_freqs[carrier_bin]) if carrier_freqs.size else 0.0

    gpeak_i = int(np.argmax(psd))
    global_offset_hz = float(freqs[gpeak_i])
    peak_to_noise_db = 10.0 * math.log10(float(psd[gpeak_i]) / noise_lin)

    lobe = np.abs(freqs) <= CA_MAINLOBE_HZ
    lobe_elev = float(np.mean(psd[lobe] > (noise_lin * 10 ** (6.0 / 10.0))))

    classification = "NOMINAL"
    if delta_n0 >= 8.0 and kurtosis < 5.0:
        classification = "WIDEBAND_NOISE_ELEVATION"
    elif peak_to_noise_db >= 18.0 and abs(global_offset_hz) < 50_000.0 and papr_db >= 8.0:
        classification = "CW_TONE_INGRESS"
    elif lobe_elev > 0.35 and 2.0 < kurtosis < 40.0 and papr_db < 14.0:
        classification = "CHIRP_SWEEP_DETECTED"
    elif peak_to_noise_db >= 15.0 and papr_db >= 6.0:
        classification = "CW_TONE_INGRESS"

    return {
        "ok": True,
        "classification": classification,
        "delta_n0_db": float(delta_n0),
        "papr_db": float(papr_db),
        "spectral_kurtosis": float(kurtosis),
        "carrier_offset_hz": float(carrier_offset_hz),
        "global_peak_offset_hz": float(global_offset_hz),
        "carrier_peak_db": float(carrier_peak_db),
        "noise_floor_db": float(noise_db),
        "baseline_noise_db": float(baseline),
        "peak_to_noise_db": float(peak_to_noise_db),
        "center_hz": GPS_L1_HZ,
        "sample_rate": int(sample_rate),
        "message": classification.lower(),
    }


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

    stats = (
        iq_power_stats(path)
        if path.is_file()
        else {"peak_db": -120.0, "mean_db": -120.0, "noise_floor_db": -120.0}
    )
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


async def analyze_l1_interference(
    arbiter: HardwareArbiter,
    *,
    duration_sec: float = DEFAULT_DURATION_S,
    lna: int = 24,
    vga: int = 20,
    baseline_noise_db: float | None = None,
    filepath: Path | None = None,
    operator_lat: float | None = None,
    operator_lon: float | None = None,
) -> dict[str, Any]:
    """Capture (or reuse path) and run full L1 integrity classification."""
    if filepath is None:
        # Fresh capture then analyze
        sample = await sample_l1_interference(
            arbiter, duration_sec=duration_sec, lna=lna, vga=vga
        )
        filepath = Path(str(sample["filepath"]))
        base_metrics = sample
    else:
        base_metrics = {}

    analysis = analyze_l1_capture(
        Path(filepath),
        sample_rate=DEFAULT_SAMPLE_RATE,
        baseline_noise_db=baseline_noise_db,
    )
    geo_marker = None
    if operator_lat is not None and operator_lon is not None:
        geo_marker = {
            "lat": float(operator_lat),
            "lon": float(operator_lon),
            "classification": analysis["classification"],
            "ts": time.time(),
        }
        try:
            from pp_dashboard.geoloc.service import stamp_rf_hit

            stamp_rf_hit(
                kind="gnss_alert",
                frequency_hz=GPS_L1_HZ,
                rssi_db=analysis.get("carrier_peak_db"),
                label=str(analysis["classification"]),
                lat=float(operator_lat),
                lon=float(operator_lon),
                notes=analysis.get("message"),
            )
        except Exception:
            log.debug("gnss alert stamp skipped", exc_info=True)

    result = {
        **base_metrics,
        **analysis,
        "geo_marker": geo_marker,
        "filepath": str(filepath),
    }
    await arbiter._fanout_telemetry(  # noqa: SLF001
        TelemetryEvent(
            type="status",
            state=arbiter.state,
            message="gnss_l1_analyze",
            metrics={
                "classification": analysis["classification"],
                "delta_n0_db": analysis.get("delta_n0_db"),
                "papr_db": analysis.get("papr_db"),
                "geo_marker": geo_marker,
            },
        )
    )
    return result
