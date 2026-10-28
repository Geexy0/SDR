"""SQLite fleet registry for lab TPMS sensors / vehicles."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SensorSighting:
    sensor_id: str
    last_ts: str
    hit_count: int
    last_model: str | None
    last_protocol: str | None
    oem_family: str | None
    last_pressure_kpa: float | None
    last_temp_c: float | None
    last_lat: float | None
    last_lon: float | None
    vehicle_id: int | None
    last_payload: dict[str, Any]


@dataclass(slots=True)
class FleetVehicle:
    id: int
    label: str
    make: str | None
    model: str | None
    year: int | None
    color: str | None
    notes: str | None
    sensor_ids: list[str]
    created_ts: str
    updated_ts: str


class TpmsFleetStore:
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
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sensors (
                    sensor_id TEXT PRIMARY KEY,
                    first_ts TEXT NOT NULL,
                    last_ts TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 1,
                    last_model TEXT,
                    last_protocol TEXT,
                    oem_family TEXT,
                    last_pressure_kpa REAL,
                    last_temp_c REAL,
                    last_lat REAL,
                    last_lon REAL,
                    vehicle_id INTEGER,
                    last_payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vehicles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    make TEXT,
                    model TEXT,
                    year INTEGER,
                    color TEXT,
                    notes TEXT,
                    created_ts TEXT NOT NULL,
                    updated_ts TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vehicle_sensors (
                    vehicle_id INTEGER NOT NULL,
                    sensor_id TEXT NOT NULL,
                    PRIMARY KEY (vehicle_id, sensor_id)
                );
                CREATE TABLE IF NOT EXISTS sightings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    sensor_id TEXT NOT NULL,
                    frequency_hz INTEGER,
                    rssi REAL,
                    pressure_kpa REAL,
                    temp_c REAL,
                    lat REAL,
                    lon REAL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sightings_sensor_ts
                    ON sightings(sensor_id, ts DESC);
                CREATE TABLE IF NOT EXISTS passages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    cluster_key TEXT NOT NULL,
                    vehicle_id INTEGER,
                    label TEXT,
                    make TEXT,
                    model_hint TEXT,
                    oem_family TEXT,
                    sensor_ids TEXT NOT NULL,
                    sensor_count INTEGER NOT NULL,
                    hit_count INTEGER NOT NULL,
                    lat REAL,
                    lon REAL
                );
                CREATE INDEX IF NOT EXISTS idx_passages_ts ON passages(ts DESC);
                """
            )
            conn.commit()

    def record_hit(
        self,
        *,
        sensor_id: str,
        payload: dict[str, Any],
        oem_family: str | None,
        frequency_hz: int | None,
        lat: float | None,
        lon: float | None,
    ) -> SensorSighting:
        ts = datetime.now(timezone.utc).isoformat()
        model = str(payload.get("model") or "") or None
        protocol = str(payload.get("protocol") or payload.get("type") or "") or None
        pressure = _num(payload.get("pressure_kPa"))
        if pressure is None and payload.get("pressure_PSI") is not None:
            psi = _num(payload.get("pressure_PSI"))
            pressure = None if psi is None else psi * 6.89476
        if pressure is None and payload.get("pressure_bar") is not None:
            bar = _num(payload.get("pressure_bar"))
            pressure = None if bar is None else bar * 100.0
        temp = _num(payload.get("temperature_C") or payload.get("temp_C"))
        rssi = _num(payload.get("rssi") or payload.get("snr") or payload.get("noise"))

        with self._connect() as conn:
            row = conn.execute(
                "SELECT hit_count, vehicle_id FROM sensors WHERE sensor_id = ?",
                (sensor_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO sensors (
                        sensor_id, first_ts, last_ts, hit_count, last_model, last_protocol,
                        oem_family, last_pressure_kpa, last_temp_c, last_lat, last_lon,
                        vehicle_id, last_payload
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        sensor_id,
                        ts,
                        ts,
                        model,
                        protocol,
                        oem_family,
                        pressure,
                        temp,
                        lat,
                        lon,
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )
                vehicle_id = None
                hit_count = 1
            else:
                hit_count = int(row["hit_count"]) + 1
                vehicle_id = row["vehicle_id"]
                conn.execute(
                    """
                    UPDATE sensors SET
                        last_ts = ?, hit_count = ?, last_model = ?, last_protocol = ?,
                        oem_family = COALESCE(?, oem_family),
                        last_pressure_kpa = ?, last_temp_c = ?,
                        last_lat = COALESCE(?, last_lat),
                        last_lon = COALESCE(?, last_lon),
                        last_payload = ?
                    WHERE sensor_id = ?
                    """,
                    (
                        ts,
                        hit_count,
                        model,
                        protocol,
                        oem_family,
                        pressure,
                        temp,
                        lat,
                        lon,
                        json.dumps(payload, separators=(",", ":")),
                        sensor_id,
                    ),
                )
            conn.execute(
                """
                INSERT INTO sightings (
                    ts, sensor_id, frequency_hz, rssi, pressure_kpa, temp_c, lat, lon, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    sensor_id,
                    frequency_hz,
                    rssi,
                    pressure,
                    temp,
                    lat,
                    lon,
                    json.dumps(payload, separators=(",", ":")),
                ),
            )
            conn.commit()

        return SensorSighting(
            sensor_id=sensor_id,
            last_ts=ts,
            hit_count=hit_count,
            last_model=model,
            last_protocol=protocol,
            oem_family=oem_family,
            last_pressure_kpa=pressure,
            last_temp_c=temp,
            last_lat=lat,
            last_lon=lon,
            vehicle_id=int(vehicle_id) if vehicle_id is not None else None,
            last_payload=payload,
        )

    def list_sensors(self, limit: int = 200) -> list[SensorSighting]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sensors ORDER BY last_ts DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._sensor_row(r) for r in rows]

    def get_sensor(self, sensor_id: str) -> SensorSighting | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sensors WHERE sensor_id = ?", (sensor_id,)
            ).fetchone()
        return None if row is None else self._sensor_row(row)

    def upsert_vehicle(
        self,
        *,
        label: str,
        make: str | None = None,
        model: str | None = None,
        year: int | None = None,
        color: str | None = None,
        notes: str | None = None,
        sensor_ids: list[str] | None = None,
        vehicle_id: int | None = None,
    ) -> FleetVehicle:
        ts = datetime.now(timezone.utc).isoformat()
        sensor_ids = sensor_ids or []
        with self._connect() as conn:
            if vehicle_id is None:
                cur = conn.execute(
                    """
                    INSERT INTO vehicles (label, make, model, year, color, notes, created_ts, updated_ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (label, make, model, year, color, notes, ts, ts),
                )
                vehicle_id = int(cur.lastrowid)
            else:
                conn.execute(
                    """
                    UPDATE vehicles SET
                        label = ?, make = ?, model = ?, year = ?, color = ?, notes = ?,
                        updated_ts = ?
                    WHERE id = ?
                    """,
                    (label, make, model, year, color, notes, ts, vehicle_id),
                )
                conn.execute(
                    "DELETE FROM vehicle_sensors WHERE vehicle_id = ?", (vehicle_id,)
                )
                conn.execute(
                    "UPDATE sensors SET vehicle_id = NULL WHERE vehicle_id = ?",
                    (vehicle_id,),
                )
            for sid in sensor_ids:
                sid_n = sid.strip().upper()
                if not sid_n:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO vehicle_sensors (vehicle_id, sensor_id) VALUES (?, ?)
                    """,
                    (vehicle_id, sid_n),
                )
                # Ensure sensor row exists
                exists = conn.execute(
                    "SELECT 1 FROM sensors WHERE sensor_id = ?", (sid_n,)
                ).fetchone()
                if exists is None:
                    conn.execute(
                        """
                        INSERT INTO sensors (
                            sensor_id, first_ts, last_ts, hit_count, last_payload, vehicle_id
                        ) VALUES (?, ?, ?, 0, '{}', ?)
                        """,
                        (sid_n, ts, ts, vehicle_id),
                    )
                else:
                    conn.execute(
                        "UPDATE sensors SET vehicle_id = ? WHERE sensor_id = ?",
                        (vehicle_id, sid_n),
                    )
            conn.commit()
        vehicle = self.get_vehicle(vehicle_id)
        assert vehicle is not None
        return vehicle

    def get_vehicle(self, vehicle_id: int) -> FleetVehicle | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)
            ).fetchone()
            if row is None:
                return None
            sids = [
                str(r["sensor_id"])
                for r in conn.execute(
                    "SELECT sensor_id FROM vehicle_sensors WHERE vehicle_id = ?",
                    (vehicle_id,),
                ).fetchall()
            ]
        return FleetVehicle(
            id=int(row["id"]),
            label=str(row["label"]),
            make=row["make"],
            model=row["model"],
            year=row["year"],
            color=row["color"],
            notes=row["notes"],
            sensor_ids=sids,
            created_ts=str(row["created_ts"]),
            updated_ts=str(row["updated_ts"]),
        )

    def list_vehicles(self) -> list[FleetVehicle]:
        with self._connect() as conn:
            ids = [
                int(r["id"])
                for r in conn.execute(
                    "SELECT id FROM vehicles ORDER BY updated_ts DESC"
                ).fetchall()
            ]
        out: list[FleetVehicle] = []
        for vid in ids:
            v = self.get_vehicle(vid)
            if v:
                out.append(v)
        return out

    def find_vehicle_id_for_sensors(self, sensor_ids: set[str]) -> int | None:
        """Return vehicle that already owns any of these sensors."""
        if not sensor_ids:
            return None
        with self._connect() as conn:
            for sid in sensor_ids:
                row = conn.execute(
                    "SELECT vehicle_id FROM sensors WHERE sensor_id = ? AND vehicle_id IS NOT NULL",
                    (sid,),
                ).fetchone()
                if row and row["vehicle_id"] is not None:
                    return int(row["vehicle_id"])
                row2 = conn.execute(
                    "SELECT vehicle_id FROM vehicle_sensors WHERE sensor_id = ?",
                    (sid,),
                ).fetchone()
                if row2:
                    return int(row2["vehicle_id"])
        return None

    def ensure_vehicle_for_cluster(
        self,
        *,
        sensor_ids: set[str],
        label: str,
        make: str | None,
        model: str | None,
        oem_family: str | None,
    ) -> FleetVehicle:
        existing_id = self.find_vehicle_id_for_sensors(sensor_ids)
        notes = f"auto-cluster OEM={oem_family}" if oem_family else "auto-cluster"
        if existing_id is not None:
            current = self.get_vehicle(existing_id)
            assert current is not None
            merged = sorted(set(current.sensor_ids) | sensor_ids)
            return self.upsert_vehicle(
                label=current.label or label,
                make=current.make or make,
                model=current.model or model,
                year=current.year,
                color=current.color,
                notes=current.notes or notes,
                sensor_ids=merged,
                vehicle_id=existing_id,
            )
        return self.upsert_vehicle(
            label=label,
            make=make,
            model=model,
            notes=notes,
            sensor_ids=sorted(sensor_ids),
        )

    def record_passage(
        self,
        *,
        cluster_key: str,
        vehicle_id: int | None,
        label: str | None,
        make: str | None,
        model_hint: str | None,
        oem_family: str | None,
        sensor_ids: set[str],
        hit_count: int,
        lat: float | None,
        lon: float | None,
    ) -> dict[str, Any]:
        ts = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO passages (
                    ts, cluster_key, vehicle_id, label, make, model_hint, oem_family,
                    sensor_ids, sensor_count, hit_count, lat, lon
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    cluster_key,
                    vehicle_id,
                    label,
                    make,
                    model_hint,
                    oem_family,
                    json.dumps(sorted(sensor_ids)),
                    len(sensor_ids),
                    hit_count,
                    lat,
                    lon,
                ),
            )
            conn.commit()
            pid = int(cur.lastrowid)
        return {
            "id": pid,
            "ts": ts,
            "cluster_key": cluster_key,
            "vehicle_id": vehicle_id,
            "label": label,
            "make": make,
            "model_hint": model_hint,
            "oem_family": oem_family,
            "sensor_ids": sorted(sensor_ids),
            "sensor_count": len(sensor_ids),
            "hit_count": hit_count,
            "lat": lat,
            "lon": lon,
        }

    def list_passages(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM passages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                sids = json.loads(r["sensor_ids"] or "[]")
            except json.JSONDecodeError:
                sids = []
            out.append(
                {
                    "id": int(r["id"]),
                    "ts": str(r["ts"]),
                    "cluster_key": str(r["cluster_key"]),
                    "vehicle_id": r["vehicle_id"],
                    "label": r["label"],
                    "make": r["make"],
                    "model_hint": r["model_hint"],
                    "oem_family": r["oem_family"],
                    "sensor_ids": sids,
                    "sensor_count": int(r["sensor_count"]),
                    "hit_count": int(r["hit_count"]),
                    "lat": r["lat"],
                    "lon": r["lon"],
                }
            )
        return out

    def clear_all(self) -> dict[str, int]:
        """Wipe sensors / vehicles / passages (lab fixture reset)."""
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM sensors")
            n_sensors = int(cur.fetchone()[0])
            cur = conn.execute("SELECT COUNT(*) FROM vehicles")
            n_vehicles = int(cur.fetchone()[0])
            cur = conn.execute("SELECT COUNT(*) FROM passages")
            n_passages = int(cur.fetchone()[0])
            conn.executescript(
                """
                DELETE FROM vehicle_sensors;
                DELETE FROM passages;
                DELETE FROM sensors;
                DELETE FROM vehicles;
                """
            )
            conn.commit()
        return {
            "sensors_deleted": n_sensors,
            "vehicles_deleted": n_vehicles,
            "passages_deleted": n_passages,
        }

    def link_sensor(self, sensor_id: str, vehicle_id: int | None) -> SensorSighting | None:
        sid = sensor_id.strip().upper()
        with self._connect() as conn:
            if vehicle_id is None:
                conn.execute(
                    "DELETE FROM vehicle_sensors WHERE sensor_id = ?", (sid,)
                )
                conn.execute(
                    "UPDATE sensors SET vehicle_id = NULL WHERE sensor_id = ?", (sid,)
                )
            else:
                conn.execute(
                    "DELETE FROM vehicle_sensors WHERE sensor_id = ?", (sid,)
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO vehicle_sensors (vehicle_id, sensor_id)
                    VALUES (?, ?)
                    """,
                    (vehicle_id, sid),
                )
                conn.execute(
                    "UPDATE sensors SET vehicle_id = ? WHERE sensor_id = ?",
                    (vehicle_id, sid),
                )
            conn.commit()
        return self.get_sensor(sid)

    @staticmethod
    def _sensor_row(r: sqlite3.Row) -> SensorSighting:
        payload_raw = r["last_payload"] or "{}"
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload = {}
        return SensorSighting(
            sensor_id=str(r["sensor_id"]),
            last_ts=str(r["last_ts"]),
            hit_count=int(r["hit_count"]),
            last_model=r["last_model"],
            last_protocol=r["last_protocol"],
            oem_family=r["oem_family"],
            last_pressure_kpa=r["last_pressure_kpa"],
            last_temp_c=r["last_temp_c"],
            last_lat=r["last_lat"],
            last_lon=r["last_lon"],
            vehicle_id=int(r["vehicle_id"]) if r["vehicle_id"] is not None else None,
            last_payload=payload if isinstance(payload, dict) else {},
        )


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
