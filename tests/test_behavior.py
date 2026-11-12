"""Behaviour that has regressed before: listeners, failed sends, intents, options."""
import aiohttp
import pytest

from common import (
    PRODUCTION_CLIMATE_CODES,
    FailingController,
    FakeEntry,
    RecordingController,
    capture_intents,
)


async def _no_last_state():
    return None


async def test_sensor_listener_released_on_remove(hass, make_entity):
    entity = make_entity("climate", 1080, temperature_sensor="sensor.room")
    entity.async_get_last_state = _no_last_state
    await entity.async_added_to_hass()

    hass.states.async_set("sensor.room", "25.0")
    await hass.async_block_till_done()
    assert entity._current_temperature == 25.0

    await entity.async_will_remove_from_hass()
    for remove in list(entity._on_remove or []):
        remove()

    hass.states.async_set("sensor.room", "27.5")
    await hass.async_block_till_done()
    assert entity._current_temperature == 25.0, "removed entity still receives sensor updates"


@pytest.mark.parametrize("code", PRODUCTION_CLIMATE_CODES)
async def test_climate_every_combination_sends(make_entity, code):
    entity = make_entity("climate", code)
    controller = entity._controller = RecordingController()
    published = capture_intents(entity)

    combinations = 0
    for mode in entity._operation_modes[1:]:
        for fan_mode in entity._fan_modes:
            for swing in entity._swing_modes or [None]:
                temperature = entity._min_temperature
                while temperature <= entity._max_temperature:
                    entity._hvac_mode = mode
                    entity._current_fan_mode = fan_mode
                    entity._current_swing_mode = swing
                    entity._target_temperature = temperature
                    assert await entity.send_command(), (mode, fan_mode, swing, temperature)
                    combinations += 1
                    temperature += entity._precision

    assert len(published) == combinations
    assert len(controller.sent) >= combinations


async def test_climate_normal_path(make_entity):
    from homeassistant.components.climate import HVACMode

    entity = make_entity("climate", 1090)
    controller = entity._controller = RecordingController()
    published = capture_intents(entity)

    await entity.async_turn_on()
    await entity.async_set_temperature(temperature=entity._min_temperature + 1, hvac_mode=HVACMode.COOL)
    await entity.async_set_fan_mode(entity._fan_modes[-1])
    await entity.async_turn_off()

    assert len(controller.sent) == 4
    assert [p["hvac_mode"] for p in published][-1] == "off"
    assert entity._hvac_mode == HVACMode.OFF
    assert entity._last_on_operation == HVACMode.COOL
    assert entity._target_temperature == entity._min_temperature + 1


async def test_climate_failed_send_keeps_state(make_entity):
    from homeassistant.components.climate import HVACMode

    entity = make_entity("climate", 1080)
    published = capture_intents(entity)
    entity._controller = RecordingController()
    await entity.async_set_hvac_mode(HVACMode.COOL)
    await entity.async_set_temperature(temperature=24)
    before = (entity._hvac_mode, entity._target_temperature, entity._current_fan_mode)
    sent_intents = len(published)

    entity._controller = FailingController()
    await entity.async_set_temperature(temperature=27)
    await entity.async_set_fan_mode(entity._fan_modes[-1])
    await entity.async_turn_off()

    assert (entity._hvac_mode, entity._target_temperature, entity._current_fan_mode) == before
    assert len(published) == sent_intents


async def test_climate_stores_hvac_mode_enum(make_entity):
    from homeassistant.components.climate import HVACMode

    entity = make_entity("climate", 1080)
    entity._controller = RecordingController()
    capture_intents(entity)
    await entity.async_set_hvac_mode("cool")
    assert isinstance(entity._hvac_mode, HVACMode)
    assert isinstance(entity._last_on_operation, HVACMode)


async def test_media_player_intent_carries_new_state(make_entity):
    entity = make_entity("media_player", 1440)
    entity._controller = RecordingController()
    published = capture_intents(entity)

    await entity.async_turn_on()
    assert published[-1]["state"] == "on"
    await entity.async_turn_off()
    assert published[-1]["state"] == "off"
    assert published[-1]["input_source"] is None


async def test_media_player_without_commands(make_entity):
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    device = {
        "manufacturer": "test",
        "supportedModels": [],
        "supportedController": "MQTT",
        "commandsEncoding": "Raw",
        "commands": {},
    }
    entity = make_entity("media_player", device=device)
    assert isinstance(entity.supported_features, MediaPlayerEntityFeature)
    entity.state_attributes
    entity.capability_attributes


async def test_mqtt_controller_raises_when_publish_fails(hass):
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.smartir.controller import MQTTController

    async def failing_publish(call):
        raise HomeAssistantError("broker down")

    hass.services.async_register("mqtt", "publish", failing_publish)
    controller = MQTTController(hass, "MQTT", "Raw", "zigbee2mqtt/0xtest/set", 0)
    with pytest.raises(HomeAssistantError):
        await controller.send('{"ir_code_to_send": "abc"}')


async def test_lookin_controller_raises_when_request_fails(hass, monkeypatch):
    from custom_components.smartir import controller as controller_module

    class FailingSession:
        def get(self, *args, **kwargs):
            raise aiohttp.ClientConnectionError("unreachable")

    monkeypatch.setattr(controller_module, "async_get_clientsession", lambda hass: FailingSession())
    controller = controller_module.LookinController(hass, "LOOKin", "Raw", "192.0.2.1", 0)
    with pytest.raises(aiohttp.ClientError):
        await controller.send("1234")


async def test_options_flow_can_clear_a_sensor():
    from custom_components.smartir import config_flow

    entry_data = {
        "platform": "climate",
        "name": "test",
        "device_code": 1080,
        "controller_data": "zigbee2mqtt/0xtest/set",
        "temperature_sensor": "sensor.old",
    }

    class Flow(config_flow.SmartIROptionsFlow):
        config_entry = FakeEntry(entry_data, "climate_1080")

    saved = {}
    flow = Flow()
    flow.async_create_entry = lambda data=None, **kwargs: saved.update(options=data)
    # The user emptied the temperature sensor field, so it is absent from the input.
    await flow.async_step_init(
        {
            "delay": 0.5,
            "power_sensor_restore_state": False,
            "enable_intent_sync": False,
            "intent_topic_base": "smartir/intent",
        }
    )
    merged = {**entry_data, **saved["options"]}
    assert merged["temperature_sensor"] is None
