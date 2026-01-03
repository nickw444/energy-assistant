"""HASS Energy binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HassEnergyRuntimeData
from .coordinator import (
    HassEnergyCoordinator,
    PlanPayload,
    build_plan_series,
    get_timestep0,
    inverter_step_getter,
    inverter_value_getter,
    sorted_items,
)
from .device import entity_unique_id, inverter_device_info, suggested_object_id
from .hass_energy_client import EmsPlanOutput, PlanLatestResponse, TimestepPlan


class HassEnergyCurtailmentSensor(CoordinatorEntity[PlanPayload | None], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Curtailment"
    _unrecorded_attributes = frozenset({"plan"})

    def __init__(
        self,
        coordinator: HassEnergyCoordinator,
        *,
        unique_id: str,
        suggested_object_id: str | None,
        value_getter: Callable[[PlanLatestResponse], Any],
        series_getter: Callable[[TimestepPlan], Any],
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._value_getter = value_getter
        self._series_getter = series_getter
        if suggested_object_id is not None:
            self._attr_suggested_object_id = suggested_object_id
        self._attr_device_info = device_info
        self._attr_icon = "mdi:solar-power-variant"

    @property
    def is_on(self) -> bool | None:
        payload = self.coordinator.data
        if not payload:
            return None
        value = self._value_getter(payload.response)
        if value is None:
            return None
        return bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payload = self.coordinator.data
        if not payload:
            return {}
        return {
            "plan": build_plan_series(
                payload.response.plan,
                self._series_getter,
                _normalize_bool,
            ),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: HassEnergyRuntimeData = entry.runtime_data
    coordinator = runtime.coordinator
    base_url = runtime.base_url

    entities = _build_curtailment_entities(coordinator, base_url)
    if entities:
        async_add_entities(entities)


def _build_curtailment_entities(
    coordinator: HassEnergyCoordinator,
    base_url: str,
) -> list[BinarySensorEntity]:
    payload = coordinator.data
    if not payload:
        return []
    return _build_curtailment_entities_for_plan(coordinator, payload.response.plan, base_url)


def _build_curtailment_entities_for_plan(
    coordinator: HassEnergyCoordinator,
    plan: EmsPlanOutput,
    base_url: str,
) -> list[BinarySensorEntity]:
    timestep0 = get_timestep0(plan)
    if not timestep0:
        return []

    entities: list[BinarySensorEntity] = []
    for name, inverter in sorted_items(timestep0.inverters):
        if inverter.curtailment is None:
            continue
        inverter_device = inverter_device_info(base_url, name)
        entities.append(
            HassEnergyCurtailmentSensor(
                coordinator,
                unique_id=entity_unique_id(base_url, "inverter", name, "curtailment"),
                suggested_object_id=suggested_object_id(
                    "inverter",
                    name,
                    "curtailment",
                ),
                value_getter=inverter_value_getter(name, "curtailment"),
                series_getter=inverter_step_getter(name, "curtailment"),
                device_info=inverter_device,
            )
        )
    return entities


def _normalize_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)
