"""SQLite packet log for Sub-GHz sniffer."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class PacketRow:
    id: int
    ts: str
    frequency_hz: int | None
    protocol: str | None
    model: str | None
    data_json: str
    raw_pulses_us: str | None


class PacketStore:
    def __init__(self, path: Path) -> None:
        self.path = path
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    frequency_hz INTEGER,
                    protocol TEXT,
                    model TEXT,
                    data_json TEXT NOT NULL,
                    raw_pulses_us TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_packets_ts ON packets(ts DESC)"
            )
            conn.commit()

    def insert(
        self,
        payload: dict[str, Any],
        *,
        frequency_hz: int | None = None,
        raw_pulses_us: list[int] | None = None,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        protocol = str(payload.get("protocol") or payload.get("type") or "") or None
        model = str(payload.get("model") or payload.get("id") or "") or None
        freq = frequency_hz
        if freq is None:
            for key in ("freq", "frequency", "frequency_hz"):
                if key in payload:
                    try:
                        freq = int(float(payload[key]))
                        break
                    except (TypeError, ValueError):
                        pass
        pulses = json.dumps(raw_pulses_us) if raw_pulses_us is not None else None
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO packets (ts, frequency_hz, protocol, model, data_json, raw_pulses_us)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, freq, protocol, model, json.dumps(payload, separators=(",", ":")), pulses),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_recent(self, limit: int = 100) -> list[PacketRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, ts, frequency_hz, protocol, model, data_json, raw_pulses_us
                FROM packets ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            PacketRow(
                id=int(r["id"]),
                ts=str(r["ts"]),
                frequency_hz=r["frequency_hz"],
                protocol=r["protocol"],
                model=r["model"],
                data_json=str(r["data_json"]),
                raw_pulses_us=r["raw_pulses_us"],
            )
            for r in rows
        ]

    def get(self, packet_id: int) -> PacketRow | None:
        with self._connect() as conn:
            r = conn.execute(
                """
                SELECT id, ts, frequency_hz, protocol, model, data_json, raw_pulses_us
                FROM packets WHERE id = ?
                """,
                (packet_id,),
            ).fetchone()
        if r is None:
            return None
        return PacketRow(
            id=int(r["id"]),
            ts=str(r["ts"]),
            frequency_hz=r["frequency_hz"],
            protocol=r["protocol"],
            model=r["model"],
            data_json=str(r["data_json"]),
            raw_pulses_us=r["raw_pulses_us"],
        )
