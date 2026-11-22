"""Shared test fixtures for the FLEETA integration."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.fleeta.const import API_BASE_URL, DOMAIN

PSN = "TESTPSN000000001"
API_KEY = "flt_live_testkey000000000000"

USAGE = {
    "data": {
        "period": "2026-11",
        "tier": "pro",
        "cameraLimit": 1,
        "apiCalls": {"used": 1000, "limit": 10500, "measured": True},
        "rate": {"limitRps": 10, "burst": 25},
    }
}

DEVICES = {
    "data": [
        {
            "psn": PSN,
            "name": "BMW X5 50e",
            "model": "DR750-2CH LTE",
            "fwVersion": "1.012",
            "category": "cloud",
            "status": "parked",
            "lastConnectedAt": "2026-11-21T15:19:44.120Z",
            "groupId": None,
            "vehicle": {},
            "sim": {"apn": "mob.asm.net", "iccid": "****0001"},
            "battery": {"connected": False},
            "connectivity": "lte",
        }
    ],
    "pagination": {"page": 1, "perPage": 100, "total": 1, "totalPages": 1},
}

LOCATIONS = {
    "data": [
        {
            "psn": PSN,
            "name": "BMW X5 50e",
            "latitude": 52.9712,
            "longitude": 0.0601,
            "speedKmh": 0,
            "heading": 90,
            "status": "parked",
            "at": "2026-11-21T15:20:00.000Z",
        }
    ],
    "pagination": {"nextCursor": None, "hasMore": False},
}

FEED_BOOTSTRAP = {"data": [], "pagination": {"nextCursor": "cursor-1", "hasMore": False}}

PARKING_EVENT = {
    "eventId": "evt_0001",
    "psn": PSN,
    "type": "parking_impact",
    "occurredAt": "2026-11-21T16:00:00.000Z",
    "location": {"latitude": 52.9712, "longitude": 0.0601},
    "speedKmh": None,
    "channels": ["front", "rear"],
    "hasVideo": True,
}


PICTURE_URL = "https://media.example-cdn.com/thumbs/evt_0001.jpg?sig=abc"
PICTURE = b"\xff\xd8\xff\xe0" + b"\x00" * 200

TRIPS = {
    "data": [
        {
            "tripId": "trip_0002",
            "psn": PSN,
            "startedAt": "2026-11-21T13:50:00.000Z",
            "endedAt": "2026-11-21T14:29:00.000Z",
            "distanceKm": 32.2,
            "durationMin": 39,
            "maxSpeedKmh": 96.6,
            "avgSpeedKmh": 140.0,
            "startLocation": {"latitude": 52.94, "longitude": -0.02},
            "endLocation": {"latitude": 52.9712, "longitude": 0.0601},
            "trackCount": 120,
        },
        {
            "tripId": "trip_0001",
            "psn": PSN,
            "startedAt": "2026-11-21T08:00:00.000Z",
            "endedAt": "2026-11-21T08:20:00.000Z",
            "distanceKm": 12.0,
            "durationMin": None,
            "maxSpeedKmh": 80.0,
            "avgSpeedKmh": 40.0,
            "startLocation": None,
            "endLocation": None,
            "trackCount": 40,
        },
        {
            "tripId": "trip_stuck",
            "psn": PSN,
            "startedAt": "2026-08-01T08:00:00.000Z",
            "endedAt": None,
            "distanceKm": 5.0,
            "durationMin": None,
            "maxSpeedKmh": 50.0,
            "avgSpeedKmh": 30.0,
            "startLocation": None,
            "endLocation": None,
            "trackCount": 10,
        },
    ],
    "pagination": {"nextCursor": None, "hasMore": False},
}

RECENT_EVENTS = {
    "data": [dict(PARKING_EVENT, thumbnailUrl=PICTURE_URL)],
    "pagination": {"nextCursor": None, "hasMore": False},
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Generator[None]:
    """Enable loading custom integrations in every test."""
    yield


def mock_api(
    aioclient_mock: AiohttpClientMocker,
    *,
    usage: dict[str, Any] | None = None,
    locations: dict[str, Any] | None = None,
    feed: dict[str, Any] | None = None,
    trips: dict[str, Any] | None = None,
    recent_events: dict[str, Any] | None = None,
    status: int = 200,
) -> None:
    """Register the FLEETA endpoints on the aiohttp mocker."""
    aioclient_mock.clear_requests()
    if status != 200:
        body = {"status": status, "code": "error", "detail": "test error"}
        for path in (
            "/v1/usage",
            "/v1/devices",
            "/v1/fleet/locations",
            "/v1/events/feed",
            f"/v1/devices/{PSN}/trips",
            "/v1/events",
        ):
            aioclient_mock.get(f"{API_BASE_URL}{path}", status=status, json=body)
        return
    aioclient_mock.get(f"{API_BASE_URL}/v1/usage", json=usage or USAGE)
    aioclient_mock.get(f"{API_BASE_URL}/v1/devices", json=DEVICES)
    aioclient_mock.get(f"{API_BASE_URL}/v1/fleet/locations", json=locations or LOCATIONS)
    aioclient_mock.get(f"{API_BASE_URL}/v1/events/feed", json=feed or FEED_BOOTSTRAP)
    aioclient_mock.get(f"{API_BASE_URL}/v1/devices/{PSN}/trips", json=trips or TRIPS)
    aioclient_mock.get(f"{API_BASE_URL}/v1/events", json=recent_events or RECENT_EVENTS)
    aioclient_mock.get(PICTURE_URL, content=PICTURE, headers={"Content-Type": "image/jpeg"})


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A FLEETA config entry."""
    return MockConfigEntry(domain=DOMAIN, title="FLEETA", data={CONF_API_KEY: API_KEY})


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration with mocked API responses."""
    mock_api(aioclient_mock)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
