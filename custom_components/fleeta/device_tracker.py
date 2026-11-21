"""GPS tracker for each FLEETA dashcam."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FleetaConfigEntry, FleetaCoordinator
from .entity import FleetaVehicleEntity, async_add_vehicle_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FleetaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the trackers."""

    def _factory(coordinator: FleetaCoordinator, psn: str) -> list[FleetaTracker]:
        return [FleetaTracker(coordinator, psn)]

    async_add_vehicle_entities(entry, async_add_entities, _factory)


class FleetaTracker(FleetaVehicleEntity, TrackerEntity):
    """Where the vehicle is, from the dashcam's GPS."""

    _attr_name = None
    _attr_translation_key = "vehicle"

    def __init__(self, coordinator: FleetaCoordinator, psn: str) -> None:
        super().__init__(coordinator, psn, "tracker")

    @property
    def source_type(self) -> SourceType:
        """The position comes from GPS."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Latitude of the last GPS fix."""
        vehicle = self.vehicle
        return vehicle.latitude if vehicle and vehicle.has_position else None

    @property
    def longitude(self) -> float | None:
        """Longitude of the last GPS fix."""
        vehicle = self.vehicle
        return vehicle.longitude if vehicle and vehicle.has_position else None

    @property
    def location_accuracy(self) -> float:
        """FLEETA gives no accuracy figure; dashcam GPS is typically within 10 m."""
        return 10

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Extra details shown with the position."""
        vehicle = self.vehicle
        if vehicle is None:
            return {}
        return {
            "heading": vehicle.heading,
            "last_gps_fix": vehicle.position_time.isoformat() if vehicle.position_time else None,
            "fleeta_status": vehicle.status,
        }
