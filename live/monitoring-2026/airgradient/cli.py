"""Command-line interface for secure AirGradient collection and export."""

from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .api import AirGradientAPI, ApiError, AuthenticationError, NoDataError
from .config import (
    Config,
    ConfigError,
    PROJECT_ROOT,
    database_path,
    read_env_file,
    write_config,
)
from .export import export_csv
from .observations import ObservationError, parse_measure, parse_measures_payload
from .storage import ObservationStore, StorageError


MAX_API_RANGE_SECONDS = 10 * 24 * 60 * 60 - 1
RECONCILE_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_EXPORT = PROJECT_ROOT / "outputs" / "airgradient" / "airgradient_observations.csv"


def utc_text(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def api_time(epoch: int) -> str:
    """AirGradient's documented ISO 8601 basic UTC format."""

    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_boundary(value: str, end_of_date: bool = False) -> int:
    value = value.strip()
    if len(value) == 10:
        parsed_date = date.fromisoformat(value)
        parsed = datetime.combine(parsed_date, datetime_time.min, tzinfo=timezone.utc)
        if end_of_date:
            return int((parsed + timedelta(days=1)).timestamp()) - 1
        return int(parsed.timestamp())
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed_datetime = datetime.fromisoformat(normalized)
    if parsed_datetime.tzinfo is None:
        raise ConfigError(
            "AirGradient date/time boundaries must include a UTC offset (for example Z)"
        )
    return int(parsed_datetime.astimezone(timezone.utc).timestamp())


def time_chunks(
    start_epoch: int,
    end_epoch: int,
    max_span_seconds: int = MAX_API_RANGE_SECONDS,
) -> Iterable[Tuple[int, int]]:
    """Yield inclusive windows within the API's documented ten-day maximum."""

    if max_span_seconds < 0:
        raise ValueError("max_span_seconds must be non-negative")
    cursor = int(start_epoch)
    end_epoch = int(end_epoch)
    while cursor <= end_epoch:
        chunk_end = min(end_epoch, cursor + max_span_seconds)
        yield cursor, chunk_end
        cursor = chunk_end + 1


def _description(measurement: Dict[str, Any]) -> str:
    name = measurement.get("location_name") or "Unnamed"
    kind = measurement.get("location_type") or "unspecified"
    model = measurement.get("model") or "unknown model"
    return f"{name} ({kind}, {model}, ID {measurement['location_id']})"


def _select_locations(
    current: Sequence[Dict[str, Any]], requested_ids: Sequence[int]
) -> Tuple[Dict[str, Any], ...]:
    by_id = {int(row["location_id"]): row for row in current}
    if requested_ids:
        return tuple(
            by_id[location_id]
            for location_id in dict.fromkeys(int(value) for value in requested_ids)
            if location_id in by_id
        )
    if not current:
        raise ConfigError("No AirGradient locations with current measurements were found")
    if len(current) == 1:
        return (current[0],)
    if not sys.stdin.isatty():
        raise ConfigError(
            "More than one AirGradient location was found; repeat --location-id to select them"
        )

    print("\nChoose one or more locations:")
    for index, row in enumerate(current, 1):
        print(f"  {index}. {_description(row)}")
    while True:
        answer = input("Location numbers (comma-separated, or 'all'): ").strip().lower()
        if answer == "all":
            return tuple(current)
        try:
            indexes = tuple(dict.fromkeys(int(part.strip()) for part in answer.split(",")))
        except ValueError:
            indexes = ()
        if indexes and all(1 <= index <= len(current) for index in indexes):
            return tuple(current[index - 1] for index in indexes)
        print(f"Enter numbers from 1 to {len(current)}, or 'all'.")


def _validated_discovery(
    api: AirGradientAPI,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    place = api.place()
    try:
        rows = api.locations_current()
    except NoDataError:
        rows = []
    parsed = parse_measures_payload(rows)
    return place, parsed


def configure(args: argparse.Namespace) -> int:
    file_values = read_env_file()
    environment_token = os.environ.get("AIRGRADIENT_TOKEN")
    if args.new_token and environment_token:
        raise ConfigError(
            "AIRGRADIENT_TOKEN is set in the process environment and would override the "
            "new file. Remove or update it before using --new-token."
        )
    token = None if args.new_token else (
        environment_token or file_values.get("AIRGRADIENT_TOKEN")
    )
    if not token:
        print(
            "Regenerate the token in AirGradient Dashboard > General Settings > "
            "Connectivity before configuring this collector."
        )
        token = getpass.getpass("Paste the replacement API token (input is hidden): ").strip()
    if not token:
        raise ConfigError("No AirGradient API token was entered")
    if any(character.isspace() for character in token):
        raise ConfigError("The AirGradient API token contains whitespace")

    print("Checking the token and discovering locations...")
    api = AirGradientAPI(token)
    try:
        place, current = _validated_discovery(api)
    except AuthenticationError:
        if environment_token:
            raise ConfigError(
                "The AIRGRADIENT_TOKEN environment variable was rejected. Remove or update "
                "it, then run configure again."
            ) from None
        if args.new_token or not sys.stdin.isatty():
            raise
        print("The saved token was rejected. Regenerate and paste its replacement.")
        token = getpass.getpass("Replacement token (input is hidden): ").strip()
        if not token or any(character.isspace() for character in token):
            raise ConfigError("No valid replacement token was entered")
        api = AirGradientAPI(token)
        place, current = _validated_discovery(api)

    requested_ids = tuple(
        dict.fromkeys(int(value) for value in (args.location_ids or ()))
    )
    selected = _select_locations(current, requested_ids)
    configured_ids = requested_ids or tuple(
        int(row["location_id"]) for row in selected
    )
    available_ids = {int(row["location_id"]) for row in selected}
    unavailable_ids = tuple(
        location_id for location_id in configured_ids if location_id not in available_ids
    )
    config = Config(
        token=token,
        location_ids=configured_ids,
    )
    path = write_config(config)
    with ObservationStore(database_path()) as store:
        store.save_metadata(place, selected)

    print(f"Configured place: {place.get('name') or 'Unnamed'}")
    for row in selected:
        print(f"Configured location: {_description(row)}")
    for location_id in unavailable_ids:
        print(
            f"Warning: location {location_id} has no current measurement; "
            "it was configured without live metadata."
        )
    print(f"Credentials saved: {path}")
    print("The token was not displayed. Keep .airgradient.env private.")
    return 0


def doctor(args: argparse.Namespace) -> int:
    config = Config.load()
    api = AirGradientAPI(config.token)
    place, current = _validated_discovery(api)
    by_id = {int(row["location_id"]): row for row in current}
    unavailable = [location_id for location_id in config.location_ids if location_id not in by_id]
    available = [location_id for location_id in config.location_ids if location_id in by_id]
    if not available:
        raise ConfigError(
            "None of the configured AirGradient locations has an accessible current reading"
        )
    if unavailable:
        print(
            "Warning: configured AirGradient location(s) currently unavailable: "
            + ", ".join(str(value) for value in unavailable)
        )

    with ObservationStore(database_path()) as store:
        store.save_metadata(place, (by_id[value] for value in available))
        database_rows = store.observation_count()

    print("AirGradient API connection: OK")
    print(
        f"Place: {place.get('name') or 'Unnamed'} "
        f"({place.get('timezoneId') or 'timezone not supplied'})"
    )
    now = int(time.time())
    for location_id in available:
        row = by_id[location_id]
        age_minutes = max(0, now - int(row["observed_at_utc"])) // 60
        print(f"Location: {_description(row)}")
        print(f"  Latest: {utc_text(int(row['observed_at_utc']))} ({age_minutes} minute(s) old)")
        if age_minutes > 15:
            print("  Warning: this current reading is more than 15 minutes old.")
    print(f"Database check: OK ({database_rows} stored measurement(s))")
    return 0


def _sync_window(
    args: argparse.Namespace,
    latest_epoch: Optional[int],
    last_reconcile_epoch: Optional[int],
    now: Optional[int] = None,
) -> Tuple[int, int, bool]:
    now = int(now if now is not None else time.time())
    if args.from_time:
        start = parse_boundary(args.from_time)
    elif latest_epoch is not None:
        start = max(1, int(latest_epoch) - args.overlap_minutes * 60)
    else:
        start = max(1, now - args.initial_days * 24 * 60 * 60 + 1)
    end = parse_boundary(args.to_time, end_of_date=True) if args.to_time else now
    end = min(end, now)

    full_reconcile = False
    if not args.from_time and not args.to_time:
        reconcile_due = (
            last_reconcile_epoch is None
            or now - int(last_reconcile_epoch) >= RECONCILE_INTERVAL_SECONDS
        )
        reconcile_start = max(1, now - MAX_API_RANGE_SECONDS)
        if latest_epoch is not None and reconcile_due:
            start = min(start, reconcile_start)
        full_reconcile = start <= reconcile_start
    if start > end:
        raise ConfigError("The requested sync start is after its end")
    return start, end, full_reconcile


def _reject_hourly_history(
    measurements: Sequence[Dict[str, Any]], location_id: int
) -> None:
    """Fail closed when /past has clearly degraded to hourly buckets.

    AirGradient documents that older /past data may be returned in either
    five-minute or 60-minute buckets.  Four dominant adjacent hourly gaps are
    enough evidence to stop; sparse five-minute data is otherwise left alone.
    """

    epochs = sorted({int(row["observed_at_utc"]) for row in measurements})
    gaps = [stop - start for start, stop in zip(epochs, epochs[1:]) if stop > start]
    if len(gaps) < 4:
        return
    hourly = sum(abs(gap - 3600) <= 60 for gap in gaps)
    if hourly >= 4 and hourly / len(gaps) >= 0.75:
        raise ObservationError(
            f"AirGradient returned predominantly hourly history for location {location_id}; "
            "refusing to store it as five-minute data"
        )


def sync(args: argparse.Namespace) -> int:
    config = Config.load()
    api = AirGradientAPI(config.token)
    totals = {"requests": 0, "received": 0, "inserted": 0, "updated": 0, "unchanged": 0}
    now = int(time.time())

    # Refresh the current physical sensor assignment before importing history.
    # This makes ObservationStore's serial-change guard effective on every sync,
    # not only when configure/doctor happens to be run first.
    place, current = _validated_discovery(api)
    configured = set(config.location_ids)
    current_configured = [
        row for row in current if int(row["location_id"]) in configured
    ]

    with ObservationStore(database_path()) as store:
        store.save_metadata(place, current_configured)
        for location_id in config.location_ids:
            start, end, full_reconcile = _sync_window(
                args,
                store.latest_epoch(location_id),
                store.last_reconcile_epoch(location_id),
                now,
            )
            for chunk_start, chunk_end in time_chunks(start, end):
                try:
                    payload = api.location_past(
                        location_id, api_time(chunk_start), api_time(chunk_end)
                    )
                except NoDataError:
                    totals["requests"] += 1
                    print(
                        f"Location {location_id}: no data in "
                        f"{utc_text(chunk_start)} to {utc_text(chunk_end)}; continuing"
                    )
                    continue
                measurements = parse_measures_payload(payload, location_id)
                _reject_hourly_history(measurements, location_id)
                result = store.upsert_observations(location_id, measurements)
                totals["requests"] += 1
                totals["received"] += result.received
                totals["inserted"] += result.inserted
                totals["updated"] += result.updated
                totals["unchanged"] += result.unchanged
            if full_reconcile:
                store.mark_reconciled(location_id, now)
            newest = store.latest_epoch(location_id)
            count = store.observation_count(location_id)
            print(
                f"Location {location_id}: {utc_text(start)} to {utc_text(end)}; "
                f"{count} stored"
            )
            if newest is not None:
                print(
                    f"  Newest: {utc_text(newest)} "
                    f"({max(0, now - newest) // 60} minute(s) old)"
                )
            if full_reconcile:
                print("  Reconciliation: refreshed the recent ten-day window")

    print(
        f"API requests: {totals['requests']}; measurements received: {totals['received']}"
    )
    print(
        f"Database: {totals['inserted']} new, {totals['updated']} revised, "
        f"{totals['unchanged']} unchanged"
    )
    return 0


def _number(value: Any, decimals: int = 1) -> str:
    return "-" if value is None else f"{float(value):.{decimals}f}"


def latest(args: argparse.Namespace) -> int:
    config = Config.load()
    api = AirGradientAPI(config.token)
    measurements = [
        parse_measure(api.location_current(location_id), location_id)
        for location_id in config.location_ids
    ]
    with ObservationStore(database_path()) as store:
        for measurement in measurements:
            store.upsert_observations(int(measurement["location_id"]), [measurement])

    for row in measurements:
        print(
            f"AirGradient location {row['location_id']} - "
            f"{utc_text(int(row['observed_at_utc']))}"
        )
        print(
            "  PM2.5:      "
            f"{_number(row.get('pm25_corrected_ug_m3'))} corrected / "
            f"{_number(row.get('pm25_raw_ug_m3'))} raw ug/m3"
        )
        print(
            "  Temperature: "
            f"{_number(row.get('temperature_corrected_c'))} corrected / "
            f"{_number(row.get('temperature_raw_c'))} raw C"
        )
        print(
            "  Humidity:    "
            f"{_number(row.get('relative_humidity_corrected_pct'), 0)} corrected / "
            f"{_number(row.get('relative_humidity_raw_pct'), 0)} raw %"
        )
        print(f"  CO2 raw:     {_number(row.get('co2_raw_ppm'), 0)} ppm")
    return 0


def export_command(args: argparse.Namespace) -> int:
    config = Config.load()
    db_path = database_path()
    if not db_path.exists():
        raise ConfigError("The AirGradient database does not exist yet; run sync first")
    start = parse_boundary(args.from_time) if args.from_time else None
    end_exclusive = (
        parse_boundary(args.to_time, end_of_date=True) + 1 if args.to_time else None
    )
    output = Path(args.output).expanduser() if args.output else DEFAULT_EXPORT
    if output.resolve() == db_path.resolve():
        raise ConfigError("The CSV output path cannot be the AirGradient SQLite database")
    selected = tuple(args.location_ids or config.location_ids)
    unknown = sorted(set(selected) - set(config.location_ids))
    if unknown:
        raise ConfigError(
            "Requested location(s) are not configured: "
            + ", ".join(str(value) for value in unknown)
        )
    with ObservationStore(db_path) as store:
        count = export_csv(
            store.iter_observations(selected, start, end_exclusive), output
        )
    print(f"Exported {count} measurement(s) to {output.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m airgradient",
        description="Collect AirGradient cloud measurements into SQLite and CSV.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser = subparsers.add_parser(
        "configure", help="securely save a token and discover monitor locations"
    )
    configure_parser.add_argument(
        "--location-id", dest="location_ids", action="append", type=int
    )
    configure_parser.add_argument(
        "--new-token", action="store_true", help="prompt for a replacement token"
    )
    configure_parser.set_defaults(handler=configure)

    doctor_parser = subparsers.add_parser(
        "doctor", help="verify authentication, locations, current data and SQLite"
    )
    doctor_parser.set_defaults(handler=doctor)

    sync_parser = subparsers.add_parser(
        "sync", help="incrementally collect recent five-minute dashboard history"
    )
    sync_parser.add_argument(
        "--initial-days", type=int, default=10,
        help="days to fetch when SQLite is empty (default: 10)",
    )
    sync_parser.add_argument(
        "--overlap-minutes", type=int, default=15,
        help="re-fetch this overlap to capture provider corrections (default: 15)",
    )
    sync_parser.add_argument("--from", dest="from_time", help="UTC ISO date/time override")
    sync_parser.add_argument("--to", dest="to_time", help="inclusive UTC ISO date/time")
    sync_parser.set_defaults(handler=sync)

    latest_parser = subparsers.add_parser(
        "latest", help="show and store the latest reading for each configured location"
    )
    latest_parser.set_defaults(handler=latest)

    export_parser = subparsers.add_parser(
        "export", help="export stored measurements to a stable CSV schema"
    )
    export_parser.add_argument("--from", dest="from_time", help="inclusive UTC boundary")
    export_parser.add_argument("--to", dest="to_time", help="inclusive UTC boundary")
    export_parser.add_argument("--output", help="CSV path")
    export_parser.add_argument(
        "--location-id", dest="location_ids", action="append", type=int
    )
    export_parser.set_defaults(handler=export_command)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "initial_days", 1) < 1:
        parser.error("--initial-days must be at least 1")
    if getattr(args, "overlap_minutes", 0) < 0:
        parser.error("--overlap-minutes must not be negative")
    if any(value <= 0 for value in (getattr(args, "location_ids", None) or ())):
        parser.error("--location-id must be positive")
    try:
        return int(args.handler(args))
    except (
        ConfigError,
        ApiError,
        ObservationError,
        StorageError,
        sqlite3.Error,
        OSError,
        ValueError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
