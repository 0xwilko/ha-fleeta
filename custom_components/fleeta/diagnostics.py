"""Diagnostics for the FLEETA integration (secrets and positions removed)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .coordinator import FleetaConfigEntry

TO_REDACT = {CONF_API_KEY, "latitude", "longitude", "psn"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FleetaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "usage": asdict(data.usage),
        "interval_seconds": data.interval.total_seconds(),
        "vehicles": [async_redact_data(asdict(vehicle), TO_REDACT) for vehicle in data.vehicles.values()],
    }
