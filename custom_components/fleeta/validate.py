"""Checks applied to every value that arrives from the FLEETA API.

Nothing from the API is trusted. Text is cleaned and shortened, numbers must be
finite and inside a sensible range, identifiers must match a strict pattern,
and anything that fails a check is dropped (treated as unknown) rather than
passed on to Home Assistant.
"""

from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any

from homeassistant.util import dt as dt_util

# Dashcam serial numbers (psn) and event/cursor identifiers.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-:.=+/]{1,128}$")
_PSN_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_TYPE_PATTERN = re.compile(r"^[a-z0-9_]{1,48}$")
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_\-=+/.]{1,512}$")
# Control characters, including line breaks and the Unicode bidi overrides.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")

MAX_TEXT = 64
MAX_CHANNELS = 8
MAX_SPEED_KMH = 400.0


def text(value: Any, max_len: int = MAX_TEXT) -> str | None:
    """Return cleaned, shortened text, or None if there is nothing usable."""
    if not isinstance(value, str):
        return None
    cleaned = _CONTROL.sub("", value).strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def psn(value: Any) -> str | None:
    """Return the dashcam serial if it is a plain alphanumeric code."""
    if isinstance(value, str) and _PSN_PATTERN.fullmatch(value):
        return value
    return None


def identifier(value: Any) -> str | None:
    """Return an opaque API identifier (event id, cursor) if it looks sane."""
    if isinstance(value, str) and _ID_PATTERN.fullmatch(value):
        return value
    return None


def cursor(value: Any) -> str | None:
    """Return an opaque paging cursor if it is plain base64-style text."""
    if isinstance(value, str) and _CURSOR_PATTERN.fullmatch(value):
        return value
    return None


def event_type(value: Any) -> str | None:
    """Return an event type in the API's snake_case form."""
    if isinstance(value, str) and _TYPE_PATTERN.fullmatch(value):
        return value
    return None


def number(value: Any, low: float, high: float) -> float | None:
    """Return a finite number inside [low, high], keeping None apart from 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        return None
    return result


def coordinates(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    """Return a valid (latitude, longitude) pair, or None.

    0,0 is treated as "no GPS fix" rather than a real position.
    """
    lat = number(latitude, -90.0, 90.0)
    lon = number(longitude, -180.0, 180.0)
    if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
        return None
    return lat, lon


def speed(value: Any) -> float | None:
    """Return a plausible road speed in km/h."""
    return number(value, 0.0, MAX_SPEED_KMH)


def heading(value: Any) -> float | None:
    """Return a compass heading in degrees."""
    return number(value, 0.0, 360.0)


def timestamp(value: Any) -> datetime | None:
    """Return a timezone-aware timestamp parsed from ISO 8601."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = dt_util.parse_datetime(value)
    except ValueError:
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def channels(value: Any) -> list[str]:
    """Return the camera channels an event recorded on (e.g. front, rear)."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:MAX_CHANNELS]:
        channel = event_type(item)
        if channel:
            result.append(channel)
    return result


def event(raw: Any) -> dict[str, Any] | None:
    """Build a clean event from a feed row, or None if it is unusable."""
    if not isinstance(raw, dict):
        return None
    event_id = identifier(raw.get("eventId"))
    camera = psn(raw.get("psn"))
    if event_id is None or camera is None:
        return None
    location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    position = coordinates(location.get("latitude"), location.get("longitude"))
    occurred = timestamp(raw.get("occurredAt"))
    return {
        "event_id": event_id,
        "psn": camera,
        "type": event_type(raw.get("type")),
        "occurred_at": occurred.isoformat() if occurred else None,
        "speed_kmh": speed(raw.get("speedKmh")),
        "latitude": position[0] if position else None,
        "longitude": position[1] if position else None,
        "has_video": raw.get("hasVideo") is True,
        "channels": channels(raw.get("channels")),
    }
