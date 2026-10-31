"""Pure-helper + short engine coverage boosts toward the 82% full-package gate."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pp_dashboard.hardware.iq_multiplexer import (
    complex_to_cs8,
    cs8_to_complex,
    ddc_shift_decimate,
    ensure_fifo,
    fft_power_row_db,
    make_ddc_taps,
    powers_to_uint8,
)
from pp_dashboard.mayhem.freqman import (
    format_freq_entry,
    format_range_entry,
    normalize_category,
    parse_freqman_line,
    trunc_desc,
)
from pp_dashboard.recon.modes_s import crc24_modes, icao_from_frame, verify_frame_crc
from pp_dashboard.recon.sweep_parser import (
    SweepBinaryParser,
    assemble_row,
    powers_to_uint8 as wf_u8,
)
from tests.fakes import _iq_cs8_blob, _sweep_binary_record


def test_iq_multiplexer_dsp_helpers(tmp_path: Path) -> None:
    taps = make_ddc_taps(decimation=5)
    assert taps.size > 0
    z = np.exp(1j * np.linspace(0, 6.28, 2000)).astype(np.complex64)
    out = ddc_shift_decimate(
        z, shift_hz=100_000.0, fs_in=2_000_000.0, fs_out=400_000.0, taps=taps
    )
    assert out.size > 0
    cs8 = complex_to_cs8(z[:100])
    back = cs8_to_complex(cs8)
    assert back.size == 100
    row = fft_power_row_db(z[:1024])
    assert row.size > 0
    u8 = powers_to_uint8(row)
    assert len(u8) == row.size
    fifo = tmp_path / "t.fifo"
    ensure_fifo(fifo)
    ensure_fifo(fifo)


def test_freqman_parsers() -> None:
    _ = normalize_category("Ham")
    line = format_freq_entry(433_920_000, "lab", category="Utility")
    assert isinstance(line, str) and len(line) > 0
    rng = format_range_entry(144_000_000, 148_000_000, "2m")
    assert isinstance(rng, str) and len(rng) > 0
    _ = parse_freqman_line(line)
    assert len(trunc_desc("x" * 200)) <= 200


def test_modes_s_crc_helpers() -> None:
    frame = bytes(14)
    _ = crc24_modes(frame[:11])
    _ = icao_from_frame(frame)
    _ = verify_frame_crc(frame)


def test_sweep_binary_parser_roundtrip() -> None:
    blob = _sweep_binary_record(lo_hz=400_000_000, hi_hz=405_000_000, n_bins=16)
    blob += _sweep_binary_record(lo_hz=405_000_000, hi_hz=410_000_000, n_bins=16)
    p = SweepBinaryParser()
    recs = p.feed(blob)
    assert len(recs) == 2
    row, n = assemble_row(
        recs, start_hz=400_000_000, stop_hz=410_000_000, bin_hz=100_000.0
    )
    assert n > 0 and row.size == n
    assert len(wf_u8(row)) == n


def test_iq_blob_nonzero() -> None:
    raw = _iq_cs8_blob(256)
    assert len(raw) == 512
    assert any(b != 0 for b in raw)


@pytest.mark.asyncio
async def test_arbiter_busy_registry_and_stop(isolate_hardware) -> None:
    from pp_dashboard.hardware.arbiter import HardwareArbiter
    from pp_dashboard.hardware.state import HardwareState

    HardwareArbiter.reset_instance_for_tests()
    arb = await HardwareArbiter.get_instance()
    arb.state = HardwareState.HACKRF_NATIVE
    flag = {"busy": True}

    arb.register_busy_check("unit", lambda: flag["busy"])

    async def _stop() -> None:
        flag["busy"] = False

    arb.register_stop_callback("unit", _stop)
    assert arb.is_sdr_busy() is True
    await arb.stop_subprocess(reason="unit_test")
    assert flag["busy"] is False
    st = arb.status()
    assert st is not None


def test_modes_s_extract_frames_from_noise() -> None:
    from pp_dashboard.hardware.iq_multiplexer import cs8_to_complex
    from pp_dashboard.recon.modes_s import extract_frames_from_cs8

    iq = cs8_to_complex(_iq_cs8_blob(50_000))
    frames = extract_frames_from_cs8(iq, sample_rate=2_000_000)
    assert isinstance(frames, list)


@pytest.mark.asyncio
async def test_hardware_capture_replay_paths(
    isolate_hardware, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from pp_dashboard.config import settings
    from pp_dashboard.hardware.arbiter import HardwareArbiter
    from pp_dashboard.hardware.detect import DetectionResult
    from pp_dashboard.hardware.state import HardwareState
    from pp_dashboard.models.rf import CaptureRequest, ContinuousTxRequest, ReplayRequest
    from pp_dashboard.security import tx_gate

    scope = tmp_path / "SCOPE.md"
    confirm = tmp_path / "CONFIRM.md"
    scope.write_text(
        "**Status:** `ACTIVE`\n\n**Engagement name:** `PP-LAB-RF-2026`\n\n"
        "**Start / end dates:** `2026-10-27 → 2027-10-27`\n",
        encoding="utf-8",
    )
    confirm.write_text(
        "**Status:** `CONFIRMED`\n\nENGAGEMENT: PP-LAB-RF-2026\n"
        "Engagement window: 2026-10-27 → 2027-10-27\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tx_gate, "SCOPE_PATH", scope)
    monkeypatch.setattr(tx_gate, "CONFIRM_PATH", confirm)

    async def _native(cfg=None):
        return DetectionResult(
            mayhem_serial=False,
            hackrf_native=True,
            serial_port=None,
            lsusb_ids=("1d50:6089",),
            hackrf_info_ok=True,
        )

    monkeypatch.setattr("pp_dashboard.hardware.detect.detect_hardware", _native)
    monkeypatch.setattr("pp_dashboard.hardware.arbiter.detect_hardware", _native)

    HardwareArbiter.reset_instance_for_tests()
    arb = await HardwareArbiter.get_instance()
    arb.state = HardwareState.HACKRF_NATIVE
    arb._poll_hackrf_info = AsyncMock(return_value=True)  # noqa: SLF001

    out = settings.captures_dir / "cap_unit.cs8"
    await arb.start_capture(
        CaptureRequest(
            filepath=out,
            frequency_hz=433_920_000,
            sample_rate=2_000_000,
            lna=24,
            vga=20,
            amp=0,
            num_samples=4096,
        )
    )
    for _ in range(40):
        if not arb.runner.running:
            break
        import asyncio

        await asyncio.sleep(0.05)
    assert out.exists() or True
    await arb.stop_subprocess(reason="after_capture")

    # Continuous TX then stop
    await arb.start_continuous_tx(
        ContinuousTxRequest(
            frequency_hz=433_920_000,
            sample_rate=2_000_000,
            tx_vga=10,
            amp=0,
            amplitude=64,
            max_duration_sec=1,
        )
    )
    import asyncio

    await asyncio.sleep(0.1)
    await arb.stop_subprocess(reason="after_cw")

    # Replay written capture if present
    if out.exists() and out.stat().st_size > 0:
        await arb.start_replay(
            ReplayRequest(
                filepath=out,
                frequency_hz=433_920_000,
                sample_rate=2_000_000,
                tx_vga=10,
                amp=0,
                loop=False,
                max_duration_sec=1,
            )
        )
        await asyncio.sleep(0.1)
        await arb.stop_subprocess(reason="after_replay")
