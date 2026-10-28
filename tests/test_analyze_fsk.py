"""Multi-mode IQ analysis: envelope, FSK freq deviation, phase, constellation."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pp_dashboard.subghz.analyze import (
    analyze_cs8,
    downsample_trace,
    estimate_fsk_params,
    find_bit_preambles,
    instantaneous_frequency,
    instantaneous_phase,
    slice_fsk_bits,
)


def _write_fsk_cs8(path: Path, *, fs: int = 2_000_000, baud: float = 10_000.0) -> None:
    """2-FSK IQ: ±25 kHz around carrier, NRZ alternating then AAAA-like."""
    n_bits = 64
    samples_per_bit = int(fs / baud)
    # Preamble 0xAAAA = 16 bits alternating starting with 1
    bits = []
    for _ in range(4):
        bits.extend([1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0])  # AAAA
    bits = bits[:n_bits]
    n = samples_per_bit * len(bits)
    t = np.arange(n, dtype=np.float64) / fs
    # Instantaneous freq track
    f_inst = np.empty(n, dtype=np.float64)
    for i, b in enumerate(bits):
        a = i * samples_per_bit
        f_inst[a : a + samples_per_bit] = 25_000.0 if b else -25_000.0
    phase = 2 * np.pi * np.cumsum(f_inst) / fs
    i = (np.cos(phase) * 80).astype(np.int8)
    q = (np.sin(phase) * 80).astype(np.int8)
    inter = np.empty(n * 2, dtype=np.int8)
    inter[0::2] = i
    inter[1::2] = q
    path.write_bytes(inter.tobytes())


def test_instantaneous_frequency_on_tone() -> None:
    fs = 200_000.0
    f0 = 12_500.0
    n = 8000
    t = np.arange(n) / fs
    iq = np.exp(1j * 2 * np.pi * f0 * t).astype(np.complex64)
    freq = instantaneous_frequency(iq, sample_rate=int(fs))
    assert freq.size == n
    assert abs(float(np.median(freq[100:-100])) - f0) < 500.0


def test_instantaneous_phase_unwraps_quadrant() -> None:
    iq = np.array([1 + 0j, 0 + 1j, -1 + 0j, 0 - 1j], dtype=np.complex64)
    phi = instantaneous_phase(iq)
    assert phi.shape == (4,)
    assert abs(phi[1] - np.pi / 2) < 1e-3


def test_estimate_fsk_deviation_and_baud(tmp_path: Path) -> None:
    path = tmp_path / "fsk.cs8"
    fs = 2_000_000
    _write_fsk_cs8(path, fs=fs, baud=10_000.0)
    result = analyze_cs8(path, sample_rate=fs)
    assert result.freq_dev_ds is not None and len(result.freq_dev_ds) > 100
    assert result.phase_ds is not None and len(result.phase_ds) == len(result.freq_dev_ds)
    assert result.envelope_ds is not None
    fsk = result.fsk
    assert fsk is not None
    assert abs(fsk.deviation_hz - 25_000.0) < 5_000.0
    assert abs(fsk.baud_est - 10_000.0) / 10_000.0 < 0.35
    assert fsk.bits
    assert any(p["pattern"] == "0xAAAA" for p in fsk.preambles)


def test_slice_and_preamble_helpers() -> None:
    # Synthetic mean-centered deviation at 4 samples/bit
    bits_true = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0]
    f = np.repeat([10.0 if b else -10.0 for b in bits_true], 4)
    bits = slice_fsk_bits(f, threshold=0.0, samples_per_bit=4)
    assert bits[:16] == "".join(str(b) for b in bits_true)
    found = find_bit_preambles(bits)
    assert any(x["pattern"] == "0xAAAA" for x in found)


def test_downsample_trace_peak_hold() -> None:
    x = np.linspace(0, 1, 10_000, dtype=np.float32)
    y = downsample_trace(x, max_points=100)
    assert len(y) == 100
