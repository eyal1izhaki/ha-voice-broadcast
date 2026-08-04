"""Tests for the Voice Broadcast config flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.voice_broadcast.const import DOMAIN


async def test_user_flow_creates_an_entry(hass: HomeAssistant) -> None:
    """The flow confirms and creates an entry with no configuration."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Voice Broadcast"
    assert result["data"] == {}


async def test_only_one_instance_allowed(
    hass: HomeAssistant, setup_integration
) -> None:
    """Setting the integration up twice aborts."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
