"""SQLite persistence for Tempest observations and station metadata."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from .observations import OBS_ST_FIELDS


SCHEMA_VERSION = 1


class StorageError(RuntimeError):
    """The database exists but cannot be used by this collector version."""


@dataclass(frozen=True)
class StoreResult:
    received: int
    inserted: int
    updated: int
    unchanged: int


class ObservationStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.connection: Optional[sqlite3.Connection] = None

    def __enter__(self) -> "ObservationStore":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self._create_schema()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.connection is not None:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
            self.connection.close()
            self.connection = None

    @property
    def db(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("ObservationStore is not open")
        return self.connection

    def _create_schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_info (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                version INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stations (
                station_id INTEGER PRIMARY KEY,
                name TEXT,
                public_name TEXT,
                latitude REAL,
                longitude REAL,
                elevation_m REAL,
                timezone TEXT,
                updated_at_utc INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS devices (
                device_id INTEGER PRIMARY KEY,
                station_id INTEGER NOT NULL,
                serial_number TEXT,
                name TEXT,
                environment TEXT,
                device_type TEXT,
                hardware_revision TEXT,
                firmware_revision TEXT,
                updated_at_utc INTEGER NOT NULL,
                FOREIGN KEY (station_id) REFERENCES stations(station_id)
            );

            CREATE TABLE IF NOT EXISTS observations (
                device_id INTEGER NOT NULL,
                observed_at_utc INTEGER NOT NULL,
                wind_lull_mps REAL,
                wind_avg_mps REAL,
                wind_gust_mps REAL,
                wind_direction_deg REAL,
                wind_sample_interval_s INTEGER,
                station_pressure_mb REAL,
                air_temperature_c REAL,
                relative_humidity_pct REAL,
                illuminance_lux REAL,
                uv_index REAL,
                solar_radiation_wm2 REAL,
                rain_interval_mm REAL,
                precipitation_type INTEGER,
                lightning_avg_distance_km REAL,
                lightning_strike_count INTEGER,
                battery_v REAL,
                report_interval_min INTEGER,
                local_day_rain_mm REAL,
                nearcast_rain_interval_mm REAL,
                local_day_nearcast_rain_mm REAL,
                precipitation_analysis_type INTEGER,
                raw_json TEXT NOT NULL,
                first_collected_at_utc INTEGER NOT NULL,
                last_collected_at_utc INTEGER NOT NULL,
                PRIMARY KEY (device_id, observed_at_utc)
            ) WITHOUT ROWID;

            CREATE INDEX IF NOT EXISTS observations_time_idx
                ON observations(observed_at_utc);

            CREATE TABLE IF NOT EXISTS sync_state (
                device_id INTEGER PRIMARY KEY,
                last_full_reconcile_at_utc INTEGER NOT NULL
            );
            """
        )
        row = self.db.execute("SELECT version FROM schema_info WHERE singleton = 1").fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO schema_info(singleton, version) VALUES (1, ?)",
                (SCHEMA_VERSION,),
            )
        elif int(row["version"]) != SCHEMA_VERSION:
            raise StorageError(
                f"Unsupported Tempest database schema {row['version']}; expected {SCHEMA_VERSION}"
            )

    def save_metadata(
        self,
        station: Dict[str, Any],
        timezone_name: Optional[str] = None,
    ) -> None:
        """Store useful metadata while deliberately omitting the Wi-Fi network name."""

        station_id = int(station["station_id"])
        station_meta = station.get("station_meta") or {}
        now = int(time.time())
        self.db.execute(
            """
            INSERT INTO stations(
                station_id, name, public_name, latitude, longitude, elevation_m,
                timezone, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(station_id) DO UPDATE SET
                name = excluded.name,
                public_name = excluded.public_name,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                elevation_m = excluded.elevation_m,
                timezone = COALESCE(excluded.timezone, stations.timezone),
                updated_at_utc = excluded.updated_at_utc
            """,
            (
                station_id,
                station.get("name"),
                station.get("public_name"),
                station.get("latitude"),
                station.get("longitude"),
                station_meta.get("elevation"),
                timezone_name,
                now,
            ),
        )
        for device in station.get("devices") or []:
            if not isinstance(device, dict) or not device.get("device_id"):
                continue
            device_meta = device.get("device_meta") or {}
            self.db.execute(
                """
                INSERT INTO devices(
                    device_id, station_id, serial_number, name, environment,
                    device_type, hardware_revision, firmware_revision, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    station_id = excluded.station_id,
                    serial_number = excluded.serial_number,
                    name = excluded.name,
                    environment = excluded.environment,
                    device_type = excluded.device_type,
                    hardware_revision = excluded.hardware_revision,
                    firmware_revision = excluded.firmware_revision,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    int(device["device_id"]),
                    station_id,
                    device.get("serial_number"),
                    device_meta.get("name"),
                    device_meta.get("environment"),
                    device.get("device_type"),
                    device.get("hardware_revision"),
                    device.get("firmware_revision"),
                    now,
                ),
            )
        self.db.commit()

    def latest_epoch(self, device_id: int) -> Optional[int]:
        row = self.db.execute(
            "SELECT MAX(observed_at_utc) AS latest FROM observations WHERE device_id = ?",
            (int(device_id),),
        ).fetchone()
        return int(row["latest"]) if row and row["latest"] is not None else None

    def observation_count(self, device_id: Optional[int] = None) -> int:
        if device_id is None:
            row = self.db.execute("SELECT COUNT(*) AS n FROM observations").fetchone()
        else:
            row = self.db.execute(
                "SELECT COUNT(*) AS n FROM observations WHERE device_id = ?", (int(device_id),)
            ).fetchone()
        return int(row["n"])

    def last_reconcile_epoch(self, device_id: int) -> Optional[int]:
        row = self.db.execute(
            "SELECT last_full_reconcile_at_utc FROM sync_state WHERE device_id = ?",
            (int(device_id),),
        ).fetchone()
        return int(row["last_full_reconcile_at_utc"]) if row else None

    def mark_reconciled(self, device_id: int, reconciled_at: Optional[int] = None) -> None:
        reconciled_at = int(reconciled_at or time.time())
        self.db.execute(
            """
            INSERT INTO sync_state(device_id, last_full_reconcile_at_utc) VALUES (?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                last_full_reconcile_at_utc = excluded.last_full_reconcile_at_utc
            """,
            (int(device_id), reconciled_at),
        )
        self.db.commit()

    def upsert_observations(
        self,
        device_id: int,
        observations: Iterable[Dict[str, Any]],
        collected_at: Optional[int] = None,
    ) -> StoreResult:
        collected_at = int(collected_at or time.time())
        # If the provider repeats an epoch in one response, the last row wins.
        unique: Dict[int, Dict[str, Any]] = {
            int(observation["observed_at_utc"]): observation for observation in observations
        }
        if not unique:
            return StoreResult(received=0, inserted=0, updated=0, unchanged=0)

        first_epoch, last_epoch = min(unique), max(unique)
        existing_rows = self.db.execute(
            """
            SELECT observed_at_utc, raw_json
            FROM observations
            WHERE device_id = ? AND observed_at_utc BETWEEN ? AND ?
            """,
            (int(device_id), first_epoch, last_epoch),
        ).fetchall()
        existing = {int(row["observed_at_utc"]): row["raw_json"] for row in existing_rows}
        inserted = sum(epoch not in existing for epoch in unique)
        updated = sum(
            epoch in existing and existing[epoch] != observation["raw_json"]
            for epoch, observation in unique.items()
        )
        unchanged = len(unique) - inserted - updated

        value_columns: Sequence[str] = OBS_ST_FIELDS[1:]
        insert_columns = (
            "device_id",
            "observed_at_utc",
            *value_columns,
            "raw_json",
            "first_collected_at_utc",
            "last_collected_at_utc",
        )
        placeholders = ", ".join("?" for _ in insert_columns)
        update_columns = (*value_columns, "raw_json", "last_collected_at_utc")
        update_sql = ", ".join(f"{column} = excluded.{column}" for column in update_columns)
        sql = (
            f"INSERT INTO observations({', '.join(insert_columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(device_id, observed_at_utc) DO UPDATE SET {update_sql}"
        )
        values: List[Sequence[Any]] = []
        for epoch in sorted(unique):
            observation = unique[epoch]
            values.append(
                (
                    int(device_id),
                    epoch,
                    *(observation.get(column) for column in value_columns),
                    observation["raw_json"],
                    collected_at,
                    collected_at,
                )
            )
        self.db.executemany(sql, values)
        self.db.commit()
        return StoreResult(
            received=len(unique), inserted=inserted, updated=updated, unchanged=unchanged
        )

    def iter_observations(
        self,
        device_id: Optional[int] = None,
        start_epoch: Optional[int] = None,
        end_epoch_exclusive: Optional[int] = None,
    ) -> Iterator[sqlite3.Row]:
        clauses: List[str] = []
        parameters: List[Any] = []
        if device_id is not None:
            clauses.append("device_id = ?")
            parameters.append(int(device_id))
        if start_epoch is not None:
            clauses.append("observed_at_utc >= ?")
            parameters.append(int(start_epoch))
        if end_epoch_exclusive is not None:
            clauses.append("observed_at_utc < ?")
            parameters.append(int(end_epoch_exclusive))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cursor = self.db.execute(
            "SELECT * FROM observations" + where + " ORDER BY observed_at_utc, device_id",
            parameters,
        )
        yield from cursor

    def selected_metadata(self, station_id: int, device_id: int) -> Optional[sqlite3.Row]:
        return self.db.execute(
            """
            SELECT s.name AS station_name, s.timezone, d.serial_number,
                   d.name AS device_name, d.environment
            FROM stations s JOIN devices d ON d.station_id = s.station_id
            WHERE s.station_id = ? AND d.device_id = ?
            """,
            (int(station_id), int(device_id)),
        ).fetchone()
