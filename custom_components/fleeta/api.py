"""Small async client for the FLEETA Open API (read-only calls only)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote

import aiohttp

from . import validate
from .const import API_BASE_URL, API_TIMEOUT_SECONDS, LOGGER

# FLEETA responses for one account are a few kilobytes; refuse anything huge.
MAX_RESPONSE_BYTES = 2_000_000
# Event pictures are small stills; anything bigger is refused.
MAX_PICTURE_BYTES = 1_500_000


async def _read_limited(resp: aiohttp.ClientResponse, limit: int) -> bytes | None:
    """Read the body, or return None as soon as it grows past the limit."""
    declared = getattr(resp, "content_length", None)
    if isinstance(declared, int) and declared > limit:
        return None
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(65_536):
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


class FleetaError(Exception):
    """Base error for the FLEETA API."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class FleetaConnectionError(FleetaError):
    """Network problem, timeout or server-side (5xx) error."""


class FleetaAuthError(FleetaError):
    """The API key was rejected (401)."""


class FleetaForbiddenError(FleetaError):
    """The plan or key scope does not allow this call (403)."""


class FleetaRateLimitError(FleetaError):
    """Too many requests (429)."""

    @property
    def monthly_limit(self) -> bool:
        """True when the monthly call allowance is used up."""
        return self.code == "monthly_limit_exceeded"


class FleetaApi:
    """FLEETA Open API client.

    The API key is only ever placed in the Authorization header. It is never
    logged or included in an exception message.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        base_url: str = API_BASE_URL,
    ) -> None:
        self._session = session
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self.calls_made = 0

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Perform a GET request and return the decoded JSON body."""
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        self.calls_made += 1
        try:
            # Redirects are refused so the key can never be sent to another host.
            async with self._session.get(
                url,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS),
                allow_redirects=False,
            ) as resp:
                if resp.status == 200:
                    raw = await _read_limited(resp, MAX_RESPONSE_BYTES)
                    if raw is None:
                        raise FleetaConnectionError(f"Response from {path} is too large")
                    try:
                        body = json.loads(raw)
                    except ValueError as err:
                        raise FleetaConnectionError(f"Invalid JSON from {path}") from err
                    if not isinstance(body, dict):
                        raise FleetaConnectionError(f"Unexpected response from {path}")
                    return body
                await self._raise_for_status(path, resp)
        except (TimeoutError, asyncio.TimeoutError) as err:
            raise FleetaConnectionError(f"Timed out calling {path}") from err
        except aiohttp.ClientError as err:
            raise FleetaConnectionError(f"Error calling {path}: {type(err).__name__}") from err
        raise FleetaConnectionError(f"No response from {path}")  # pragma: no cover

    @staticmethod
    async def _raise_for_status(path: str, resp: aiohttp.ClientResponse) -> None:
        """Turn an error response into the matching exception.

        FLEETA returns two error shapes: RFC 9457 problem details
        ({type, title, status, code, detail, requestId}) from the application,
        and {message} from the gateway.
        """
        code: str | None = None
        detail = validate.text(getattr(resp, "reason", None), 120) or ""
        try:
            raw = await _read_limited(resp, 16_384)
            body = json.loads(raw) if raw else None
        except (ValueError, aiohttp.ClientError):
            body = None
        if isinstance(body, dict):
            code = validate.event_type(body.get("code"))
            for key in ("detail", "title", "message"):
                cleaned = validate.text(body.get(key), 120)
                if cleaned:
                    detail = cleaned
                    break

        # Error text from the API is cleaned and shortened before it can reach
        # the log or the Home Assistant UI.
        message = f"{path} returned {resp.status}: {detail}"
        LOGGER.debug("FLEETA API error: %s (code=%s)", message, code)
        if resp.status == 401:
            raise FleetaAuthError(message, status=resp.status, code=code)
        if resp.status == 403:
            raise FleetaForbiddenError(message, status=resp.status, code=code)
        if resp.status == 429:
            raise FleetaRateLimitError(message, status=resp.status, code=code)
        raise FleetaConnectionError(message, status=resp.status, code=code)

    async def get_usage(self) -> dict[str, Any]:
        """Return this month's API usage, tier and limits (GET /v1/usage)."""
        body = await self._get("/v1/usage")
        data = body.get("data")
        return data if isinstance(data, dict) else {}

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return every dashcam on the account (GET /v1/devices, offset paging)."""
        devices: list[dict[str, Any]] = []
        page = 1
        while True:
            body = await self._get("/v1/devices", {"page": page, "perPage": 100})
            rows = body.get("data")
            if isinstance(rows, list):
                devices.extend(row for row in rows if isinstance(row, dict) and row.get("psn"))
            pagination = body.get("pagination") or {}
            total_pages = pagination.get("totalPages")
            if not isinstance(total_pages, int) or page >= total_pages or page >= 20:
                return devices
            page += 1

    async def get_locations(self) -> list[dict[str, Any]]:
        """Return the latest GPS position of every dashcam (GET /v1/fleet/locations).

        One call with the maximum page size; the cursor on this endpoint is an
        offset in disguise, so paging is avoided.
        """
        body = await self._get("/v1/fleet/locations", {"limit": 200})
        rows = body.get("data")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict) and row.get("psn")]

    async def get_events_feed(self, after: str | None) -> tuple[list[dict[str, Any]], str | None, bool]:
        """Read new safety events from the feed (GET /v1/events/feed).

        With no cursor, start=latest returns only the current tail position so
        old events are not replayed as new. Returns (rows, cursor, has_more).
        When the feed returns a null cursor the previous one is kept.
        """
        params: dict[str, Any] = {"limit": 50}
        if after:
            params["after"] = after
        else:
            params["start"] = "latest"
        body = await self._get("/v1/events/feed", params)
        rows = body.get("data")
        pagination = body.get("pagination") or {}
        next_cursor = pagination.get("nextCursor")
        cursor = next_cursor if isinstance(next_cursor, str) and next_cursor else after
        events = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        if not after:
            # Bootstrap call: only the position matters.
            events = []
        return events, cursor, bool(pagination.get("hasMore"))

    async def get_trips(self, psn: str) -> list[dict[str, Any]]:
        """Return the dashcam's recent trips (GET /v1/devices/{psn}/trips)."""
        body = await self._get(f"/v1/devices/{quote(psn, safe='')}/trips", {"limit": 20})
        rows = body.get("data")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    async def get_recent_events(self, psn: str) -> list[dict[str, Any]]:
        """Return the dashcam's latest events with their picture links.

        GET /v1/events?include=thumbnail puts a short-lived picture link on
        each row in the same single call.
        """
        body = await self._get("/v1/events", {"psn": psn, "limit": 5, "include": "thumbnail"})
        rows = body.get("data")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    async def download_picture(self, url: str) -> tuple[bytes, str] | None:
        """Download an event picture from the link FLEETA handed out.

        The link is already signed, so the API key is never sent with it.
        Only real JPEG, PNG or WebP pictures under the size limit are kept.
        Returns None if the picture cannot be fetched.
        """
        safe_url = validate.https_url(url)
        if safe_url is None:
            return None
        try:
            async with self._session.get(
                safe_url,
                headers={"Accept": "image/*"},
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS),
                allow_redirects=False,
            ) as resp:
                if resp.status != 200:
                    LOGGER.debug("FLEETA picture download returned %s", resp.status)
                    return None
                data = await _read_limited(resp, MAX_PICTURE_BYTES)
        except (TimeoutError, asyncio.TimeoutError, aiohttp.ClientError) as err:
            LOGGER.debug("FLEETA picture download failed: %s", type(err).__name__)
            return None
        content_type = validate.image_type(data)
        if data is None or content_type is None:
            return None
        return data, content_type
