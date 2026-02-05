"""Energy Assistant button entities."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EnergyAssistantRuntimeData
from .energy_assistant_client import EnergyAssistantApiClient


class EnergyAssistantRunButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "energy_assistant_run_button"
    _attr_unique_id = "energy_assistant_trigger_run"

    def __init__(self, client: EnergyAssistantApiClient) -> None:
        self._client = client

    async def async_press(self) -> None:
        await self._client.run_plan()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: EnergyAssistantRuntimeData = entry.runtime_data
    client: EnergyAssistantApiClient = runtime.client
    async_add_entities([EnergyAssistantRunButton(client)])
