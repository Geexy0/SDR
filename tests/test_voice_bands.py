"""Voice band plan + demod helpers."""

from __future__ import annotations

import numpy as np
import pytest

from pp_dashboard.recon.voice_bands import VOICE_BANDS, resolve_bands
from pp_dashboard.recon.ctcss import goertzel_power
from pp_dashboard.recon.voice_recon import (
    _demod_iq,
    _demod_iq_to_pcm16,
    _iq_rms_dbfs,
    _snap_lna,
    _snap_vga,
)


def test_voice_bands_include_pmr_and_exclude_dispatch_ids() -> None:
    ids = {b.id for b in VOICE_BANDS}
    assert "pmr446" in ids
    assert "marine_vhf" in ids
    assert "airband" in ids
    # SCOPE forbids emergency/police dispatch aids as recon targets.
    assert "police" not in ids
    assert "emergency_dispatch" not in ids


def test_resolve_bands_default_and_unknown() -> None:
    defaults = resolve_bands(None)
    assert any(b.id == "pmr446" for b in defaults)
    with pytest.raises(ValueError, match="unknown"):
        resolve_bands(["not_a_band"])


def test_gain_snaps() -> None:
    assert _snap_lna(17) == 16
    assert _snap_lna(39) == 40
    assert _snap_vga(21) == 20
    assert _snap_vga(61) == 60
    assert _snap_vga(62) == 62


def test_iq_rms_and_demod() -> None:
    # Tone-ish IQ
    t = np.linspace(0, 2 * np.pi, 2048, dtype=np.float32)
    i = (np.cos(t) * 40).astype(np.int8)
    q = (np.sin(t) * 40).astype(np.int8)
    inter = np.empty(i.size * 2, dtype=np.int8)
    inter[0::2] = i
    inter[1::2] = q
    raw = inter.tobytes()
    db = _iq_rms_dbfs(raw)
    assert db > -40
    pcm = _demod_iq_to_pcm16(raw, mode="nbfm", sample_rate=2_000_000)
    assert len(pcm) > 0
    assert len(pcm) % 2 == 0


def test_demod_exposes_discriminator_for_ctcss() -> None:
    """FM disc before speech HPF must retain a synthetic PL tone."""
    fs_iq = 2_000_000
    audio_hz = 48_000
    pl = 88.5
    delta_hz = 400.0
    n = 400_000
    t = np.arange(n, dtype=np.float64) / fs_iq
    # Classic narrow FM: phase = -(Δf/fm) cos(2π fm t)
    phase = -(delta_hz / pl) * np.cos(2 * np.pi * pl * t)
    i = (np.cos(phase) * 100).astype(np.int8)
    q = (np.sin(phase) * 100).astype(np.int8)
    inter = np.empty(n * 2, dtype=np.int8)
    inter[0::2] = i
    inter[1::2] = q
    pcm, disc = _demod_iq(inter.tobytes(), mode="nbfm", sample_rate=fs_iq, audio_hz=audio_hz)
    assert len(pcm) > 0
    assert disc is not None and disc.size > 1000
    # Pre-HPF discriminator must keep energy at PL (exact tone ID covered in test_ctcss)
    p_pl = goertzel_power(disc[-8192:], float(audio_hz), pl)
    p_far = goertzel_power(disc[-8192:], float(audio_hz), 203.5)
    assert p_pl > p_far * 4
