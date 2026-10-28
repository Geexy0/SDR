"""RF Pydantic validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pp_dashboard.config import settings
from pp_dashboard.models.rf import CaptureRequest, ContinuousTxRequest, ReplayRequest, SweepRequest


@pytest.fixture(autouse=True)
def _captures_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "captures_dir", tmp_path)
    return tmp_path


def test_capture_accepts_valid_gains(tmp_path: Path) -> None:
    req = CaptureRequest(
        filepath=tmp_path / "out.cs8",
        frequency_hz=433_920_000,
        sample_rate=10_000_000,
        lna=16,
        vga=20,
        amp=0,
        num_samples=2_000_000,
    )
    assert req.lna == 16
    assert req.vga == 20


@pytest.mark.parametrize("lna", [1, 7, 41])
def test_capture_rejects_bad_lna(tmp_path: Path, lna: int) -> None:
    with pytest.raises(ValidationError):
        CaptureRequest(
            filepath=tmp_path / "out.cs8",
            frequency_hz=100_000_000,
            lna=lna,
            vga=20,
            num_samples=1000,
        )


@pytest.mark.parametrize("vga", [1, 61, 63])
def test_capture_rejects_bad_rx_vga(tmp_path: Path, vga: int) -> None:
    with pytest.raises(ValidationError):
        CaptureRequest(
            filepath=tmp_path / "out.cs8",
            frequency_hz=100_000_000,
            lna=8,
            vga=vga,
            num_samples=1000,
        )


def test_freq_bounds() -> None:
    with pytest.raises(ValidationError):
        ContinuousTxRequest(
            frequency_hz=999_999,
            amplitude=64,
            tx_vga=10,
        )
    with pytest.raises(ValidationError):
        ContinuousTxRequest(
            frequency_hz=6_000_000_001,
            amplitude=64,
            tx_vga=10,
        )


def test_tx_vga_bounds() -> None:
    ContinuousTxRequest(frequency_hz=2_400_000_000, amplitude=10, tx_vga=47)
    with pytest.raises(ValidationError):
        ContinuousTxRequest(frequency_hz=2_400_000_000, amplitude=10, tx_vga=48)


def test_replay_requires_cs8(tmp_path: Path) -> None:
    bad = tmp_path / "x.iq"
    bad.write_bytes(b"\x00\x00")
    with pytest.raises(ValidationError):
        ReplayRequest(
            filepath=bad,
            frequency_hz=433_920_000,
            tx_vga=20,
        )
    good = tmp_path / "x.cs8"
    good.write_bytes(b"\x00\x00")
    ReplayRequest(filepath=good, frequency_hz=433_920_000, tx_vga=20)


def test_capture_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        CaptureRequest(
            filepath=Path("/tmp/evil.cs8"),
            frequency_hz=100_000_000,
            lna=8,
            vga=20,
            num_samples=1000,
        )


def test_sweep_range() -> None:
    SweepRequest(min_mhz=100, max_mhz=200, bin_width_hz=100_000, lna=16, vga=20)
    with pytest.raises(ValidationError):
        SweepRequest(min_mhz=200, max_mhz=100, bin_width_hz=100_000, lna=16, vga=20)
