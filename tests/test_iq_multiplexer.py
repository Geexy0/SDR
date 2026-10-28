"""Wideband IQMultiplexer — DDC, FFT waterfall rows, MULTIPLEX_RX state."""

from __future__ import annotations

import math

import numpy as np
import pytest

from pp_dashboard.hardware.iq_multiplexer import (
    DEFAULT_CENTER_HZ,
    DEFAULT_SAMPLE_RATE,
    FFT_SIZE,
    SUBGHZ_FIFO_PATH,
    VOICE_FIFO_PATH,
    complex_to_cs8,
    cs8_to_complex,
    ddc_shift_decimate,
    fft_power_row_db,
    make_ddc_taps,
)
from pp_dashboard.hardware.state import HardwareState
from pp_dashboard.recon.sweep_parser import powers_to_uint8


def test_multiplex_rx_state_exists() -> None:
    assert HardwareState.MULTIPLEX_RX.value == "MULTIPLEX_RX"
    assert HardwareState.MULTIPLEX_RX.is_busy


def test_defaults_cover_430_440() -> None:
    assert DEFAULT_CENTER_HZ == 435_000_000
    assert DEFAULT_SAMPLE_RATE == 10_000_000
    assert FFT_SIZE == 2048
    half = DEFAULT_SAMPLE_RATE / 2
    assert DEFAULT_CENTER_HZ - half == 430_000_000
    assert DEFAULT_CENTER_HZ + half == 440_000_000


def test_fifo_paths() -> None:
    assert str(SUBGHZ_FIFO_PATH) == "/tmp/pp_subghz.cs8"
    assert str(VOICE_FIFO_PATH) == "/tmp/pp_voice.cs8"


def test_cs8_roundtrip() -> None:
    z = np.array([0.5 + 0.25j, -0.25 + 0.5j], dtype=np.complex64)
    raw = complex_to_cs8(z)
    assert len(raw) == 4
    back = cs8_to_complex(raw)
    assert back.shape == (2,)
    assert abs(back[0].real - 0.5) < 0.02


def test_ddc_shifts_tone_to_baseband() -> None:
    fs = 10_000_000.0
    n = 20_000
    t = np.arange(n, dtype=np.float64) / fs
    # Tone at +2 MHz relative to center
    tone_hz = 2_000_000.0
    iq = np.exp(2j * math.pi * tone_hz * t).astype(np.complex64)
    taps = make_ddc_taps(decimation=5, taps=63)  # 10e6 → 2e6
    out = ddc_shift_decimate(
        iq,
        shift_hz=tone_hz,
        fs_in=fs,
        fs_out=2_000_000.0,
        taps=taps,
    )
    assert out.size > 100
    # After mixing by -tone_hz, energy should concentrate near DC
    spec = np.fft.fftshift(np.fft.fft(out[:2048] * np.hanning(2048)))
    mag = np.abs(spec)
    peak = int(np.argmax(mag))
    center = mag.size // 2
    assert abs(peak - center) <= 8


def test_ddc_rejects_bad_decimation() -> None:
    iq = np.zeros(1000, dtype=np.complex64)
    with pytest.raises(ValueError):
        ddc_shift_decimate(iq, shift_hz=0, fs_in=10e6, fs_out=3e6)


def test_fft_power_row_shape_and_peak() -> None:
    fs = 10_000_000.0
    n = FFT_SIZE
    t = np.arange(n, dtype=np.float64) / fs
    # Tone near +1 MHz → bin index after fftshift
    iq = (0.4 * np.exp(2j * math.pi * 1_000_000.0 * t)).astype(np.complex64)
    powers = fft_power_row_db(iq, fft_size=FFT_SIZE)
    assert powers.shape == (FFT_SIZE,)
    assert powers.dtype == np.float32
    peak = int(np.argmax(powers))
    # fftshifted: DC at center; +1 MHz ≈ center + 1e6/(fs/fft)
    expected = FFT_SIZE // 2 + int(round(1_000_000.0 / (fs / FFT_SIZE)))
    assert abs(peak - expected) <= 2
    u8 = powers_to_uint8(powers, -110.0, -20.0)
    assert len(u8) == FFT_SIZE
