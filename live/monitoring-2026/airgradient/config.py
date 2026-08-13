"""Private local configuration for the AirGradient collector."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".airgradient.env"
DEFAULT_DB_FILE = PROJECT_ROOT / "data" / "airgradient" / "airgradient.sqlite3"
ALLOWED_KEYS = {"AIRGRADIENT_TOKEN", "AIRGRADIENT_LOCATION_IDS"}


class ConfigError(RuntimeError):
    """Required local configuration is absent or invalid."""


def env_file_path() -> Path:
    override = os.environ.get("AIRGRADIENT_ENV_FILE")
    return Path(override).expanduser() if override else DEFAULT_ENV_FILE


def database_path() -> Path:
    override = os.environ.get("AIRGRADIENT_DB")
    return Path(override).expanduser() if override else DEFAULT_DB_FILE


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def read_env_file(path: Optional[Path] = None) -> Dict[str, str]:
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
        if key in ALLOWED_KEYS:
            values[key] = _unquote(value)
    return values


def _setting(name: str, values: Dict[str, str]) -> Optional[str]:
    environment_value = os.environ.get(name)
    if environment_value is not None:
        return environment_value.strip()
    file_value = values.get(name)
    return file_value.strip() if file_value is not None else None


def parse_location_ids(value: Optional[str], required: bool = True) -> Tuple[int, ...]:
    if not value:
        if required:
            raise ConfigError(
                "AIRGRADIENT_LOCATION_IDS is not configured. Run "
                "`python -m airgradient configure` first."
            )
        return ()
    result = []
    seen = set()
    for part in value.split(","):
        try:
            location_id = int(part.strip())
        except ValueError as exc:
            raise ConfigError("AIRGRADIENT_LOCATION_IDS must be comma-separated integers") from exc
        if location_id <= 0:
            raise ConfigError("AIRGRADIENT_LOCATION_IDS must contain only positive integers")
        if location_id not in seen:
            result.append(location_id)
            seen.add(location_id)
    return tuple(result)


@dataclass(frozen=True)
class Config:
    token: str
    location_ids: Tuple[int, ...] = ()

    @classmethod
    def load(cls, require_ids: bool = True, path: Optional[Path] = None) -> "Config":
        values = read_env_file(path)
        token = _setting("AIRGRADIENT_TOKEN", values)
        if not token:
            raise ConfigError(
                "AIRGRADIENT_TOKEN is not configured. Regenerate a dashboard API token, "
                "then run `python -m airgradient configure`."
            )
        if any(character.isspace() for character in token):
            raise ConfigError("AIRGRADIENT_TOKEN contains whitespace")
        return cls(
            token=token,
            location_ids=parse_location_ids(
                _setting("AIRGRADIENT_LOCATION_IDS", values), required=require_ids
            ),
        )


def write_config(config: Config, path: Optional[Path] = None) -> Path:
    if not config.location_ids:
        raise ConfigError("At least one location ID is required before saving configuration")
    if any(character in config.token for character in "\r\n"):
        raise ConfigError("The token contains an invalid newline")
    if any(int(location_id) <= 0 for location_id in config.location_ids):
        raise ConfigError("Location IDs must be positive")

    path = path or env_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = (
        "# AirGradient API configuration. Treat this file like a password.\n"
        f"AIRGRADIENT_TOKEN={config.token}\n"
        "AIRGRADIENT_LOCATION_IDS="
        + ",".join(str(int(location_id)) for location_id in config.location_ids)
        + "\n"
    )
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(contents)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path
