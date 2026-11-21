"""Base entities for the FLEETA integration."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, WEB_VIEWER_URL
from .coordinator import FleetaConfigEntry, FleetaCoordinator, FleetaVehicle


class FleetaVehicleEntity(CoordinatorEntity[FleetaCoordinator]):
    """An entity that belongs to one dashcam/vehicle."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FleetaCoordinator, psn: str, key: str) -> None:
        super().__init__(coordinator)
        self.psn = psn
        self._attr_unique_id = f"{psn}_{key}"
        vehicle = coordinator.data.vehicles[psn]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, psn)},
            name=vehicle.name,
            manufacturer=MANUFACTURER,
            model=vehicle.model,
            sw_version=vehicle.firmware,
            serial_number=psn,
            configuration_url=WEB_VIEWER_URL,
        )

    @property
    def vehicle(self) -> FleetaVehicle | None:
        """The latest data for this vehicle."""
        return self.coordinator.data.vehicles.get(self.psn)

    @property
    def available(self) -> bool:
        """Available while the cloud answers and the camera is on the account."""
        return super().available and self.vehicle is not None


class FleetaAccountEntity(CoordinatorEntity[FleetaCoordinator]):
    """An entity that belongs to the FLEETA account (API usage)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FleetaCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="FLEETA",
            manufacturer=MANUFACTURER,
            model="FLEETA Open API",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=WEB_VIEWER_URL,
        )


def async_add_vehicle_entities(
    entry: FleetaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[FleetaCoordinator, str], Iterable[Entity]],
) -> None:
    """Add entities for every vehicle now, and for any camera added later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [psn for psn in coordinator.data.vehicles if psn not in known]
        if not new:
            return
        known.update(new)
        entities: list[Entity] = []
        for psn in new:
            entities.extend(factory(coordinator, psn))
        async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
