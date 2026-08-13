"""Local configuration for the Tempest collector.

The personal access token belongs in ``.tempest.env`` or the process
environment, never in source code or command-line arguments.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".tempest.env"
DEFAULT_DB_FILE = PROJECT_ROOT / "data" / "tempest" / "tempest.sqlite3"


class ConfigError(RuntimeError):
    """Raised when required local configuration is absent or invalid."""


def env_file_path() -> Path:
    override = os.environ.get("TEMPEST_ENV_FILE")
    return Path(override).expanduser() if override else DEFAULT_ENV_FILE


def database_path() -> Path:
    override = os.environ.get("TEMPEST_DB")
    return Path(override).expanduser() if override else DEFAULT_DB_FILE


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def read_env_file(path: Optional[Path] = None) -> Dict[str, str]:
    """Read the small dotenv subset used by this collector."""

    path = path or env_file_path()
    if not path.exists():
        return {}

    values: Dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"Invalid configuration line {line_number} in {path.name}")
        key, value = line.split("=", 1)
        key = key.strip()
        if key in {"TEMPEST_TOKEN", "TEMPEST_STATION_ID", "TEMPEST_DEVICE_ID"}:
            values[key] = _unquote(value)
    return values


def _setting(name: str, file_values: Dict[str, str]) -> Optional[str]:
    # A process environment variable deliberately overrides the local file.
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    value = file_values.get(name)
    return value.strip() if value is not None else None


def _positive_int(name: str, value: Optional[str], required: bool) -> Optional[int]:
    if not value:
        if required:
            raise ConfigError(
                f"{name} is not configured. Run `python -m tempest configure` first."
            )
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ConfigError(f"{name} must be positive")
    return parsed


@dataclass(frozen=True)
class Config:
    token: str
    station_id: Optional[int] = None
    device_id: Optional[int] = None

    @classmethod
    def load(cls, require_ids: bool = True, path: Optional[Path] = None) -> "Config":
        values = read_env_file(path)
        token = _setting("TEMPEST_TOKEN", values)
        if not token:
            raise ConfigError(
                "TEMPEST_TOKEN is not configured. Create a personal access token, then "
                "run `python -m tempest configure`."
            )
        if any(character.isspace() for character in token):
            raise ConfigError("TEMPEST_TOKEN contains whitespace")
        return cls(
            token=token,
            station_id=_positive_int(
                "TEMPEST_STATION_ID", _setting("TEMPEST_STATION_ID", values), require_ids
            ),
            device_id=_positive_int(
                "TEMPEST_DEVICE_ID", _setting("TEMPEST_DEVICE_ID", values), require_ids
            ),
        )


def write_config(config: Config, path: Optional[Path] = None) -> Path:
    """Atomically write the dedicated local dotenv file."""

    if not config.station_id or not config.device_id:
        raise ConfigError("Station and device IDs are required before saving configuration")
    if any(character in config.token for character in "\r\n"):
        raise ConfigError("The token contains an invalid newline")

    path = path or env_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = (
        "# Tempest personal access configuration. Do not share this file.\n"
        f"TEMPEST_TOKEN={config.token}\n"
        f"TEMPEST_STATION_ID={config.station_id}\n"
        f"TEMPEST_DEVICE_ID={config.device_id}\n"
    )
    temporary_handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(temporary_handle.name)
    try:
        with temporary_handle:
            temporary_handle.write(contents)
        temporary.replace(path)
    finally:
        # Do not leave an extra plaintext credential behind after a failed replace.
        if temporary.exists():
            temporary.unlink()
    return path
