"""The FLEETA (BlackVue cloud dashcam) integration."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FleetaApi
from .const import DOMAIN, MANUFACTURER, WEB_VIEWER_URL
from .coordinator import FleetaConfigEntry, FleetaCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: FleetaConfigEntry) -> bool:
    """Set up FLEETA from a config entry."""
    api = FleetaApi(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    coordinator = FleetaCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Register the account device first so the dashcams can be linked to it.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="FLEETA",
        manufacturer=MANUFACTURER,
        model="FLEETA Open API",
        entry_type=dr.DeviceEntryType.SERVICE,
        configuration_url=WEB_VIEWER_URL,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: FleetaConfigEntry) -> None:
    """Reload when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FleetaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
