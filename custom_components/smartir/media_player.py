import asyncio
from functools import partial
import json
import logging
import os
from typing import Any

import aiofiles
import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerEntity, PLATFORM_SCHEMA as MEDIA_PLAYER_PLATFORM_SCHEMA)
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature, MediaType)
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

from . import COMPONENT_ABS_DIR, Helper
from .const import (
    CODES_SOURCE_URL,
    CONF_CONTROLLER_DATA,
    CONF_DELAY,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_CODE,
    CONF_ENABLE_INTENT_SYNC,
    CONF_INTENT_ID,
    CONF_INTENT_SOURCE_ID,
    CONF_INTENT_TOPIC_BASE,
    CONF_PLATFORM,
    CONF_POWER_SENSOR,
    CONF_SOURCE_NAMES,
    CONF_UNIQUE_ID,
    DEFAULT_DELAY,
    DEFAULT_INTENT_TOPIC_BASE,
    DEFAULT_MEDIA_PLAYER_DEVICE_CLASS,
    DOMAIN,
)
from .controller import get_controller
from .intent_sync import SmartIRIntentMixin

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "SmartIR Media Player"

PLATFORM_SCHEMA = MEDIA_PLAYER_PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_UNIQUE_ID): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_DEVICE_CODE): cv.positive_int,
    vol.Required(CONF_CONTROLLER_DATA): cv.string,
    vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
    vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
    vol.Optional(CONF_SOURCE_NAMES): dict,
    vol.Optional(CONF_DEVICE_CLASS, default=DEFAULT_MEDIA_PLAYER_DEVICE_CLASS): cv.string,
})


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """YAML platform setup — soft-imports each entry into a ConfigEntry."""
    _LOGGER.warning(
        "Configuring SmartIR media_player via YAML is deprecated. "
        "Entry '%s' will be imported automatically; please remove it from configuration.yaml "
        "after confirming the entity works.",
        config.get(CONF_NAME, DEFAULT_NAME),
    )
    import_data = dict(config)
    import_data[CONF_PLATFORM] = Platform.MEDIA_PLAYER.value
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
    """Set up a SmartIR media_player from a config entry."""
    merged: dict[str, Any] = {**entry.data, **entry.options}
    device_code = int(merged[CONF_DEVICE_CODE])
    device_files_absdir = os.path.join(COMPONENT_ABS_DIR, 'codes', 'media_player')

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
            codes_source = CODES_SOURCE_URL.format(
                platform=Platform.MEDIA_PLAYER.value, device_code=device_code
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

    async_add_entities([SmartIRMediaPlayer(hass, entry, merged, device_data)])


class SmartIRMediaPlayer(SmartIRIntentMixin, MediaPlayerEntity, RestoreEntity):
    _attr_should_poll = False

    def __init__(self, hass, entry: ConfigEntry, config: dict[str, Any], device_data):
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = entry.unique_id or config.get(CONF_UNIQUE_ID)
        self._name = config.get(CONF_NAME, DEFAULT_NAME)
        self._device_code = int(config[CONF_DEVICE_CODE])
        self._controller_data = config[CONF_CONTROLLER_DATA]
        self._delay = float(config.get(CONF_DELAY, DEFAULT_DELAY))
        self._power_sensor = config.get(CONF_POWER_SENSOR)
        self._device_class = config.get(CONF_DEVICE_CLASS, DEFAULT_MEDIA_PLAYER_DEVICE_CLASS)

        self._manufacturer = device_data['manufacturer']
        self._supported_models = device_data['supportedModels']
        self._supported_controller = device_data['supportedController']
        self._commands_encoding = device_data['commandsEncoding']
        self._commands = device_data.get('commands', {})

        self._state = STATE_OFF
        self._sources_list = []
        self._source = None
        self._support_flags = 0

        if self._commands.get('off'):
            self._support_flags |= MediaPlayerEntityFeature.TURN_OFF
        if self._commands.get('on'):
            self._support_flags |= MediaPlayerEntityFeature.TURN_ON
        if self._commands.get('previousChannel'):
            self._support_flags |= MediaPlayerEntityFeature.PREVIOUS_TRACK
        if self._commands.get('nextChannel'):
            self._support_flags |= MediaPlayerEntityFeature.NEXT_TRACK
        if self._commands.get('volumeDown') or self._commands.get('volumeUp'):
            self._support_flags |= MediaPlayerEntityFeature.VOLUME_STEP
        if self._commands.get('mute'):
            self._support_flags |= MediaPlayerEntityFeature.VOLUME_MUTE

        if self._commands.get('sources'):
            self._support_flags |= (MediaPlayerEntityFeature.SELECT_SOURCE | MediaPlayerEntityFeature.PLAY_MEDIA)

            for source, new_name in config.get(CONF_SOURCE_NAMES, {}).items():
                if source in self._commands['sources']:
                    source_cmd = self._commands['sources'].pop(source)
                    if new_name is not None:
                        self._commands['sources'][new_name] = source_cmd

            self._sources_list = list(self._commands['sources'].keys())

        self._temp_lock = asyncio.Lock()

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
        if last_state is not None and last_state.state in (STATE_ON, STATE_OFF):
            self._state = last_state.state

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
    def device_class(self): return self._device_class

    @property
    def state(self): return self._state

    @property
    def media_title(self): return None

    @property
    def media_content_type(self): return MediaType.CHANNEL

    @property
    def source_list(self): return self._sources_list

    @property
    def source(self): return self._source

    @property
    def supported_features(self): return self._support_flags

    @property
    def extra_state_attributes(self):
        return {
            'device_code': self._device_code,
            'manufacturer': self._manufacturer,
            'supported_models': self._supported_models,
            'supported_controller': self._supported_controller,
            'commands_encoding': self._commands_encoding,
        }

    async def async_turn_off(self):
        """Turn the media player off."""
        await self.send_command(self._commands.get('off'))
        if self._power_sensor is None:
            self._state = STATE_OFF
            self._source = None
            self.async_write_ha_state()

    async def async_turn_on(self):
        """Turn the media player on."""
        await self.send_command(self._commands.get('on'))
        if self._power_sensor is None:
            self._state = STATE_ON
            self.async_write_ha_state()

    async def async_media_previous_track(self):
        """Send previous track command."""
        await self.send_command(self._commands.get('previousChannel'))

    async def async_media_next_track(self):
        """Send next track command."""
        await self.send_command(self._commands.get('nextChannel'))

    async def async_volume_down(self):
        """Turn volume down for media player."""
        await self.send_command(self._commands.get('volumeDown'))

    async def async_volume_up(self):
        """Turn volume up for media player."""
        await self.send_command(self._commands.get('volumeUp'))

    async def async_mute_volume(self, mute):
        """Mute the volume."""
        await self.send_command(self._commands.get('mute'))

    async def async_select_source(self, source):
        """Select channel from source."""
        self._source = source
        source_cmd = self._commands.get('sources', {}).get(source)
        if source_cmd:
            await self.send_command(source_cmd)
            self.async_write_ha_state()

    async def async_play_media(self, media_type, media_id, **kwargs):
        """Support channel change through play_media service."""
        if self._state == STATE_OFF:
            await self.async_turn_on()

        if media_type != MediaType.CHANNEL:
            _LOGGER.error("Invalid media type. Expected %s", MediaType.CHANNEL)
            return

        if not str(media_id).isdigit():
            _LOGGER.error("media_id must be a numeric channel number")
            return

        self._source = f"Channel {media_id}"
        for digit in str(media_id):
            digit_cmd = self._commands.get('sources', {}).get(f"Channel {digit}")
            if digit_cmd:
                await self.send_command(digit_cmd)

        self.async_write_ha_state()

    async def send_command(self, command):
        """Send command securely."""
        if not command:
            return

        async with self._temp_lock:
            try:
                await self._controller.send(command)
                await self._intent_publish(self._build_intent_payload())
            except Exception:
                _LOGGER.exception("Failed to send command to the Media Player controller")

    def _build_intent_payload(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "input_source": self._source,
        }

    def _apply_intent(self, payload: dict[str, Any]) -> None:
        state = payload.get("state")
        if state in (STATE_ON, STATE_OFF):
            self._state = state
            if state == STATE_OFF:
                self._source = None

        input_source = payload.get("input_source")
        if input_source is None or input_source in self._sources_list:
            self._source = input_source

    async def _async_power_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle power sensor changes."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        if new_state.state == STATE_OFF:
            self._state = STATE_OFF
            self._source = None
        elif new_state.state == STATE_ON:
            self._state = STATE_ON

        self.async_write_ha_state()
