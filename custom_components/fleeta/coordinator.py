"""Data update coordinator for the FLEETA integration."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import validate
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
    STATUSES,
    TRIPS_REFRESH,
    USAGE_REFRESH,
)

type FleetaConfigEntry = ConfigEntry[FleetaCoordinator]

# A FLEETA account should never hold more cameras than this; anything beyond
# it is ignored so a bad response cannot flood Home Assistant with devices.
MAX_VEHICLES = 100

# Trips and event pictures are only fetched for this many cameras, so a large
# fleet cannot use up the allowance on extras.
MAX_EXTRA_VEHICLES = 5
# A new event's picture can take a little while to appear; try a few cycles.
PICTURE_ATTEMPTS = 3
TRIP_RETRY = timedelta(minutes=30)


def _status(value: Any) -> str | None:
    """Return a known FLEETA status."""
    return value if value in STATUSES else None


def _count(value: Any) -> int | None:
    """Return a non-negative whole number."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


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
class FleetaPicture:
    """The still picture of a dashcam's latest event."""

    event_id: str
    event_type: str | None
    occurred_at: datetime | None
    data: bytes = field(repr=False)
    content_type: str
    fetched: datetime


@dataclass
class FleetaData:
    """Everything the entities read."""

    vehicles: dict[str, FleetaVehicle] = field(default_factory=dict)
    usage: FleetaUsage = field(default_factory=FleetaUsage)
    interval: timedelta = FALLBACK_INTERVAL
    # Kept apart from the vehicles so diagnostics never include them.
    trips: dict[str, dict[str, Any]] = field(default_factory=dict)
    pictures: dict[str, FleetaPicture] = field(default_factory=dict)


def _trip_end(trip: dict[str, Any], vehicle: FleetaVehicle, now: datetime) -> datetime | None:
    """Work out when a trip ended when FLEETA has not filled in the end time.

    FLEETA sometimes leaves the end time empty. If it gave a duration, the end
    is the start plus the duration. Otherwise, if the trip started in the last
    day and the car has stopped since, the trip ended at the car's last GPS fix.
    Old trips with no end are left as not finished.
    """
    started: datetime = trip["started_at"]
    if trip["duration_min"]:
        return started + timedelta(minutes=trip["duration_min"])
    fix = vehicle.position_time
    if (
        vehicle.status != STATUS_DRIVING
        and fix is not None
        and started < fix <= now
        and now - started <= timedelta(days=1)
    ):
        return fix
    return None


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
        # Trip and picture bookkeeping, per camera.
        self._last_status: dict[str, str | None] = {}
        self._trips_checked: dict[str, datetime] = {}
        self._pictures_checked: set[str] = set()
        self._picture_wanted: dict[str, tuple[str, int]] = {}

    @property
    def fixed_minutes(self) -> int:
        """Minimum polling interval from the options (0 = automatic)."""
        value = self.config_entry.options.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES)
        return value if isinstance(value, int) and 0 <= value <= 60 else DEFAULT_SCAN_INTERVAL_MINUTES

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
            await self._refresh_extras(now)
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
        self._state.usage = FleetaUsage(
            tier=validate.event_type(usage.get("tier")),
            period=validate.text(usage.get("period"), 16),
            used=_count(calls.get("used")),
            limit=_count(calls.get("limit")),
            fetched=now,
            calls_since_fetch=0,
        )

    def _vehicle_for(self, raw_psn: Any, raw_name: Any) -> FleetaVehicle | None:
        """Find or create the vehicle for a row, if its serial is valid."""
        camera = validate.psn(raw_psn)
        if camera is None:
            LOGGER.debug("Ignoring a FLEETA row with an invalid camera serial")
            return None
        vehicles = self._state.vehicles
        vehicle = vehicles.get(camera)
        if vehicle is None:
            if len(vehicles) >= MAX_VEHICLES:
                return None
            vehicle = FleetaVehicle(psn=camera, name=validate.text(raw_name) or camera)
            vehicles[camera] = vehicle
        return vehicle

    async def _refresh_devices(self, now: datetime) -> None:
        """Read the dashcam list (names, models, firmware, battery)."""
        for row in await self.api.get_devices():
            vehicle = self._vehicle_for(row.get("psn"), row.get("name"))
            if vehicle is None:
                continue
            vehicle.name = validate.text(row.get("name")) or vehicle.name
            vehicle.model = validate.text(row.get("model")) or vehicle.model
            vehicle.firmware = validate.text(row.get("fwVersion"), 32) or vehicle.firmware
            vehicle.status = _status(row.get("status")) or vehicle.status
            vehicle.last_connected = validate.timestamp(row.get("lastConnectedAt")) or vehicle.last_connected
            battery = row.get("battery") if isinstance(row.get("battery"), dict) else {}
            vehicle.battery_connected = battery.get("connected") is True
            # When the battery pack is not connected the level key is missing:
            # leave it unknown rather than reporting 0 %.
            vehicle.battery_level = (
                validate.number(battery.get("level"), 0.0, 100.0) if vehicle.battery_connected else None
            )
        self._devices_fetched = now

    async def _refresh_locations(self) -> None:
        """Read the latest position of every dashcam."""
        for row in await self.api.get_locations():
            known = validate.psn(row.get("psn")) in self._state.vehicles
            vehicle = self._vehicle_for(row.get("psn"), row.get("name"))
            if vehicle is None:
                continue
            if not known:
                # A new camera: pick up its details on the next cycle.
                self._devices_fetched = None
            position = validate.coordinates(row.get("latitude"), row.get("longitude"))
            vehicle.latitude, vehicle.longitude = position if position else (None, None)
            vehicle.speed_kmh = validate.speed(row.get("speedKmh"))
            vehicle.heading = validate.heading(row.get("heading"))
            vehicle.position_time = validate.timestamp(row.get("at"))
            vehicle.status = _status(row.get("status")) or vehicle.status
            if vehicle.status == STATUS_OFFLINE:
                vehicle.speed_kmh = None

    async def _refresh_events(self) -> None:
        """Read new events from the feed and hand them to the event entities."""
        events, cursor, has_more = await self.api.get_events_feed(self._events_cursor)
        pages = 1
        all_events = list(events)
        # Catch up if more than one page arrived (rare; capped to protect the allowance).
        while self._events_cursor and has_more and pages < 3:
            self._events_cursor = validate.cursor(cursor) or self._events_cursor
            events, cursor, has_more = await self.api.get_events_feed(self._events_cursor)
            all_events.extend(events)
            pages += 1
        self._events_cursor = validate.cursor(cursor) or self._events_cursor

        signal = SIGNAL_NEW_EVENT.format(entry_id=self.config_entry.entry_id)
        for raw in all_events:
            event = validate.event(raw)
            if event is None or event["event_id"] in self._seen_event_ids:
                continue
            self._seen_event_ids.append(event["event_id"])
            async_dispatcher_send(self.hass, signal, event)
            if event["has_video"] and event["psn"] in self._state.vehicles:
                # Fetch this event's picture (the newest event wins).
                self._picture_wanted[event["psn"]] = (event["event_id"], 0)

    async def _refresh_extras(self, now: datetime) -> None:
        """Fetch the last trip and the latest event picture when they change.

        These are extras: a failure here is logged and tried again later, and
        never marks the cameras unavailable. Only a rejected key or a used-up
        monthly allowance stops the update.
        """
        for psn in list(self._state.vehicles)[:MAX_EXTRA_VEHICLES]:
            vehicle = self._state.vehicles[psn]
            try:
                await self._refresh_trip(psn, vehicle, now)
                await self._refresh_picture(psn, now)
            except FleetaAuthError:
                raise
            except FleetaRateLimitError as err:
                if err.monthly_limit:
                    raise
                LOGGER.debug("FLEETA asked to slow down; trip and picture wait: %s", err)
                return
            except FleetaError as err:
                LOGGER.debug("FLEETA trip or picture not available yet: %s", err)
            finally:
                self._last_status[psn] = vehicle.status

    async def _refresh_trip(self, psn: str, vehicle: FleetaVehicle, now: datetime) -> None:
        """Read the last trip once a drive has ended (and now and then)."""
        if vehicle.status == STATUS_DRIVING:
            return
        checked = self._trips_checked.get(psn)
        drive_ended = self._last_status.get(psn) == STATUS_DRIVING
        if checked is not None and not drive_ended and now - checked < TRIPS_REFRESH:
            return
        # If the call fails, try again in half an hour rather than waiting hours.
        self._trips_checked[psn] = now - TRIPS_REFRESH + TRIP_RETRY
        rows = await self.api.get_trips(psn)
        self._trips_checked[psn] = now
        trips = [trip for trip in map(validate.trip, rows) if trip]
        for trip in trips:
            if trip["ended_at"] is None:
                trip["ended_at"] = _trip_end(trip, vehicle, now)
                trip["end_guessed"] = True
        finished = [trip for trip in trips if trip["ended_at"]]
        if not finished:
            LOGGER.debug("FLEETA trips: %s row(s), %s usable, none finished", len(rows), len(trips))
            return
        last = max(finished, key=lambda trip: trip["ended_at"])
        known = self._state.trips.get(psn)
        if known and known["trip_id"] == last["trip_id"] and known["distance_km"]:
            return
        if last["distance_km"] is None:
            # FLEETA does not always give a distance: add it up from the trip's GPS points.
            km, first_at, last_at = validate.track_summary(await self.api.get_trip_track(last["trip_id"]))
            last["distance_source"] = "fleeta_track"
            if km is None:
                # No usable track from FLEETA: use the positions Home Assistant recorded.
                km = await self._distance_from_history(psn, last["started_at"], last["ended_at"])
                last["distance_source"] = "home_assistant"
            last["distance_km"] = km
            if last.get("end_guessed") and last_at and last_at > last["started_at"]:
                # The last GPS point of the trip is a better end time than our guess.
                last["ended_at"] = last_at
            if last_at and first_at and last["duration_min"] is None:
                last["duration_min"] = round((last_at - first_at).total_seconds() / 60, 1)
        if last["duration_min"] is None:
            last["duration_min"] = round((last["ended_at"] - last["started_at"]).total_seconds() / 60, 1)
        LOGGER.debug(
            "FLEETA trips: %s row(s), %s finished; last started %s, ended %s, %s km, %s min, row keys %s",
            len(rows),
            len(finished),
            last["started_at"].isoformat(),
            last["ended_at"].isoformat(),
            last["distance_km"],
            last["duration_min"],
            sorted(k for k in rows[0] if isinstance(k, str))[:20] if rows else [],
        )
        if last["distance_km"]:
            self._state.trips[psn] = last

    async def _distance_from_history(self, psn: str, start: datetime, end: datetime) -> float | None:
        """Add up the car's positions that Home Assistant recorded during a trip."""
        if "recorder" not in self.hass.config.components:
            return None
        entity_id = er.async_get(self.hass).async_get_entity_id("device_tracker", DOMAIN, f"{psn}_tracker")
        if entity_id is None:
            return None
        # Imported here so the integration still loads without the recorder.
        from homeassistant.components.recorder import history  # noqa: PLC0415
        from homeassistant.helpers.recorder import get_instance  # noqa: PLC0415

        try:
            states = await get_instance(self.hass).async_add_executor_job(
                partial(
                    history.get_significant_states,
                    self.hass,
                    start - timedelta(minutes=1),
                    end + timedelta(minutes=1),
                    [entity_id],
                    significant_changes_only=False,
                )
            )
        except Exception as err:  # noqa: BLE001 - history is an extra; never fail the update
            LOGGER.debug("Could not read recorded positions: %s", type(err).__name__)
            return None
        points = [
            {
                "at": state.last_updated.isoformat(),
                "latitude": state.attributes.get("latitude"),
                "longitude": state.attributes.get("longitude"),
            }
            for state in states.get(entity_id, [])
            if isinstance(state, State)
        ]
        km, _, _ = validate.track_summary(points)
        LOGGER.debug("FLEETA trip from Home Assistant history: %s position(s), %s km", len(points), km)
        return km

    async def _refresh_picture(self, psn: str, now: datetime) -> None:
        """Fetch the picture for a new event (or the latest one at start-up)."""
        wanted = self._picture_wanted.get(psn)
        if wanted is None and psn in self._pictures_checked:
            return
        self._pictures_checked.add(psn)
        rows = await self.api.get_recent_events(psn)
        candidates: list[tuple[dict[str, Any], str]] = []
        for raw in rows:
            event = validate.event(raw)
            url = raw.get("thumbnailUrl")
            if event and event["psn"] == psn and isinstance(url, str) and url:
                candidates.append((event, url))

        if wanted is not None:
            event_id, attempts = wanted
            match = next(((e, u) for e, u in candidates if e["event_id"] == event_id), None)
            if match is None:
                # The picture is not ready yet: try again next cycle, a few times.
                if attempts + 1 >= PICTURE_ATTEMPTS:
                    self._picture_wanted.pop(psn, None)
                else:
                    self._picture_wanted[psn] = (event_id, attempts + 1)
                return
            self._picture_wanted.pop(psn, None)
        else:
            if not candidates:
                return
            match = max(candidates, key=lambda item: item[0]["occurred_at"] or "")

        event, url = match
        current = self._state.pictures.get(psn)
        if current is not None and current.event_id == event["event_id"]:
            return
        picture = await self.api.download_picture(url)
        if picture is None:
            return
        data, content_type = picture
        self._state.pictures[psn] = FleetaPicture(
            event_id=event["event_id"],
            event_type=event["type"],
            occurred_at=validate.timestamp(event["occurred_at"]),
            data=data,
            content_type=content_type,
            fetched=now,
        )
