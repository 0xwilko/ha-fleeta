"""Hostile and malformed API data must never reach Home Assistant unchecked."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.fleeta import validate
from custom_components.fleeta.const import API_BASE_URL

from .conftest import LOCATIONS, PSN, mock_api

SCRIPT = '<img src=x onerror="alert(1)">'


def test_text_cleaning() -> None:
    """Control characters, bidi tricks and long strings are removed or cut."""
    assert validate.text("BMW\nX5\x00 50e‮") == "BMWX5 50e"
    assert validate.text("a" * 500) == "a" * validate.MAX_TEXT
    assert validate.text("   ") is None
    assert validate.text(123) is None
    assert validate.text(None) is None


def test_identifiers() -> None:
    """Serials, ids, types and cursors must match strict patterns."""
    assert validate.psn(PSN) == PSN
    for bad in (SCRIPT, "../etc/passwd", "a b", "", "x" * 65, None, 7):
        assert validate.psn(bad) is None
    assert validate.event_type("parking_impact") == "parking_impact"
    for bad in (SCRIPT, "Parking Impact", "x" * 49, None):
        assert validate.event_type(bad) is None
    assert validate.cursor("eyJsYXN0SWQiOiJldnRfMDAzMiJ9") is not None
    assert validate.cursor("abc def") is None
    assert validate.identifier("evt_0001") == "evt_0001"
    assert validate.identifier(SCRIPT) is None


def test_numbers() -> None:
    """Out-of-range, infinite, NaN and boolean values are rejected."""
    assert validate.coordinates(52.97, 0.06) == (52.97, 0.06)
    assert validate.coordinates(0, 0) is None
    assert validate.coordinates(91, 0.06) is None
    assert validate.coordinates(52.97, 181) is None
    assert validate.coordinates(float("nan"), 0.06) is None
    assert validate.coordinates("52.97", 0.06) is None
    assert validate.speed(-5) is None
    assert validate.speed(9999) is None
    assert validate.speed(float("inf")) is None
    assert validate.speed(True) is None
    assert validate.heading(361) is None
    assert validate.number(0, 0, 10) == 0.0


def test_timestamps() -> None:
    """Only timezone-aware ISO timestamps are accepted."""
    assert validate.timestamp("2026-11-21T16:00:00.000Z") is not None
    assert validate.timestamp("2026-11-21T16:00:00") is None
    assert validate.timestamp("not a date") is None
    assert validate.timestamp("2" * 100) is None


def test_event_cleaning() -> None:
    """Event rows are rebuilt from checked fields only."""
    raw = {
        "eventId": "evt_1",
        "psn": PSN,
        "type": SCRIPT,
        "occurredAt": "2026-11-21T16:00:00Z",
        "speedKmh": "fast",
        "location": {"latitude": 999, "longitude": 0.06},
        "hasVideo": "yes",
        "channels": ["front", SCRIPT, 5, "rear"] + ["x"] * 20,
        "extra": SCRIPT,
    }
    event = validate.event(raw)
    assert event is not None
    assert event["type"] is None
    assert event["speed_kmh"] is None
    assert event["latitude"] is None and event["longitude"] is None
    assert event["has_video"] is False
    assert event["channels"][:2] == ["front", "rear"]
    assert len(event["channels"]) <= validate.MAX_CHANNELS
    assert "extra" not in event
    assert validate.event({"eventId": SCRIPT, "psn": PSN}) is None
    assert validate.event({"eventId": "evt_1", "psn": SCRIPT}) is None
    assert validate.event("not a dict") is None


async def test_hostile_location_data(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Bad rows are skipped and bad values become unknown, never shown raw."""
    coordinator = setup_integration.runtime_data
    hostile = {
        "data": [
            dict(
                LOCATIONS["data"][0],
                name=SCRIPT,
                latitude=500,
                longitude="0.06",
                speedKmh=-40,
                heading=999,
                status=SCRIPT,
                at="yesterday",
            ),
            {"psn": SCRIPT, "name": "intruder", "latitude": 52.0, "longitude": 0.1},
            {"psn": "../../x", "latitude": 52.0, "longitude": 0.1},
        ],
        "pagination": {"nextCursor": None, "hasMore": False},
    }
    mock_api(aioclient_mock, locations=hostile)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert set(coordinator.data.vehicles) == {PSN}
    tracker = hass.states.get("device_tracker.bmw_x5_50e")
    assert tracker.attributes.get("latitude") is None
    assert tracker.attributes["heading"] is None
    assert hass.states.get("sensor.bmw_x5_50e_speed").state == "unknown"
    # An unknown status keeps the last good one rather than showing junk.
    assert hass.states.get("sensor.bmw_x5_50e_status").state == "parked"
    assert hass.states.get("sensor.bmw_x5_50e_last_gps_fix").state == "unknown"
    for state in hass.states.async_all():
        assert "onerror" not in str(state.attributes)
        assert "onerror" not in state.state


async def test_hostile_event_data(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An event with an unsafe type fires as 'other' with no raw text."""
    coordinator = setup_integration.runtime_data
    bad_event = {
        "eventId": "evt_77",
        "psn": PSN,
        "type": SCRIPT,
        "occurredAt": "2026-11-21T16:00:00Z",
        "location": None,
        "channels": [SCRIPT],
        "hasVideo": True,
    }
    mock_api(aioclient_mock, feed={"data": [bad_event], "pagination": {"nextCursor": SCRIPT, "hasMore": False}})
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("event.bmw_x5_50e_dashcam_event")
    assert state.attributes["event_type"] == "other"
    assert state.attributes["fleeta_type"] == "other"
    assert state.attributes["channels"] == []
    # A malformed cursor is ignored and the last good one kept.
    assert coordinator._events_cursor == "cursor-1"


async def test_redirect_is_not_followed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A redirect is treated as an error, so the key is never sent elsewhere."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.fleeta.api import FleetaApi, FleetaConnectionError

    aioclient_mock.get(
        f"{API_BASE_URL}/v1/usage",
        status=302,
        headers={"Location": "https://attacker.example/steal"},
    )
    aioclient_mock.get("https://attacker.example/steal", json={"data": {}})
    api = FleetaApi(async_get_clientsession(hass), "flt_live_secret000000000")
    try:
        await api.get_usage()
    except FleetaConnectionError as err:
        assert "flt_live_secret" not in str(err)
    else:  # pragma: no cover
        raise AssertionError("redirect should fail")
    assert all("attacker" not in str(call[1]) for call in aioclient_mock.mock_calls)


async def test_error_text_is_cleaned(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """API error text is shortened and stripped of control characters."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.fleeta.api import FleetaApi, FleetaConnectionError

    aioclient_mock.get(
        f"{API_BASE_URL}/v1/usage",
        status=500,
        json={"detail": "boom\n" + "A" * 5000, "code": SCRIPT},
    )
    api = FleetaApi(async_get_clientsession(hass), "flt_live_secret000000000")
    try:
        await api.get_usage()
    except FleetaConnectionError as err:
        assert "\n" not in str(err)
        assert len(str(err)) < 200
        assert err.code is None
    else:  # pragma: no cover
        raise AssertionError("500 should fail")
