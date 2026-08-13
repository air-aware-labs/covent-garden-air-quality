"""Small standard-library client for the WeatherFlow Tempest REST API."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional


BASE_URL = "https://swd.weatherflow.com/swd/rest"
USER_AGENT = "AirAwareLabs-CoventGarden-Tempest/1.0"


class ApiError(RuntimeError):
    """A safe-to-display API error that never includes the token or full URL."""


class AuthenticationError(ApiError):
    """The personal access token was rejected."""


def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


class TempestAPI:
    def __init__(
        self,
        token: str,
        timeout: float = 30.0,
        max_attempts: int = 4,
        opener: Optional[Callable[..., Any]] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("A Tempest token is required")
        self._token = token
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleeper

    def _delay(self, attempt: int, retry_after: Optional[str] = None) -> None:
        server_delay = _retry_after_seconds(retry_after)
        delay = server_delay if server_delay is not None else min(30.0, 2 ** attempt + random.random())
        self._sleep(delay)

    def _get_json(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        query = dict(params or {})
        query["token"] = self._token
        url = f"{BASE_URL}/{endpoint.lstrip('/')}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )

        for attempt in range(self.max_attempts):
            try:
                with self._opener(request, timeout=self.timeout) as response:
                    raw = response.read()
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ApiError(f"Tempest returned invalid JSON for {endpoint}") from exc
                if not isinstance(payload, dict):
                    raise ApiError(f"Tempest returned an unexpected response for {endpoint}")
                status = payload.get("status")
                if isinstance(status, dict) and status.get("status_code") not in (None, 0):
                    message = status.get("status_message") or "unknown API error"
                    raise ApiError(f"Tempest API error for {endpoint}: {message}")
                return payload
            except urllib.error.HTTPError as exc:
                code = exc.code
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                exc.close()
                if code in (401, 403):
                    raise AuthenticationError(
                        "Tempest rejected the personal access token. Create a new token in "
                        "Settings > Data Authorizations and run configure with --new-token."
                    ) from None
                retryable = code == 429 or 500 <= code <= 599
                if retryable and attempt + 1 < self.max_attempts:
                    self._delay(attempt, retry_after)
                    continue
                if code == 404:
                    raise ApiError(f"Tempest resource not found for {endpoint}") from None
                raise ApiError(f"Tempest HTTP {code} for {endpoint}") from None
            except urllib.error.URLError as exc:
                if attempt + 1 < self.max_attempts:
                    self._delay(attempt)
                    continue
                # Some proxy/opening layers put the complete request URL in ``reason``.
                # The URL contains the query-string token, so never interpolate it here.
                raise ApiError(f"Could not reach Tempest for {endpoint}: network connection failed") from None
            except TimeoutError:
                if attempt + 1 < self.max_attempts:
                    self._delay(attempt)
                    continue
                raise ApiError(f"Tempest request timed out for {endpoint}") from None

        raise ApiError(f"Tempest request failed for {endpoint}")

    def stations(self) -> List[Dict[str, Any]]:
        payload = self._get_json("stations")
        # The original Swagger schema calls this array ``locations``. The live
        # API changed it to ``stations`` in 2026, so accept both during rollout.
        station_rows = payload.get("stations")
        if station_rows is None:
            station_rows = payload.get("locations", [])
        if not isinstance(station_rows, list):
            raise ApiError("Tempest returned an invalid station list")
        return [station for station in station_rows if isinstance(station, dict)]

    def station_latest(self, station_id: int) -> Dict[str, Any]:
        return self._get_json(f"observations/station/{int(station_id)}")

    def device_observations(
        self,
        device_id: int,
        time_start: Optional[int] = None,
        time_end: Optional[int] = None,
    ) -> Dict[str, Any]:
        if (time_start is None) != (time_end is None):
            raise ValueError("time_start and time_end must be supplied together")
        params: Dict[str, Any] = {}
        if time_start is not None and time_end is not None:
            if time_end < time_start:
                raise ValueError("time_end must not precede time_start")
            params.update(time_start=int(time_start), time_end=int(time_end))
        return self._get_json(f"observations/device/{int(device_id)}", params)
