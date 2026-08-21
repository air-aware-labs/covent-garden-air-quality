#!/usr/bin/env python3
"""Build a sanitized five-minute AirGradient episode for the digital twin.

The secure collector writes one normalized API observation per row.  This
builder maps those observations to the five named units with the explicit local
registry, aggregates them into UTC-aligned left-labelled five-minute buckets,
and writes a payload that contains measurements only.  Location IDs, serial
numbers and credentials never enter the scene payload.

The historic 5--10 August export remains a separate raw-PM episode.  Rows whose
five-minute bucket is already represented by that episode are excluded here.
Gaps are represented by null values; no concentration is interpolated.

Run from ``monitoring-2026`` with::

    python scripts/build_airgradient_observations.py

If the normalized API export does not exist, or contains no usable post-historic
rows, the command removes any previously generated Current AQ episode.  Stale
data therefore cannot remain the twin's default by accident.
"""

from __future__ import annotations

import csv
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs" / "airgradient" / "airgradient_observations.csv"
REGISTRY = ROOT / "data" / "airgradient_monitor_registry.csv"
HISTORIC = ROOT / "outputs" / "observations.json"
OUT_JSON = ROOT / "outputs" / "airgradient" / "airgradient_current_5min.json"
OUT_CSV = ROOT / "outputs" / "airgradient" / "airgradient_current_5min.csv"
SCENE_OUT = ROOT.parent / "digital-twin-v4" / "scene" / "airgradient_current.json"
SITE_GEOMETRY = ROOT.parent / "digital-twin-v4" / "site_geometry.json"

STEP_SECONDS = 300
EXPECTED_DATAPOINTS = 5
STALE_AFTER_SECONDS = 20 * 60
MIN_ALIGNMENT_POINTS = 12
HOURLY_DATAPOINT_THRESHOLD = 30

# These names are the normalized collector contract.  A few aliases keep the
# builder tolerant of early development exports without weakening the explicit
# output schema.
TIME_FIELDS = ("observed_at_utc", "observed_utc", "timestamp_utc")
EPOCH_FIELDS = ("observed_at_epoch_utc", "observed_epoch", "epoch")

MEASUREMENTS = (
    "pm1_raw_ug_m3",
    "pm25_raw_ug_m3",
    "pm10_raw_ug_m3",
    "pm1_corrected_ug_m3",
    "pm25_corrected_ug_m3",
    "pm10_corrected_ug_m3",
    "particle_count_0_3um_per_dl",
    "temperature_raw_c",
    "temperature_corrected_c",
    "relative_humidity_raw_pct",
    "relative_humidity_corrected_pct",
    "co2_raw_ppm",
    "co2_corrected_ppm",
    "tvoc_ppb",
    "tvoc_index",
    "nox_index",
    "battery_v",
    "panel_v",
    "wifi_dbm",
    "pressure_hpa",
)

ALIASES = {
    "pm1_raw_ug_m3": ("pm1_raw_ug_m3", "pm01_raw_ug_m3"),
    "pm1_corrected_ug_m3": ("pm1_corrected_ug_m3", "pm01_corrected_ug_m3"),
    "particle_count_0_3um_per_dl": ("particle_count_0_3um_per_dl", "particle_count_0_3_um"),
    "relative_humidity_raw_pct": ("relative_humidity_raw_pct", "relative_humidity_raw_percent"),
    "relative_humidity_corrected_pct": (
        "relative_humidity_corrected_pct",
        "relative_humidity_corrected_percent",
    ),
}

ROUND_DIGITS = {
    "battery_v": 3,
    "panel_v": 3,
    "wifi_dbm": 1,
    "pressure_hpa": 1,
    "particle_count_0_3um_per_dl": 0,
}


@dataclass(frozen=True)
class Monitor:
    location_id: str
    unit: str
    serial_number: str
    location_type: str
    correction: str
    correction_effective_date: str
    deployment_status: str


def _text(row: Mapping[str, object], names: Sequence[str]) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _number(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number >= 0 else None


def _epoch(row: Mapping[str, object]) -> int:
    epoch = _integer(_text(row, EPOCH_FIELDS))
    if epoch is not None:
        return epoch
    raw = _text(row, TIME_FIELDS)
    if not raw:
        raise ValueError("AirGradient row has no UTC observation time")
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("AirGradient observation time must include a UTC offset")
    return int(parsed.timestamp())


def iso_utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_registry(path: Path = REGISTRY) -> list[Monitor]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    monitors = [
        Monitor(
            location_id=row["location_id"].strip(),
            unit=row["unit"].strip(),
            serial_number=row["serial_number"].strip().lower(),
            location_type=row["location_type"].strip(),
            correction=row["pm_correction"].strip(),
            correction_effective_date=row["correction_effective_date"].strip(),
            deployment_status=row["deployment_status"].strip(),
        )
        for row in rows
    ]
    if not monitors:
        raise ValueError("AirGradient monitor registry is empty")
    keys = {(m.location_id, m.serial_number) for m in monitors}
    units = {m.unit for m in monitors}
    if len(keys) != len(monitors) or len(units) != len(monitors):
        raise ValueError("AirGradient monitor registry has duplicate identity or unit entries")
    return sorted(monitors, key=lambda monitor: int(monitor.unit))


def load_scene_deployment_status(path: Path = SITE_GEOMETRY) -> dict[str, str]:
    """Read public-safe installed/proposed state from the scene geometry.

    The private provider registry establishes instrument identity. Geometry is
    the authority for physical placement so a cached GitHub Actions registry
    cannot make a confirmed installation appear provisional again.
    """
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    positions = payload.get("monitor_states", {}).get("deployment", {}).get("positions", {})
    return {
        str(unit): str(position.get("deployment_status", "")).strip()
        for unit, position in positions.items()
        if str(position.get("deployment_status", "")).strip()
    }


def historic_last_bucket(path: Path = HISTORIC) -> int | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload or not payload.get("start_utc") or not payload.get("n"):
        return None
    start = _epoch({"observed_at_utc": payload["start_utc"]})
    step = int(payload.get("step_seconds") or int(payload.get("step_minutes", 5)) * 60)
    return start + (int(payload["n"]) - 1) * step


def audit_timestamp_alignment(
    rows: Sequence[Mapping[str, object]],
    monitors: Sequence[Monitor],
    historic_path: Path = HISTORIC,
    minimum_points: int = MIN_ALIGNMENT_POINTS,
) -> dict[str, object]:
    """Compare API raw PM against the fixed export at -5, 0 and +5 minutes.

    AirGradient documents five-minute history but not whether its timestamps label
    the start or end of each bucket. The existing dashboard export is known to be
    left-labelled, so overlapping raw PM provides an empirical, site-specific audit.
    Provider identities and readings are never included in the returned summary.
    """

    if not historic_path.exists():
        return {"verified": False, "status": "historic_reference_missing", "pairs": 0}
    historic = json.loads(historic_path.read_text(encoding="utf-8"))
    if not historic or not historic.get("start_utc") or not historic.get("pm25"):
        return {"verified": False, "status": "historic_reference_invalid", "pairs": 0}
    step = int(
        historic.get("step_seconds")
        or int(historic.get("step_minutes", 5)) * 60
    )
    if step != STEP_SECONDS:
        return {"verified": False, "status": "historic_cadence_mismatch", "pairs": 0}

    start = _epoch({"observed_at_utc": historic["start_utc"]})
    identity_to_unit = {
        (monitor.location_id, monitor.serial_number): monitor.unit for monitor in monitors
    }
    errors: dict[int, list[float]] = {-STEP_SECONDS: [], 0: [], STEP_SECONDS: []}
    for row in rows:
        try:
            location_id = _text(row, ("location_id",))
            serial = _text(row, ("serial_number", "sensor_id")).lower().removeprefix(
                "airgradient:"
            )
            unit = identity_to_unit.get((location_id, serial))
            raw = _measurement(row, "pm25_raw_ug_m3")
            bucket = _epoch(row) // STEP_SECONDS * STEP_SECONDS
        except (TypeError, ValueError, OverflowError):
            continue
        series = historic.get("pm25", {}).get(unit) if unit else None
        if raw is None or not isinstance(series, list):
            continue
        for shift in errors:
            target = bucket + shift
            delta = target - start
            if delta < 0 or delta % STEP_SECONDS:
                continue
            index = delta // STEP_SECONDS
            if index >= len(series):
                continue
            reference = _number(series[index])
            if reference is not None:
                errors[shift].append(abs(raw - reference))

    counts = {str(shift): len(values) for shift, values in errors.items()}
    if max(counts.values(), default=0) < minimum_points:
        return {
            "verified": False,
            "status": "insufficient_overlap",
            "pairs": max(counts.values(), default=0),
            "comparison_counts": counts,
        }
    means = {
        shift: sum(values) / len(values)
        for shift, values in errors.items()
        if len(values) >= minimum_points
    }
    ranked = sorted((value, abs(shift), shift) for shift, value in means.items())
    best_error, _, best_shift = ranked[0]
    second_error = ranked[1][0] if len(ranked) > 1 else math.inf
    summary: dict[str, object] = {
        "verified": False,
        "status": "ambiguous_overlap",
        "pairs": len(errors[best_shift]),
        "comparison_counts": counts,
        "mean_absolute_error_ug_m3": {
            str(shift): round(value, 4) for shift, value in means.items()
        },
    }
    if second_error - best_error <= 1e-6:
        return summary
    if best_shift == 0:
        summary.update(verified=True, status="verified_left_labelled")
        return summary
    zero_error = means.get(0, math.inf)
    if best_error + 0.05 < zero_error:
        summary.update(status="shift_detected", suggested_shift_seconds=best_shift)
    return summary


def validate_five_minute_resolution(rows: Sequence[Mapping[str, object]]) -> None:
    """Reject data that clearly represents AirGradient's hourly fallback."""

    hourly_like = sum(
        1
        for row in rows
        if (_integer(row.get("datapoints")) or 0) >= HOURLY_DATAPOINT_THRESHOLD
    )
    if hourly_like >= 2:
        raise ValueError(
            "AirGradient export contains hourly aggregate rows; refusing to place them "
            "on a five-minute timeline"
        )


def clear_outputs(
    paths: Sequence[Path] = (OUT_JSON, OUT_CSV, SCENE_OUT),
) -> list[Path]:
    """Remove generated Current AQ artifacts so a stale episode cannot linger."""

    removed: list[Path] = []
    for path in paths:
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def _measurement(row: Mapping[str, object], name: str) -> float | None:
    return _number(_text(row, ALIASES.get(name, (name,))))


def _mean(values: Iterable[float | None], name: str) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    digits = ROUND_DIGITS.get(name, 2)
    return round(sum(present) / len(present), digits)


def _source(rows: Sequence[Mapping[str, object]]) -> str:
    codes = sorted({_text(row, ("source_code",)) for row in rows if _text(row, ("source_code",))})
    if not codes:
        return "airgradient_api"
    return codes[0] if len(codes) == 1 else "mixed_api"


def _quality(rows: Sequence[Mapping[str, object]], source: str) -> tuple[str, float | None, int | None]:
    datapoints = [_integer(row.get("datapoints")) for row in rows]
    known = [value for value in datapoints if value is not None]
    total = sum(known) if known else None
    if total is not None:
        coverage = round(min(total / EXPECTED_DATAPOINTS, 1.0), 3)
    elif source in {"airgradient_raw", "mixed_api"}:
        coverage = round(min(len(rows) / EXPECTED_DATAPOINTS, 1.0), 3)
    else:
        coverage = None
    if coverage is None:
        quality = "observed"
    else:
        quality = "complete" if coverage >= 1 else "partial"
    return quality, coverage, total


def _empty_by_unit(units: Sequence[str], n: int) -> dict[str, list[object | None]]:
    return {unit: [None] * n for unit in units}


def build_payload(
    rows: Iterable[Mapping[str, object]],
    monitors: Sequence[Monitor],
    historic_last: int | None,
    alignment: Mapping[str, object] | None = None,
    now_epoch: int | None = None,
    deployment_status_by_unit: Mapping[str, str] | None = None,
) -> tuple[dict[str, object] | None, list[dict[str, object]], dict[str, int]]:
    """Return sanitized scene payload, long five-minute rows, and import counts."""

    by_identity = {(m.location_id, m.serial_number): m for m in monitors}
    scene_status = dict(deployment_status_by_unit or {})
    buckets: dict[int, dict[str, list[Mapping[str, object]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    counts = {"accepted": 0, "historic_overlap": 0, "unknown_identity": 0, "invalid": 0}

    for row in rows:
        try:
            location_id = _text(row, ("location_id",))
            serial = _text(row, ("serial_number", "sensor_id")).lower().removeprefix(
                "airgradient:"
            )
            monitor = by_identity.get((location_id, serial))
            if monitor is None:
                counts["unknown_identity"] += 1
                continue
            bucket = _epoch(row) // STEP_SECONDS * STEP_SECONDS
        except (TypeError, ValueError, OverflowError):
            counts["invalid"] += 1
            continue
        if historic_last is not None and bucket <= historic_last:
            counts["historic_overlap"] += 1
            continue
        buckets[bucket][monitor.unit].append(row)
        counts["accepted"] += 1

    if not buckets:
        return None, [], counts

    units = [m.unit for m in monitors]
    outdoor = [m.unit for m in monitors if m.location_type.lower() == "outdoor"]
    indoor = next((m.unit for m in monitors if m.location_type.lower() == "indoor"), None)
    first = min(buckets)
    last = max(buckets)
    n = (last - first) // STEP_SECONDS + 1

    measurement_arrays = {name: _empty_by_unit(units, n) for name in MEASUREMENTS}
    display = _empty_by_unit(units, n)
    display_basis = _empty_by_unit(units, n)
    quality = _empty_by_unit(units, n)
    coverage = _empty_by_unit(units, n)
    sample_count = _empty_by_unit(units, n)
    datapoints = _empty_by_unit(units, n)
    source_code = _empty_by_unit(units, n)
    long_rows: list[dict[str, object]] = []

    monitor_by_unit = {monitor.unit: monitor for monitor in monitors}
    for bucket, by_unit in sorted(buckets.items()):
        frame = (bucket - first) // STEP_SECONDS
        for unit, group in sorted(by_unit.items(), key=lambda item: int(item[0])):
            source = _source(group)
            qcode, fraction, point_count = _quality(group, source)
            values = {
                name: _mean((_measurement(row, name) for row in group), name)
                for name in MEASUREMENTS
            }
            raw = values["pm25_raw_ug_m3"]
            corrected = values["pm25_corrected_ug_m3"]
            shown = corrected if corrected is not None else raw
            basis = "corrected" if corrected is not None else "raw" if raw is not None else None
            for name, value in values.items():
                measurement_arrays[name][unit][frame] = value
            display[unit][frame] = shown
            display_basis[unit][frame] = basis
            quality[unit][frame] = qcode
            coverage[unit][frame] = fraction
            sample_count[unit][frame] = len(group)
            datapoints[unit][frame] = point_count
            source_code[unit][frame] = source

            monitor = monitor_by_unit[unit]
            long_rows.append(
                {
                    "bucket_start_utc": iso_utc(bucket),
                    "bucket_end_utc": iso_utc(bucket + STEP_SECONDS),
                    "unit": unit,
                    "location_type": monitor.location_type,
                    "deployment_status": scene_status.get(unit, monitor.deployment_status),
                    "source_code": source,
                    "quality_code": qcode,
                    "coverage_fraction": "" if fraction is None else fraction,
                    "sample_count": len(group),
                    "datapoints": "" if point_count is None else point_count,
                    "pm25_display_ug_m3": "" if shown is None else shown,
                    "pm25_display_basis": basis or "",
                    **{name: "" if value is None else value for name, value in values.items()},
                }
            )

    observed_frames = sum(1 for frame in range(n) if any(display[u][frame] is not None for u in units))
    default_frame = max(
        (frame for frame in range(n) if any(display[u][frame] is not None for u in units)),
        default=0,
    )
    correction_register = {
        m.unit: {
            "pm_correction": m.correction,
            "effective_date": m.correction_effective_date,
        }
        for m in monitors
    }
    built_at = int(time.time() if now_epoch is None else now_epoch)
    age_seconds = max(0, built_at - last)
    alignment_summary = dict(
        alignment
        or {"verified": False, "status": "not_audited", "pairs": 0}
    )
    payload: dict[str, object] = {
        "episode_kind": "current_airgradient_api",
        "episode_label": "Current AQ",
        "start_utc": iso_utc(first),
        "end_utc": iso_utc(last),
        "latest_observation_utc": iso_utc(last),
        "built_at_utc": iso_utc(built_at),
        "age_seconds_at_build": age_seconds,
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "stale_at_build": age_seconds > STALE_AFTER_SECONDS,
        "step_seconds": STEP_SECONDS,
        "step_minutes": STEP_SECONDS // 60,
        "n": n,
        "observed_frame_count": observed_frames,
        "missing_frame_count": n - observed_frames,
        "units": units,
        "outdoor_units": outdoor,
        "indoor_unit": indoor,
        "channel": "PM2.5 corrected where present; raw retained",
        "default_frame": default_frame,
        "pm25": display,
        "pm25_display_basis": display_basis,
        "quality_code": quality,
        "coverage_fraction": coverage,
        "sample_count": sample_count,
        "datapoints": datapoints,
        "source_code": source_code,
        **measurement_arrays,
        "correction_register": correction_register,
        "deployment_status": {
            monitor.unit: scene_status.get(monitor.unit, monitor.deployment_status)
            for monitor in monitors
        },
        "timestamp_alignment": alignment_summary,
        "provenance": {
            "source": INPUT.name,
            "aggregation": (
                "UTC-aligned left-labelled five-minute buckets; arithmetic mean only when "
                "multiple API observations occupy the same unit/bucket; no interpolation. "
                f"Timestamp audit: {alignment_summary.get('status', 'not_audited')}"
            ),
            "identity": "Stable local location/device registry mapped to public unit numbers",
            "display": "Corrected PM2.5 where supplied by the API, otherwise raw PM2.5",
            "historic_boundary": (
                None if historic_last is None else iso_utc(historic_last)
            ),
            "placement": (
                "AQ values identify instruments by stable local unit number. The installed "
                "scene hosts and mounts were confirmed by the 19 August 2026 photographs; "
                "their plan positions and heights remain approximate rather than surveyed."
            ),
        },
    }
    return payload, long_rows, counts


def write_outputs(
    payload: Mapping[str, object],
    long_rows: Sequence[Mapping[str, object]],
    out_json: Path = OUT_JSON,
    out_csv: Path = OUT_CSV,
    scene_out: Path = SCENE_OUT,
) -> None:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # The browser payload is intentionally identity-free.  The local long CSV
    # uses unit numbers too; provider IDs remain only in the normalized archive.
    lowered = encoded.lower()
    if "serial_number" in lowered or "airgradient_token" in lowered:
        raise ValueError("Unsafe AirGradient identity or credential field in scene payload")
    for path in (out_json, scene_out):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded, encoding="utf-8")

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "bucket_start_utc",
        "bucket_end_utc",
        "unit",
        "location_type",
        "deployment_status",
        "source_code",
        "quality_code",
        "coverage_fraction",
        "sample_count",
        "datapoints",
        "pm25_display_ug_m3",
        "pm25_display_basis",
        *MEASUREMENTS,
    ]
    with out_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(long_rows)


def main() -> int:
    if not INPUT.exists():
        removed = clear_outputs()
        print(
            f"AirGradient export not found; Current AQ omitted: {INPUT} "
            f"({len(removed)} stale generated file(s) removed)"
        )
        return 0
    monitors = load_registry()
    with INPUT.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    validate_five_minute_resolution(rows)
    alignment = audit_timestamp_alignment(rows, monitors)
    if alignment.get("status") == "shift_detected":
        raise ValueError(
            "AirGradient API timestamps are shifted relative to the historic export; "
            "refusing to join AQ and Tempest until the bucket semantics are resolved"
        )
    payload, long_rows, counts = build_payload(
        rows,
        monitors,
        historic_last_bucket(),
        alignment=alignment,
        deployment_status_by_unit=load_scene_deployment_status(),
    )
    if payload is None:
        removed = clear_outputs()
        print(
            "No post-historic AirGradient observations were available; "
            f"Current AQ omitted ({counts}; {len(removed)} stale generated file(s) removed)"
        )
        return 0
    write_outputs(payload, long_rows)
    print(
        f"AirGradient current AQ: {payload['n']} five-minute frames, "
        f"{payload['observed_frame_count']} with measurements; {counts}"
    )
    print(f"  {OUT_CSV}")
    print(f"  {SCENE_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
