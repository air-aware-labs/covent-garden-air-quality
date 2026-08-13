"""Command-line interface for configuring, collecting and exporting Tempest data."""

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

from .api import ApiError, AuthenticationError, TempestAPI
from .config import (
    Config,
    ConfigError,
    PROJECT_ROOT,
    database_path,
    read_env_file,
    write_config,
)
from .export import export_csv
from .observations import ObservationError, parse_device_payload
from .storage import ObservationStore, StorageError


MAX_API_RANGE_SECONDS = 5 * 24 * 60 * 60 - 1
RECONCILE_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_EXPORT = PROJECT_ROOT / "outputs" / "tempest" / "tempest_observations.csv"
COMPASS_POINTS = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)


def utc_text(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def local_text(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_boundary(value: str, end_of_date: bool = False) -> int:
    """Parse an ISO date/time as UTC; date-only end bounds include the whole day."""

    value = value.strip()
    if len(value) == 10:
        parsed_date = date.fromisoformat(value)
        parsed = datetime.combine(parsed_date, datetime_time.min, tzinfo=timezone.utc)
        if end_of_date:
            parsed += timedelta(days=1)
            return int(parsed.timestamp()) - 1
        return int(parsed.timestamp())
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed_datetime = datetime.fromisoformat(normalized)
    if parsed_datetime.tzinfo is None:
        parsed_datetime = parsed_datetime.replace(tzinfo=timezone.utc)
    return int(parsed_datetime.astimezone(timezone.utc).timestamp())


def time_chunks(
    start_epoch: int,
    end_epoch: int,
    max_span_seconds: int = MAX_API_RANGE_SECONDS,
) -> Iterable[Tuple[int, int]]:
    """Yield inclusive, non-overlapping API windows no longer than five days."""

    if max_span_seconds < 0:
        raise ValueError("max_span_seconds must be non-negative")
    cursor = int(start_epoch)
    end_epoch = int(end_epoch)
    while cursor <= end_epoch:
        chunk_end = min(end_epoch, cursor + max_span_seconds)
        yield cursor, chunk_end
        cursor = chunk_end + 1


def _choose(items: Sequence[Dict[str, Any]], label: str, describe: Any) -> Dict[str, Any]:
    if not items:
        raise ConfigError(f"No {label}s were found")
    if len(items) == 1:
        return items[0]
    if not sys.stdin.isatty():
        raise ConfigError(f"More than one {label} was found; specify its ID explicitly")
    print(f"\nChoose a {label}:")
    for index, item in enumerate(items, 1):
        print(f"  {index}. {describe(item)}")
    while True:
        answer = input(f"{label.capitalize()} number: ").strip()
        try:
            selection = int(answer)
        except ValueError:
            selection = 0
        if 1 <= selection <= len(items):
            return items[selection - 1]
        print(f"Enter a number from 1 to {len(items)}.")


def _selected_station(
    stations: Sequence[Dict[str, Any]], requested_id: Optional[int]
) -> Dict[str, Any]:
    if requested_id is not None:
        for station in stations:
            if int(station.get("station_id", 0)) == requested_id:
                return station
        raise ConfigError(f"Station {requested_id} was not found in this Tempest account")
    return _choose(
        stations,
        "station",
        lambda station: f"{station.get('name') or 'Unnamed'} (ID {station.get('station_id')})",
    )


def _device_description(device: Dict[str, Any]) -> str:
    metadata = device.get("device_meta") or {}
    name = metadata.get("name") or "Tempest device"
    serial = device.get("serial_number") or "no active serial"
    return f"{name} — {serial} (ID {device.get('device_id')})"


def _likely_tempest(device: Dict[str, Any]) -> bool:
    serial = str(device.get("serial_number") or "").upper()
    device_type = str(device.get("device_type") or "").upper()
    return serial.startswith("ST-") or device_type in {"ST", "TEMPEST"}


def _selected_device(
    api: TempestAPI,
    station: Dict[str, Any],
    requested_id: Optional[int],
) -> Dict[str, Any]:
    devices = [
        device
        for device in station.get("devices") or []
        if isinstance(device, dict) and device.get("device_id") and device.get("serial_number")
    ]
    if requested_id is not None:
        for device in devices:
            if int(device["device_id"]) == requested_id:
                return device
        raise ConfigError(f"Active device {requested_id} was not found in the selected station")

    likely = [device for device in devices if _likely_tempest(device)]
    if len(likely) == 1:
        return likely[0]

    confirmed: List[Dict[str, Any]] = []
    for device in likely or devices:
        try:
            payload = api.device_observations(int(device["device_id"]))
        except ApiError:
            continue
        if payload.get("type") == "obs_st":
            confirmed.append(device)
    return _choose(confirmed, "Tempest device", _device_description)


def _find_station(stations: Sequence[Dict[str, Any]], station_id: int) -> Dict[str, Any]:
    for station in stations:
        if int(station.get("station_id", 0)) == station_id:
            return station
    raise ConfigError(f"Configured station {station_id} is no longer available to this account")


def _timezone_from_station_payload(payload: Dict[str, Any]) -> Optional[str]:
    value = payload.get("timezone")
    return str(value) if value else None


def configure(args: argparse.Namespace) -> int:
    file_values = read_env_file()
    environment_token = os.environ.get("TEMPEST_TOKEN")
    if args.new_token and environment_token:
        raise ConfigError(
            "TEMPEST_TOKEN is set in the process environment and would override the new file. "
            "Remove or update that environment variable before using --new-token."
        )
    token = None if args.new_token else (
        environment_token or file_values.get("TEMPEST_TOKEN")
    )
    if not token:
        print("Create the token at tempestwx.com > Settings > Data Authorizations > Create Token.")
        token = getpass.getpass("Paste the personal access token (input is hidden): ").strip()
    if not token:
        raise ConfigError("No personal access token was entered")
    if any(character.isspace() for character in token):
        raise ConfigError("The personal access token contains whitespace")

    print("Checking the token and discovering stations…")
    api = TempestAPI(token)
    try:
        stations = api.stations()
    except AuthenticationError:
        if environment_token:
            raise ConfigError(
                "The TEMPEST_TOKEN environment variable was rejected. Remove or update that "
                "environment variable, then run configure again."
            ) from None
        if args.new_token or not sys.stdin.isatty():
            raise
        print("The saved token was rejected. Create and paste a replacement token.")
        token = getpass.getpass("Replacement token (input is hidden): ").strip()
        if not token or any(character.isspace() for character in token):
            raise ConfigError("No valid replacement token was entered")
        api = TempestAPI(token)
        stations = api.stations()
    station = _selected_station(stations, args.station_id)
    device = _selected_device(api, station, args.device_id)
    station_id = int(station["station_id"])
    device_id = int(device["device_id"])

    timezone_name: Optional[str] = None
    try:
        timezone_name = _timezone_from_station_payload(api.station_latest(station_id))
    except ApiError:
        # Metadata discovery can still finish while a newly installed sensor is waking up.
        pass

    config = Config(token=token, station_id=station_id, device_id=device_id)
    path = write_config(config)
    with ObservationStore(database_path()) as store:
        store.save_metadata(station, timezone_name)

    print(f"Configured station: {station.get('name') or 'Unnamed'} (ID {station_id})")
    print(f"Configured device:  {_device_description(device)}")
    print(f"Credentials saved:  {path}")
    print("The token was not displayed. Keep the .tempest.env file private.")
    return 0


def doctor(args: argparse.Namespace) -> int:
    config = Config.load()
    assert config.station_id is not None and config.device_id is not None
    api = TempestAPI(config.token)
    stations = api.stations()
    station = _find_station(stations, config.station_id)
    devices = station.get("devices") or []
    device = next(
        (
            item
            for item in devices
            if isinstance(item, dict) and int(item.get("device_id", 0)) == config.device_id
        ),
        None,
    )
    if device is None:
        raise ConfigError(f"Configured device {config.device_id} is not in the station metadata")

    station_payload = api.station_latest(config.station_id)
    timezone_name = _timezone_from_station_payload(station_payload)
    device_payload = api.device_observations(config.device_id)
    observations = parse_device_payload(device_payload)

    with ObservationStore(database_path()) as store:
        store.save_metadata(station, timezone_name)
        database_rows = store.observation_count(config.device_id)

    print("Tempest API connection: OK")
    print(f"Station: {station.get('name') or 'Unnamed'} (ID {config.station_id})")
    print(f"Device:  {_device_description(device)}")
    print(f"Feed:    {device_payload.get('type')} (native SI units)")
    if observations:
        newest = max(observations, key=lambda observation: observation["observed_at_utc"])
        print(f"Latest:  {utc_text(int(newest['observed_at_utc']))}")
        age_seconds = max(0, int(time.time()) - int(newest["observed_at_utc"]))
        if age_seconds > 10 * 60:
            print(f"Warning: the latest reading is {age_seconds // 60} minutes old.")
    else:
        raise ObservationError(
            "The device is recognised but has not returned an observation yet. "
            "Wait until a current reading appears in the Tempest app, then run doctor again."
        )
    print(f"Database check: OK ({database_rows} stored observation(s))")
    return 0


def _sync_window(
    args: argparse.Namespace,
    latest_epoch: Optional[int],
    last_reconcile_epoch: Optional[int],
) -> Tuple[int, int, bool]:
    now = int(time.time())
    if args.from_time:
        start = parse_boundary(args.from_time)
    elif latest_epoch is not None:
        start = max(1, latest_epoch - args.overlap_minutes * 60)
    else:
        # Inclusive API bounds: +1 keeps an N-day initial window within exactly N days.
        start = max(1, now - args.initial_days * 24 * 60 * 60 + 1)

    end = parse_boundary(args.to_time, end_of_date=True) if args.to_time else now
    end = min(end, now)
    full_reconcile = False
    if not args.from_time and not args.to_time:
        reconcile_due = (
            last_reconcile_epoch is None
            or now - last_reconcile_epoch >= RECONCILE_INTERVAL_SECONDS
        )
        reconcile_start = max(1, now - MAX_API_RANGE_SECONDS)
        if latest_epoch is not None and reconcile_due:
            start = min(start, reconcile_start)
        full_reconcile = start <= reconcile_start
    if start > end:
        raise ConfigError("The requested sync start is after its end")
    return start, end, full_reconcile


def sync(args: argparse.Namespace) -> int:
    config = Config.load()
    assert config.device_id is not None
    api = TempestAPI(config.token)
    total_received = total_inserted = total_updated = total_unchanged = 0
    request_count = 0

    with ObservationStore(database_path()) as store:
        start, end, full_reconcile = _sync_window(
            args,
            store.latest_epoch(config.device_id),
            store.last_reconcile_epoch(config.device_id),
        )
        for chunk_start, chunk_end in time_chunks(start, end):
            payload = api.device_observations(config.device_id, chunk_start, chunk_end)
            returned_device = payload.get("device_id")
            if returned_device is not None and int(returned_device) != config.device_id:
                raise ObservationError(
                    f"Tempest returned device {returned_device} while {config.device_id} was requested"
                )
            observations = parse_device_payload(payload)
            result = store.upsert_observations(config.device_id, observations)
            total_received += result.received
            total_inserted += result.inserted
            total_updated += result.updated
            total_unchanged += result.unchanged
            request_count += 1
        if full_reconcile:
            store.mark_reconciled(config.device_id, int(time.time()))
        newest = store.latest_epoch(config.device_id)
        database_rows = store.observation_count(config.device_id)

    print(f"Sync window: {utc_text(start)} to {utc_text(end)}")
    print(f"API requests: {request_count}; observations received: {total_received}")
    print(
        f"Database: {total_inserted} new, {total_updated} revised, "
        f"{total_unchanged} unchanged; {database_rows} total"
    )
    if full_reconcile:
        print("Reconciliation: refreshed the complete five-day high-resolution window")
    if newest is not None:
        age_minutes = max(0, int(time.time()) - newest) // 60
        print(f"Newest reading: {utc_text(newest)} ({age_minutes} minute(s) old)")
    return 0


def _number(value: Any, decimals: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{decimals}f}"


def _direction(value: Any) -> str:
    if value is None:
        return "—"
    degrees = float(value) % 360
    point = COMPASS_POINTS[int((degrees + 11.25) // 22.5) % 16]
    return f"{degrees:.0f}° {point}"


def latest(args: argparse.Namespace) -> int:
    config = Config.load()
    assert config.device_id is not None
    payload = TempestAPI(config.token).device_observations(config.device_id)
    observations = parse_device_payload(payload)
    if not observations:
        raise ObservationError("The Tempest device returned no latest observation")
    observation = max(observations, key=lambda item: item["observed_at_utc"])
    with ObservationStore(database_path()) as store:
        store.upsert_observations(config.device_id, [observation])

    epoch = int(observation["observed_at_utc"])
    wind_mps = observation.get("wind_avg_mps")
    wind_mph = float(wind_mps) * 2.236936 if wind_mps is not None else None
    gust_mps = observation.get("wind_gust_mps")
    gust_mph = float(gust_mps) * 2.236936 if gust_mps is not None else None
    print(f"Tempest reading — {local_text(epoch)}")
    print(f"  UTC:          {utc_text(epoch)}")
    print(f"  Temperature:  {_number(observation.get('air_temperature_c'))} °C")
    print(f"  Humidity:     {_number(observation.get('relative_humidity_pct'), 0)} %")
    print(f"  Pressure:     {_number(observation.get('station_pressure_mb'))} hPa")
    print(
        f"  Wind:         {_number(wind_mps)} m/s ({_number(wind_mph)} mph) "
        f"from {_direction(observation.get('wind_direction_deg'))}"
    )
    print(f"  Gust:         {_number(gust_mps)} m/s ({_number(gust_mph)} mph)")
    print(f"  Rain today:   {_number(observation.get('local_day_rain_mm'), 2)} mm")
    print(f"  Solar / UV:   {_number(observation.get('solar_radiation_wm2'), 0)} W/m² / "
          f"{_number(observation.get('uv_index'), 1)}")
    print(f"  Battery:      {_number(observation.get('battery_v'), 2)} V")
    return 0


def export_command(args: argparse.Namespace) -> int:
    config = Config.load()
    assert config.device_id is not None
    db_path = database_path()
    if not db_path.exists():
        raise ConfigError("The Tempest database does not exist yet; run sync first")
    start = parse_boundary(args.from_time) if args.from_time else None
    end_exclusive = None
    if args.to_time:
        end_exclusive = parse_boundary(args.to_time, end_of_date=True) + 1
    output = Path(args.output).expanduser() if args.output else DEFAULT_EXPORT
    if output.resolve() == db_path.resolve():
        raise ConfigError("The CSV output path cannot be the Tempest SQLite database")
    with ObservationStore(db_path) as store:
        rows = store.iter_observations(config.device_id, start, end_exclusive)
        count = export_csv(rows, output)
    print(f"Exported {count} observation(s) to {output.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tempest",
        description="Collect WeatherFlow Tempest observations into SQLite and CSV.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser = subparsers.add_parser(
        "configure", help="securely save a token and discover the station/device IDs"
    )
    configure_parser.add_argument("--station-id", type=int)
    configure_parser.add_argument("--device-id", type=int)
    configure_parser.add_argument(
        "--new-token", action="store_true", help="ignore any saved token and prompt for a new one"
    )
    configure_parser.set_defaults(handler=configure)

    doctor_parser = subparsers.add_parser(
        "doctor", help="verify authentication, metadata, live data and the database"
    )
    doctor_parser.set_defaults(handler=doctor)

    sync_parser = subparsers.add_parser(
        "sync", help="resume collection from the database high-water mark"
    )
    sync_parser.add_argument(
        "--initial-days",
        type=int,
        default=5,
        help="days to fetch when the database is empty (default: 5)",
    )
    sync_parser.add_argument(
        "--overlap-minutes",
        type=int,
        default=10,
        help="re-fetch this overlap so corrected rainfall is updated (default: 10)",
    )
    sync_parser.add_argument("--from", dest="from_time", help="UTC ISO date/time override")
    sync_parser.add_argument("--to", dest="to_time", help="inclusive UTC ISO date/time")
    sync_parser.set_defaults(handler=sync)

    latest_parser = subparsers.add_parser("latest", help="show and store the latest reading")
    latest_parser.set_defaults(handler=latest)

    export_parser = subparsers.add_parser("export", help="export stored observations to CSV")
    export_parser.add_argument("--from", dest="from_time", help="inclusive UTC ISO date/time")
    export_parser.add_argument("--to", dest="to_time", help="inclusive UTC ISO date/time/date")
    export_parser.add_argument("--output", help="CSV path (default: outputs/tempest/...)")
    export_parser.set_defaults(handler=export_command)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "initial_days", 1) < 1:
        parser.error("--initial-days must be at least 1")
    if getattr(args, "overlap_minutes", 0) < 0:
        parser.error("--overlap-minutes must not be negative")
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
