"""Energy Assistant Home Assistant integration (proof of concept)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config import ConfigType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_BASE_URL,
    CONF_TIMEOUT,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
)
from .coordinator import EnergyAssistantCoordinator
from .energy_assistant_client import EnergyAssistantApiClient

PLATFORMS = ["sensor", "binary_sensor", "button"]


@dataclass(slots=True)
class EnergyAssistantRuntimeData:
    client: EnergyAssistantApiClient
    coordinator: EnergyAssistantCoordinator
    base_url: str


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration via configuration.yaml."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Energy Assistant from a config entry."""
    session = async_get_clientsession(hass)
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL).rstrip("/")
    timeout = entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
    client = EnergyAssistantApiClient(session, base_url, timeout)
    coordinator = EnergyAssistantCoordinator(
        hass,
        client,
        DEFAULT_SCAN_INTERVAL,
    )
    await coordinator.async_config_entry_first_refresh()
    coordinator.start_long_poll_loop()
    entry.runtime_data = EnergyAssistantRuntimeData(
        client=client,
        coordinator=coordinator,
        base_url=base_url,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime_data: EnergyAssistantRuntimeData | None = entry.runtime_data
    if runtime_data is not None:
        runtime_data.coordinator.stop_long_poll_loop()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data = None
    return unload_ok
