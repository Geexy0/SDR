"""VOX voice capture SQLite + WAV helpers."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

from pp_dashboard.recon.voice_recordings import (
    VoiceRecordingStore,
    VoxSpooler,
    write_wav_s16le,
)


def test_write_wav_s16le_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "t.wav"
    # 100 ms silence + tone-ish
    pcm = (b"\x00\x10" * 2400)
    write_wav_s16le(path, pcm, sample_rate=48_000)
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 48_000
        assert w.getnframes() == 2400


def test_recording_store_insert_list(tmp_path: Path) -> None:
    db = tmp_path / "voice.sqlite3"
    voice_dir = tmp_path / "voice"
    store = VoiceRecordingStore(db, voice_dir)
    wav = voice_dir / "2026-01-01T00-00-00_ham_2m_145500000.wav"
    write_wav_s16le(wav, b"\x00\x00" * 4800, sample_rate=48_000)
    row = store.insert(
        band_name="ham_2m",
        freq_hz=145_500_000,
        peak_rssi_db=-42.5,
        ctcss_hz=88.5,
        duration_sec=0.1,
        filepath=wav,
    )
    assert row["id"] >= 1
    assert abs(row["ctcss_hz"] - 88.5) < 1e-6
    listed = store.list_recent(limit=10)
    assert len(listed) == 1
    assert listed[0]["band_name"] == "ham_2m"
    got = store.get(row["id"])
    assert got is not None
    assert Path(got["filepath"]).name == wav.name


def test_vox_spooler_opens_after_500ms(tmp_path: Path) -> None:
    store = VoiceRecordingStore(tmp_path / "v.sqlite3", tmp_path / "voice")
    vox = VoxSpooler(store, open_hold_sec=0.5, close_hold_sec=0.3)
    vox.configure(band_name="pmr446", freq_hz=446_006_250)
    pcm = b"\x00\x10" * 4800  # 100 ms @ 48k
    t0 = 1000.0
    assert vox.feed(rssi_db=-40, squelch_db=-50, pcm=pcm, now=t0) is None
    assert not vox.recording
    assert vox.feed(rssi_db=-40, squelch_db=-50, pcm=pcm, now=t0 + 0.6) is None
    assert vox.recording
    # Drop below squelch and hold to finalize
    assert vox.feed(rssi_db=-60, squelch_db=-50, pcm=pcm, now=t0 + 0.7) is None
    row = vox.feed(rssi_db=-60, squelch_db=-50, pcm=pcm, now=t0 + 1.1)
    assert row is not None
    assert row["band_name"] == "pmr446"
    assert Path(row["filepath"]).is_file()
