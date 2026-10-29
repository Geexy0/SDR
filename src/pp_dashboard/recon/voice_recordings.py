"""SQLite + WAV spool for Auto Voice Recon VOX captures."""

from __future__ import annotations

import sqlite3
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_wav_s16le(path: Path, pcm: bytes, *, sample_rate: int = 48_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)


class VoiceRecordingStore:
    def __init__(self, db_path: Path, voice_dir: Path) -> None:
        self.db_path = db_path
        self.voice_dir = voice_dir
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.voice_dir.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_recordings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    band_name TEXT NOT NULL,
                    freq_hz INTEGER NOT NULL,
                    peak_rssi_db REAL,
                    ctcss_hz REAL,
                    duration_sec REAL NOT NULL,
                    filepath TEXT NOT NULL,
                    lat REAL,
                    lon REAL
                )
                """
            )
            cols = {r[1] for r in conn.execute("PRAGMA table_info(voice_recordings)").fetchall()}
            if "lat" not in cols:
                conn.execute("ALTER TABLE voice_recordings ADD COLUMN lat REAL")
            if "lon" not in cols:
                conn.execute("ALTER TABLE voice_recordings ADD COLUMN lon REAL")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_voice_ts ON voice_recordings(timestamp DESC)"
            )
            conn.commit()

    def insert(
        self,
        *,
        band_name: str,
        freq_hz: int,
        peak_rssi_db: float | None,
        ctcss_hz: float | None,
        duration_sec: float,
        filepath: Path,
        timestamp: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
    ) -> dict[str, Any]:
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        if lat is None or lon is None:
            try:
                from pp_dashboard.geoloc.service import GeolocService

                svc = GeolocService._instance  # noqa: SLF001
                if svc is not None:
                    op = svc.store.get_operator()
                    if op is not None:
                        lat = float(op["lat"])
                        lon = float(op["lon"])
            except Exception:
                pass
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO voice_recordings
                  (timestamp, band_name, freq_hz, peak_rssi_db, ctcss_hz, duration_sec,
                   filepath, lat, lon)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    band_name,
                    int(freq_hz),
                    peak_rssi_db,
                    ctcss_hz,
                    float(duration_sec),
                    str(filepath),
                    lat,
                    lon,
                ),
            )
            conn.commit()
            rid = int(cur.lastrowid)
        if lat is not None and lon is not None:
            try:
                from pp_dashboard.geoloc.service import stamp_rf_hit

                stamp_rf_hit(
                    kind="voice",
                    frequency_hz=int(freq_hz),
                    rssi_db=peak_rssi_db,
                    label=band_name,
                    ref_id=str(rid),
                    lat=lat,
                    lon=lon,
                )
            except Exception:
                pass
        row = self.get(rid)
        assert row is not None
        return row

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, band_name, freq_hz, peak_rssi_db, ctcss_hz,
                       duration_sec, filepath, lat, lon
                FROM voice_recordings
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, recording_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            r = conn.execute(
                """
                SELECT id, timestamp, band_name, freq_hz, peak_rssi_db, ctcss_hz,
                       duration_sec, filepath, lat, lon
                FROM voice_recordings WHERE id = ?
                """,
                (int(recording_id),),
            ).fetchone()
        return None if r is None else self._row(r)

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        keys = set(r.keys())
        return {
            "id": int(r["id"]),
            "timestamp": str(r["timestamp"]),
            "band_name": str(r["band_name"]),
            "freq_hz": int(r["freq_hz"]),
            "peak_rssi_db": r["peak_rssi_db"],
            "ctcss_hz": r["ctcss_hz"],
            "duration_sec": float(r["duration_sec"]),
            "filepath": str(r["filepath"]),
            "lat": r["lat"] if "lat" in keys else None,
            "lon": r["lon"] if "lon" in keys else None,
        }

    def make_capture_path(self, *, band_name: str, freq_hz: int) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe_band = "".join(c if c.isalnum() or c in "-_" else "_" for c in band_name)[:40]
        return self.voice_dir / f"{stamp}_{safe_band}_{int(freq_hz)}.wav"


class VoxSpooler:
    """
    Start writing PCM after RSSI stays above squelch for open_hold_sec;
    finalize after RSSI stays below for close_hold_sec.
    """

    def __init__(
        self,
        store: VoiceRecordingStore,
        *,
        sample_rate: int = 48_000,
        open_hold_sec: float = 0.5,
        close_hold_sec: float = 0.75,
    ) -> None:
        self.store = store
        self.sample_rate = sample_rate
        self.open_hold_sec = open_hold_sec
        self.close_hold_sec = close_hold_sec
        self._above_since: float | None = None
        self._below_since: float | None = None
        self._recording = False
        self._buf = bytearray()
        self._peak_rssi: float | None = None
        self._ctcss_hz: float | None = None
        self._band = ""
        self._freq_hz = 0
        self._started_mono: float | None = None

    @property
    def recording(self) -> bool:
        return self._recording

    def configure(self, *, band_name: str, freq_hz: int) -> None:
        self._band = band_name
        self._freq_hz = int(freq_hz)

    def note_ctcss(self, hz: float | None) -> None:
        if hz is not None:
            self._ctcss_hz = hz

    def feed(
        self,
        *,
        rssi_db: float,
        squelch_db: float,
        pcm: bytes,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Return a finalized recording row when a segment closes, else None."""
        t = time.monotonic() if now is None else now
        above = rssi_db > squelch_db
        if above:
            self._below_since = None
            if self._above_since is None:
                self._above_since = t
            if not self._recording and (t - self._above_since) >= self.open_hold_sec:
                self._recording = True
                self._buf = bytearray()
                self._peak_rssi = rssi_db
                self._started_mono = t
            if self._recording:
                if pcm:
                    self._buf.extend(pcm)
                if self._peak_rssi is None or rssi_db > self._peak_rssi:
                    self._peak_rssi = rssi_db
            return None

        self._above_since = None
        if not self._recording:
            return None
        if self._below_since is None:
            self._below_since = t
        if (t - self._below_since) < self.close_hold_sec:
            if pcm:
                self._buf.extend(pcm)
            return None
        return self._finalize()

    def force_finalize(self) -> dict[str, Any] | None:
        if not self._recording:
            return None
        return self._finalize()

    def _finalize(self) -> dict[str, Any] | None:
        self._recording = False
        self._above_since = None
        self._below_since = None
        pcm = bytes(self._buf)
        self._buf = bytearray()
        started = self._started_mono
        self._started_mono = None
        if len(pcm) < self.sample_rate // 20:  # <50 ms of s16 mono
            return None
        duration = len(pcm) / 2 / float(self.sample_rate)
        _ = started
        path = self.store.make_capture_path(band_name=self._band, freq_hz=self._freq_hz)
        write_wav_s16le(path, pcm, sample_rate=self.sample_rate)
        row = self.store.insert(
            band_name=self._band,
            freq_hz=self._freq_hz,
            peak_rssi_db=self._peak_rssi,
            ctcss_hz=self._ctcss_hz,
            duration_sec=duration,
            filepath=path,
        )
        self._peak_rssi = None
        self._ctcss_hz = None
        return row
