"""Energy Assistant binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EnergyAssistantRuntimeData
from .coordinator import (
    EnergyAssistantCoordinator,
    build_plan_series,
    get_timestep0,
    intent_load_value_getter,
    inverter_step_getter,
    inverter_value_getter,
    sorted_items,
)
from .device import (
    entity_unique_id,
    inverter_device_info,
    load_device_info,
    suggested_object_id,
)
from .energy_assistant_client import EmsPlanOutput, PlanLatestResponse, TimestepPlan


# NOTE: homeassistant-stubs has several type conflicts that require ignores:
# 1. type: ignore[misc] on class - conflicting `available` property types between
#    CoordinatorEntity and Entity (property vs cached_property).
# 2. pyright: ignore[reportIncompatibleVariableOverride] on properties - stubs define
#    is_on/extra_state_attributes as cached_property but we override with property.
# These are stubs issues, not runtime issues. Remove ignores when stubs are fixed.
class EnergyAssistantCurtailmentSensor(  # type: ignore[misc]
    CoordinatorEntity[EnergyAssistantCoordinator],
    BinarySensorEntity,
):
    _attr_has_entity_name = True
    _attr_name = "Curtailment"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"plan"})

    def __init__(
        self,
        coordinator: EnergyAssistantCoordinator,
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
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        payload = self.coordinator.data
        if not payload:
            return None
        value = self._value_getter(payload.response)
        return bool(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
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


# NOTE: homeassistant-stubs has several type conflicts that require ignores:
# 1. type: ignore[misc] on class - conflicting `available` property types between
#    CoordinatorEntity and Entity (property vs cached_property).
# 2. pyright: ignore[reportIncompatibleVariableOverride] on properties - stubs define
#    is_on as cached_property but we override with property.
# These are stubs issues, not runtime issues. Remove ignores when stubs are fixed.
class EnergyAssistantPlanFlagSensor(  # type: ignore[misc]
    CoordinatorEntity[EnergyAssistantCoordinator],
    BinarySensorEntity,
):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnergyAssistantCoordinator,
        *,
        unique_id: str,
        suggested_object_id: str | None,
        name: str,
        value_getter: Callable[[PlanLatestResponse], Any],
        device_info: DeviceInfo,
        icon: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._value_getter = value_getter
        if suggested_object_id is not None:
            self._attr_suggested_object_id = suggested_object_id
        self._attr_device_info = device_info
        if icon:
            self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        payload = self.coordinator.data
        if not payload:
            return None
        value = self._value_getter(payload.response)
        return bool(value) if value is not None else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: EnergyAssistantRuntimeData = entry.runtime_data
    coordinator = runtime.coordinator
    base_url = runtime.base_url

    entities = _build_curtailment_entities(coordinator, base_url)
    entities.extend(_build_intent_entities(coordinator, base_url))
    if entities:
        async_add_entities(entities)


def _build_curtailment_entities(
    coordinator: EnergyAssistantCoordinator,
    base_url: str,
) -> list[BinarySensorEntity]:
    payload = coordinator.data
    if not payload:
        return []
    return _build_curtailment_entities_for_plan(coordinator, payload.response.plan, base_url)


def _build_intent_entities(
    coordinator: EnergyAssistantCoordinator,
    base_url: str,
) -> list[BinarySensorEntity]:
    payload = coordinator.data
    if not payload:
        return []
    intent = payload.response.intent
    if not intent.loads:
        return []

    entities: list[BinarySensorEntity] = []
    for name in sorted(intent.loads.keys()):
        load_device = load_device_info(base_url, name)
        entities.append(
            EnergyAssistantPlanFlagSensor(
                coordinator,
                unique_id=entity_unique_id(base_url, "plan", "ev", name, "charge_on"),
                suggested_object_id=suggested_object_id(
                    "intent",
                    "ev",
                    name,
                    "charge_on",
                ),
                name="Charge On",
                value_getter=intent_load_value_getter(name, "charge_on"),
                device_info=load_device,
                icon="mdi:ev-plug",
            )
        )
    return entities


def _build_curtailment_entities_for_plan(
    coordinator: EnergyAssistantCoordinator,
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
            EnergyAssistantCurtailmentSensor(
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
