"""Tests for setting up FLEETA and its entities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntryState, SOURCE_REAUTH
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.fleeta.const import (
    DOMAIN,
    FALLBACK_INTERVAL,
    LIMIT_REACHED_INTERVAL,
    MAX_INTERVAL,
    MIN_INTERVAL,
)
from custom_components.fleeta.coordinator import FleetaUsage, calculate_interval

from .conftest import LOCATIONS, PARKING_EVENT, PSN, mock_api


async def test_setup_creates_entities(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """Setting up creates the tracker, sensors, binary sensor and event."""
    assert setup_integration.state is ConfigEntryState.LOADED

    tracker = hass.states.get("device_tracker.bmw_x5_50e")
    assert tracker is not None
    assert tracker.attributes["latitude"] == 52.9712
    assert tracker.attributes["longitude"] == 0.0601
    assert tracker.attributes["source_type"] == "gps"
    assert tracker.attributes["fleeta_status"] == "parked"

    assert hass.states.get("sensor.bmw_x5_50e_status").state == "parked"
    assert hass.states.get("sensor.bmw_x5_50e_last_gps_fix").state == "2026-11-21T15:20:00+00:00"
    speed = hass.states.get("sensor.bmw_x5_50e_speed")
    assert float(speed.state) == 0
    assert speed.attributes["unit_of_measurement"] == "mph"
    assert hass.states.get("binary_sensor.bmw_x5_50e_cloud_connection").state == "on"
    assert hass.states.get("event.bmw_x5_50e_dashcam_event").state == STATE_UNKNOWN

    # No battery pack connected, so no battery sensor.
    assert hass.states.get("sensor.bmw_x5_50e_battery") is None

    calls = hass.states.get("sensor.fleeta_api_calls_this_month")
    assert calls.attributes["monthly_limit"] == 10500
    assert calls.attributes["tier"] == "pro"
    assert int(calls.state) >= 1000

    registry = er.async_get(hass)
    entry = registry.async_get("device_tracker.bmw_x5_50e")
    assert entry.unique_id == f"{PSN}_tracker"


async def test_new_event_fires(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A new event from the feed fires the event entity once."""
    coordinator = setup_integration.runtime_data
    feed = {"data": [PARKING_EVENT], "pagination": {"nextCursor": "cursor-2", "hasMore": False}}
    mock_api(aioclient_mock, feed=feed)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # The second call carries the cursor from the first, not start=latest.
    feed_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/v1/events/feed"]
    assert feed_calls and feed_calls[-1][1].query.get("after") == "cursor-1"

    state = hass.states.get("event.bmw_x5_50e_dashcam_event")
    assert state.attributes["event_type"] == "parking_impact"
    assert state.attributes["has_video"] is True
    first_fired = state.state

    # The same event delivered again is ignored.
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("event.bmw_x5_50e_dashcam_event").state == first_fired


async def test_unknown_event_type_maps_to_other(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An event type we do not know yet still fires, as 'other'."""
    coordinator = setup_integration.runtime_data
    new_type = dict(PARKING_EVENT, eventId="evt_9", type="something_new", location=None)
    mock_api(aioclient_mock, feed={"data": [new_type], "pagination": {"nextCursor": "c3", "hasMore": False}})
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("event.bmw_x5_50e_dashcam_event")
    assert state.attributes["event_type"] == "other"
    assert state.attributes["fleeta_type"] == "something_new"


async def test_moving_vehicle(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Speed and status update while driving."""
    coordinator = setup_integration.runtime_data
    driving = {
        "data": [dict(LOCATIONS["data"][0], speedKmh=64.4, status="driving", latitude=52.98)],
        "pagination": {"nextCursor": None, "hasMore": False},
    }
    mock_api(aioclient_mock, locations=driving)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bmw_x5_50e_status").state == "driving"
    assert round(float(hass.states.get("sensor.bmw_x5_50e_speed").state)) == 40
    assert hass.states.get("device_tracker.bmw_x5_50e").attributes["latitude"] == 52.98


async def test_offline_vehicle(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Offline: connection off and speed unknown, position kept."""
    coordinator = setup_integration.runtime_data
    offline = {
        "data": [dict(LOCATIONS["data"][0], speedKmh=None, heading=None, status="offline")],
        "pagination": {"nextCursor": None, "hasMore": False},
    }
    mock_api(aioclient_mock, locations=offline)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.bmw_x5_50e_cloud_connection").state == "off"
    assert hass.states.get("sensor.bmw_x5_50e_speed").state == STATE_UNKNOWN
    assert hass.states.get("device_tracker.bmw_x5_50e").attributes["latitude"] == 52.9712


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A revoked key starts the re-authentication flow."""
    coordinator = setup_integration.runtime_data
    mock_api(aioclient_mock, status=401)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_monthly_limit_slows_down(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """When the monthly allowance is used up, polling drops to hourly."""
    coordinator = setup_integration.runtime_data
    aioclient_mock.clear_requests()
    body = {"status": 429, "code": "monthly_limit_exceeded", "detail": "limit"}
    for path in ("/v1/usage", "/v1/devices", "/v1/fleet/locations", "/v1/events/feed"):
        aioclient_mock.get(f"https://openapi.fleeta.io{path}", status=429, json=body)
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert coordinator.update_interval == LIMIT_REACHED_INTERVAL
    assert hass.states.get("device_tracker.bmw_x5_50e").state == "unavailable"


async def test_unload(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """The entry unloads cleanly."""
    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics_redacts(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """Diagnostics never contain the key, serial or position."""
    from custom_components.fleeta.diagnostics import async_get_config_entry_diagnostics

    result = await async_get_config_entry_diagnostics(hass, setup_integration)
    text = str(result)
    assert "flt_live_testkey" not in text
    assert PSN not in text
    assert "52.9712" not in text


def test_interval_pacing() -> None:
    """Pacing spreads the allowance over the month and never overspends."""
    now = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)

    unknown = FleetaUsage()
    assert calculate_interval(unknown, now, moving=False) == FALLBACK_INTERVAL

    # Pro, one camera, start of the month.
    fresh = FleetaUsage(limit=10500, used=0)
    parked = calculate_interval(fresh, now, moving=False)
    moving = calculate_interval(fresh, now, moving=True)
    assert MIN_INTERVAL <= moving < parked <= MAX_INTERVAL
    # Worst case (always parked) still stays inside the allowance.
    month = timedelta(days=30).total_seconds()
    assert month / parked.total_seconds() * 2 < 10500

    # Allowance used up.
    spent = FleetaUsage(limit=10500, used=10500)
    assert calculate_interval(spent, now, moving=True) == LIMIT_REACHED_INTERVAL

    # Option can only slow things down.
    assert calculate_interval(fresh, now, moving=True, fixed_minutes=20) == timedelta(minutes=20)
    assert calculate_interval(spent, now, moving=True, fixed_minutes=5) == LIMIT_REACHED_INTERVAL

    # Free tier allowance (300) is too small to track with: slowest rate.
    free = FleetaUsage(limit=300, used=0)
    assert calculate_interval(free, now, moving=True) == LIMIT_REACHED_INTERVAL

    # Starter, one camera (3,500): parked pace stays inside the allowance.
    starter = FleetaUsage(limit=3500, used=0)
    starter_parked = calculate_interval(starter, now, moving=False)
    assert month / starter_parked.total_seconds() * 2 + 360 < 3500
