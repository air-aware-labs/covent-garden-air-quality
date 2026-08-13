"""Parse and name the fixed-position Tempest ``obs_st`` record."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, List, Tuple


OBS_ST_FIELDS: Tuple[str, ...] = (
    "observed_at_utc",
    "wind_lull_mps",
    "wind_avg_mps",
    "wind_gust_mps",
    "wind_direction_deg",
    "wind_sample_interval_s",
    "station_pressure_mb",
    "air_temperature_c",
    "relative_humidity_pct",
    "illuminance_lux",
    "uv_index",
    "solar_radiation_wm2",
    "rain_interval_mm",
    "precipitation_type",
    "lightning_avg_distance_km",
    "lightning_strike_count",
    "battery_v",
    "report_interval_min",
    "local_day_rain_mm",
    "nearcast_rain_interval_mm",
    "local_day_nearcast_rain_mm",
    "precipitation_analysis_type",
)

MIN_OBS_ST_FIELDS = 18


class ObservationError(ValueError):
    """The API returned a record that cannot safely be interpreted."""


def parse_obs_st_row(row: Iterable[Any]) -> Dict[str, Any]:
    if not isinstance(row, (list, tuple)):
        raise ObservationError("A Tempest observation row was not an array")
    original = list(row)
    if len(original) < MIN_OBS_ST_FIELDS:
        raise ObservationError(
            f"A Tempest observation row had {len(original)} fields; expected at least "
            f"{MIN_OBS_ST_FIELDS}"
        )
    padded = (original + [None] * len(OBS_ST_FIELDS))[: len(OBS_ST_FIELDS)]
    for index, value in enumerate(padded):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ObservationError(
                f"Tempest field {OBS_ST_FIELDS[index]} was not numeric or null"
            )
        if not math.isfinite(float(value)):
            raise ObservationError(f"Tempest field {OBS_ST_FIELDS[index]} was not finite")
    try:
        epoch = int(padded[0])
    except (TypeError, ValueError, OverflowError) as exc:
        raise ObservationError("A Tempest observation had an invalid UTC epoch") from exc
    if epoch <= 0:
        raise ObservationError("A Tempest observation had a non-positive UTC epoch")
    if float(padded[0]) != epoch:
        raise ObservationError("A Tempest observation UTC epoch was not a whole second")
    padded[0] = epoch
    parsed = dict(zip(OBS_ST_FIELDS, padded))
    # Keep the complete provider row so future trailing fields are not lost.
    parsed["raw_json"] = json.dumps(original, ensure_ascii=False, separators=(",", ":"))
    return parsed


def parse_device_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    observation_type = payload.get("type")
    if observation_type != "obs_st":
        raise ObservationError(
            f"Device returned {observation_type or 'no observation type'}, not Tempest obs_st"
        )
    rows = payload.get("obs", [])
    if not isinstance(rows, list):
        raise ObservationError("Tempest observations were not returned as an array")
    return [parse_obs_st_row(row) for row in rows]
