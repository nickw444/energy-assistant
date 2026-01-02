"""HASS Energy button entities."""

from __future__ import annotations

import aiohttp
import async_timeout

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_BASE_URL, CONF_TIMEOUT, DEFAULT_BASE_URL, DEFAULT_TIMEOUT


class HassEnergyRunButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "hass_energy_run_button"
    _attr_unique_id = "hass_energy_trigger_run"

    def __init__(self, session: aiohttp.ClientSession, base_url: str, timeout: int) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def async_press(self) -> None:
        url = f"{self._base_url}/plan/run"
        async with async_timeout.timeout(self._timeout):
            async with self._session.post(url) as resp:
                resp.raise_for_status()
                _ = await resp.json()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    session = async_get_clientsession(hass)
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    timeout = entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)

    async_add_entities([HassEnergyRunButton(session, base_url, timeout)])
