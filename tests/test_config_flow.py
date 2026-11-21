"""Tests for the FLEETA config and options flows."""

from __future__ import annotations

import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.fleeta.const import CONF_SCAN_INTERVAL_MINUTES, DOMAIN

from .conftest import API_KEY, mock_api


async def test_user_flow_success(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A good key creates the entry."""
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: f"  {API_KEY} "})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "FLEETA"
    assert result["data"] == {CONF_API_KEY: API_KEY}


@pytest.mark.parametrize(
    ("status", "key", "error_field", "error"),
    [
        (200, "not-a-key", CONF_API_KEY, "invalid_key_format"),
        (401, API_KEY, "base", "invalid_auth"),
        (403, API_KEY, "base", "plan_not_supported"),
        (429, API_KEY, "base", "rate_limited"),
        (503, API_KEY, "base", "cannot_connect"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    key: str,
    error_field: str,
    error: str,
) -> None:
    """Problems with the key are shown on the form, and it can be retried."""
    mock_api(aioclient_mock, status=status)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: key})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {error_field: error}

    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: API_KEY})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_single_instance(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """Only one FLEETA account can be added."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reauth(
    hass: HomeAssistant, setup_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A new key can be entered after the old one stops working."""
    result = await setup_integration.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "flt_live_newkey00000000000"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert setup_integration.data[CONF_API_KEY] == "flt_live_newkey00000000000"


async def test_options_flow(
    hass: HomeAssistant, setup_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """The minimum interval option is saved and applied."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    mock_api(aioclient_mock)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL_MINUTES: 20}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_integration.options == {CONF_SCAN_INTERVAL_MINUTES: 20}
    coordinator = setup_integration.runtime_data
    assert coordinator.update_interval.total_seconds() >= 20 * 60
