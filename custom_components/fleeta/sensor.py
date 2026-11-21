"""Sensors for the FLEETA integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfSpeed, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import STATUSES
from .coordinator import FleetaConfigEntry, FleetaCoordinator, FleetaData, FleetaVehicle
from .entity import FleetaAccountEntity, FleetaVehicleEntity, async_add_vehicle_entities

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FleetaVehicleSensorDescription(SensorEntityDescription):
    """Describes a per-vehicle sensor."""

    value_fn: Callable[[FleetaVehicle], float | str | datetime | None]
    exists_fn: Callable[[FleetaVehicle], bool] = lambda _: True


@dataclass(frozen=True, kw_only=True)
class FleetaAccountSensorDescription(SensorEntityDescription):
    """Describes an account-level sensor."""

    value_fn: Callable[[FleetaData], float | str | None]
    attrs_fn: Callable[[FleetaData], dict[str, Any]] = lambda _: {}


VEHICLE_SENSORS: tuple[FleetaVehicleSensorDescription, ...] = (
    FleetaVehicleSensorDescription(
        key="speed",
        translation_key="speed",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        suggested_unit_of_measurement=UnitOfSpeed.MILES_PER_HOUR,
        suggested_display_precision=0,
        value_fn=lambda v: v.speed_kmh,
    ),
    FleetaVehicleSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUSES,
        value_fn=lambda v: v.status if v.status in STATUSES else None,
    ),
    FleetaVehicleSensorDescription(
        key="last_gps_fix",
        translation_key="last_gps_fix",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda v: v.position_time,
    ),
    FleetaVehicleSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda v: v.battery_level,
        exists_fn=lambda v: v.battery_connected,
    ),
)

ACCOUNT_SENSORS: tuple[FleetaAccountSensorDescription, ...] = (
    FleetaAccountSensorDescription(
        key="api_calls_used",
        translation_key="api_calls_used",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="calls",
        value_fn=lambda d: d.usage.used_estimate,
        attrs_fn=lambda d: {
            "monthly_limit": d.usage.limit,
            "tier": d.usage.tier,
            "period": d.usage.period,
            "last_checked": d.usage.fetched.isoformat() if d.usage.fetched else None,
        },
    ),
    FleetaAccountSensorDescription(
        key="update_interval",
        translation_key="update_interval",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=1,
        value_fn=lambda d: round(d.interval.total_seconds() / 60, 1),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FleetaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        FleetaAccountSensor(coordinator, description) for description in ACCOUNT_SENSORS
    )

    def _factory(coordinator: FleetaCoordinator, psn: str) -> list[FleetaVehicleSensor]:
        vehicle = coordinator.data.vehicles[psn]
        return [
            FleetaVehicleSensor(coordinator, psn, description)
            for description in VEHICLE_SENSORS
            if description.exists_fn(vehicle)
        ]

    async_add_vehicle_entities(entry, async_add_entities, _factory)


class FleetaVehicleSensor(FleetaVehicleEntity, SensorEntity):
    """A sensor for one vehicle."""

    entity_description: FleetaVehicleSensorDescription

    def __init__(
        self,
        coordinator: FleetaCoordinator,
        psn: str,
        description: FleetaVehicleSensorDescription,
    ) -> None:
        super().__init__(coordinator, psn, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | datetime | None:
        """Current value."""
        vehicle = self.vehicle
        return self.entity_description.value_fn(vehicle) if vehicle else None


class FleetaAccountSensor(FleetaAccountEntity, SensorEntity):
    """A sensor for the FLEETA account."""

    entity_description: FleetaAccountSensorDescription

    def __init__(self, coordinator: FleetaCoordinator, description: FleetaAccountSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        """Current value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Extra details."""
        return self.entity_description.attrs_fn(self.coordinator.data)
