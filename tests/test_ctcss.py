"""CTCSS / PL tone Goertzel bank."""

from __future__ import annotations

import numpy as np

from pp_dashboard.recon.ctcss import CTCSS_TONES_HZ, detect_ctcss_hz, goertzel_power


def test_ctcss_tone_table_has_fifty_standard_tones() -> None:
    assert len(CTCSS_TONES_HZ) == 50
    assert 88.5 in CTCSS_TONES_HZ
    assert 123.0 in CTCSS_TONES_HZ
    assert 67.0 == CTCSS_TONES_HZ[0]
    assert 254.1 == CTCSS_TONES_HZ[-1]


def test_goertzel_peaks_at_injected_tone() -> None:
    fs = 48_000.0
    tone = 123.0
    t = np.arange(4096, dtype=np.float64) / fs
    sig = 0.25 * np.sin(2 * np.pi * tone * t)
    p_hit = goertzel_power(sig, fs, tone)
    p_miss = goertzel_power(sig, fs, 100.0)
    assert p_hit > p_miss * 5


def test_detect_ctcss_hz_on_synthetic_pl() -> None:
    fs = 48_000.0
    tone = 88.5
    t = np.arange(8192, dtype=np.float64) / fs
    # Soft PL under noise floor of speech-ish band
    sig = 0.15 * np.sin(2 * np.pi * tone * t) + 0.02 * np.random.default_rng(0).normal(
        size=t.size
    )
    found = detect_ctcss_hz(sig, sample_rate=fs)
    assert found is not None
    assert abs(found - tone) < 0.6
