"""Config entry setup and unload follow the HA 2026.11 convention (None / raise)."""
from types import SimpleNamespace

import pytest

from common import FakeEntry


class FakeConfigEntries:
    def __init__(self):
        self.forwarded = []
        self.unloaded = []
        self.updated = []

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append(list(platforms))

    async def async_unload_platforms_or_raise(self, entry, platforms):
        self.unloaded.append(list(platforms))

    def async_update_entry(self, entry, data):
        self.updated.append(data)
        entry.data = data
        return True


def _entry(platform="climate"):
    return FakeEntry(
        {
            "platform": platform,
            "name": "test",
            "device_code": 1080,
            "controller_data": "zigbee2mqtt/0xtest/set",
        },
        "climate_1080",
    )


async def test_setup_entry_returns_none_and_forwards():
    from custom_components.smartir import async_setup_entry

    hass = SimpleNamespace(config_entries=FakeConfigEntries())
    entry = _entry()
    assert await async_setup_entry(hass, entry) is None
    assert hass.config_entries.forwarded == [["climate"]]
    # Entries created before multi-HA sync get their intent metadata backfilled.
    assert entry.data["intent_id"] == "climate_1080_zigbee2mqtt_0xtest_set"
    assert entry.data["intent_source_id"]


async def test_setup_entry_raises_on_unknown_platform():
    from homeassistant.exceptions import ConfigEntryError

    from custom_components.smartir import async_setup_entry

    hass = SimpleNamespace(config_entries=FakeConfigEntries())
    with pytest.raises(ConfigEntryError):
        await async_setup_entry(hass, _entry("vacuum"))
    assert hass.config_entries.forwarded == []


async def test_unload_entry_returns_none():
    from custom_components.smartir import async_unload_entry

    hass = SimpleNamespace(config_entries=FakeConfigEntries())
    assert await async_unload_entry(hass, _entry()) is None
    assert hass.config_entries.unloaded == [["climate"]]


async def test_ha_has_the_apis_we_rely_on():
    """Guard the minimum HA version declared in manifest.json and hacs.json."""
    from homeassistant.components.climate import ClimateEntity
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.helpers.entity import Entity

    assert hasattr(ConfigEntries, "async_unload_platforms_or_raise")
    assert hasattr(ClimateEntity, "native_temperature_unit")
    assert hasattr(Entity, "async_entity_id_changed")
