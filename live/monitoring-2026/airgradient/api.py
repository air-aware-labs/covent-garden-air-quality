"""Small standard-library client for AirGradient's official cloud API v1."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional


BASE_URL = "https://api.airgradient.com/public/api/v1"
USER_AGENT = "AirAwareLabs-CoventGarden-AirGradient/1.0"


class ApiError(RuntimeError):
    """A safe-to-display API error that excludes tokens and request URLs."""


class AuthenticationError(ApiError):
    """The place API token was rejected."""


class NoDataError(ApiError):
    """The requested location/window has no measurements."""


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


class AirGradientAPI:
    """Read-only client. AirGradient v1 documents its token as a query API key."""

    def __init__(
        self,
        token: str,
        timeout: float = 30.0,
        max_attempts: int = 4,
        opener: Optional[Callable[..., Any]] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("An AirGradient API token is required")
        self._token = token
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleeper

    def _delay(self, attempt: int, retry_after: Optional[str] = None) -> None:
        server_delay = _retry_after_seconds(retry_after)
        delay = server_delay if server_delay is not None else min(
            30.0, 2 ** attempt + random.random()
        )
        self._sleep(delay)

    def _get_json(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
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
                    return json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ApiError(
                        f"AirGradient returned invalid JSON for {endpoint}"
                    ) from exc
            except urllib.error.HTTPError as exc:
                code = exc.code
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                exc.close()
                if code in (401, 403):
                    raise AuthenticationError(
                        "AirGradient rejected the API token. Regenerate it in General "
                        "Settings > Connectivity, then run configure with --new-token."
                    ) from None
                if (code == 429 or 500 <= code <= 599) and attempt + 1 < self.max_attempts:
                    self._delay(attempt, retry_after)
                    continue
                if code == 404:
                    raise NoDataError(f"AirGradient has no data for {endpoint}") from None
                raise ApiError(f"AirGradient HTTP {code} for {endpoint}") from None
            except urllib.error.URLError:
                if attempt + 1 < self.max_attempts:
                    self._delay(attempt)
                    continue
                # URLError.reason can contain the full token-bearing URL.
                raise ApiError(
                    f"Could not reach AirGradient for {endpoint}: network connection failed"
                ) from None
            except TimeoutError:
                if attempt + 1 < self.max_attempts:
                    self._delay(attempt)
                    continue
                raise ApiError(f"AirGradient request timed out for {endpoint}") from None
        raise ApiError(f"AirGradient request failed for {endpoint}")

    @staticmethod
    def _object(payload: Any, label: str) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ApiError(f"AirGradient returned an invalid {label}")
        return payload

    @staticmethod
    def _array(payload: Any, label: str) -> List[Dict[str, Any]]:
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise ApiError(f"AirGradient returned an invalid {label}")
        return payload

    def place(self) -> Dict[str, Any]:
        return self._object(self._get_json("place"), "place response")

    def locations_current(self) -> List[Dict[str, Any]]:
        return self._array(
            self._get_json("locations/measures/current"), "current location list"
        )

    def location_current(self, location_id: int) -> Dict[str, Any]:
        return self._object(
            self._get_json(f"locations/{int(location_id)}/measures/current"),
            "current measurement",
        )

    def location_past(
        self, location_id: int, from_utc: str, to_utc: str
    ) -> List[Dict[str, Any]]:
        return self._array(
            self._get_json(
                f"locations/{int(location_id)}/measures/past",
                {"from": from_utc, "to": to_utc},
            ),
            "past measurement list",
        )
