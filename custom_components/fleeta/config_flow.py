"""Config flow for the FLEETA integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    FleetaApi,
    FleetaAuthError,
    FleetaConnectionError,
    FleetaError,
    FleetaForbiddenError,
    FleetaRateLimitError,
)
from .const import (
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    LOGGER,
    WEB_VIEWER_URL,
)
from .coordinator import FleetaConfigEntry

# Links are passed in, not written into the translations (a Home Assistant rule).
KEY_HELP = {"viewer_url": WEB_VIEWER_URL}

API_KEY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")
        )
    }
)


async def _validate_key(hass: HomeAssistant, api_key: str) -> dict[str, str]:
    """Check the key with two read-only calls. Returns errors, empty if fine."""
    if not api_key.strip().startswith("flt_"):
        return {CONF_API_KEY: "invalid_key_format"}
    api = FleetaApi(async_get_clientsession(hass), api_key)
    try:
        await api.get_locations()
    except FleetaAuthError:
        return {"base": "invalid_auth"}
    except FleetaForbiddenError:
        return {"base": "plan_not_supported"}
    except FleetaRateLimitError:
        return {"base": "rate_limited"}
    except FleetaConnectionError:
        return {"base": "cannot_connect"}
    except FleetaError:
        LOGGER.exception("Unexpected FLEETA error while checking the API key")
        return {"base": "unknown"}
    return {}


class FleetaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the FLEETA setup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            errors = await _validate_key(self.hass, api_key)
            if not errors:
                return self.async_create_entry(title="FLEETA", data={CONF_API_KEY: api_key})
        return self.async_show_form(
            step_id="user", data_schema=API_KEY_SCHEMA, errors=errors, description_placeholders=KEY_HELP
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The key stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            errors = await _validate_key(self.hass, api_key)
            if not errors:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_API_KEY: api_key}
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=API_KEY_SCHEMA, errors=errors, description_placeholders=KEY_HELP
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: FleetaConfigEntry) -> FleetaOptionsFlow:
        """Options for the polling interval."""
        return FleetaOptionsFlow()


class FleetaOptionsFlow(OptionsFlow):
    """Change how often FLEETA is checked."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL_MINUTES: int(user_input[CONF_SCAN_INTERVAL_MINUTES])}
            )
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL_MINUTES, default=current): NumberSelector(
                    NumberSelectorConfig(min=0, max=60, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="min")
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
