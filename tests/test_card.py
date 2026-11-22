"""The FLEETA Car card is served by the integration and never blocks it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.fleeta import _async_register_card
from custom_components.fleeta.const import CARD_URL, DOMAIN, WEB_VIEWER_URL


async def test_card_is_served_and_loaded_once(hass: HomeAssistant) -> None:
    """The card file is served and added to every dashboard, once."""
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    with patch("custom_components.fleeta.add_extra_js_url") as add_js:
        await _async_register_card(hass)
        await _async_register_card(hass)

    hass.http.async_register_static_paths.assert_awaited_once()
    served = hass.http.async_register_static_paths.call_args[0][0][0]
    assert served.url_path == CARD_URL
    assert Path(served.path).is_file()
    add_js.assert_called_once()
    assert add_js.call_args[0][1] == f"{CARD_URL}?v=1.2.0"


async def test_card_problem_never_blocks_setup(hass: HomeAssistant) -> None:
    """If serving the card fails, setup carries on."""
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock(side_effect=RuntimeError("boom"))
    await _async_register_card(hass)


async def test_setup_screen_links_to_fleeta(hass: HomeAssistant) -> None:
    """The setup screen gets the FLEETA web viewer link for its steps."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["description_placeholders"] == {"viewer_url": WEB_VIEWER_URL}


def test_card_file_has_no_unsafe_html() -> None:
    """Values from FLEETA are only ever written as text, never as HTML."""
    js = (Path(__file__).parent.parent / "custom_components" / DOMAIN / "frontend" / "fleeta-card.js").read_text()
    # innerHTML is only used for fixed markup with no ${...} values in it
    for chunk in js.split("innerHTML")[1:]:
        assignment = chunk.split(";")[0]
        assert "${" not in assignment
