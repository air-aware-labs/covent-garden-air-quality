#!/usr/bin/env python3
"""Build the five-minute Tempest record embedded in the digital twin.

The Tempest API reports an observation at the *end* of its represented
interval.  AirGradient exports are labelled at the *start* of a five-minute
bucket, so this builder maps (observation - report interval, observation] onto
UTC-aligned [start, start + 5 minutes) buckets.  That keeps future AQ/weather
joins on the same clock without shifting the weather by five minutes.

No API credential is read or written here.  The input is the local CSV created
by ``python -m tempest export`` and the output contains measurements only.

Run from monitoring-2026:

    python scripts/build_tempest_weather.py
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs" / "tempest" / "tempest_observations.csv"
JSON_OUT = ROOT / "outputs" / "tempest" / "tempest_5min.json"
CSV_OUT = ROOT / "outputs" / "tempest" / "tempest_5min.csv"
SCENE_OUT = ROOT.parent / "digital-twin-v4" / "scene" / "tempest_weather.json"

STEP_SECONDS = 300
CALM_THRESHOLD_M_S = 0.2

MEAN_FIELDS = {
    "air_temperature_c": "temperature_c",
    "relative_humidity_percent": "relative_humidity_pct",
    "station_pressure_hpa": "station_pressure_hpa",
    "wind_average_m_s": "wind_average_m_s",
    "solar_radiation_w_m2": "solar_radiation_w_m2",
    "uv_index": "uv_index",
    "illuminance_lux": "illuminance_lux",
}

ARRAY_FIELDS = (
    "source_code",
    "quality_code",
    "coverage_fraction",
    "sample_count",
    "temperature_c",
    "relative_humidity_pct",
    "station_pressure_hpa",
    "wind_lull_m_s",
    "wind_average_m_s",
    "wind_gust_m_s",
    "wind_from_deg",
    "rain_raw_5min_mm",
    "rain_nearcast_5min_mm",
    "rain_display_5min_mm",
    "rain_best_estimate_5min_mm",
    "rain_selection_code",
    "local_day_rain_mm",
    "local_day_nearcast_rain_mm",
    "local_day_display_rain_mm",
    "precipitation_type_code",
    "solar_radiation_w_m2",
    "uv_index",
    "illuminance_lux",
    "lightning_strike_count",
    "lightning_average_distance_km",
    "battery_v",
)


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def load_rows(path: Path = INPUT) -> list[dict[str, Any]]:
    """Load and deduplicate exported observations by device and UTC epoch."""
    by_key: dict[tuple[str, int], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            epoch = _integer(row.get("observed_at_epoch_utc"))
            interval = _number(row.get("report_interval_minutes"))
            if epoch is None or interval is None or interval <= 0:
                continue
            row["_epoch"] = epoch
            row["_duration_s"] = interval * 60.0
            by_key[(row.get("device_id", ""), epoch)] = row
    return sorted(by_key.values(), key=lambda row: (row["_epoch"], row.get("device_id", "")))


def _coverage_seconds(intervals: Iterable[tuple[float, float]]) -> float:
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged)


def _last_value(values: list[tuple[int, float | None]]) -> float | None:
    present = [(epoch, value) for epoch, value in values if value is not None]
    return max(present, default=(0, None), key=lambda item: item[0])[1]


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Tempest reporting intervals onto left-labelled 5-minute bins."""
    if not rows:
        raise ValueError("No Tempest observations were available to aggregate")

    buckets: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "intervals": [],
            "samples": set(),
            "weighted": defaultdict(float),
            "weights": defaultdict(float),
            "wind_u": 0.0,
            "wind_v": 0.0,
            "wind_weight": 0.0,
            "lulls": [],
            "gusts": [],
            "rain_raw": 0.0,
            "rain_raw_seen": False,
            "rain_nearcast": 0.0,
            "rain_nearcast_seen": False,
            "rain_display": 0.0,
            "rain_display_seen": False,
            "rain_best": 0.0,
            "rain_best_seen": False,
            "rain_selection": set(),
            "local_day": [],
            "local_day_nearcast": [],
            "local_day_display": [],
            "precipitation": 0,
            "precipitation_seen": False,
            "lightning_count": 0.0,
            "lightning_seen": False,
            "lightning_distance_sum": 0.0,
            "lightning_distance_weight": 0.0,
            "battery": [],
        }
    )

    for row in rows:
        end = float(row["_epoch"])
        duration = float(row["_duration_s"])
        start = end - duration
        bucket = math.floor(start / STEP_SECONDS) * STEP_SECONDS
        while bucket < end:
            overlap_start = max(start, bucket)
            overlap_end = min(end, bucket + STEP_SECONDS)
            overlap = overlap_end - overlap_start
            if overlap <= 0:
                bucket += STEP_SECONDS
                continue
            part = buckets[int(bucket)]
            part["intervals"].append((overlap_start, overlap_end))
            part["samples"].add((row.get("device_id", ""), row["_epoch"]))

            for source, output in MEAN_FIELDS.items():
                value = _number(row.get(source))
                if value is not None:
                    part["weighted"][output] += value * overlap
                    part["weights"][output] += overlap

            speed = _number(row.get("wind_average_m_s"))
            direction = _number(row.get("wind_direction_degrees"))
            if speed is not None and direction is not None:
                theta = math.radians(direction % 360.0)
                # Meteorological direction is where the wind comes FROM.
                part["wind_u"] += -speed * math.sin(theta) * overlap
                part["wind_v"] += -speed * math.cos(theta) * overlap
                part["wind_weight"] += overlap

            lull = _number(row.get("wind_lull_m_s"))
            gust = _number(row.get("wind_gust_m_s"))
            if lull is not None:
                part["lulls"].append(lull)
            if gust is not None:
                part["gusts"].append(gust)

            fraction = overlap / duration
            raw_rain = _number(row.get("rain_interval_mm"))
            nearcast_rain = _number(row.get("nearcast_rain_interval_mm"))
            analysis_type = _integer(row.get("precipitation_analysis_type_code"))
            if raw_rain is not None:
                part["rain_raw"] += raw_rain * fraction
                part["rain_raw_seen"] = True
            if nearcast_rain is not None:
                part["rain_nearcast"] += nearcast_rain * fraction
                part["rain_nearcast_seen"] = True

            # Match the owner's Tempest display: use Rain Check/Nearcast only
            # when display is enabled (analysis type 1); otherwise retain raw.
            display_rain = nearcast_rain if analysis_type == 1 and nearcast_rain is not None else raw_rain
            display_code = "nearcast_display" if analysis_type == 1 and nearcast_rain is not None else "raw"
            if display_rain is not None:
                part["rain_display"] += display_rain * fraction
                part["rain_display_seen"] = True
                part["rain_selection"].add(display_code)
            best_rain = nearcast_rain if nearcast_rain is not None else raw_rain
            if best_rain is not None:
                part["rain_best"] += best_rain * fraction
                part["rain_best_seen"] = True

            part["local_day"].append((row["_epoch"], _number(row.get("local_day_rain_mm"))))
            part["local_day_nearcast"].append(
                (row["_epoch"], _number(row.get("local_day_nearcast_rain_mm")))
            )
            raw_day = _number(row.get("local_day_rain_mm"))
            nearcast_day = _number(row.get("local_day_nearcast_rain_mm"))
            display_day = nearcast_day if analysis_type == 1 and nearcast_day is not None else raw_day
            part["local_day_display"].append((row["_epoch"], display_day))

            precipitation = _integer(row.get("precipitation_type_code"))
            if precipitation is not None:
                part["precipitation"] |= precipitation
                part["precipitation_seen"] = True

            strikes = _number(row.get("lightning_strike_count"))
            distance = _number(row.get("lightning_average_distance_km"))
            if strikes is not None:
                allocated = strikes * fraction
                part["lightning_count"] += allocated
                part["lightning_seen"] = True
                if distance is not None and allocated > 0:
                    part["lightning_distance_sum"] += distance * allocated
                    part["lightning_distance_weight"] += allocated
            part["battery"].append((row["_epoch"], _number(row.get("battery_v"))))
            bucket += STEP_SECONDS

    first_bucket = min(buckets)
    last_bucket = max(buckets)
    frame_starts = list(range(first_bucket, last_bucket + STEP_SECONDS, STEP_SECONDS))
    arrays: dict[str, list[Any]] = {field: [] for field in ARRAY_FIELDS}

    for bucket in frame_starts:
        part = buckets.get(bucket)
        if not part:
            values = {field: None for field in ARRAY_FIELDS}
            values.update(
                source_code="missing",
                quality_code="missing",
                coverage_fraction=0.0,
                sample_count=0,
            )
        else:
            coverage = min(_coverage_seconds(part["intervals"]), STEP_SECONDS)
            coverage_fraction = coverage / STEP_SECONDS
            values = {
                "source_code": "tempest",
                "quality_code": "complete" if coverage_fraction >= 0.999 else "partial",
                "coverage_fraction": round(coverage_fraction, 3),
                "sample_count": len(part["samples"]),
            }
            for field in MEAN_FIELDS.values():
                weight = part["weights"].get(field, 0.0)
                values[field] = _round(part["weighted"][field] / weight if weight else None)

            values["wind_lull_m_s"] = _round(min(part["lulls"]) if part["lulls"] else None)
            values["wind_gust_m_s"] = _round(max(part["gusts"]) if part["gusts"] else None)
            if part["wind_weight"]:
                u = part["wind_u"] / part["wind_weight"]
                v = part["wind_v"] / part["wind_weight"]
                resultant = math.hypot(u, v)
                values["wind_from_deg"] = (
                    _round((math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0, 1)
                    if resultant >= CALM_THRESHOLD_M_S
                    else None
                )
            else:
                values["wind_from_deg"] = None

            values["rain_raw_5min_mm"] = _round(part["rain_raw"] if part["rain_raw_seen"] else None, 3)
            values["rain_nearcast_5min_mm"] = _round(
                part["rain_nearcast"] if part["rain_nearcast_seen"] else None, 3
            )
            values["rain_display_5min_mm"] = _round(
                part["rain_display"] if part["rain_display_seen"] else None, 3
            )
            values["rain_best_estimate_5min_mm"] = _round(
                part["rain_best"] if part["rain_best_seen"] else None, 3
            )
            codes = part["rain_selection"]
            values["rain_selection_code"] = (
                next(iter(codes)) if len(codes) == 1 else "mixed" if codes else "missing"
            )
            values["local_day_rain_mm"] = _round(_last_value(part["local_day"]), 3)
            values["local_day_nearcast_rain_mm"] = _round(
                _last_value(part["local_day_nearcast"]), 3
            )
            values["local_day_display_rain_mm"] = _round(
                _last_value(part["local_day_display"]), 3
            )
            values["precipitation_type_code"] = (
                part["precipitation"] if part["precipitation_seen"] else None
            )
            if part["lightning_seen"]:
                count = part["lightning_count"]
                values["lightning_strike_count"] = int(round(count)) if abs(count - round(count)) < 1e-9 else _round(count)
            else:
                values["lightning_strike_count"] = None
            values["lightning_average_distance_km"] = _round(
                part["lightning_distance_sum"] / part["lightning_distance_weight"]
                if part["lightning_distance_weight"]
                else None
            )
            values["battery_v"] = _round(_last_value(part["battery"]), 2)

        for field in ARRAY_FIELDS:
            arrays[field].append(values.get(field))

    latest_index = len(frame_starts) - 1
    latest = {field: arrays[field][latest_index] for field in ARRAY_FIELDS}
    latest["bucket_start_utc"] = _iso(frame_starts[latest_index])
    latest["bucket_end_utc"] = _iso(frame_starts[latest_index] + STEP_SECONDS)

    built_at = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "provider": "WeatherFlow Tempest",
        "record_type": "roof-level observed weather",
        "start_utc": _iso(first_bucket),
        "step_seconds": STEP_SECONDS,
        "n": len(frame_starts),
        "default_frame": latest_index,
        "timezone": "Europe/London",
        "bucket_semantics": (
            "UTC-aligned [start,start+5m) buckets labelled by start; Tempest reports "
            "are allocated from their preceding reporting interval"
        ),
        "built_at_utc": built_at,
        "installation": {
            "confirmed_date_local": "2026-08-12",
            "latitude": 51.49448956457452,
            "longitude": -0.09173226134409403,
            "coordinate_source": "GPS position supplied by site owner; not surveyed",
            "note": (
                "Early readings were collected during setup. The 4.28 mm local-day rain "
                "accumulation conflicts with the clear installation photograph and may be "
                "a handling/vibration artefact; it is retained but flagged, not removed."
            ),
        },
        "provenance": {
            "input": INPUT.name,
            "credential_handling": "No token or account credential is included in this file.",
            "aggregation": (
                "Duration-weighted scalar means; speed-weighted vector mean wind direction; "
                "minimum lull; maximum gust; interval rain summed; local-day rain takes the "
                "last observation; no interpolation or forward fill."
            ),
            "current_overlap_note": (
                "The current AirGradient export ends 10 August 2026 and this Tempest record "
                "starts 12 August 2026, so the twin keeps them as separate episodes."
            ),
        },
        "latest": latest,
        **arrays,
    }
    return payload


def write_csv(payload: dict[str, Any], path: Path = CSV_OUT) -> None:
    headers = ("bucket_start_utc", "bucket_end_utc", *ARRAY_FIELDS)
    start = int(datetime.fromisoformat(payload["start_utc"].replace("Z", "+00:00")).timestamp())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for index in range(payload["n"]):
            row = {
                "bucket_start_utc": _iso(start + index * STEP_SECONDS),
                "bucket_end_utc": _iso(start + (index + 1) * STEP_SECONDS),
            }
            row.update({field: payload[field][index] for field in ARRAY_FIELDS})
            writer.writerow(row)


def main() -> None:
    rows = load_rows()
    payload = aggregate_rows(rows)
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    for path in (JSON_OUT, SCENE_OUT):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded, encoding="utf-8")
    write_csv(payload)

    complete = payload["quality_code"].count("complete")
    partial = payload["quality_code"].count("partial")
    missing = payload["quality_code"].count("missing")
    latest = payload["latest"]
    print(
        f"{payload['n']} five-minute Tempest buckets: "
        f"{complete} complete, {partial} partial, {missing} missing"
    )
    print(
        f"Latest bucket {latest['bucket_start_utc']}: "
        f"{latest['temperature_c']} C, {latest['relative_humidity_pct']}% RH, "
        f"wind {latest['wind_average_m_s']} m/s"
    )
    print(f"Written to {JSON_OUT}, {CSV_OUT} and {SCENE_OUT}")


if __name__ == "__main__":
    main()
