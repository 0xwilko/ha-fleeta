"""Data update coordinator for the FLEETA integration."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    FleetaApi,
    FleetaAuthError,
    FleetaConnectionError,
    FleetaError,
    FleetaForbiddenError,
    FleetaRateLimitError,
)
from .const import (
    BUDGET_SHARE,
    CALLS_PER_CYCLE,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEVICES_REFRESH,
    DOMAIN,
    FALLBACK_INTERVAL,
    LIMIT_REACHED_INTERVAL,
    LOGGER,
    MAX_INTERVAL,
    MIN_INTERVAL,
    MOVING_FACTOR,
    PARKED_FACTOR,
    SIGNAL_NEW_EVENT,
    STATUS_DRIVING,
    STATUS_OFFLINE,
    USAGE_REFRESH,
)

type FleetaConfigEntry = ConfigEntry[FleetaCoordinator]


def _parse_time(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp from the API."""
    if not isinstance(value, str) or not value:
        return None
    return dt_util.parse_datetime(value)


def _number(value: Any) -> float | None:
    """Return a float, keeping None (not measured) apart from 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass
class FleetaVehicle:
    """One dashcam and the vehicle it is fitted to."""

    psn: str
    name: str
    model: str | None = None
    firmware: str | None = None
    status: str | None = None
    last_connected: datetime | None = None
    battery_connected: bool = False
    battery_level: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    speed_kmh: float | None = None
    heading: float | None = None
    position_time: datetime | None = None

    @property
    def has_position(self) -> bool:
        """True when a GPS position is known."""
        return self.latitude is not None and self.longitude is not None

    @property
    def is_moving(self) -> bool:
        """True when the latest fix shows the vehicle moving."""
        return self.status == STATUS_DRIVING and (self.speed_kmh or 0) > 0


@dataclass
class FleetaUsage:
    """Monthly API allowance as reported by GET /v1/usage."""

    tier: str | None = None
    period: str | None = None
    used: int | None = None
    limit: int | None = None
    fetched: datetime | None = None
    calls_since_fetch: int = 0

    @property
    def used_estimate(self) -> int | None:
        """Calls used this month, including ours since the last usage check."""
        if self.used is None:
            return None
        return self.used + self.calls_since_fetch


@dataclass
class FleetaData:
    """Everything the entities read."""

    vehicles: dict[str, FleetaVehicle] = field(default_factory=dict)
    usage: FleetaUsage = field(default_factory=FleetaUsage)
    interval: timedelta = FALLBACK_INTERVAL


def _seconds_left_in_month(now: datetime) -> float:
    """Seconds until the allowance resets (1st of next month, UTC)."""
    now_utc = dt_util.as_utc(now)
    if now_utc.month == 12:
        reset = now_utc.replace(year=now_utc.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        reset = now_utc.replace(month=now_utc.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return max((reset - now_utc).total_seconds(), 60.0)


def calculate_interval(
    usage: FleetaUsage,
    now: datetime,
    *,
    moving: bool,
    fixed_minutes: int = 0,
) -> timedelta:
    """Work out how long to wait before the next polling cycle.

    The remaining monthly allowance is spread over the time left in the month,
    then sped up while the car is moving and slowed down while it is parked.
    Because this is recalculated every cycle from the calls actually left, it
    cannot overspend. The option only ever makes polling slower, never faster
    than the allowance permits.
    """
    automatic = _automatic_interval(usage, now, moving=moving)
    if fixed_minutes > 0:
        return max(automatic, timedelta(minutes=fixed_minutes))
    return automatic


def _automatic_interval(usage: FleetaUsage, now: datetime, *, moving: bool) -> timedelta:
    """Interval paced to the monthly allowance."""
    used = usage.used_estimate
    if usage.limit is None or used is None:
        return FALLBACK_INTERVAL

    # Keep back enough for the slow refreshes (devices + usage) for the rest of the month.
    seconds_left = _seconds_left_in_month(now)
    slow_calls = seconds_left / DEVICES_REFRESH.total_seconds() + seconds_left / USAGE_REFRESH.total_seconds()
    calls_left = (usage.limit - used) * BUDGET_SHARE - slow_calls
    if calls_left <= CALLS_PER_CYCLE:
        return LIMIT_REACHED_INTERVAL

    pace = seconds_left * CALLS_PER_CYCLE / calls_left
    pace *= MOVING_FACTOR if moving else PARKED_FACTOR
    pace = min(max(pace, MIN_INTERVAL.total_seconds()), MAX_INTERVAL.total_seconds())
    return timedelta(seconds=round(pace))


class FleetaCoordinator(DataUpdateCoordinator[FleetaData]):
    """Polls FLEETA within the account's monthly API allowance."""

    config_entry: FleetaConfigEntry

    def __init__(self, hass: HomeAssistant, entry: FleetaConfigEntry, api: FleetaApi) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=FALLBACK_INTERVAL,
        )
        self.api = api
        self._devices_fetched: datetime | None = None
        self._events_cursor: str | None = None
        self._seen_event_ids: deque[str] = deque(maxlen=200)
        self._state = FleetaData()

    @property
    def fixed_minutes(self) -> int:
        """Fixed polling interval from the options (0 = automatic)."""
        return int(self.config_entry.options.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES))

    async def _async_update_data(self) -> FleetaData:
        """Fetch the latest data from FLEETA."""
        now = dt_util.utcnow()
        state = self._state
        calls_before = self.api.calls_made
        try:
            if state.usage.fetched is None or now - state.usage.fetched >= USAGE_REFRESH:
                await self._refresh_usage(now)
            if self._devices_fetched is None or now - self._devices_fetched >= DEVICES_REFRESH:
                await self._refresh_devices(now)
            await self._refresh_locations()
            await self._refresh_events()
        except FleetaAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except FleetaRateLimitError as err:
            if err.monthly_limit:
                self.update_interval = LIMIT_REACHED_INTERVAL
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="monthly_limit"
                ) from err
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="rate_limited") from err
        except FleetaForbiddenError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="forbidden",
                translation_placeholders={"error": str(err)},
            ) from err
        except FleetaError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            if state.usage.fetched is not None:
                state.usage.calls_since_fetch += self.api.calls_made - calls_before

        moving = any(vehicle.is_moving for vehicle in state.vehicles.values())
        state.interval = calculate_interval(state.usage, now, moving=moving, fixed_minutes=self.fixed_minutes)
        self.update_interval = state.interval
        LOGGER.debug(
            "FLEETA: %s vehicle(s), moving=%s, calls used=%s/%s, next poll in %s",
            len(state.vehicles),
            moving,
            state.usage.used_estimate,
            state.usage.limit,
            state.interval,
        )
        return state

    async def _refresh_usage(self, now: datetime) -> None:
        """Read the monthly allowance. A failure here is not fatal."""
        try:
            usage = await self.api.get_usage()
        except FleetaForbiddenError as err:
            # Some plans cannot read usage. Fall back to a fixed pace and try
            # again at the next usage refresh.
            LOGGER.debug("FLEETA usage not available on this plan: %s", err)
            self._state.usage = FleetaUsage(fetched=now)
            return
        except FleetaConnectionError as err:
            LOGGER.debug("Could not read FLEETA usage, will retry: %s", err)
            return
        calls = usage.get("apiCalls") if isinstance(usage.get("apiCalls"), dict) else {}
        used = calls.get("used")
        limit = calls.get("limit")
        self._state.usage = FleetaUsage(
            tier=usage.get("tier") if isinstance(usage.get("tier"), str) else None,
            period=usage.get("period") if isinstance(usage.get("period"), str) else None,
            used=used if isinstance(used, int) else None,
            limit=limit if isinstance(limit, int) else None,
            fetched=now,
            calls_since_fetch=0,
        )

    async def _refresh_devices(self, now: datetime) -> None:
        """Read the dashcam list (names, models, firmware, battery)."""
        rows = await self.api.get_devices()
        vehicles = self._state.vehicles
        for row in rows:
            psn = str(row["psn"])
            vehicle = vehicles.get(psn) or FleetaVehicle(psn=psn, name=str(row.get("name") or psn))
            vehicle.name = str(row.get("name") or vehicle.name)
            vehicle.model = row.get("model") if isinstance(row.get("model"), str) else vehicle.model
            vehicle.firmware = row.get("fwVersion") if isinstance(row.get("fwVersion"), str) else vehicle.firmware
            vehicle.status = row.get("status") if isinstance(row.get("status"), str) else vehicle.status
            vehicle.last_connected = _parse_time(row.get("lastConnectedAt")) or vehicle.last_connected
            battery = row.get("battery") if isinstance(row.get("battery"), dict) else {}
            vehicle.battery_connected = bool(battery.get("connected"))
            # When the battery pack is not connected the level key is missing:
            # leave it unknown rather than reporting 0 %.
            vehicle.battery_level = _number(battery.get("level")) if vehicle.battery_connected else None
            vehicles[psn] = vehicle
        self._devices_fetched = now

    async def _refresh_locations(self) -> None:
        """Read the latest position of every dashcam."""
        rows = await self.api.get_locations()
        vehicles = self._state.vehicles
        for row in rows:
            psn = str(row["psn"])
            vehicle = vehicles.get(psn)
            if vehicle is None:
                vehicle = FleetaVehicle(psn=psn, name=str(row.get("name") or psn))
                vehicles[psn] = vehicle
                # A new camera: pick up its details on the next cycle.
                self._devices_fetched = None
            vehicle.latitude = _number(row.get("latitude"))
            vehicle.longitude = _number(row.get("longitude"))
            vehicle.speed_kmh = _number(row.get("speedKmh"))
            vehicle.heading = _number(row.get("heading"))
            vehicle.position_time = _parse_time(row.get("at"))
            if isinstance(row.get("status"), str):
                vehicle.status = row["status"]
            if vehicle.status == STATUS_OFFLINE:
                vehicle.speed_kmh = None

    async def _refresh_events(self) -> None:
        """Read new events from the feed and hand them to the event entities."""
        events, cursor, has_more = await self.api.get_events_feed(self._events_cursor)
        pages = 1
        all_events = list(events)
        # Catch up if more than one page arrived (rare; capped to protect the allowance).
        while self._events_cursor and has_more and pages < 3:
            self._events_cursor = cursor
            events, cursor, has_more = await self.api.get_events_feed(cursor)
            all_events.extend(events)
            pages += 1
        self._events_cursor = cursor

        signal = SIGNAL_NEW_EVENT.format(entry_id=self.config_entry.entry_id)
        for event in all_events:
            event_id = event.get("eventId")
            if not isinstance(event_id, str) or event_id in self._seen_event_ids:
                continue
            self._seen_event_ids.append(event_id)
            async_dispatcher_send(self.hass, signal, event)
