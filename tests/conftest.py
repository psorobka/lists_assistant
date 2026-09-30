"""Run integrations in a real Home Assistant test instance."""

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Allow the test HA instance to discover this custom integration."""


@pytest.fixture
async def native_shopping(hass, tmp_path):
    """Real Shopping List and its public todo services, isolated from the user's HA."""
    hass.config.config_dir = str(tmp_path)
    entry = MockConfigEntry(domain="shopping_list", unique_id="shopping_list", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("todo", "shopping_list", entry.entry_id)
    assert entity_id is not None
    return entity_id
