"""Validate and name AirGradient measurement records without guessing missing values."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


# Provider field, internal field, value kind. Raw and corrected channels remain separate.
FIELD_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("locationName", "location_name", "string"),
    ("locationType", "location_type", "string"),
    ("serialno", "serial_number", "string"),
    ("model", "model", "string"),
    # The history endpoint currently serializes this count as a decimal string,
    # while other numeric measurements remain JSON numbers.
    ("datapoints", "datapoints", "integer_text"),
    ("pm01", "pm01_raw_ug_m3", "number"),
    ("pm02", "pm25_raw_ug_m3", "number"),
    ("pm10", "pm10_raw_ug_m3", "number"),
    ("pm01_corrected", "pm01_corrected_ug_m3", "number"),
    ("pm02_corrected", "pm25_corrected_ug_m3", "number"),
    ("pm10_corrected", "pm10_corrected_ug_m3", "number"),
    ("pm003Count", "particle_count_0_3um_per_dl", "number"),
    ("atmp", "temperature_raw_c", "number"),
    ("atmp_corrected", "temperature_corrected_c", "number"),
    ("rhum", "relative_humidity_raw_pct", "number"),
    ("rhum_corrected", "relative_humidity_corrected_pct", "number"),
    ("rco2", "co2_raw_ppm", "number"),
    ("rco2_corrected", "co2_corrected_ppm", "number"),
    ("tvoc", "tvoc_ppb", "number"),
    ("tvocIndex", "tvoc_index", "number"),
    ("noxIndex", "nox_index", "number"),
    ("batteryVoltage", "battery_v", "number"),
    ("panelVoltage", "panel_v", "number"),
    ("wifi", "wifi_dbm", "number"),
    # The standard Measure schema can carry the configured location coordinate.
    ("latitude", "location_latitude_deg", "number"),
    ("longitude", "location_longitude_deg", "number"),
    # GoMeasure extends Measure with pressure and optional mobile/GPS fields.
    ("pres", "pressure_hpa", "number"),
    ("lat", "gps_latitude_deg", "number"),
    ("lng", "gps_longitude_deg", "number"),
    ("alt", "gps_altitude_m", "number"),
    ("speed", "speed_m_s", "number"),
    ("hdop", "gps_hdop", "number"),
    ("motion", "motion_code", "integer"),
)

VALUE_FIELDS: Tuple[str, ...] = tuple(spec[1] for spec in FIELD_SPECS) + (
    "anomalies_json",
)


class ObservationError(ValueError):
    """A provider record cannot be interpreted safely."""


def parse_timestamp(value: Any) -> int:
    if not isinstance(value, str) or not value.strip():
        raise ObservationError("An AirGradient measurement had no valid timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ObservationError("An AirGradient measurement timestamp was not ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ObservationError("An AirGradient measurement timestamp had no UTC offset")
    epoch_float = parsed.astimezone(timezone.utc).timestamp()
    epoch = int(epoch_float)
    if epoch <= 0 or epoch_float != epoch:
        raise ObservationError(
            "An AirGradient measurement timestamp was not a positive whole second"
        )
    return epoch


def _location_id(value: Any, expected: Optional[int]) -> int:
    if value is None:
        if expected is None:
            raise ObservationError("An AirGradient measurement had no locationId")
        return int(expected)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ObservationError("AirGradient locationId was not numeric")
    location_id = int(value)
    if location_id <= 0 or float(value) != location_id:
        raise ObservationError("AirGradient locationId was not a positive integer")
    if expected is not None and location_id != int(expected):
        raise ObservationError(
            f"AirGradient returned location {location_id} while {int(expected)} was requested"
        )
    return location_id


def _value(source: str, value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "string":
        if not isinstance(value, str):
            raise ObservationError(f"AirGradient field {source} was not text or null")
        return value
    if kind == "integer_text":
        if isinstance(value, str):
            text = value.strip()
            if not text or any(character < "0" or character > "9" for character in text):
                raise ObservationError(
                    f"AirGradient field {source} was not a non-negative whole number"
                )
            value = int(text)
        kind = "integer"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ObservationError(f"AirGradient field {source} was not numeric or null")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ObservationError(f"AirGradient field {source} was not finite")
    if kind == "integer":
        integer = int(numeric)
        if numeric != integer:
            raise ObservationError(f"AirGradient field {source} was not a whole number")
        if source == "datapoints" and integer < 0:
            raise ObservationError(
                "AirGradient field datapoints was not a non-negative whole number"
            )
        return integer
    return numeric


def parse_measure(
    row: Dict[str, Any], expected_location_id: Optional[int] = None
) -> Dict[str, Any]:
    if not isinstance(row, dict):
        raise ObservationError("An AirGradient measurement was not an object")
    parsed: Dict[str, Any] = {
        "location_id": _location_id(row.get("locationId"), expected_location_id),
        "observed_at_utc": parse_timestamp(row.get("timestamp")),
    }
    for source, destination, kind in FIELD_SPECS:
        parsed[destination] = _value(source, row.get(source), kind)

    anomalies = row.get("anomalies")
    if anomalies is None:
        parsed["anomalies_json"] = None
    elif not isinstance(anomalies, list) or any(not isinstance(item, str) for item in anomalies):
        raise ObservationError("AirGradient anomalies was not an array of text values or null")
    else:
        parsed["anomalies_json"] = json.dumps(
            anomalies, ensure_ascii=False, separators=(",", ":")
        )
    try:
        parsed["raw_json"] = json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ObservationError("An AirGradient measurement was not valid JSON") from exc
    return parsed


def parse_measures_payload(
    rows: Iterable[Dict[str, Any]], expected_location_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        raise ObservationError("AirGradient measurements were not returned as an array")
    return [parse_measure(row, expected_location_id) for row in rows]
