"""Dashcam event entity (parking impacts, harsh braking and so on)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_OTHER, EVENT_TYPES, SIGNAL_NEW_EVENT
from .coordinator import FleetaConfigEntry, FleetaCoordinator
from .entity import FleetaVehicleEntity, async_add_vehicle_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FleetaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the event entities."""

    def _factory(coordinator: FleetaCoordinator, psn: str) -> list[FleetaDashcamEvent]:
        return [FleetaDashcamEvent(coordinator, psn)]

    async_add_vehicle_entities(entry, async_add_entities, _factory)


class FleetaDashcamEvent(FleetaVehicleEntity, EventEntity):
    """Fires when the dashcam reports an event to FLEETA."""

    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "dashcam_event"

    def __init__(self, coordinator: FleetaCoordinator, psn: str) -> None:
        super().__init__(coordinator, psn, "dashcam_event")

    async def async_added_to_hass(self) -> None:
        """Listen for new events from the coordinator."""
        await super().async_added_to_hass()
        signal = SIGNAL_NEW_EVENT.format(entry_id=self.coordinator.config_entry.entry_id)
        self.async_on_remove(async_dispatcher_connect(self.hass, signal, self._handle_event))

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        """Fire the event if it belongs to this dashcam."""
        if event.get("psn") != self.psn:
            return
        raw_type = str(event.get("type") or EVENT_OTHER)
        event_type = raw_type if raw_type in EVENT_TYPES else EVENT_OTHER
        location = event.get("location") if isinstance(event.get("location"), dict) else {}
        attributes: dict[str, Any] = {
            "fleeta_type": raw_type,
            "event_id": event.get("eventId"),
            "occurred_at": event.get("occurredAt"),
            "speed_kmh": event.get("speedKmh"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "has_video": event.get("hasVideo"),
            "channels": event.get("channels") or [],
        }
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()
