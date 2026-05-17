"""SmartIR integration for Home Assistant."""
from __future__ import annotations

import binascii
import logging
import os.path
import struct
import uuid

import aiofiles
import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_INTENT_SOURCE_ID, CONF_PLATFORM, DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

COMPONENT_ABS_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """No-op YAML setup (only the empty `smartir:` block is accepted)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a SmartIR device from a config entry."""
    platform = entry.data.get(CONF_PLATFORM)
    if platform not in {p.value for p in PLATFORMS}:
        _LOGGER.error("Unknown platform in config entry %s: %r", entry.entry_id, platform)
        return False

    # Backfill intent_source_id for entries created before intent sync existed.
    if CONF_INTENT_SOURCE_ID not in entry.data:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_INTENT_SOURCE_ID: uuid.uuid4().hex},
        )
        _LOGGER.info(
            "Backfilled intent_source_id for entry '%s' (%s).",
            entry.title, entry.entry_id,
        )

    await hass.config_entries.async_forward_entry_setups(entry, [platform])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a SmartIR config entry."""
    platform = entry.data.get(CONF_PLATFORM)
    if platform not in {p.value for p in PLATFORMS}:
        return True
    return await hass.config_entries.async_unload_platforms(entry, [platform])


class Helper:
    @staticmethod
    async def downloader(session: aiohttp.ClientSession, source: str, dest: str) -> None:
        """Download a file using the shared aiohttp session."""
        async with session.get(source, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 200:
                async with aiofiles.open(dest, mode='wb') as f:
                    await f.write(await response.read())
            else:
                raise aiohttp.ClientResponseError(
                    request_info=response.request_info,
                    history=response.history,
                    status=response.status,
                    message=f"File not found or inaccessible: {source}",
                )

    @staticmethod
    def pronto2lirc(pronto: str) -> list[int]:
        codes = [
            int(
                binascii.hexlify(
                    pronto[i:i + 2].encode('utf-8')
                    if isinstance(pronto, str) else pronto[i:i + 2]
                ),
                16,
            )
            for i in range(0, len(pronto), 2)
        ]

        if codes[0]:
            raise ValueError("Pronto code should start with 0000")
        if len(codes) != 4 + 2 * (codes[2] + codes[3]):
            raise ValueError("Number of pulse widths does not match the preamble")

        frequency = 1 / (codes[1] * 0.241246)
        return [int(round(code / frequency)) for code in codes[4:]]

    @staticmethod
    def lirc2broadlink(pulses: list[int]) -> bytearray:
        array = bytearray()

        for pulse in pulses:
            pulse = int(pulse * 269 / 8192)

            if pulse < 256:
                array += bytearray(struct.pack('>B', pulse))
            else:
                array += bytearray([0x00])
                array += bytearray(struct.pack('>H', pulse))

        packet = bytearray([0x26, 0x00])
        packet += bytearray(struct.pack('<H', len(array)))
        packet += array
        packet += bytearray([0x0d, 0x05])

        remainder = (len(packet) + 4) % 16
        if remainder:
            packet += bytearray(16 - remainder)
        return packet
