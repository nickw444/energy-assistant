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
    PlanLatestResponse,
    build_plan_series,
    component_intent_value_getter,
    component_series_getter,
    component_value_getter,
    components_of_type,
)
from .device import (
    entity_unique_id,
    ev_device_info,
    pv_device_info,
    suggested_object_id,
)
from .energy_assistant_client import (
    EmsPlanOutput,
    EmsSeriesPoint,
    LoadControlledEvComponentPlan,
    PvComponentPlan,
)


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
        suggested_object_id_value: str | None,
        value_getter: Callable[[PlanLatestResponse], Any],
        series_getter: Callable[[PlanLatestResponse], list[EmsSeriesPoint]],
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._value_getter = value_getter
        self._series_getter = series_getter
        if suggested_object_id_value is not None:
            self._attr_suggested_object_id = suggested_object_id_value
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
                self._series_getter(payload.response),
                _normalize_bool,
            ),
        }


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
        suggested_object_id_value: str | None,
        name: str,
        value_getter: Callable[[PlanLatestResponse], Any],
        device_info: DeviceInfo,
        icon: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._value_getter = value_getter
        if suggested_object_id_value is not None:
            self._attr_suggested_object_id = suggested_object_id_value
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
    entities.extend(_build_ev_intent_entities(coordinator, base_url))
    if entities:
        async_add_entities(entities)


def _build_curtailment_entities(
    coordinator: EnergyAssistantCoordinator,
    base_url: str,
) -> list[BinarySensorEntity]:
    payload = coordinator.data
    if payload is None:
        return []
    plan: EmsPlanOutput = payload.response.plan
    entities: list[BinarySensorEntity] = []
    for component_id in components_of_type(plan, PvComponentPlan):
        device = pv_device_info(base_url, component_id)
        entities.append(
            EnergyAssistantCurtailmentSensor(
                coordinator,
                unique_id=entity_unique_id(base_url, "pv", component_id, "curtailment"),
                suggested_object_id_value=suggested_object_id("pv", component_id, "curtailment"),
                value_getter=component_value_getter(component_id, "curtailment"),
                series_getter=component_series_getter(component_id, "curtailment"),
                device_info=device,
            )
        )
    return entities


def _build_ev_intent_entities(
    coordinator: EnergyAssistantCoordinator,
    base_url: str,
) -> list[BinarySensorEntity]:
    payload = coordinator.data
    if payload is None:
        return []
    plan: EmsPlanOutput = payload.response.plan
    entities: list[BinarySensorEntity] = []
    for component_id in components_of_type(plan, LoadControlledEvComponentPlan):
        device = ev_device_info(base_url, component_id)
        entities.append(
            EnergyAssistantPlanFlagSensor(
                coordinator,
                unique_id=entity_unique_id(base_url, "ev", component_id, "charge_on"),
                suggested_object_id_value=suggested_object_id("ev", component_id, "charge_on"),
                name="Charge On",
                value_getter=component_intent_value_getter(component_id, "charge_on"),
                device_info=device,
                icon="mdi:ev-plug-type2",
            )
        )
    return entities


def _normalize_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)
