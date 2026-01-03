"""HASS Energy button entities."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HassEnergyRuntimeData
from .hass_energy_client import HassEnergyApiClient


class HassEnergyRunButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "hass_energy_run_button"
    _attr_unique_id = "hass_energy_trigger_run"

    def __init__(self, client: HassEnergyApiClient) -> None:
        self._client = client

    async def async_press(self) -> None:
        await self._client.run_plan()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: HassEnergyRuntimeData = entry.runtime_data
    client: HassEnergyApiClient = runtime.client
    async_add_entities([HassEnergyRunButton(client)])
