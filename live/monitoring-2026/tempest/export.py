"""CSV export with explicit units and Excel-friendly UTF-8 encoding."""

from __future__ import annotations

import csv
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Tuple


EXPORT_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("device_id", "device_id"),
    ("observed_at_utc", "observed_at_epoch_utc"),
    ("wind_lull_mps", "wind_lull_m_s"),
    ("wind_avg_mps", "wind_average_m_s"),
    ("wind_gust_mps", "wind_gust_m_s"),
    ("wind_direction_deg", "wind_direction_degrees"),
    ("wind_sample_interval_s", "wind_sample_interval_seconds"),
    ("station_pressure_mb", "station_pressure_hpa"),
    ("air_temperature_c", "air_temperature_c"),
    ("relative_humidity_pct", "relative_humidity_percent"),
    ("illuminance_lux", "illuminance_lux"),
    ("uv_index", "uv_index"),
    ("solar_radiation_wm2", "solar_radiation_w_m2"),
    ("rain_interval_mm", "rain_interval_mm"),
    ("precipitation_type", "precipitation_type_code"),
    ("lightning_avg_distance_km", "lightning_average_distance_km"),
    ("lightning_strike_count", "lightning_strike_count"),
    ("battery_v", "battery_v"),
    ("report_interval_min", "report_interval_minutes"),
    ("local_day_rain_mm", "local_day_rain_mm"),
    ("nearcast_rain_interval_mm", "nearcast_rain_interval_mm"),
    ("local_day_nearcast_rain_mm", "local_day_nearcast_rain_mm"),
    ("precipitation_analysis_type", "precipitation_analysis_type_code"),
)

CSV_HEADERS = (
    "observed_at_utc",
    "observed_at_pc_local",
    *(header for _, header in EXPORT_COLUMNS),
)


def _timestamp_strings(epoch: int) -> Tuple[str, str]:
    utc_time = datetime.fromtimestamp(int(epoch), timezone.utc)
    utc_text = utc_time.isoformat(timespec="seconds").replace("+00:00", "Z")
    local_text = utc_time.astimezone().isoformat(timespec="seconds")
    return utc_text, local_text


def export_csv(
    rows: Iterable[Dict[str, object]],
    output: Path,
) -> int:
    """Write observations atomically, returning the number of data rows."""

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path
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
                utc_text, local_text = _timestamp_strings(int(row["observed_at_utc"]))
                values = [utc_text, local_text]
                values.extend("" if row[column] is None else row[column] for column, _ in EXPORT_COLUMNS)
                writer.writerow(values)
                count += 1
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return count
