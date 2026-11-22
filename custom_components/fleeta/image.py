"""Picture of the latest dashcam event (a still from the camera)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FleetaConfigEntry, FleetaCoordinator, FleetaPicture
from .entity import FleetaVehicleEntity, async_add_vehicle_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FleetaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the event picture entities."""

    def _factory(coordinator: FleetaCoordinator, psn: str) -> list[FleetaEventPicture]:
        return [FleetaEventPicture(coordinator, psn)]

    async_add_vehicle_entities(entry, async_add_entities, _factory)


class FleetaEventPicture(FleetaVehicleEntity, ImageEntity):
    """The still picture FLEETA keeps for the latest event with video.

    The picture is downloaded once by the integration and served by Home
    Assistant, so the short-lived FLEETA link never reaches the browser.
    """

    _attr_translation_key = "last_event_picture"

    def __init__(self, coordinator: FleetaCoordinator, psn: str) -> None:
        FleetaVehicleEntity.__init__(self, coordinator, psn, "last_event_picture")
        ImageEntity.__init__(self, coordinator.hass)
        self._shown: str | None = None
        self._update_from_picture()

    @property
    def _picture(self) -> FleetaPicture | None:
        return self.coordinator.data.pictures.get(self.psn)

    def _update_from_picture(self) -> None:
        picture = self._picture
        if picture is None:
            return
        if picture.event_id != self._shown:
            self._shown = picture.event_id
            self._attr_image_last_updated = picture.fetched
            self._attr_content_type = picture.content_type

    @callback
    def _handle_coordinator_update(self) -> None:
        """Pick up a new picture."""
        self._update_from_picture()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return the picture bytes."""
        picture = self._picture
        return picture.data if picture else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which event the picture belongs to."""
        picture = self._picture
        if picture is None:
            return {}
        occurred: datetime | None = picture.occurred_at
        return {
            "event_id": picture.event_id,
            "event_type": picture.event_type,
            "occurred_at": occurred.isoformat() if occurred else None,
        }
