"""Energy Assistant sensors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_DOLLAR, PERCENTAGE, UnitOfEnergy, UnitOfTime
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
    plan_horizon_hours,
    single_component,
)
from .device import (
    battery_device_info,
    entity_unique_id,
    ev_device_info,
    inverter_device_info,
    pv_device_info,
    root_device_info,
    suggested_object_id,
)
from .energy_assistant_client import (
    BatteryComponentPlan,
    EmsPlanOutput,
    EmsSeriesPoint,
    GridComponentPlan,
    InverterComponentPlan,
    LoadComponentPlan,
    LoadControlledEvComponentPlan,
    PvComponentPlan,
)


class EnergyAssistantPlanSensor(  # type: ignore[misc]
    CoordinatorEntity[EnergyAssistantCoordinator],
    SensorEntity,
):
    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"plan"})

    def __init__(
        self,
        coordinator: EnergyAssistantCoordinator,
        device_info: DeviceInfo,
        unique_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        payload = self.coordinator.data
        if not payload:
            return None
        return str(payload.response.plan.status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        payload = self.coordinator.data
        if not payload:
            return {}
        return {"plan": payload.plan_dump}


class EnergyAssistantPlanUpdatedSensor(  # type: ignore[misc]
    CoordinatorEntity[EnergyAssistantCoordinator],
    SensorEntity,
):
    _attr_has_entity_name = True
    _attr_name = "Updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: EnergyAssistantCoordinator,
        device_info: DeviceInfo,
        unique_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info

    @property
    def native_value(self) -> Any:  # pyright: ignore[reportIncompatibleVariableOverride]
        payload = self.coordinator.data
        if not payload:
            return None
        return payload.response.plan.generated_at


class EnergyAssistantPlanValueSensor(  # type: ignore[misc]
    CoordinatorEntity[EnergyAssistantCoordinator],
    SensorEntity,
):
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"plan"})

    def __init__(
        self,
        coordinator: EnergyAssistantCoordinator,
        *,
        unique_id: str,
        suggested_object_id_value: str | None,
        name: str,
        value_getter: Callable[[PlanLatestResponse], Any],
        series_getter: Callable[[PlanLatestResponse], list[EmsSeriesPoint]] | None,
        device_info: DeviceInfo | None,
        unit: str | None,
        icon: str | None,
        entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._attr_entity_category = entity_category
        self._value_getter = value_getter
        self._series_getter = series_getter
        if suggested_object_id_value is not None:
            self._attr_suggested_object_id = suggested_object_id_value
        if device_info is not None:
            self._attr_device_info = device_info
        if unit:
            self._attr_native_unit_of_measurement = unit
        if icon:
            self._attr_icon = icon

    @property
    def native_value(self) -> Any:  # pyright: ignore[reportIncompatibleVariableOverride]
        payload = self.coordinator.data
        if not payload:
            return None
        return _normalize_value(self._value_getter(payload.response))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        payload = self.coordinator.data
        if not payload or self._series_getter is None:
            return {}
        return {
            "plan": build_plan_series(
                self._series_getter(payload.response),
                _normalize_value,
            ),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: EnergyAssistantRuntimeData = entry.runtime_data
    coordinator = runtime.coordinator
    base_url = runtime.base_url
    root_device = root_device_info(base_url)

    entities: list[SensorEntity] = [
        EnergyAssistantPlanSensor(
            coordinator,
            root_device,
            entity_unique_id(base_url, "plan", "status"),
        ),
        EnergyAssistantPlanUpdatedSensor(
            coordinator,
            root_device,
            entity_unique_id(base_url, "plan", "updated_at"),
        ),
    ]
    payload = coordinator.data
    if payload is not None:
        entities.extend(_build_plan_entities(coordinator, payload.response.plan, base_url))
    async_add_entities(entities)


def _build_plan_entities(
    coordinator: EnergyAssistantCoordinator,
    plan: EmsPlanOutput,
    base_url: str,
) -> list[SensorEntity]:
    base_device = root_device_info(base_url)
    entities: list[SensorEntity] = []

    grid_entry = single_component(plan, GridComponentPlan)
    if grid_entry is not None:
        grid_id, _grid = grid_entry
        entities.extend(
            [
                _value_sensor(
                    coordinator,
                    base_url,
                    base_device,
                    "grid",
                    "net_power",
                    "Grid Net Power",
                    component_value_getter(grid_id, "net_kw"),
                    component_series_getter(grid_id, "net_kw"),
                    "kW",
                    "mdi:transmission-tower",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    base_device,
                    "grid",
                    "import_power",
                    "Grid Import Power",
                    component_value_getter(grid_id, "import_kw"),
                    component_series_getter(grid_id, "import_kw"),
                    "kW",
                    "mdi:transmission-tower-import",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    base_device,
                    "grid",
                    "export_power",
                    "Grid Export Power",
                    component_value_getter(grid_id, "export_kw"),
                    component_series_getter(grid_id, "export_kw"),
                    "kW",
                    "mdi:transmission-tower-export",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    base_device,
                    "price",
                    "import",
                    "Price Import",
                    component_value_getter(grid_id, "price_import_raw"),
                    component_series_getter(grid_id, "price_import_raw"),
                    f"{CURRENCY_DOLLAR}/kWh",
                    "mdi:cash",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    base_device,
                    "price",
                    "import_effective",
                    "Price Import Effective",
                    component_value_getter(grid_id, "price_import_effective"),
                    component_series_getter(grid_id, "price_import_effective"),
                    f"{CURRENCY_DOLLAR}/kWh",
                    "mdi:cash",
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    base_device,
                    "price",
                    "export",
                    "Price Export",
                    component_value_getter(grid_id, "price_export_raw"),
                    component_series_getter(grid_id, "price_export_raw"),
                    f"{CURRENCY_DOLLAR}/kWh",
                    "mdi:cash-minus",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    base_device,
                    "price",
                    "export_effective",
                    "Price Export Effective",
                    component_value_getter(grid_id, "price_export_effective"),
                    component_series_getter(grid_id, "price_export_effective"),
                    f"{CURRENCY_DOLLAR}/kWh",
                    "mdi:cash-minus",
                ),
            ]
        )

    load_entry = single_component(plan, LoadComponentPlan)
    if load_entry is not None:
        load_id, _load = load_entry
        entities.append(
            _value_sensor(
                coordinator,
                base_url,
                base_device,
                "load",
                "base_power",
                "Load Base Power",
                component_value_getter(load_id, "power_kw"),
                component_series_getter(load_id, "power_kw"),
                "kW",
                "mdi:home-lightning-bolt",
                entity_category=None,
            )
        )

    entities.extend(
        [
            EnergyAssistantPlanValueSensor(
                coordinator,
                unique_id=entity_unique_id(base_url, "cost", "forecast"),
                suggested_object_id_value=None,
                name="Cost Forecast",
                value_getter=lambda response: response.plan.objective_value,
                series_getter=None,
                device_info=base_device,
                unit=CURRENCY_DOLLAR,
                icon="mdi:cash-multiple",
                entity_category=None,
            ),
            EnergyAssistantPlanValueSensor(
                coordinator,
                unique_id=entity_unique_id(base_url, "horizon", "length"),
                suggested_object_id_value=None,
                name="Horizon Length",
                value_getter=lambda response: plan_horizon_hours(response.plan),
                series_getter=None,
                device_info=base_device,
                unit=UnitOfTime.HOURS,
                icon="mdi:timeline-clock",
                entity_category=None,
            ),
        ]
    )

    for component_id in components_of_type(plan, InverterComponentPlan):
        device = inverter_device_info(base_url, component_id)
        entities.extend(
            [
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "inverter",
                    component_id,
                    "net_power",
                    "Inverter Net Power",
                    component_value_getter(component_id, "ac_net_kw"),
                    component_series_getter(component_id, "ac_net_kw"),
                    "kW",
                    "mdi:current-ac",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "inverter",
                    component_id,
                    "mode",
                    "Inverter Mode",
                    component_intent_value_getter(component_id, "mode"),
                    None,
                    None,
                    "mdi:transition",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "inverter",
                    component_id,
                    "export_limit_kw",
                    "Export Limit",
                    component_intent_value_getter(component_id, "export_limit_kw"),
                    None,
                    "kW",
                    "mdi:transmission-tower-export",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "inverter",
                    component_id,
                    "force_charge_kw",
                    "Force Charge Power",
                    component_intent_value_getter(component_id, "force_charge_kw"),
                    None,
                    "kW",
                    "mdi:battery-charging",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "inverter",
                    component_id,
                    "force_discharge_kw",
                    "Force Discharge Power",
                    component_intent_value_getter(component_id, "force_discharge_kw"),
                    None,
                    "kW",
                    "mdi:battery-minus",
                    entity_category=None,
                ),
            ]
        )

    for component_id in components_of_type(plan, PvComponentPlan):
        device = pv_device_info(base_url, component_id)
        entities.extend(
            [
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "pv",
                    component_id,
                    "actual_kw",
                    "PV Power",
                    component_value_getter(component_id, "actual_kw"),
                    component_series_getter(component_id, "actual_kw"),
                    "kW",
                    "mdi:solar-power",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "pv",
                    component_id,
                    "available_kw",
                    "PV Available Power",
                    component_value_getter(component_id, "available_kw"),
                    component_series_getter(component_id, "available_kw"),
                    "kW",
                    "mdi:solar-power-variant",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "pv",
                    component_id,
                    "curtail_kw",
                    "PV Curtailment Power",
                    component_value_getter(component_id, "curtail_kw"),
                    component_series_getter(component_id, "curtail_kw"),
                    "kW",
                    "mdi:solar-power-variant-outline",
                    entity_category=None,
                ),
            ]
        )

    for component_id in components_of_type(plan, BatteryComponentPlan):
        device = battery_device_info(base_url, component_id)
        entities.extend(
            [
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "battery",
                    component_id,
                    "charge_kw",
                    "Charge Power",
                    component_value_getter(component_id, "charge_kw"),
                    component_series_getter(component_id, "charge_kw"),
                    "kW",
                    "mdi:battery-charging",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "battery",
                    component_id,
                    "discharge_kw",
                    "Discharge Power",
                    component_value_getter(component_id, "discharge_kw"),
                    component_series_getter(component_id, "discharge_kw"),
                    "kW",
                    "mdi:battery-minus",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "battery",
                    component_id,
                    "soc_kwh",
                    "Stored Energy",
                    component_value_getter(component_id, "soc_kwh"),
                    component_series_getter(component_id, "soc_kwh"),
                    UnitOfEnergy.KILO_WATT_HOUR,
                    "mdi:battery",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "battery",
                    component_id,
                    "soc_pct",
                    "State Of Charge",
                    component_value_getter(component_id, "soc_pct"),
                    component_series_getter(component_id, "soc_pct"),
                    PERCENTAGE,
                    "mdi:battery",
                    entity_category=None,
                ),
            ]
        )

    for component_id in components_of_type(plan, LoadControlledEvComponentPlan):
        device = ev_device_info(base_url, component_id)
        entities.extend(
            [
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "ev",
                    component_id,
                    "charge_power",
                    "Charge Power",
                    component_value_getter(component_id, "charge_kw"),
                    component_series_getter(component_id, "charge_kw"),
                    "kW",
                    "mdi:ev-station",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "ev",
                    component_id,
                    "soc",
                    "Stored Energy",
                    component_value_getter(component_id, "soc_kwh"),
                    component_series_getter(component_id, "soc_kwh"),
                    UnitOfEnergy.KILO_WATT_HOUR,
                    "mdi:car-electric",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "ev",
                    component_id,
                    "soc_pct",
                    "SoC",
                    component_value_getter(component_id, "soc_pct"),
                    component_series_getter(component_id, "soc_pct"),
                    PERCENTAGE,
                    "mdi:car-electric",
                    entity_category=None,
                ),
                _value_sensor(
                    coordinator,
                    base_url,
                    device,
                    "ev",
                    component_id,
                    "intent_charge_kw",
                    "Intent Charge Power",
                    component_intent_value_getter(component_id, "charge_kw"),
                    None,
                    "kW",
                    "mdi:ev-plug-type2",
                    entity_category=None,
                ),
            ]
        )

    return entities


def _value_sensor(
    coordinator: EnergyAssistantCoordinator,
    base_url: str,
    device_info: DeviceInfo,
    kind: str,
    *args: Any,
    entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC,
) -> EnergyAssistantPlanValueSensor:
    if len(args) < 5:
        raise ValueError("Expected path parts plus sensor configuration.")
    *parts, name, value_getter, series_getter, unit, icon = args
    if len(parts) < 1:
        raise ValueError("Expected at least one path part for the sensor id.")
    path_parts = tuple(str(part) for part in parts)
    object_parts = path_parts[:-1] if len(path_parts) > 1 else path_parts
    return EnergyAssistantPlanValueSensor(
        coordinator,
        unique_id=entity_unique_id(base_url, kind, *path_parts),
        suggested_object_id_value=suggested_object_id(kind, *object_parts),
        name=str(name),
        value_getter=value_getter,
        series_getter=series_getter,
        device_info=device_info,
        unit=unit,
        icon=icon,
        entity_category=entity_category,
    )


def _round_kw(value: float) -> float:
    return round(float(value), 3)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return _round_kw(value)
    return value
