"""Stable measurement-only CSV export with explicit units."""

from __future__ import annotations

import csv
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Tuple


EXPORT_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("location_name", "location_name"),
    ("location_type", "location_type"),
    ("serial_number", "serial_number"),
    ("model", "model"),
    ("datapoints", "datapoints"),
    ("pm01_raw_ug_m3", "pm01_raw_ug_m3"),
    ("pm25_raw_ug_m3", "pm25_raw_ug_m3"),
    ("pm10_raw_ug_m3", "pm10_raw_ug_m3"),
    ("pm01_corrected_ug_m3", "pm01_corrected_ug_m3"),
    ("pm25_corrected_ug_m3", "pm25_corrected_ug_m3"),
    ("pm10_corrected_ug_m3", "pm10_corrected_ug_m3"),
    ("particle_count_0_3um_per_dl", "particle_count_0_3um_per_dl"),
    ("temperature_raw_c", "temperature_raw_c"),
    ("temperature_corrected_c", "temperature_corrected_c"),
    ("relative_humidity_raw_pct", "relative_humidity_raw_pct"),
    ("relative_humidity_corrected_pct", "relative_humidity_corrected_pct"),
    ("co2_raw_ppm", "co2_raw_ppm"),
    ("co2_corrected_ppm", "co2_corrected_ppm"),
    ("tvoc_ppb", "tvoc_ppb"),
    ("tvoc_index", "tvoc_index"),
    ("nox_index", "nox_index"),
    ("battery_v", "battery_v"),
    ("panel_v", "panel_v"),
    ("wifi_dbm", "wifi_dbm"),
    ("location_latitude_deg", "location_latitude_deg"),
    ("location_longitude_deg", "location_longitude_deg"),
    ("pressure_hpa", "pressure_hpa"),
    ("gps_latitude_deg", "gps_latitude_deg"),
    ("gps_longitude_deg", "gps_longitude_deg"),
    ("gps_altitude_m", "gps_altitude_m"),
    ("speed_m_s", "speed_m_s"),
    ("gps_hdop", "gps_hdop"),
    ("motion_code", "motion_code"),
    ("anomalies_json", "anomalies"),
)

CSV_HEADERS: Tuple[str, ...] = (
    "location_id",
    "observed_at_utc",
    "observed_epoch",
    *(header for _, header in EXPORT_COLUMNS),
)


def _utc_text(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def export_csv(rows: Iterable[Dict[str, object]], output: Path) -> int:
    """Atomically export provider measurements, excluding secrets and collector metadata."""

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8-sig",
        newline="",
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(CSV_HEADERS)
            for row in rows:
                epoch = int(row["observed_at_utc"])
                values = [int(row["location_id"]), _utc_text(epoch), epoch]
                values.extend(
                    "" if row[column] is None else row[column]
                    for column, _ in EXPORT_COLUMNS
                )
                writer.writerow(values)
                count += 1
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return count
