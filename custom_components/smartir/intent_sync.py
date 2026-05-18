"""MQTT intent topic sync for SmartIR (multi-HA state propagation).

Each entity publishes a JSON "intent" message to `<topic_base>/<unique_id>`
(retain=true) after successful IR transmission, and subscribes to the same
topic. Other HA instances with the same SmartIR entry receive the intent,
update their entity state, and DO NOT re-send IR (loop-prevention via
per-entry source_id UUID).

Strict per design `design_multi_ha_intent_sync.md`:
- source_id is a per-entry UUID generated at ConfigFlow creation.
- Publish only after the IR send succeeds (do not optimistically pre-publish).
- opt-in default OFF.
- Subclasses must override `_apply_intent()`. Payload validation in
  `_apply_intent` MUST follow CLAUDE.md "Entity 還原狀態" rules: never trust
  the value, keep the prior valid value when validation fails.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
import re
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


def compute_intent_id(platform: str, device_code: int | str, controller_data: str) -> str:
    """Derive a deterministic intent topic identifier from physical device identity.

    Two HA instances controlling the same physical device share the same
    `platform`, `device_code`, and `controller_data` regardless of how each
    HA was configured (Config Flow vs YAML). The user-chosen `name` and the
    HA-assigned `unique_id` are NOT part of the identity — they may legitimately
    differ across HAs.
    """
    slug = re.sub(r"\W+", "_", str(controller_data).lower()).strip("_")
    return f"{platform}_{device_code}_{slug}"


class SmartIRIntentMixin:
    """Mix-in for SmartIR entities to optionally sync state via MQTT."""

    hass: HomeAssistant  # provided by the underlying Entity

    def _intent_setup(
        self,
        enabled: bool,
        topic_base: str,
        intent_id: str | None,
        source_id: str | None,
    ) -> None:
        """Initialize intent sync state. Call from entity __init__.

        `intent_id` is the deterministic physical-identity slug (see
        `compute_intent_id`), NOT the HA entity unique_id. This decouples the
        cross-HA topic from per-instance unique_id quirks (e.g., Config Flow
        fallback vs YAML explicit unique_id).
        """
        self._intent_enabled: bool = bool(enabled and intent_id and source_id)
        self._intent_source: str = source_id or ""
        self._intent_topic: str | None = (
            f"{topic_base.rstrip('/')}/{intent_id}" if self._intent_enabled else None
        )
        self._intent_unsub: Callable[[], None] | None = None

    async def _intent_subscribe(self) -> None:
        """Subscribe to the intent topic. Call from async_added_to_hass."""
        if not self._intent_enabled or not self._intent_topic:
            return
        if not mqtt.mqtt_config_entry_enabled(self.hass):
            _LOGGER.warning(
                "SmartIR intent sync enabled but MQTT integration not configured; "
                "skipping subscribe to %s",
                self._intent_topic,
            )
            return
        try:
            self._intent_unsub = await mqtt.async_subscribe(
                self.hass, self._intent_topic, self._on_intent_message
            )
        except Exception:
            _LOGGER.exception(
                "Failed to subscribe to intent topic %s", self._intent_topic
            )

    def _intent_unsubscribe(self) -> None:
        """Unsubscribe. Call from async_will_remove_from_hass."""
        if self._intent_unsub is not None:
            self._intent_unsub()
            self._intent_unsub = None

    async def _intent_publish(self, payload: dict[str, Any]) -> None:
        """Publish intent (retain=true). Call after successful IR send."""
        if not self._intent_enabled or not self._intent_topic:
            return
        if not mqtt.mqtt_config_entry_enabled(self.hass):
            return
        message = {
            **payload,
            "source": self._intent_source,
            "ts": dt_util.utcnow().isoformat(),
        }
        try:
            await mqtt.async_publish(
                self.hass,
                self._intent_topic,
                json.dumps(message),
                qos=0,
                retain=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Intent publish to %s skipped: %s", self._intent_topic, err
            )
        except Exception:
            _LOGGER.exception(
                "Failed to publish intent to %s", self._intent_topic
            )

    @callback
    def _on_intent_message(self, msg) -> None:
        """Handle inbound intent from another HA instance."""
        raw = msg.payload
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            _LOGGER.warning("Invalid intent payload on %s: %r", msg.topic, raw)
            return
        if not isinstance(payload, dict):
            _LOGGER.warning(
                "Intent payload on %s is not a JSON object: %r", msg.topic, payload
            )
            return
        if payload.get("source") == self._intent_source:
            return  # Loop prevention: our own publish.
        try:
            self._apply_intent(payload)
        except Exception:
            _LOGGER.exception(
                "Failed to apply intent on %s; ignoring message", msg.topic
            )
            return
        self.async_write_ha_state()

    def _apply_intent(self, payload: dict[str, Any]) -> None:
        """Update internal state from intent payload.

        Subclasses MUST validate each field per CLAUDE.md
        'Entity 還原狀態' rules: ignore invalid values, keep prior state.
        """
        raise NotImplementedError
