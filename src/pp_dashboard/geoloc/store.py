"""SQLite operator GPS, RF breadcrumbs, and LOB store."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class GeolocStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS operator (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    accuracy_m REAL,
                    alt_m REAL,
                    source TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS breadcrumbs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    frequency_hz INTEGER,
                    rssi_db REAL,
                    label TEXT,
                    ref_id TEXT,
                    notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_bc_ts ON breadcrumbs(ts DESC);
                CREATE INDEX IF NOT EXISTS idx_bc_kind ON breadcrumbs(kind);
                CREATE TABLE IF NOT EXISTS lobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    bearing_deg REAL NOT NULL,
                    label TEXT,
                    frequency_hz INTEGER
                );
                CREATE TABLE IF NOT EXISTS gnss_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    center_hz INTEGER NOT NULL,
                    peak_db REAL,
                    mean_db REAL,
                    noise_floor_db REAL,
                    jammed INTEGER NOT NULL DEFAULT 0,
                    notes TEXT
                );
                """
            )
            conn.commit()

    def set_operator(
        self,
        lat: float,
        lon: float,
        *,
        accuracy_m: float | None = None,
        alt_m: float | None = None,
        source: str = "manual",
    ) -> dict[str, Any]:
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise ValueError("lat/lon out of range")
        ts = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO operator (id, lat, lon, accuracy_m, alt_m, source, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  lat=excluded.lat, lon=excluded.lon, accuracy_m=excluded.accuracy_m,
                  alt_m=excluded.alt_m, source=excluded.source, updated_at=excluded.updated_at
                """,
                (float(lat), float(lon), accuracy_m, alt_m, source, ts),
            )
            conn.commit()
        return self.get_operator()  # type: ignore[return-value]

    def get_operator(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM operator WHERE id=1").fetchone()
        return None if row is None else dict(row)

    def add_breadcrumb(
        self,
        *,
        kind: str,
        lat: float,
        lon: float,
        frequency_hz: int | None = None,
        rssi_db: float | None = None,
        label: str | None = None,
        ref_id: str | None = None,
        notes: str | None = None,
        ts: str | None = None,
    ) -> int:
        stamp = ts or datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO breadcrumbs
                  (ts, kind, lat, lon, frequency_hz, rssi_db, label, ref_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stamp,
                    kind,
                    float(lat),
                    float(lon),
                    frequency_hz,
                    rssi_db,
                    label,
                    ref_id,
                    notes,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_breadcrumbs(
        self, *, kind: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        with self._connect() as conn:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM breadcrumbs WHERE kind=? ORDER BY id DESC LIMIT ?",
                    (kind, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM breadcrumbs ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def heatmap_cells(self, *, cell_deg: float = 0.002) -> list[dict[str, Any]]:
        cell = max(0.0001, float(cell_deg))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  ROUND(lat / ?, 0) * ? AS cell_lat,
                  ROUND(lon / ?, 0) * ? AS cell_lon,
                  COUNT(*) AS hit_count,
                  AVG(rssi_db) AS avg_rssi_db,
                  MAX(rssi_db) AS peak_rssi_db
                FROM breadcrumbs
                GROUP BY cell_lat, cell_lon
                ORDER BY hit_count DESC
                LIMIT 2000
                """,
                (cell, cell, cell, cell),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_lob(
        self,
        lat: float,
        lon: float,
        *,
        bearing_deg: float,
        label: str | None = None,
        frequency_hz: int | None = None,
    ) -> int:
        bearing = float(bearing_deg) % 360.0
        ts = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO lobs (ts, lat, lon, bearing_deg, label, frequency_hz)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, float(lat), float(lon), bearing, label, frequency_hz),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_lobs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM lobs ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def clear_lobs(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM lobs")
            conn.commit()

    def add_gnss_sample(
        self,
        *,
        center_hz: int,
        peak_db: float,
        mean_db: float,
        noise_floor_db: float,
        jammed: bool,
        notes: str | None = None,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO gnss_samples
                  (ts, center_hz, peak_db, mean_db, noise_floor_db, jammed, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    int(center_hz),
                    peak_db,
                    mean_db,
                    noise_floor_db,
                    1 if jammed else 0,
                    notes,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_gnss_samples(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM gnss_samples ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
