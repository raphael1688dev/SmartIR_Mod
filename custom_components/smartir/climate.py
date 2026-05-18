import asyncio
from functools import partial
import json
import logging
import os
from typing import Any

import aiofiles
import voluptuous as vol

from homeassistant.components.climate import ClimateEntity, PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA
from homeassistant.components.climate.const import (
    ClimateEntityFeature, HVACMode, HVAC_MODES, ATTR_HVAC_MODE)
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.const import (
    CONF_NAME, STATE_ON, STATE_OFF, STATE_UNKNOWN, STATE_UNAVAILABLE, ATTR_TEMPERATURE,
    PRECISION_WHOLE, Platform)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback, AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import COMPONENT_ABS_DIR, Helper
from .const import (
    CONF_CONTROLLER_DATA,
    CONF_DELAY,
    CONF_DEVICE_CODE,
    CONF_ENABLE_INTENT_SYNC,
    CONF_HUMIDITY_SENSOR,
    CONF_INTENT_ID,
    CONF_INTENT_SOURCE_ID,
    CONF_INTENT_TOPIC_BASE,
    CONF_PLATFORM,
    CONF_POWER_SENSOR,
    CONF_POWER_SENSOR_RESTORE_STATE,
    CONF_TEMPERATURE_SENSOR,
    CONF_UNIQUE_ID,
    DEFAULT_DELAY,
    DEFAULT_INTENT_TOPIC_BASE,
    DOMAIN,
)
from .controller import get_controller
from .intent_sync import SmartIRIntentMixin

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "SmartIR Climate"

SUPPORT_FLAGS = (
    ClimateEntityFeature.TURN_OFF |
    ClimateEntityFeature.TURN_ON |
    ClimateEntityFeature.TARGET_TEMPERATURE |
    ClimateEntityFeature.FAN_MODE
)

PLATFORM_SCHEMA = CLIMATE_PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_UNIQUE_ID): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_DEVICE_CODE): cv.positive_int,
    vol.Required(CONF_CONTROLLER_DATA): cv.string,
    vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
    vol.Optional(CONF_TEMPERATURE_SENSOR): cv.entity_id,
    vol.Optional(CONF_HUMIDITY_SENSOR): cv.entity_id,
    vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
    vol.Optional(CONF_POWER_SENSOR_RESTORE_STATE, default=False): cv.boolean,
})


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """YAML platform setup — soft-imports each entry into a ConfigEntry."""
    _LOGGER.warning(
        "Configuring SmartIR climate via YAML is deprecated. "
        "Entry '%s' will be imported automatically; please remove it from configuration.yaml "
        "after confirming the entity works.",
        config.get(CONF_NAME, DEFAULT_NAME),
    )
    import_data = dict(config)
    import_data[CONF_PLATFORM] = Platform.CLIMATE.value
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=import_data
        )
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a SmartIR climate from a config entry."""
    merged: dict[str, Any] = {**entry.data, **entry.options}
    device_code = int(merged[CONF_DEVICE_CODE])
    device_files_absdir = os.path.join(COMPONENT_ABS_DIR, 'codes', 'climate')

    await hass.async_add_executor_job(
        partial(os.makedirs, device_files_absdir, exist_ok=True)
    )

    device_json_path = os.path.join(device_files_absdir, f"{device_code}.json")
    file_exists = await hass.async_add_executor_job(os.path.exists, device_json_path)

    if not file_exists:
        _LOGGER.warning(
            "Couldn't find the device JSON file. The component will try to "
            "download it from the GitHub repo."
        )
        try:
            codes_source = (
                f"https://raw.githubusercontent.com/raphael1688dev/SmartIR_Mod/main/"
                f"codes/climate/{device_code}.json"
            )
            session = async_get_clientsession(hass)
            await Helper.downloader(session, codes_source, device_json_path)
        except Exception:
            _LOGGER.exception(
                "There was an error while downloading the device JSON file. "
                "Please check your internet connection or manually place the file."
            )
            return

    try:
        async with aiofiles.open(device_json_path, mode='r') as j:
            content = await j.read()
            device_data = json.loads(content)
    except Exception:
        _LOGGER.exception("The device JSON file is invalid or corrupted.")
        return

    async_add_entities([SmartIRClimate(hass, entry, merged, device_data)])


class SmartIRClimate(SmartIRIntentMixin, ClimateEntity, RestoreEntity):
    def __init__(self, hass, entry: ConfigEntry, config: dict[str, Any], device_data):
        _LOGGER.debug(
            "SmartIRClimate init started for device %s. Supported models: %s",
            config.get(CONF_NAME), device_data.get('supportedModels', []),
        )
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = entry.unique_id or config.get(CONF_UNIQUE_ID)
        self._name = config.get(CONF_NAME, DEFAULT_NAME)
        self._device_code = int(config[CONF_DEVICE_CODE])
        self._controller_data = config[CONF_CONTROLLER_DATA]
        self._delay = float(config.get(CONF_DELAY, DEFAULT_DELAY))
        self._temperature_sensor = config.get(CONF_TEMPERATURE_SENSOR)
        self._humidity_sensor = config.get(CONF_HUMIDITY_SENSOR)
        self._power_sensor = config.get(CONF_POWER_SENSOR)
        self._power_sensor_restore_state = bool(config.get(CONF_POWER_SENSOR_RESTORE_STATE, False))

        self._manufacturer = device_data['manufacturer']
        self._supported_models = device_data['supportedModels']
        self._supported_controller = device_data['supportedController']
        self._commands_encoding = device_data['commandsEncoding']
        self._min_temperature = device_data['minTemperature']
        self._max_temperature = device_data['maxTemperature']
        self._precision = device_data['precision']

        valid_hvac_modes = [x for x in device_data['operationModes'] if x in HVAC_MODES]
        self._operation_modes = [HVACMode.OFF] + valid_hvac_modes
        self._fan_modes = device_data['fanModes']
        self._swing_modes = device_data.get('swingModes')
        self._commands = device_data['commands']

        self._target_temperature = self._min_temperature
        self._hvac_mode = HVACMode.OFF
        self._current_fan_mode = self._fan_modes[0]
        self._current_swing_mode = None
        self._last_on_operation = None

        self._current_temperature = None
        self._current_humidity = None

        self._unit = hass.config.units.temperature_unit

        self._support_flags = SUPPORT_FLAGS
        self._support_swing = False

        if self._swing_modes:
            self._support_flags |= ClimateEntityFeature.SWING_MODE
            self._current_swing_mode = self._swing_modes[0]
            self._support_swing = True

        self._temp_lock = asyncio.Lock()
        self._on_by_remote = False

        self._controller = get_controller(
            self.hass,
            self._supported_controller,
            self._commands_encoding,
            self._controller_data,
            self._delay,
        )

        self._intent_setup(
            enabled=bool(config.get(CONF_ENABLE_INTENT_SYNC, False)),
            topic_base=config.get(CONF_INTENT_TOPIC_BASE, DEFAULT_INTENT_TOPIC_BASE),
            intent_id=entry.data.get(CONF_INTENT_ID),
            source_id=entry.data.get(CONF_INTENT_SOURCE_ID),
        )

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        _LOGGER.debug("async_added_to_hass %s %s %s", self, self.name, self.supported_features)

        last_state = await self.async_get_last_state()

        if last_state is not None:
            if last_state.state in self._operation_modes:
                self._hvac_mode = last_state.state

            restored_fan_mode = last_state.attributes.get('fan_mode')
            if restored_fan_mode in self._fan_modes:
                self._current_fan_mode = restored_fan_mode

            restored_swing_mode = last_state.attributes.get('swing_mode')
            if self._swing_modes and restored_swing_mode in self._swing_modes:
                self._current_swing_mode = restored_swing_mode

            restored_temp = last_state.attributes.get('temperature')
            if isinstance(restored_temp, (int, float)) and self._min_temperature <= restored_temp <= self._max_temperature:
                self._target_temperature = restored_temp

            if 'last_on_operation' in last_state.attributes:
                self._last_on_operation = last_state.attributes['last_on_operation']

        if self._temperature_sensor:
            async_track_state_change_event(
                self.hass, self._temperature_sensor, self._async_temp_sensor_changed
            )
            temp_sensor_state = self.hass.states.get(self._temperature_sensor)
            if temp_sensor_state and temp_sensor_state.state != STATE_UNKNOWN:
                self._async_update_temp(temp_sensor_state)

        if self._humidity_sensor:
            async_track_state_change_event(
                self.hass, self._humidity_sensor, self._async_humidity_sensor_changed
            )
            humidity_sensor_state = self.hass.states.get(self._humidity_sensor)
            if humidity_sensor_state and humidity_sensor_state.state != STATE_UNKNOWN:
                self._async_update_humidity(humidity_sensor_state)

        if self._power_sensor:
            async_track_state_change_event(
                self.hass, self._power_sensor, self._async_power_sensor_changed
            )

        await self._intent_subscribe()

    async def async_will_remove_from_hass(self) -> None:
        self._intent_unsubscribe()
        await super().async_will_remove_from_hass()

    @property
    def name(self): return self._name

    @property
    def state(self): return self._hvac_mode

    @property
    def temperature_unit(self): return self._unit

    @property
    def min_temp(self): return self._min_temperature

    @property
    def max_temp(self): return self._max_temperature

    @property
    def target_temperature(self): return self._target_temperature

    @property
    def target_temperature_step(self): return self._precision

    @property
    def hvac_modes(self): return self._operation_modes

    @property
    def hvac_mode(self): return self._hvac_mode

    @property
    def last_on_operation(self): return self._last_on_operation

    @property
    def fan_modes(self): return self._fan_modes

    @property
    def fan_mode(self): return self._current_fan_mode

    @property
    def swing_modes(self): return self._swing_modes

    @property
    def swing_mode(self): return self._current_swing_mode

    @property
    def current_temperature(self): return self._current_temperature

    @property
    def current_humidity(self): return self._current_humidity

    @property
    def supported_features(self): return self._support_flags

    @property
    def extra_state_attributes(self):
        return {
            'last_on_operation': self._last_on_operation,
            'device_code': self._device_code,
            'manufacturer': self._manufacturer,
            'supported_models': self._supported_models,
            'supported_controller': self._supported_controller,
            'commands_encoding': self._commands_encoding,
        }

    async def async_set_temperature(self, **kwargs):
        """Set new target temperatures."""
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        temperature = kwargs.get(ATTR_TEMPERATURE)

        if temperature is None:
            return

        if not (self._min_temperature <= temperature <= self._max_temperature):
            _LOGGER.warning("The temperature value %s is out of min/max range", temperature)
            return

        self._target_temperature = round(temperature) if self._precision == PRECISION_WHOLE else round(temperature, 1)

        if hvac_mode:
            await self.async_set_hvac_mode(hvac_mode)
            return

        if self._hvac_mode != HVACMode.OFF:
            await self.send_command()

        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        """Set operation mode."""
        self._hvac_mode = hvac_mode
        if hvac_mode != HVACMode.OFF:
            self._last_on_operation = hvac_mode

        await self.send_command()
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode):
        """Set fan mode."""
        self._current_fan_mode = fan_mode
        if self._hvac_mode != HVACMode.OFF:
            await self.send_command()
        self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode):
        """Set swing mode."""
        self._current_swing_mode = swing_mode
        if self._hvac_mode != HVACMode.OFF:
            await self.send_command()
        self.async_write_ha_state()

    async def async_turn_off(self):
        """Turn off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self):
        """Turn on."""
        mode_to_set = self._last_on_operation if self._last_on_operation else (
            self._operation_modes[1] if len(self._operation_modes) > 1 else HVACMode.AUTO
        )
        await self.async_set_hvac_mode(mode_to_set)

    async def send_command(self):
        async with self._temp_lock:
            try:
                self._on_by_remote = False
                operation_mode = self._hvac_mode
                fan_mode = self._current_fan_mode
                swing_mode = self._current_swing_mode
                target_temperature = f"{self._target_temperature:g}"

                if operation_mode == HVACMode.OFF:
                    await self._controller.send(self._commands['off'])
                    await self._intent_publish(self._build_intent_payload())
                    return

                if 'on' in self._commands:
                    await self._controller.send(self._commands['on'])
                    await asyncio.sleep(self._delay)

                if self._support_swing:
                    cmd = self._commands[operation_mode][fan_mode][swing_mode][target_temperature]
                else:
                    cmd = self._commands[operation_mode][fan_mode][target_temperature]

                await self._controller.send(cmd)
                await self._intent_publish(self._build_intent_payload())

            except Exception:
                _LOGGER.exception("Failed to send command to the controller")

    def _build_intent_payload(self) -> dict[str, Any]:
        return {
            "hvac_mode": str(self._hvac_mode) if self._hvac_mode is not None else None,
            "fan_mode": self._current_fan_mode,
            "swing_mode": self._current_swing_mode,
            "temperature": self._target_temperature,
        }

    def _apply_intent(self, payload: dict[str, Any]) -> None:
        """Apply state from another HA's intent. Validate per CLAUDE.md rules."""
        hvac_mode = payload.get("hvac_mode")
        if hvac_mode in self._operation_modes:
            self._hvac_mode = hvac_mode

        fan_mode = payload.get("fan_mode")
        if fan_mode in self._fan_modes:
            self._current_fan_mode = fan_mode

        swing_mode = payload.get("swing_mode")
        if self._swing_modes and swing_mode in self._swing_modes:
            self._current_swing_mode = swing_mode

        temperature = payload.get("temperature")
        if (
            isinstance(temperature, (int, float))
            and self._min_temperature <= temperature <= self._max_temperature
        ):
            self._target_temperature = temperature

        if hvac_mode in self._operation_modes and hvac_mode != HVACMode.OFF:
            self._last_on_operation = hvac_mode

    async def _async_temp_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle temperature sensor changes."""
        new_state = event.data.get("new_state")
        if new_state:
            self._async_update_temp(new_state)
            self.async_write_ha_state()

    async def _async_humidity_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle humidity sensor changes."""
        new_state = event.data.get("new_state")
        if new_state:
            self._async_update_humidity(new_state)
            self.async_write_ha_state()

    async def _async_power_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle power state sensor changes."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if new_state is None or (old_state and new_state.state == old_state.state):
            return

        if new_state.state == STATE_ON and self._hvac_mode == HVACMode.OFF:
            self._on_by_remote = True
            if self._power_sensor_restore_state and self._last_on_operation:
                self._hvac_mode = self._last_on_operation
            else:
                self._hvac_mode = self._operation_modes[1] if len(self._operation_modes) > 1 else HVACMode.AUTO
            self.async_write_ha_state()

        elif new_state.state == STATE_OFF and self._hvac_mode != HVACMode.OFF:
            self._on_by_remote = False
            self._hvac_mode = HVACMode.OFF
            self.async_write_ha_state()

    @callback
    def _async_update_temp(self, state):
        """Update thermostat with latest state from temperature sensor."""
        try:
            if state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self._current_temperature = float(state.state)
        except ValueError as ex:
            _LOGGER.error("Unable to update from temperature sensor: %s", ex)

    @callback
    def _async_update_humidity(self, state):
        """Update thermostat with latest state from humidity sensor."""
        try:
            if state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self._current_humidity = float(state.state)
        except ValueError as ex:
            _LOGGER.error("Unable to update from humidity sensor: %s", ex)
