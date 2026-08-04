"""Tests for how the card is delivered to the frontend."""

from __future__ import annotations

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.voice_broadcast.const import CARD_URL, DOMAIN


def card_resources(hass: HomeAssistant) -> list[dict]:
    """Return the Lovelace resources pointing at this card."""
    return [
        item
        for item in hass.data[LOVELACE_DATA].resources.async_items()
        if str(item.get("url", "")).split("?")[0] == CARD_URL
    ]


async def setup_with_lovelace(hass: HomeAssistant) -> MockConfigEntry:
    """Set up the integration with Lovelace in storage mode."""
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "lovelace", {})

    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_card_registers_a_lovelace_resource(hass: HomeAssistant) -> None:
    """The card is delivered as a module resource, like any other custom card.

    This is what makes it load from the resource list at runtime rather than
    depending on a script tag in a possibly cached index page.
    """
    await setup_with_lovelace(hass)

    resources = card_resources(hass)
    assert len(resources) == 1
    # Created with "res_type", stored as "type".
    assert resources[0]["type"] == "module"
    # Versioned so upgrading the integration invalidates the browser cache.
    assert resources[0]["url"].startswith(f"{CARD_URL}?v=")


async def test_reload_does_not_duplicate_the_resource(hass: HomeAssistant) -> None:
    """Reloading must not stack up copies of the same resource."""
    entry = await setup_with_lovelace(hass)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert len(card_resources(hass)) == 1


async def test_outdated_resource_url_is_repointed(hass: HomeAssistant) -> None:
    """An existing resource from an older version is updated, not duplicated."""
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "lovelace", {})

    resources = hass.data[LOVELACE_DATA].resources
    await resources.async_get_info()
    await resources.async_create_item(
        {"res_type": "module", "url": f"{CARD_URL}?v=0.0.1"}
    )

    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    found = card_resources(hass)
    assert len(found) == 1
    assert found[0]["url"] != f"{CARD_URL}?v=0.0.1"


async def test_removal_deletes_the_resource(hass: HomeAssistant) -> None:
    """Removing the integration cleans up after itself."""
    entry = await setup_with_lovelace(hass)
    assert card_resources(hass)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert not card_resources(hass)
