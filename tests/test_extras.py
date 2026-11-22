"""Tests for the last trip sensor and the event picture."""

from __future__ import annotations

from http import HTTPStatus

from freezegun.api import FrozenDateTimeFactory

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.fleeta import validate
from custom_components.fleeta.const import API_BASE_URL

from .conftest import (
    DEVICES,
    FEED_BOOTSTRAP,
    LOCATIONS,
    PARKING_EVENT,
    PICTURE,
    PICTURE_URL,
    PSN,
    RECENT_EVENTS,
    TRIPS,
    USAGE,
    mock_api,
)

TRIPS_PATH = f"/v1/devices/{PSN}/trips"


def _calls(aioclient_mock: AiohttpClientMocker, path: str) -> list:
    return [c for c in aioclient_mock.mock_calls if c[1].path == path and c[1].host == "openapi.fleeta.io"]


async def test_last_trip_sensor(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """The newest finished trip is shown in miles with its times."""
    state = hass.states.get("sensor.bmw_x5_50e_last_trip")
    assert state is not None
    assert state.attributes["unit_of_measurement"] == "mi"
    assert round(float(state.state), 1) == 20.0  # 32.2 km
    assert state.attributes["started_at"] == "2026-11-21T13:50:00+00:00"
    assert state.attributes["ended_at"] == "2026-11-21T14:29:00+00:00"
    assert state.attributes["duration_minutes"] == 39
    assert state.attributes["end_latitude"] == 52.9712
    # FLEETA's average speed can be higher than its maximum, so it is not used.
    assert "avg_speed_kmh" not in state.attributes


async def test_event_picture(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_client: ClientSessionGenerator,
) -> None:
    """The latest event's picture is downloaded once and served by Home Assistant."""
    state = hass.states.get("image.bmw_x5_50e_last_event_picture")
    assert state is not None
    assert state.state != STATE_UNKNOWN
    assert state.attributes["event_id"] == "evt_0001"
    assert state.attributes["event_type"] == "parking_impact"

    client = await hass_client()
    resp = await client.get("/api/image_proxy/image.bmw_x5_50e_last_event_picture")
    assert resp.status == HTTPStatus.OK
    assert await resp.read() == PICTURE

    # The API key is never sent with the picture link.
    picture_calls = [c for c in aioclient_mock.mock_calls if str(c[1]).startswith("https://media.example-cdn.com")]
    assert len(picture_calls) == 1
    headers = picture_calls[0][3] or {}
    assert "Authorization" not in headers

    # Nothing new: the picture and trip are not fetched again.
    coordinator = setup_integration.runtime_data
    mock_api(aioclient_mock)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not _calls(aioclient_mock, "/v1/events")
    assert not _calls(aioclient_mock, TRIPS_PATH)


async def test_new_event_fetches_its_picture(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A new event with video brings its own picture."""
    coordinator = setup_integration.runtime_data
    new_event = dict(PARKING_EVENT, eventId="evt_0002", type="harsh_braking", occurredAt="2026-11-22T09:00:00.000Z")
    new_url = "https://media.example-cdn.com/thumbs/evt_0002.jpg?sig=def"
    feed = {"data": [new_event], "pagination": {"nextCursor": "cursor-2", "hasMore": False}}

    # First cycle: FLEETA has not made the picture yet.
    mock_api(aioclient_mock, feed=feed)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(_calls(aioclient_mock, "/v1/events")) == 1
    assert hass.states.get("image.bmw_x5_50e_last_event_picture").attributes["event_id"] == "evt_0001"

    # Next cycle: the picture is there.
    ready = {"data": [dict(new_event, thumbnailUrl=new_url)] + RECENT_EVENTS["data"], "pagination": {}}
    mock_api(aioclient_mock, recent_events=ready)
    aioclient_mock.get(new_url, content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, headers={"Content-Type": "image/png"})
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("image.bmw_x5_50e_last_event_picture")
    assert state.attributes["event_id"] == "evt_0002"
    assert state.attributes["event_type"] == "harsh_braking"


async def test_trip_read_again_when_drive_ends(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """While driving no trip is read; when the car stops, the new trip is read."""
    coordinator = setup_integration.runtime_data
    driving = {"data": [dict(LOCATIONS["data"][0], status="driving", speedKmh=50)], "pagination": {}}
    mock_api(aioclient_mock, locations=driving)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not _calls(aioclient_mock, TRIPS_PATH)

    newer = dict(TRIPS["data"][0], tripId="trip_0003", startedAt="2026-11-22T10:00:00.000Z",
                 endedAt="2026-11-22T10:30:00.000Z", distanceKm=16.1)
    mock_api(aioclient_mock, trips={"data": [newer] + TRIPS["data"], "pagination": {}})
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(_calls(aioclient_mock, TRIPS_PATH)) == 1
    assert round(float(hass.states.get("sensor.bmw_x5_50e_last_trip").state), 1) == 10.0


async def test_extras_failing_never_breaks_the_car(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """If trips or pictures fail, everything else still works."""
    aioclient_mock.get(f"{API_BASE_URL}/v1/usage", json=USAGE)
    aioclient_mock.get(f"{API_BASE_URL}/v1/devices", json=DEVICES)
    aioclient_mock.get(f"{API_BASE_URL}/v1/fleet/locations", json=LOCATIONS)
    aioclient_mock.get(f"{API_BASE_URL}/v1/events/feed", json=FEED_BOOTSTRAP)
    aioclient_mock.get(f"{API_BASE_URL}{TRIPS_PATH}", status=500, json={"detail": "boom"})
    aioclient_mock.get(f"{API_BASE_URL}/v1/events", status=404, json={"detail": "nope"})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("device_tracker.bmw_x5_50e").attributes["latitude"] == 52.9712
    assert hass.states.get("sensor.bmw_x5_50e_last_trip").state == STATE_UNKNOWN
    assert hass.states.get("image.bmw_x5_50e_last_event_picture").state == STATE_UNKNOWN


async def test_bad_picture_is_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A link to a local address, or a file that is not a picture, is never kept."""
    local = {"data": [dict(PARKING_EVENT, thumbnailUrl="https://192.168.1.10/x.jpg")], "pagination": {}}
    mock_api(aioclient_mock, recent_events=local)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert not [c for c in aioclient_mock.mock_calls if c[1].host == "192.168.1.10"]
    assert hass.states.get("image.bmw_x5_50e_last_event_picture").state == STATE_UNKNOWN

    api = config_entry.runtime_data.api
    aioclient_mock.get("https://media.example-cdn.com/page.html", content=b"<html>not a picture</html>")
    assert await api.download_picture("https://media.example-cdn.com/page.html") is None
    assert await api.download_picture(PICTURE_URL.replace("https", "http")) is None


def test_link_and_picture_checks() -> None:
    """Only plain https links to named hosts, and only real pictures."""
    assert validate.https_url(PICTURE_URL) == PICTURE_URL
    for bad in (
        "http://media.example-cdn.com/a.jpg",
        "https://localhost/a.jpg",
        "https://10.0.0.1/a.jpg",
        "https://[::1]/a.jpg",
        "https://printer.local/a.jpg",
        "https://user:pw@media.example-cdn.com/a.jpg",
        "https://nodot/a.jpg",
        "javascript:alert(1)",
        "https://media.example-cdn.com/a.jpg\n",
        None,
        42,
    ):
        assert validate.https_url(bad) is None, bad
    assert validate.image_type(PICTURE) == "image/jpeg"
    assert validate.image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert validate.image_type(b"<svg onload=alert(1)>") is None
    assert validate.image_type(None) is None


def test_trip_cleaning() -> None:
    """Trips with bad times are cleaned or dropped."""
    trip = validate.trip(TRIPS["data"][1])
    assert trip["duration_min"] == 20.0  # worked out from start and end
    assert validate.trip(TRIPS["data"][2])["ended_at"] is None
    backwards = dict(TRIPS["data"][0], endedAt="2026-11-21T10:00:00.000Z", durationMin=None)
    assert validate.trip(backwards)["ended_at"] is None
    assert validate.trip({"tripId": "x"}) is None
    assert validate.trip(dict(TRIPS["data"][0], distanceKm=1e9))["distance_km"] is None
    assert validate.trip("nope") is None


def test_trip_end_filled_in() -> None:
    """A recent trip with no end time ends at the last GPS fix once the car stops."""
    from datetime import datetime, timedelta, timezone

    from custom_components.fleeta.coordinator import FleetaVehicle, _trip_end

    now = datetime(2026, 11, 22, 20, 0, tzinfo=timezone.utc)
    fix = datetime(2026, 11, 22, 14, 29, tzinfo=timezone.utc)
    parked = FleetaVehicle(psn=PSN, name="Car", status="parked", position_time=fix)
    recent = {"started_at": datetime(2026, 11, 22, 13, 50, tzinfo=timezone.utc), "duration_min": None}
    assert _trip_end(recent, parked, now) == fix
    with_duration = dict(recent, duration_min=39.0)
    assert _trip_end(with_duration, parked, now) == recent["started_at"] + timedelta(minutes=39)
    driving = FleetaVehicle(psn=PSN, name="Car", status="driving", position_time=fix)
    assert _trip_end(recent, driving, now) is None
    old = {"started_at": now - timedelta(days=3), "duration_min": None}
    assert _trip_end(old, parked, now) is None


async def test_distance_from_track_when_fleeta_gives_none(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """With no distance from FLEETA, the trip's GPS points are added up."""
    freezer.move_to("2026-11-21T16:00:00+00:00")
    no_distance = dict(TRIPS["data"][0], distanceKm=None, durationMin=None, endedAt=None,
                       startedAt="2026-11-21T15:00:00.000Z")
    mock_api(aioclient_mock, trips={"data": [no_distance], "pagination": {}})
    track = {
        "data": {
            "tripId": "trip_0002",
            "points": [
                {"at": "2026-11-21T15:00:00Z", "latitude": 52.9000, "longitude": 0.0601},
                {"at": "2026-11-21T15:05:00Z", "latitude": 52.9450, "longitude": 0.0601},
                # A GPS glitch far away is skipped.
                {"at": "2026-11-21T15:06:00Z", "latitude": 55.0, "longitude": 3.0},
                {"at": "2026-11-21T15:10:00Z", "latitude": 52.9712, "longitude": 0.0601},
            ],
        }
    }
    aioclient_mock.get(f"{API_BASE_URL}/v1/trips/trip_0002/track", json=track)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.bmw_x5_50e_last_trip")
    # 52.9000 -> 52.9712 is about 7.9 km (4.9 miles)
    assert 4.8 < float(state.state) < 5.0
    assert state.attributes["duration_minutes"] == 10.0
    # FLEETA gave no end time: the trip's last GPS point is used.
    assert state.attributes["ended_at"] == "2026-11-21T15:10:00+00:00"


def test_track_summary_checks() -> None:
    """Bad points are ignored; one point is not a trip."""
    assert validate.track_summary(None) == (None, None, None)
    assert validate.track_summary([{"at": "2026-11-21T15:00:00Z", "latitude": 52.9, "longitude": 0.06}])[0] is None
    assert validate.track_summary(["x", {"latitude": "a"}])[0] is None
