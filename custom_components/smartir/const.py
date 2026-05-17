"""Shared constants for the SmartIR integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "smartir"

PLATFORMS = [
    Platform.CLIMATE,
    Platform.MEDIA_PLAYER,
    Platform.FAN,
    Platform.LIGHT,
]

CONF_PLATFORM = "platform"
CONF_UNIQUE_ID = "unique_id"
CONF_DEVICE_CODE = "device_code"
CONF_CONTROLLER_DATA = "controller_data"
CONF_DELAY = "delay"
CONF_TEMPERATURE_SENSOR = "temperature_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_POWER_SENSOR = "power_sensor"
CONF_POWER_SENSOR_RESTORE_STATE = "power_sensor_restore_state"
CONF_SOURCE_NAMES = "source_names"
CONF_DEVICE_CLASS = "device_class"

DEFAULT_DELAY = 0.5
DEFAULT_MEDIA_PLAYER_DEVICE_CLASS = "tv"

CONF_ENABLE_INTENT_SYNC = "enable_intent_sync"
CONF_INTENT_TOPIC_BASE = "intent_topic_base"
CONF_INTENT_SOURCE_ID = "intent_source_id"
DEFAULT_INTENT_TOPIC_BASE = "smartir/intent"
