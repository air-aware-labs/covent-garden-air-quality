"""SQLite persistence for AirGradient locations and five-minute measurements."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from .observations import VALUE_FIELDS


SCHEMA_VERSION = 2

# Schema v2 adds the documented Measure/GoMeasure coordinate, pressure and
# mobile fields.  Keep the migration local and additive so an archive created
# by the first collector build remains usable.
V2_OBSERVATION_COLUMNS = (
    ("location_latitude_deg", "REAL"),
    ("location_longitude_deg", "REAL"),
    ("pressure_hpa", "REAL"),
    ("gps_latitude_deg", "REAL"),
    ("gps_longitude_deg", "REAL"),
    ("gps_altitude_m", "REAL"),
    ("speed_m_s", "REAL"),
    ("gps_hdop", "REAL"),
    ("motion_code", "INTEGER"),
)


class StorageError(RuntimeError):
    """The database cannot be used by this collector version."""


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

            CREATE TABLE IF NOT EXISTS place_metadata (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                place_id INTEGER,
                name TEXT,
                timezone_id TEXT,
                country_id TEXT,
                updated_at_utc INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS locations (
                location_id INTEGER PRIMARY KEY,
                location_name TEXT,
                location_type TEXT,
                serial_number TEXT,
                model TEXT,
                updated_at_utc INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS observations (
                location_id INTEGER NOT NULL,
                observed_at_utc INTEGER NOT NULL,
                location_name TEXT,
                location_type TEXT,
                serial_number TEXT,
                model TEXT,
                datapoints INTEGER,
                pm01_raw_ug_m3 REAL,
                pm25_raw_ug_m3 REAL,
                pm10_raw_ug_m3 REAL,
                pm01_corrected_ug_m3 REAL,
                pm25_corrected_ug_m3 REAL,
                pm10_corrected_ug_m3 REAL,
                particle_count_0_3um_per_dl REAL,
                temperature_raw_c REAL,
                temperature_corrected_c REAL,
                relative_humidity_raw_pct REAL,
                relative_humidity_corrected_pct REAL,
                co2_raw_ppm REAL,
                co2_corrected_ppm REAL,
                tvoc_ppb REAL,
                tvoc_index REAL,
                nox_index REAL,
                battery_v REAL,
                panel_v REAL,
                wifi_dbm REAL,
                location_latitude_deg REAL,
                location_longitude_deg REAL,
                pressure_hpa REAL,
                gps_latitude_deg REAL,
                gps_longitude_deg REAL,
                gps_altitude_m REAL,
                speed_m_s REAL,
                gps_hdop REAL,
                motion_code INTEGER,
                anomalies_json TEXT,
                raw_json TEXT NOT NULL,
                first_collected_at_utc INTEGER NOT NULL,
                last_collected_at_utc INTEGER NOT NULL,
                PRIMARY KEY (location_id, observed_at_utc)
            ) WITHOUT ROWID;

            CREATE INDEX IF NOT EXISTS observations_time_idx
                ON observations(observed_at_utc);

            CREATE TABLE IF NOT EXISTS sync_state (
                location_id INTEGER PRIMARY KEY,
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
        elif int(row["version"]) == 1:
            existing = {
                str(column["name"])
                for column in self.db.execute("PRAGMA table_info(observations)")
            }
            for name, sql_type in V2_OBSERVATION_COLUMNS:
                if name not in existing:
                    self.db.execute(
                        f"ALTER TABLE observations ADD COLUMN {name} {sql_type}"
                    )
            self.db.execute(
                "UPDATE schema_info SET version = ? WHERE singleton = 1",
                (SCHEMA_VERSION,),
            )
        elif int(row["version"]) != SCHEMA_VERSION:
            raise StorageError(
                f"Unsupported AirGradient database schema {row['version']}; "
                f"expected {SCHEMA_VERSION}"
            )

    def save_metadata(
        self, place: Dict[str, Any], current_measurements: Iterable[Dict[str, Any]]
    ) -> None:
        measurements = list(current_measurements)

        # A location is a logical dashboard position, while the serial identifies
        # the physical instrument.  Until effective-dated assignment episodes are
        # implemented, changing a non-empty serial must fail closed: overwriting
        # it would make later /past rows inherit the wrong instrument identity.
        for measurement in measurements:
            location_id = int(measurement["location_id"])
            existing = self.location_metadata(location_id)
            existing_serial = (
                str(existing["serial_number"]).strip()
                if existing is not None and existing["serial_number"] is not None
                else ""
            )
            incoming_serial = str(measurement.get("serial_number") or "").strip()
            if (
                existing_serial
                and incoming_serial
                and existing_serial.casefold() != incoming_serial.casefold()
            ):
                raise StorageError(
                    f"AirGradient location {location_id} changed physical sensor serial; "
                    "record an effective-dated assignment before collecting more history"
                )

        now = int(time.time())
        place_id = place.get("id")
        self.db.execute(
            """
            INSERT INTO place_metadata(
                singleton, place_id, name, timezone_id, country_id, updated_at_utc
            ) VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                place_id = excluded.place_id,
                name = excluded.name,
                timezone_id = excluded.timezone_id,
                country_id = excluded.country_id,
                updated_at_utc = excluded.updated_at_utc
            """,
            (
                int(place_id) if place_id is not None else None,
                place.get("name"),
                place.get("timezoneId"),
                place.get("countryId"),
                now,
            ),
        )
        for measurement in measurements:
            serial_number = str(measurement.get("serial_number") or "").strip() or None
            self.db.execute(
                """
                INSERT INTO locations(
                    location_id, location_name, location_type, serial_number, model,
                    updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id) DO UPDATE SET
                    location_name = excluded.location_name,
                    location_type = excluded.location_type,
                    serial_number = COALESCE(excluded.serial_number, locations.serial_number),
                    model = excluded.model,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    int(measurement["location_id"]),
                    measurement.get("location_name"),
                    measurement.get("location_type"),
                    serial_number,
                    measurement.get("model"),
                    now,
                ),
            )
        self.db.commit()

    def location_metadata(self, location_id: int) -> Optional[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM locations WHERE location_id = ?", (int(location_id),)
        ).fetchone()

    def latest_epoch(self, location_id: int) -> Optional[int]:
        row = self.db.execute(
            "SELECT MAX(observed_at_utc) AS latest FROM observations WHERE location_id = ?",
            (int(location_id),),
        ).fetchone()
        return int(row["latest"]) if row and row["latest"] is not None else None

    def observation_count(self, location_id: Optional[int] = None) -> int:
        if location_id is None:
            row = self.db.execute("SELECT COUNT(*) AS n FROM observations").fetchone()
        else:
            row = self.db.execute(
                "SELECT COUNT(*) AS n FROM observations WHERE location_id = ?",
                (int(location_id),),
            ).fetchone()
        return int(row["n"])

    def last_reconcile_epoch(self, location_id: int) -> Optional[int]:
        row = self.db.execute(
            "SELECT last_full_reconcile_at_utc FROM sync_state WHERE location_id = ?",
            (int(location_id),),
        ).fetchone()
        return int(row["last_full_reconcile_at_utc"]) if row else None

    def mark_reconciled(
        self, location_id: int, reconciled_at: Optional[int] = None
    ) -> None:
        reconciled_at = int(reconciled_at or time.time())
        self.db.execute(
            """
            INSERT INTO sync_state(location_id, last_full_reconcile_at_utc) VALUES (?, ?)
            ON CONFLICT(location_id) DO UPDATE SET
                last_full_reconcile_at_utc = excluded.last_full_reconcile_at_utc
            """,
            (int(location_id), reconciled_at),
        )
        self.db.commit()

    def upsert_observations(
        self,
        location_id: int,
        observations: Iterable[Dict[str, Any]],
        collected_at: Optional[int] = None,
    ) -> StoreResult:
        location_id = int(location_id)
        collected_at = int(collected_at or time.time())
        metadata_row = self.location_metadata(location_id)
        metadata = dict(metadata_row) if metadata_row is not None else {}
        unique: Dict[int, Dict[str, Any]] = {}
        metadata_serial = str(metadata.get("serial_number") or "").strip()
        for source in observations:
            if int(source["location_id"]) != location_id:
                raise StorageError("A measurement did not belong to the requested location")
            observation = dict(source)
            observation_serial = str(observation.get("serial_number") or "").strip()
            if (
                metadata_serial
                and observation_serial
                and metadata_serial.casefold() != observation_serial.casefold()
            ):
                raise StorageError(
                    f"AirGradient history for location {location_id} belongs to a different "
                    "physical sensor; record an effective-dated assignment before importing it"
                )
            # Mutable descriptive metadata may be filled from the current location,
            # but never invent a serial for a historical row. A missing provider
            # serial therefore remains missing and the twin identity join fails closed.
            for field in ("location_name", "location_type", "model"):
                if observation.get(field) is None and metadata.get(field) is not None:
                    observation[field] = metadata[field]
            unique[int(observation["observed_at_utc"])] = observation
        if not unique:
            return StoreResult(0, 0, 0, 0)

        first_epoch, last_epoch = min(unique), max(unique)
        existing_rows = self.db.execute(
            """
            SELECT observed_at_utc, raw_json FROM observations
            WHERE location_id = ? AND observed_at_utc BETWEEN ? AND ?
            """,
            (location_id, first_epoch, last_epoch),
        ).fetchall()
        existing = {int(row["observed_at_utc"]): row["raw_json"] for row in existing_rows}
        inserted = sum(epoch not in existing for epoch in unique)
        updated = sum(
            epoch in existing and existing[epoch] != observation["raw_json"]
            for epoch, observation in unique.items()
        )
        unchanged = len(unique) - inserted - updated

        insert_columns: Sequence[str] = (
            "location_id",
            "observed_at_utc",
            *VALUE_FIELDS,
            "raw_json",
            "first_collected_at_utc",
            "last_collected_at_utc",
        )
        placeholders = ", ".join("?" for _ in insert_columns)
        update_columns = (*VALUE_FIELDS, "raw_json", "last_collected_at_utc")
        update_sql = ", ".join(f"{column} = excluded.{column}" for column in update_columns)
        sql = (
            f"INSERT INTO observations({', '.join(insert_columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(location_id, observed_at_utc) DO UPDATE SET {update_sql}"
        )
        values: List[Sequence[Any]] = []
        for epoch in sorted(unique):
            observation = unique[epoch]
            values.append(
                (
                    location_id,
                    epoch,
                    *(observation.get(column) for column in VALUE_FIELDS),
                    observation["raw_json"],
                    collected_at,
                    collected_at,
                )
            )
        self.db.executemany(sql, values)
        self.db.commit()
        return StoreResult(len(unique), inserted, updated, unchanged)

    def iter_observations(
        self,
        location_ids: Optional[Iterable[int]] = None,
        start_epoch: Optional[int] = None,
        end_epoch_exclusive: Optional[int] = None,
    ) -> Iterator[sqlite3.Row]:
        clauses: List[str] = []
        parameters: List[Any] = []
        if location_ids is not None:
            ids = tuple(dict.fromkeys(int(value) for value in location_ids))
            if not ids:
                return
            clauses.append("location_id IN (" + ",".join("?" for _ in ids) + ")")
            parameters.extend(ids)
        if start_epoch is not None:
            clauses.append("observed_at_utc >= ?")
            parameters.append(int(start_epoch))
        if end_epoch_exclusive is not None:
            clauses.append("observed_at_utc < ?")
            parameters.append(int(end_epoch_exclusive))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cursor = self.db.execute(
            "SELECT * FROM observations"
            + where
            + " ORDER BY observed_at_utc, location_id",
            parameters,
        )
        yield from cursor
