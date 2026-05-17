import asyncio
from functools import partial
import json
import logging
import os
from typing import Any

import aiofiles
import voluptuous as vol

from homeassistant.components.fan import (
    FanEntity, FanEntityFeature,
    PLATFORM_SCHEMA as FAN_PLATFORM_SCHEMA, DIRECTION_REVERSE, DIRECTION_FORWARD)
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.const import (
    CONF_NAME, STATE_OFF, STATE_ON, Platform)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback, AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from . import COMPONENT_ABS_DIR, Helper
from .const import (
    CONF_CONTROLLER_DATA,
    CONF_DELAY,
    CONF_DEVICE_CODE,
    CONF_ENABLE_INTENT_SYNC,
    CONF_INTENT_SOURCE_ID,
    CONF_INTENT_TOPIC_BASE,
    CONF_PLATFORM,
    CONF_POWER_SENSOR,
    CONF_UNIQUE_ID,
    DEFAULT_DELAY,
    DEFAULT_INTENT_TOPIC_BASE,
    DOMAIN,
)
from .controller import get_controller
from .intent import SmartIRIntentMixin

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "SmartIR Fan"

SPEED_OFF = "off"

PLATFORM_SCHEMA = FAN_PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_UNIQUE_ID): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_DEVICE_CODE): cv.positive_int,
    vol.Required(CONF_CONTROLLER_DATA): cv.string,
    vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
    vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
})


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """YAML platform setup — soft-imports each entry into a ConfigEntry."""
    _LOGGER.warning(
        "Configuring SmartIR fan via YAML is deprecated. "
        "Entry '%s' will be imported automatically; please remove it from configuration.yaml "
        "after confirming the entity works.",
        config.get(CONF_NAME, DEFAULT_NAME),
    )
    import_data = dict(config)
    import_data[CONF_PLATFORM] = Platform.FAN.value
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
    """Set up a SmartIR fan from a config entry."""
    merged: dict[str, Any] = {**entry.data, **entry.options}
    device_code = int(merged[CONF_DEVICE_CODE])
    device_files_absdir = os.path.join(COMPONENT_ABS_DIR, 'codes', 'fan')

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
                f"codes/fan/{device_code}.json"
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

    async_add_entities([SmartIRFan(hass, entry, merged, device_data)])


class SmartIRFan(SmartIRIntentMixin, FanEntity, RestoreEntity):
    def __init__(self, hass, entry: ConfigEntry, config: dict[str, Any], device_data):
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = entry.unique_id or config.get(CONF_UNIQUE_ID)
        self._name = config.get(CONF_NAME, DEFAULT_NAME)
        self._device_code = int(config[CONF_DEVICE_CODE])
        self._controller_data = config[CONF_CONTROLLER_DATA]
        self._delay = float(config.get(CONF_DELAY, DEFAULT_DELAY))
        self._power_sensor = config.get(CONF_POWER_SENSOR)

        self._manufacturer = device_data['manufacturer']
        self._supported_models = device_data['supportedModels']
        self._supported_controller = device_data['supportedController']
        self._commands_encoding = device_data['commandsEncoding']
        self._speed_list = device_data['speed']
        self._commands = device_data['commands']

        self._speed = SPEED_OFF
        self._direction = None
        self._last_on_speed = None
        self._oscillating = None

        self._support_flags = (
            FanEntityFeature.SET_SPEED
            | FanEntityFeature.TURN_OFF
            | FanEntityFeature.TURN_ON
        )

        if DIRECTION_REVERSE in self._commands and DIRECTION_FORWARD in self._commands:
            self._direction = DIRECTION_REVERSE
            self._support_flags |= FanEntityFeature.DIRECTION

        if 'oscillate' in self._commands:
            self._oscillating = False
            self._support_flags |= FanEntityFeature.OSCILLATE

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
            unique_id=self._attr_unique_id,
            source_id=entry.data.get(CONF_INTENT_SOURCE_ID),
        )

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is not None:
            restored_speed = last_state.attributes.get('speed')
            if restored_speed == SPEED_OFF or restored_speed in self._speed_list:
                self._speed = restored_speed

            if self._support_flags & FanEntityFeature.DIRECTION:
                restored_direction = last_state.attributes.get('direction')
                if restored_direction in (DIRECTION_FORWARD, DIRECTION_REVERSE):
                    self._direction = restored_direction

            restored_last_on_speed = last_state.attributes.get('last_on_speed')
            if restored_last_on_speed in self._speed_list:
                self._last_on_speed = restored_last_on_speed

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
    def state(self):
        if self._on_by_remote or self._speed != SPEED_OFF:
            return STATE_ON
        return SPEED_OFF

    @property
    def percentage(self):
        if self._speed == SPEED_OFF:
            return 0
        return ordered_list_item_to_percentage(self._speed_list, self._speed)

    @property
    def speed_count(self):
        return len(self._speed_list)

    @property
    def oscillating(self): return self._oscillating

    @property
    def current_direction(self): return self._direction

    @property
    def last_on_speed(self): return self._last_on_speed

    @property
    def supported_features(self): return self._support_flags

    @property
    def extra_state_attributes(self):
        return {
            'last_on_speed': self._last_on_speed,
            'device_code': self._device_code,
            'manufacturer': self._manufacturer,
            'supported_models': self._supported_models,
            'supported_controller': self._supported_controller,
            'commands_encoding': self._commands_encoding,
        }

    async def async_set_percentage(self, percentage: int):
        """Set the desired speed for the fan."""
        if percentage == 0:
            self._speed = SPEED_OFF
        else:
            self._speed = percentage_to_ordered_list_item(self._speed_list, percentage)

        if self._speed != SPEED_OFF:
            self._last_on_speed = self._speed

        await self.send_command()
        self.async_write_ha_state()

    async def async_oscillate(self, oscillating: bool) -> None:
        """Set oscillation of the fan."""
        self._oscillating = oscillating
        await self.send_command()
        self.async_write_ha_state()

    async def async_set_direction(self, direction: str):
        """Set the direction of the fan"""
        self._direction = direction

        if self._speed != SPEED_OFF:
            await self.send_command()

        self.async_write_ha_state()

    async def async_turn_on(self, percentage: int = None, preset_mode: str = None, **kwargs):
        """Turn on the fan."""
        if percentage is None:
            percentage = ordered_list_item_to_percentage(
                self._speed_list, self._last_on_speed or self._speed_list[0]
            )
        await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs):
        """Turn off the fan."""
        await self.async_set_percentage(0)

    async def send_command(self):
        async with self._temp_lock:
            self._on_by_remote = False
            speed = self._speed
            direction = self._direction or 'default'
            oscillating = self._oscillating

            if speed == SPEED_OFF:
                command = self._commands.get('off')
            elif oscillating and 'oscillate' in self._commands:
                command = self._commands.get('oscillate')
            else:
                command = self._commands.get(direction, {}).get(speed)

            if not command:
                _LOGGER.error("Command not found for Fan state. Direction: %s, Speed: %s", direction, speed)
                return

            try:
                await self._controller.send(command)
                await self._intent_publish(self._build_intent_payload())
            except Exception:
                _LOGGER.exception("Failed to send command to the Fan controller")

    def _build_intent_payload(self) -> dict[str, Any]:
        return {
            "speed": self._speed,
            "direction": self._direction,
            "oscillating": self._oscillating,
            "last_on_speed": self._last_on_speed,
        }

    def _apply_intent(self, payload: dict[str, Any]) -> None:
        speed = payload.get("speed")
        if speed == SPEED_OFF or speed in self._speed_list:
            self._speed = speed

        if self._support_flags & FanEntityFeature.DIRECTION:
            direction = payload.get("direction")
            if direction in (DIRECTION_FORWARD, DIRECTION_REVERSE):
                self._direction = direction

        if self._support_flags & FanEntityFeature.OSCILLATE:
            oscillating = payload.get("oscillating")
            if isinstance(oscillating, bool):
                self._oscillating = oscillating

        last_on_speed = payload.get("last_on_speed")
        if last_on_speed in self._speed_list:
            self._last_on_speed = last_on_speed

    async def _async_power_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle power sensor changes."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if new_state is None or (old_state and new_state.state == old_state.state):
            return

        if new_state.state == STATE_ON and self._speed == SPEED_OFF:
            self._on_by_remote = True
            self._speed = None
            self.async_write_ha_state()

        elif new_state.state == STATE_OFF and self._speed != SPEED_OFF:
            self._on_by_remote = False
            self._speed = SPEED_OFF
            self.async_write_ha_state()
