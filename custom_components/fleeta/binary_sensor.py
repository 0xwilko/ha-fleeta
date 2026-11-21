"""Cloud connection binary sensor for each FLEETA dashcam."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import STATUS_OFFLINE, STATUSES
from .coordinator import FleetaConfigEntry, FleetaCoordinator
from .entity import FleetaVehicleEntity, async_add_vehicle_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FleetaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""

    def _factory(coordinator: FleetaCoordinator, psn: str) -> list[FleetaCloudConnected]:
        return [FleetaCloudConnected(coordinator, psn)]

    async_add_vehicle_entities(entry, async_add_entities, _factory)


class FleetaCloudConnected(FleetaVehicleEntity, BinarySensorEntity):
    """On while the dashcam is connected to the FLEETA cloud."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "cloud_connection"

    def __init__(self, coordinator: FleetaCoordinator, psn: str) -> None:
        super().__init__(coordinator, psn, "cloud_connection")

    @property
    def is_on(self) -> bool | None:
        """Connected unless FLEETA reports the camera offline."""
        vehicle = self.vehicle
        if vehicle is None or vehicle.status not in STATUSES:
            return None
        return vehicle.status != STATUS_OFFLINE
