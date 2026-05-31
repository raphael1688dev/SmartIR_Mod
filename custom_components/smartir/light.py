import asyncio
from functools import partial
import json
import logging
import os
from typing import Any

import aiofiles
import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
    PLATFORM_SCHEMA as LIGHT_PLATFORM_SCHEMA,
)
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.const import (
    CONF_NAME,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback, AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import COMPONENT_ABS_DIR, Helper
from .const import (
    CODES_SOURCE_URL,
    CONF_CONTROLLER_DATA,
    CONF_DELAY,
    CONF_DEVICE_CODE,
    CONF_ENABLE_INTENT_SYNC,
    CONF_INTENT_ID,
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
from .intent_sync import SmartIRIntentMixin

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "SmartIR Light"

CMD_BRIGHTNESS_INCREASE = "brighten"
CMD_BRIGHTNESS_DECREASE = "dim"
CMD_COLORMODE_COLDER = "colder"
CMD_COLORMODE_WARMER = "warmer"
CMD_POWER_ON = "on"
CMD_POWER_OFF = "off"
CMD_NIGHTLIGHT = "night"

PLATFORM_SCHEMA = LIGHT_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_DEVICE_CODE): cv.positive_int,
        vol.Required(CONF_CONTROLLER_DATA): cv.string,
        vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
        vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """YAML platform setup — soft-imports each entry into a ConfigEntry."""
    _LOGGER.warning(
        "Configuring SmartIR light via YAML is deprecated. "
        "Entry '%s' will be imported automatically; please remove it from configuration.yaml "
        "after confirming the entity works.",
        config.get(CONF_NAME, DEFAULT_NAME),
    )
    import_data = dict(config)
    import_data[CONF_PLATFORM] = Platform.LIGHT.value
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
    """Set up a SmartIR light from a config entry."""
    merged: dict[str, Any] = {**entry.data, **entry.options}
    device_code = int(merged[CONF_DEVICE_CODE])
    device_files_absdir = os.path.join(COMPONENT_ABS_DIR, 'codes', 'light')

    await hass.async_add_executor_job(
        partial(os.makedirs, device_files_absdir, exist_ok=True)
    )

    device_json_path = os.path.join(device_files_absdir, f"{device_code}.json")
    file_exists = await hass.async_add_executor_job(os.path.exists, device_json_path)

    if not file_exists:
        _LOGGER.warning(
            "Couldn't find the device JSON file. The component "
            "will try to download it from the Github repo."
        )
        try:
            codes_source = CODES_SOURCE_URL.format(
                platform=Platform.LIGHT.value, device_code=device_code
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

    async_add_entities([SmartIRLight(hass, entry, merged, device_data)])


def closest_match(value, options):
    """Find the closest match in a sorted list."""
    prev_val = None
    for index, entry in enumerate(options):
        if entry > (value or 0):
            if prev_val is None:
                return index
            diff_lo = value - prev_val
            diff_hi = entry - value
            if diff_lo < diff_hi:
                return index - 1
            return index
        prev_val = entry

    return len(options) - 1


def _stepwise_command(
    current_value,
    target_value,
    levels: list,
    cmd_increase: str,
    cmd_decrease: str,
):
    """Compute the IR step command + repeat count for an up/down-style control.

    Returns (cmd, steps, new_value) or (None, 0, current_value) if no change.

    The "edge boost" — when the target is the first or last level, send
    `len(levels)` steps instead of the actual delta — exists so the device
    saturates at min/max even when our recorded current_value drifted out of
    sync with the real device. Replicates upstream's historical fan-out.
    """
    if not levels:
        return None, 0, current_value

    old_idx = closest_match(current_value, levels)
    new_idx = closest_match(target_value, levels)
    delta = new_idx - old_idx
    if delta == 0:
        return None, 0, current_value

    if delta < 0:
        cmd, steps = cmd_decrease, abs(delta)
    else:
        cmd, steps = cmd_increase, delta

    # Edge boost: ensure full saturation at min/max.
    if new_idx in (0, len(levels) - 1):
        steps = len(levels)

    return cmd, steps, levels[new_idx]


class SmartIRLight(SmartIRIntentMixin, LightEntity, RestoreEntity):
    def __init__(self, hass, entry: ConfigEntry, config: dict[str, Any], device_data):
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = entry.unique_id or config.get(CONF_UNIQUE_ID)
        self._name = config.get(CONF_NAME, DEFAULT_NAME)
        self._device_code = int(config[CONF_DEVICE_CODE])
        self._controller_data = config[CONF_CONTROLLER_DATA]
        self._delay = float(config.get(CONF_DELAY, DEFAULT_DELAY))
        self._power_sensor = config.get(CONF_POWER_SENSOR)

        self._manufacturer = device_data["manufacturer"]
        self._supported_models = device_data["supportedModels"]
        self._supported_controller = device_data["supportedController"]
        self._commands_encoding = device_data["commandsEncoding"]
        self._brightnesses = device_data.get("brightness", [])
        self._colortemps = device_data.get("colorTemperature", [])
        self._commands = device_data.get("commands", {})

        self._power = STATE_ON
        self._brightness = None
        self._colortemp = None

        self._temp_lock = asyncio.Lock()
        self._on_by_remote = False
        self._support_color_mode = ColorMode.UNKNOWN

        if CMD_COLORMODE_COLDER in self._commands and CMD_COLORMODE_WARMER in self._commands:
            self._colortemp = self.max_color_temp_kelvin
            self._support_color_mode = ColorMode.COLOR_TEMP

        if CMD_NIGHTLIGHT in self._commands or (
            CMD_BRIGHTNESS_INCREASE in self._commands and CMD_BRIGHTNESS_DECREASE in self._commands
        ):
            self._brightness = 100
            self._support_brightness = True
            if self._support_color_mode == ColorMode.UNKNOWN:
                self._support_color_mode = ColorMode.BRIGHTNESS
        else:
            self._support_brightness = False

        if (
            CMD_POWER_OFF in self._commands
            and CMD_POWER_ON in self._commands
            and self._support_color_mode == ColorMode.UNKNOWN
        ):
            self._support_color_mode = ColorMode.ONOFF

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

        last_state = await self.async_get_last_state()
        if last_state is not None:
            if last_state.state in (STATE_ON, STATE_OFF):
                self._power = last_state.state

            restored_brightness = last_state.attributes.get(ATTR_BRIGHTNESS)
            if isinstance(restored_brightness, int) and 0 <= restored_brightness <= 255:
                self._brightness = restored_brightness

            restored_colortemp = last_state.attributes.get(ATTR_COLOR_TEMP_KELVIN)
            if (
                isinstance(restored_colortemp, int)
                and self._colortemps
                and self._colortemps[0] <= restored_colortemp <= self._colortemps[-1]
            ):
                self._colortemp = restored_colortemp

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
    def supported_color_modes(self): return [self._support_color_mode]

    @property
    def color_mode(self): return self._support_color_mode

    @property
    def color_temp_kelvin(self): return self._colortemp

    @property
    def min_color_temp_kelvin(self):
        if self._colortemps:
            return self._colortemps[0]
        return None

    @property
    def max_color_temp_kelvin(self):
        if self._colortemps:
            return self._colortemps[-1]
        return None

    @property
    def is_on(self):
        return self._power == STATE_ON or self._on_by_remote

    @property
    def brightness(self): return self._brightness

    @property
    def extra_state_attributes(self):
        return {
            "device_code": self._device_code,
            "manufacturer": self._manufacturer,
            "supported_models": self._supported_models,
            "supported_controller": self._supported_controller,
            "commands_encoding": self._commands_encoding,
            "on_by_remote": self._on_by_remote,
        }

    async def async_turn_on(self, **params):
        did_something = False
        if self._power != STATE_ON and not self._on_by_remote:
            self._power = STATE_ON
            did_something = True
            await self.send_command(CMD_POWER_ON)

        if ATTR_COLOR_TEMP_KELVIN in params and ColorMode.COLOR_TEMP == self._support_color_mode:
            cmd, steps, new_colortemp = _stepwise_command(
                self._colortemp,
                params.get(ATTR_COLOR_TEMP_KELVIN),
                self._colortemps,
                cmd_increase=CMD_COLORMODE_COLDER,
                cmd_decrease=CMD_COLORMODE_WARMER,
            )
            if cmd is not None:
                _LOGGER.debug(
                    "Changing color temp from %sK to %sK via %s × %s",
                    self._colortemp, new_colortemp, cmd, steps,
                )
                self._colortemp = new_colortemp
                did_something = True
                await self.send_command(cmd, steps)

        if ATTR_BRIGHTNESS in params and self._support_brightness:
            if params.get(ATTR_BRIGHTNESS) == 1 and CMD_NIGHTLIGHT in self._commands:
                self._brightness = 1
                self._power = STATE_ON
                did_something = True
                await self.send_command(CMD_NIGHTLIGHT)

            elif self._brightnesses:
                cmd, steps, new_brightness = _stepwise_command(
                    self._brightness,
                    params.get(ATTR_BRIGHTNESS),
                    self._brightnesses,
                    cmd_increase=CMD_BRIGHTNESS_INCREASE,
                    cmd_decrease=CMD_BRIGHTNESS_DECREASE,
                )
                if cmd is not None:
                    _LOGGER.debug(
                        "Changing brightness from %s to %s via %s × %s",
                        self._brightness, new_brightness, cmd, steps,
                    )
                    self._brightness = new_brightness
                    did_something = True
                    await self.send_command(cmd, steps)

        if not did_something and not self._on_by_remote:
            self._power = STATE_ON
            await self.send_command(CMD_POWER_ON)

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._power = STATE_OFF
        await self.send_command(CMD_POWER_OFF)
        self.async_write_ha_state()

    async def async_toggle(self, **kwargs):
        await (self.async_turn_on() if not self.is_on else self.async_turn_off())

    async def send_command(self, cmd, count=1):
        if cmd not in self._commands:
            _LOGGER.error("Unknown command '%s'", cmd)
            return

        _LOGGER.debug("Sending %s remote command %s times.", cmd, count)
        remote_cmd = self._commands.get(cmd)

        async with self._temp_lock:
            self._on_by_remote = False
            try:
                for _ in range(count):
                    await self._controller.send(remote_cmd)
                await self._intent_publish(self._build_intent_payload())
            except Exception:
                _LOGGER.exception("Failed to send command to the Light controller")

    def _build_intent_payload(self) -> dict[str, Any]:
        return {
            "power": self._power,
            "brightness": self._brightness,
            "colortemp": self._colortemp,
        }

    def _apply_intent(self, payload: dict[str, Any]) -> None:
        power = payload.get("power")
        if power in (STATE_ON, STATE_OFF):
            self._power = power

        brightness = payload.get("brightness")
        if isinstance(brightness, int) and 0 <= brightness <= 255:
            self._brightness = brightness

        colortemp = payload.get("colortemp")
        if (
            isinstance(colortemp, int)
            and self._colortemps
            and self._colortemps[0] <= colortemp <= self._colortemps[-1]
        ):
            self._colortemp = colortemp

    async def _async_power_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle power sensor changes."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if new_state is None or (old_state and new_state.state == old_state.state):
            return

        if new_state.state == STATE_ON:
            self._on_by_remote = True
            self.async_write_ha_state()

        elif new_state.state == STATE_OFF:
            self._on_by_remote = False
            self._power = STATE_OFF
            self.async_write_ha_state()
