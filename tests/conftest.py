"""Shared fixtures for the SmartIR(Mod) tests.

The suite runs against a real `homeassistant` package (not
pytest-homeassistant-custom-component, which pins one HA version), so the same
tests run on every HA version CI checks: see `.github/workflows/tests.yml`.
"""
import sys

import pytest

from common import REPO, FakeEntry, load_code

sys.path.insert(0, str(REPO))

import homeassistant  # noqa: E402,F401  HA >= 2026.9 aliases voluptuous on import

try:
    import voluptuous  # noqa: F401
except ImportError:  # HA >= 2026.9 without the alias installed yet
    import probatio

    sys.modules["voluptuous"] = probatio


@pytest.fixture
async def hass(tmp_path):
    """A bare HomeAssistant instance with this integration in custom_components."""
    from homeassistant import loader
    from homeassistant.core import HomeAssistant

    (tmp_path / "custom_components").mkdir()
    (tmp_path / "custom_components" / "smartir").symlink_to(
        REPO / "custom_components" / "smartir"
    )
    instance = HomeAssistant(str(tmp_path))
    # Deprecation reports and translation lookups need the integration cache.
    loader.async_setup(instance)
    yield instance
    await instance.async_stop(force=True)


@pytest.fixture
def make_entity(hass):
    """Build a SmartIR entity from a device code file without adding it to HA."""
    from custom_components.smartir import climate, fan, light, media_player

    classes = {
        "climate": climate.SmartIRClimate,
        "fan": fan.SmartIRFan,
        "light": light.SmartIRLight,
        "media_player": media_player.SmartIRMediaPlayer,
    }

    def _make(platform, code=9999, *, device=None, options=None, **config):
        data = {
            "controller_data": "zigbee2mqtt/0xtest/set",
            "delay": 0,
            "platform": platform,
            "name": "test",
            "device_code": code,
            **config,
        }
        entity = classes[platform](
            hass,
            FakeEntry(data, f"{platform}_{code}", options),
            {**data, **(options or {})},
            device if device is not None else load_code(platform, code),
        )
        entity.hass = hass
        entity.entity_id = f"{platform}.test_{code}"
        # Not added to a platform, so there is no state machine entry to write.
        entity.async_write_ha_state = lambda: None
        return entity

    return _make
