"""The four platforms load, build entities and expose valid HA attributes."""
import logging

import pytest

from common import PRODUCTION_CLIMATE_CODES, all_codes


@pytest.mark.parametrize("platform", ["climate", "fan", "light", "media_player"])
async def test_every_device_code_builds_an_entity(make_entity, platform):
    """Every device code file must load into an entity whose attributes HA accepts."""
    failures = []
    codes = all_codes(platform)
    for code in codes:
        try:
            entity = make_entity(platform, code)
            if platform == "climate":
                entity._hvac_mode = entity._operation_modes[-1]
            entity.state
            entity.state_attributes
            entity.capability_attributes
        except Exception as err:  # noqa: BLE001
            failures.append(f"{platform}/{code}: {type(err).__name__}: {err}")
    assert not failures, "\n".join(failures)
    assert codes


@pytest.mark.parametrize("code", PRODUCTION_CLIMATE_CODES)
async def test_climate_attributes(make_entity, code):
    from homeassistant.components.climate import HVACMode

    entity = make_entity("climate", code)
    entity._hvac_mode = HVACMode.COOL
    entity._target_temperature = 24
    entity._current_temperature = 26.5

    attrs = entity.state_attributes
    caps = entity.capability_attributes
    assert entity.state == "cool"
    assert attrs["temperature"] == 24
    assert attrs["current_temperature"] == 26.5
    assert caps["min_temp"] == entity._min_temperature
    assert caps["max_temp"] == entity._max_temperature
    assert all(isinstance(mode, HVACMode) for mode in entity.hvac_modes)


async def test_climate_native_temperature_names_do_not_warn(hass, caplog):
    """HA warns about climate classes that still provide the pre-2026.11 names."""
    from homeassistant.components.climate import ClimateEntity

    from custom_components.smartir.climate import SmartIRClimate

    for old_name in ("temperature_unit", "current_temperature", "target_temperature"):
        assert old_name not in vars(SmartIRClimate), old_name

    with caplog.at_level(logging.WARNING):
        # Class creation is where HA checks; SmartIRClimate itself was already imported.
        class SmartIRClimateCopy(SmartIRClimate):
            pass

        class OldNamesOnly(ClimateEntity):
            @property
            def temperature_unit(self):
                return "°C"

    messages = [record.getMessage() for record in caplog.records]
    assert not [m for m in messages if "SmartIRClimateCopy provides the deprecated" in m]
    # Negative control: the check is active on this HA version.
    assert [m for m in messages if "OldNamesOnly provides the deprecated temperature_unit" in m]


async def test_entities_rename_in_place():
    """HA re-adds custom entities on entity_id changes unless they opt in."""
    from homeassistant.helpers.entity import _entity_class_requires_readd as requires_readd

    from custom_components.smartir import climate, fan, light, media_player

    for cls in (
        climate.SmartIRClimate,
        fan.SmartIRFan,
        light.SmartIRLight,
        media_player.SmartIRMediaPlayer,
    ):
        assert not requires_readd(cls), cls.__name__


async def test_entity_id_changed_hook_runs(make_entity):
    """The opt-in hook only calls super()."""
    for platform, code in (("climate", 1080), ("fan", 1000), ("light", 1020), ("media_player", 1440)):
        make_entity(platform, code).async_entity_id_changed(f"{platform}.old")


async def test_light_color_modes_are_valid(make_entity):
    from homeassistant.components.light import ColorMode

    for code in all_codes("light"):
        entity = make_entity("light", code)
        modes = entity.supported_color_modes
        assert isinstance(modes, set), code
        assert ColorMode.UNKNOWN not in modes, code
        assert isinstance(entity.min_color_temp_kelvin, int), code
        assert isinstance(entity.max_color_temp_kelvin, int), code


@pytest.mark.parametrize("language", ["en", "de", "zh-Hant"])
async def test_config_flow_translations_load(hass, language):
    """HA only reads translations/<lang>.json for custom integrations, English is the fallback."""
    from homeassistant.helpers import translation

    strings = await translation.async_get_translations(hass, language, "config", ["smartir"])
    assert strings.get("component.smartir.config.step.user.title")
