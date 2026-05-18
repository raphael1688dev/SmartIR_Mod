"""Config flow for SmartIR."""
from __future__ import annotations

import logging
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CONTROLLER_DATA,
    CONF_DELAY,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_CODE,
    CONF_ENABLE_INTENT_SYNC,
    CONF_HUMIDITY_SENSOR,
    CONF_INTENT_ID,
    CONF_INTENT_SOURCE_ID,
    CONF_INTENT_TOPIC_BASE,
    CONF_PLATFORM,
    CONF_POWER_SENSOR,
    CONF_POWER_SENSOR_RESTORE_STATE,
    CONF_SOURCE_NAMES,
    CONF_TEMPERATURE_SENSOR,
    CONF_UNIQUE_ID,
    DEFAULT_DELAY,
    DEFAULT_INTENT_TOPIC_BASE,
    DEFAULT_MEDIA_PLAYER_DEVICE_CLASS,
    DOMAIN,
    PLATFORMS,
)
from .intent_sync import compute_intent_id

_LOGGER = logging.getLogger(__name__)

_PLATFORM_OPTIONS = [p.value for p in PLATFORMS]

_BASE_SCHEMA = {
    vol.Required(CONF_NAME): selector.TextSelector(),
    vol.Required(CONF_DEVICE_CODE): selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1, step=1, mode=selector.NumberSelectorMode.BOX
        )
    ),
    vol.Required(CONF_CONTROLLER_DATA): selector.TextSelector(),
    vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, step=0.1, mode=selector.NumberSelectorMode.BOX
        )
    ),
}

_OPTIONAL_POWER_SENSOR = {
    vol.Optional(CONF_POWER_SENSOR): selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["binary_sensor", "sensor", "switch"])
    ),
}

CLIMATE_SCHEMA = vol.Schema(
    {
        **_BASE_SCHEMA,
        vol.Optional(CONF_TEMPERATURE_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Optional(CONF_HUMIDITY_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        **_OPTIONAL_POWER_SENSOR,
        vol.Optional(
            CONF_POWER_SENSOR_RESTORE_STATE, default=False
        ): selector.BooleanSelector(),
    }
)

MEDIA_PLAYER_SCHEMA = vol.Schema(
    {
        **_BASE_SCHEMA,
        vol.Optional(
            CONF_DEVICE_CLASS, default=DEFAULT_MEDIA_PLAYER_DEVICE_CLASS
        ): selector.TextSelector(),
        **_OPTIONAL_POWER_SENSOR,
    }
)

FAN_SCHEMA = vol.Schema(
    {
        **_BASE_SCHEMA,
        **_OPTIONAL_POWER_SENSOR,
    }
)

LIGHT_SCHEMA = vol.Schema(
    {
        **_BASE_SCHEMA,
        **_OPTIONAL_POWER_SENSOR,
    }
)

_PLATFORM_SCHEMAS: dict[str, vol.Schema] = {
    Platform.CLIMATE.value: CLIMATE_SCHEMA,
    Platform.MEDIA_PLAYER.value: MEDIA_PLAYER_SCHEMA,
    Platform.FAN.value: FAN_SCHEMA,
    Platform.LIGHT.value: LIGHT_SCHEMA,
}


def _normalize_user_input(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce numeric strings from selectors into the right types."""
    if CONF_DEVICE_CODE in data:
        data[CONF_DEVICE_CODE] = int(data[CONF_DEVICE_CODE])
    if CONF_DELAY in data:
        data[CONF_DELAY] = float(data[CONF_DELAY])
    return data


def _make_unique_id(data: dict[str, Any]) -> str:
    """Return a stable unique id: user-supplied or generated."""
    if explicit := data.get(CONF_UNIQUE_ID):
        return str(explicit)
    return f"{data[CONF_PLATFORM]}_{data[CONF_DEVICE_CODE]}_{data[CONF_NAME]}"


class SmartIRConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the SmartIR config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._platform: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the entity platform first."""
        if user_input is not None:
            self._platform = user_input[CONF_PLATFORM]
            return await getattr(self, f"async_step_{self._platform}")()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PLATFORM): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_PLATFORM_OPTIONS,
                            translation_key=CONF_PLATFORM,
                        )
                    )
                }
            ),
        )

    async def async_step_climate(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_step_platform(Platform.CLIMATE.value, user_input)

    async def async_step_media_player(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_step_platform(Platform.MEDIA_PLAYER.value, user_input)

    async def async_step_fan(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_step_platform(Platform.FAN.value, user_input)

    async def async_step_light(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_step_platform(Platform.LIGHT.value, user_input)

    async def _async_step_platform(
        self, platform: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id=platform, data_schema=_PLATFORM_SCHEMAS[platform]
            )

        data = _normalize_user_input(dict(user_input))
        data[CONF_PLATFORM] = platform
        data[CONF_INTENT_SOURCE_ID] = uuid.uuid4().hex
        data[CONF_INTENT_ID] = compute_intent_id(
            platform, data[CONF_DEVICE_CODE], data[CONF_CONTROLLER_DATA]
        )

        await self.async_set_unique_id(_make_unique_id(data))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=data[CONF_NAME], data=data)

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Import a YAML platform entry into a ConfigEntry (soft migration)."""
        data = _normalize_user_input(dict(import_data))

        if CONF_PLATFORM not in data:
            _LOGGER.error("YAML import missing 'platform' key: %s", data)
            return self.async_abort(reason="invalid_import")

        data.setdefault(CONF_INTENT_SOURCE_ID, uuid.uuid4().hex)
        data[CONF_INTENT_ID] = compute_intent_id(
            data[CONF_PLATFORM], data[CONF_DEVICE_CODE], data[CONF_CONTROLLER_DATA]
        )

        await self.async_set_unique_id(_make_unique_id(data))
        self._abort_if_unique_id_configured(updates=data)

        _LOGGER.info(
            "Imported SmartIR %s '%s' from YAML. You can now remove this entry "
            "from configuration.yaml.",
            data[CONF_PLATFORM],
            data.get(CONF_NAME),
        )
        return self.async_create_entry(title=data[CONF_NAME], data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SmartIROptionsFlow:
        return SmartIROptionsFlow()


class SmartIROptionsFlow(OptionsFlowWithReload):
    """Edit tweakable fields after the entry is created."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_normalize_user_input(dict(user_input)))

        platform = self.config_entry.data[CONF_PLATFORM]
        schema = _options_schema_for(platform)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {**self.config_entry.data, **self.config_entry.options}
            ),
        )


_INTENT_FIELDS = {
    vol.Optional(CONF_ENABLE_INTENT_SYNC, default=False): selector.BooleanSelector(),
    vol.Optional(
        CONF_INTENT_TOPIC_BASE, default=DEFAULT_INTENT_TOPIC_BASE
    ): selector.TextSelector(),
}


def _options_schema_for(platform: str) -> vol.Schema:
    """Subset of fields users can change post-setup (not name/device_code/controller)."""
    common = {
        vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, step=0.1, mode=selector.NumberSelectorMode.BOX
            )
        ),
        **_OPTIONAL_POWER_SENSOR,
    }
    if platform == Platform.CLIMATE.value:
        return vol.Schema(
            {
                vol.Optional(CONF_TEMPERATURE_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_HUMIDITY_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                **common,
                vol.Optional(
                    CONF_POWER_SENSOR_RESTORE_STATE, default=False
                ): selector.BooleanSelector(),
                **_INTENT_FIELDS,
            }
        )
    if platform == Platform.MEDIA_PLAYER.value:
        return vol.Schema(
            {
                vol.Optional(
                    CONF_DEVICE_CLASS, default=DEFAULT_MEDIA_PLAYER_DEVICE_CLASS
                ): selector.TextSelector(),
                **common,
                **_INTENT_FIELDS,
            }
        )
    return vol.Schema({**common, **_INTENT_FIELDS})
