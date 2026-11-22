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
from yarl import URL

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


MAX_TRIP_KM = 3000.0
MAX_TRIP_MINUTES = 3 * 24 * 60.0
MAX_URL = 4096


def _place(raw: Any) -> dict[str, float] | None:
    """Return {latitude, longitude} from a FLEETA location object."""
    if not isinstance(raw, dict):
        return None
    position = coordinates(raw.get("latitude"), raw.get("longitude"))
    return {"latitude": position[0], "longitude": position[1]} if position else None


def trip(raw: Any) -> dict[str, Any] | None:
    """Build a clean trip from GET /v1/devices/{psn}/trips, or None.

    FLEETA's average speed is sometimes higher than its maximum, so it is not
    used. When the duration is missing it is worked out from the start and end
    times; a trip with no end time is treated as not finished.
    """
    if not isinstance(raw, dict):
        return None
    trip_id = identifier(raw.get("tripId"))
    started = timestamp(raw.get("startedAt"))
    if trip_id is None or started is None:
        return None
    ended = timestamp(raw.get("endedAt"))
    if ended is not None and ended < started:
        ended = None
    minutes = number(raw.get("durationMin"), 0.0, MAX_TRIP_MINUTES)
    if minutes is None and ended is not None:
        minutes = number((ended - started).total_seconds() / 60, 0.0, MAX_TRIP_MINUTES)
    return {
        "trip_id": trip_id,
        "started_at": started,
        "ended_at": ended,
        "distance_km": number(raw.get("distanceKm"), 0.0, MAX_TRIP_KM),
        "duration_min": round(minutes, 1) if minutes is not None else None,
        "max_speed_kmh": speed(raw.get("maxSpeedKmh")),
        "start": _place(raw.get("startLocation")),
        "end": _place(raw.get("endLocation")),
    }


def https_url(value: Any) -> str | None:
    """Return a plain https URL to a named host, or None.

    Used for the short-lived picture links FLEETA hands out. Addresses typed as
    numbers and local names are refused so a bad link cannot reach the home
    network.
    """
    if not isinstance(value, str) or len(value) > MAX_URL or _CONTROL.search(value):
        return None
    try:
        parsed = URL(value)
    except ValueError:
        return None
    host = (parsed.host or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or "." not in host or parsed.user or parsed.password:
        return None
    if host == "localhost" or host.endswith((".local", ".lan", ".internal", ".home.arpa", ".localhost")):
        return None
    if _IP_HOST.fullmatch(host) or ":" in host:
        return None
    return value


_IP_HOST = re.compile(r"^[0-9.]+$")

# JPEG, PNG and WebP start with these bytes.
_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def image_type(data: Any) -> str | None:
    """Return the picture's content type if the bytes really are a picture."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 12:
        return None
    for signature, content_type in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


MAX_TRACK_POINTS = 2000
# A jump between two points faster than this is a GPS glitch and is skipped.
MAX_PLAUSIBLE_KMH = 250.0


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance in km between two (latitude, longitude) points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def track_summary(points: Any) -> tuple[float | None, datetime | None, datetime | None]:
    """Work out a trip's distance, first and last time from its GPS points.

    Points are put in time order, and jumps that would need an impossible
    speed are left out rather than counted.
    """
    if not isinstance(points, list):
        return None, None, None
    clean: list[tuple[datetime, tuple[float, float]]] = []
    for raw in points[:MAX_TRACK_POINTS]:
        if not isinstance(raw, dict):
            continue
        at = timestamp(raw.get("at"))
        position = coordinates(raw.get("latitude"), raw.get("longitude"))
        if at is not None and position is not None:
            clean.append((at, position))
    if len(clean) < 2:
        return None, None, None
    clean.sort(key=lambda item: item[0])
    total = 0.0
    last_at, last_pos = clean[0]
    for at, position in clean[1:]:
        km = _haversine_km(last_pos, position)
        hours = (at - last_at).total_seconds() / 3600
        if hours > 0 and km / hours > MAX_PLAUSIBLE_KMH:
            continue
        total += km
        last_at, last_pos = at, position
    return round(total, 2), clean[0][0], clean[-1][0]
