"""HASS Energy sensors (proof of concept)."""

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

from . import HassEnergyRuntimeData
from .coordinator import (
    HassEnergyCoordinator,
    build_plan_series,
    ev_step_getter,
    ev_value_getter,
    get_timestep0,
    intent_inverter_value_getter,
    intent_load_value_getter,
    inverter_step_getter,
    inverter_value_getter,
    sorted_items,
)
from .device import (
    entity_unique_id,
    inverter_device_info,
    load_device_info,
    root_device_info,
    suggested_object_id,
)
from .hass_energy_client import EmsPlanOutput, PlanLatestResponse, TimestepPlan


# NOTE: homeassistant-stubs has several type conflicts that require ignores:
# 1. type: ignore[misc] on class - conflicting `available` property types between
#    CoordinatorEntity and Entity (property vs cached_property).
# 2. pyright: ignore[reportIncompatibleVariableOverride] on properties - stubs define
#    native_value/extra_state_attributes as cached_property but we override with property.
# These are stubs issues, not runtime issues. Remove ignores when stubs are fixed.
class HassEnergyPlanSensor(  # type: ignore[misc]
    CoordinatorEntity[HassEnergyCoordinator],
    SensorEntity,
):
    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"plan"})

    def __init__(
        self,
        coordinator: HassEnergyCoordinator,
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


# NOTE: homeassistant-stubs has several type conflicts that require ignores:
# 1. type: ignore[misc] on class - conflicting `available` property types between
#    CoordinatorEntity and Entity (property vs cached_property).
# 2. pyright: ignore[reportIncompatibleVariableOverride] on properties - stubs define
#    native_value as cached_property but we override with property.
# These are stubs issues, not runtime issues. Remove ignores when stubs are fixed.
class HassEnergyPlanUpdatedSensor(  # type: ignore[misc]
    CoordinatorEntity[HassEnergyCoordinator],
    SensorEntity,
):
    _attr_has_entity_name = True
    _attr_name = "Updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HassEnergyCoordinator,
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


# NOTE: homeassistant-stubs has several type conflicts that require ignores:
# 1. type: ignore[misc] on class - conflicting `available` property types between
#    CoordinatorEntity and Entity (property vs cached_property).
# 2. pyright: ignore[reportIncompatibleVariableOverride] on properties - stubs define
#    native_value/extra_state_attributes as cached_property but we override with property.
# These are stubs issues, not runtime issues. Remove ignores when stubs are fixed.
class HassEnergyPlanValueSensor(  # type: ignore[misc]
    CoordinatorEntity[HassEnergyCoordinator],
    SensorEntity,
):
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"plan"})

    def __init__(
        self,
        coordinator: HassEnergyCoordinator,
        *,
        unique_id: str,
        suggested_object_id: str | None,
        name: str,
        value_getter: Callable[[PlanLatestResponse], Any],
        series_getter: Callable[[TimestepPlan], Any] | None,
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
        if suggested_object_id is not None:
            self._attr_suggested_object_id = suggested_object_id
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
        value = self._value_getter(payload.response)
        return _normalize_value(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        payload = self.coordinator.data
        if not payload or self._series_getter is None:
            return {}
        return {
            "plan": build_plan_series(
                payload.response.plan,
                self._series_getter,
                _normalize_value,
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
    root_device = root_device_info(base_url)

    entities: list[SensorEntity] = [
        HassEnergyPlanSensor(
            coordinator,
            root_device,
            entity_unique_id(base_url, "plan", "status"),
        ),
        HassEnergyPlanUpdatedSensor(
            coordinator,
            root_device,
            entity_unique_id(base_url, "plan", "updated_at"),
        ),
    ]
    entities.extend(_build_mpc_entities(coordinator, base_url))
    entities.extend(_build_intent_entities(coordinator, base_url))
    async_add_entities(entities)


def _build_mpc_entities(
    coordinator: HassEnergyCoordinator,
    base_url: str,
) -> list[SensorEntity]:
    payload = coordinator.data
    if not payload:
        return []
    return _build_mpc_entities_for_plan(coordinator, payload.response.plan, base_url)


def _build_intent_entities(
    coordinator: HassEnergyCoordinator,
    base_url: str,
) -> list[SensorEntity]:
    payload = coordinator.data
    if not payload:
        return []
    return _build_intent_entities_for_response(coordinator, payload.response, base_url)


def _build_intent_entities_for_response(
    coordinator: HassEnergyCoordinator,
    response: PlanLatestResponse,
    base_url: str,
) -> list[SensorEntity]:
    intent = response.intent
    entities: list[SensorEntity] = []
    timestep0 = get_timestep0(response.plan)
    if timestep0 is not None:
        base_device = root_device_info(base_url)
        entities.extend(
            [
                HassEnergyPlanValueSensor(
                    coordinator,
                    unique_id=entity_unique_id(base_url, "grid", "import_power"),
                    suggested_object_id=suggested_object_id(
                        "intent",
                        "grid",
                        "import_power",
                    ),
                    name="Grid Import Power",
                    value_getter=_timestep0_getter(lambda step: step.grid.import_kw),
                    series_getter=lambda step: step.grid.import_kw,
                    device_info=base_device,
                    unit="kW",
                    icon="mdi:transmission-tower-import",
                    entity_category=None,
                ),
                HassEnergyPlanValueSensor(
                    coordinator,
                    unique_id=entity_unique_id(base_url, "grid", "export_power"),
                    suggested_object_id=suggested_object_id(
                        "intent",
                        "grid",
                        "export_power",
                    ),
                    name="Grid Export Power",
                    value_getter=_timestep0_getter(lambda step: step.grid.export_kw),
                    series_getter=lambda step: step.grid.export_kw,
                    device_info=base_device,
                    unit="kW",
                    icon="mdi:transmission-tower-export",
                    entity_category=None,
                ),
            ]
        )
    for name in sorted(intent.inverters.keys()):
        inverter_device = inverter_device_info(base_url, name)
        entities.extend(
            [
                HassEnergyPlanValueSensor(
                    coordinator,
                    unique_id=entity_unique_id(
                        base_url,
                        "plan",
                        "inverter",
                        name,
                        "mode",
                    ),
                    suggested_object_id=suggested_object_id(
                        "intent",
                        "inverter",
                        name,
                        "mode",
                    ),
                    name="Inverter Mode",
                    value_getter=intent_inverter_value_getter(name, "mode"),
                    series_getter=None,
                    device_info=inverter_device,
                    unit=None,
                    icon="mdi:transition",
                    entity_category=None,
                ),
                HassEnergyPlanValueSensor(
                    coordinator,
                    unique_id=entity_unique_id(
                        base_url,
                        "plan",
                        "inverter",
                        name,
                        "force_charge_power",
                    ),
                    suggested_object_id=suggested_object_id(
                        "intent",
                        "inverter",
                        name,
                        "force_charge_power",
                    ),
                    name="Battery Charge Power",
                    value_getter=intent_inverter_value_getter(
                        name,
                        "force_charge_kw",
                    ),
                    series_getter=None,
                    device_info=inverter_device,
                    unit="kW",
                    icon="mdi:battery-charging",
                    entity_category=None,
                ),
                HassEnergyPlanValueSensor(
                    coordinator,
                    unique_id=entity_unique_id(
                        base_url,
                        "plan",
                        "inverter",
                        name,
                        "force_discharge_power",
                    ),
                    suggested_object_id=suggested_object_id(
                        "intent",
                        "inverter",
                        name,
                        "force_discharge_power",
                    ),
                    name="Battery Discharge Power",
                    value_getter=intent_inverter_value_getter(
                        name,
                        "force_discharge_kw",
                    ),
                    series_getter=None,
                    device_info=inverter_device,
                    unit="kW",
                    icon="mdi:battery-minus",
                    entity_category=None,
                ),
            ]
        )

    for name in sorted(intent.loads.keys()):
        load_device = load_device_info(base_url, name)
        entities.append(
            HassEnergyPlanValueSensor(
                coordinator,
                unique_id=entity_unique_id(base_url, "plan", "ev", name, "charge_power"),
                suggested_object_id=suggested_object_id(
                    "intent",
                    "ev",
                    name,
                    "charge_power",
                ),
                name="Charge Power",
                value_getter=intent_load_value_getter(name, "charge_kw"),
                series_getter=None,
                device_info=load_device,
                unit="kW",
                icon="mdi:ev-station",
                entity_category=None,
            )
        )

    return entities


def _build_mpc_entities_for_plan(
    coordinator: HassEnergyCoordinator,
    plan: EmsPlanOutput,
    base_url: str,
) -> list[SensorEntity]:
    timestep0 = get_timestep0(plan)
    if not timestep0:
        return []

    base_device = root_device_info(base_url)
    entities: list[SensorEntity] = [
        HassEnergyPlanValueSensor(
            coordinator,
            unique_id=entity_unique_id(base_url, "grid", "net_power"),
            suggested_object_id=None,
            name="Grid Net Power",
            value_getter=_timestep0_getter(lambda step: step.grid.net_kw),
            series_getter=lambda step: step.grid.net_kw,
            device_info=base_device,
            unit="kW",
            icon="mdi:transmission-tower",
        ),
        HassEnergyPlanValueSensor(
            coordinator,
            unique_id=entity_unique_id(base_url, "load", "base_power"),
            suggested_object_id=None,
            name="Load Base Power",
            value_getter=_timestep0_getter(lambda step: step.loads.base_kw),
            series_getter=lambda step: step.loads.base_kw,
            device_info=base_device,
            unit="kW",
            icon="mdi:home-lightning-bolt",
        ),
        HassEnergyPlanValueSensor(
            coordinator,
            unique_id=entity_unique_id(base_url, "load", "total_power"),
            suggested_object_id=None,
            name="Load Total Power",
            value_getter=_timestep0_getter(lambda step: step.loads.total_kw),
            series_getter=lambda step: step.loads.total_kw,
            device_info=base_device,
            unit="kW",
            icon="mdi:home-lightning-bolt",
        ),
        HassEnergyPlanValueSensor(
            coordinator,
            unique_id=entity_unique_id(base_url, "price", "import"),
            suggested_object_id=None,
            name="Price Import",
            value_getter=_timestep0_getter(lambda step: step.economics.price_import),
            series_getter=lambda step: step.economics.price_import,
            device_info=base_device,
            unit=f"{CURRENCY_DOLLAR}/kWh",
            icon="mdi:cash",
        ),
        HassEnergyPlanValueSensor(
            coordinator,
            unique_id=entity_unique_id(base_url, "price", "export"),
            suggested_object_id=None,
            name="Price Export",
            value_getter=_timestep0_getter(lambda step: step.economics.price_export),
            series_getter=lambda step: step.economics.price_export,
            device_info=base_device,
            unit=f"{CURRENCY_DOLLAR}/kWh",
            icon="mdi:cash-minus",
        ),
        HassEnergyPlanValueSensor(
            coordinator,
            unique_id=entity_unique_id(base_url, "cost", "segment"),
            suggested_object_id=None,
            name="Segment Cost",
            value_getter=_timestep0_getter(lambda step: step.economics.segment_cost),
            series_getter=lambda step: step.economics.segment_cost,
            device_info=base_device,
            unit=CURRENCY_DOLLAR,
            icon="mdi:cash",
        ),
        HassEnergyPlanValueSensor(
            coordinator,
            unique_id=entity_unique_id(base_url, "cost", "forecast"),
            suggested_object_id=None,
            name="Cost Forecast",
            value_getter=_plan_getter(_plan_last_cumulative_cost),
            series_getter=lambda step: step.economics.cumulative_cost,
            device_info=base_device,
            unit=CURRENCY_DOLLAR,
            icon="mdi:cash-multiple",
        ),
        HassEnergyPlanValueSensor(
            coordinator,
            unique_id=entity_unique_id(base_url, "horizon", "length"),
            suggested_object_id=None,
            name="Horizon Length",
            value_getter=_plan_getter(_plan_horizon_hours),
            series_getter=None,
            device_info=base_device,
            unit=UnitOfTime.HOURS,
            icon="mdi:timeline-clock",
        ),
    ]

    for name, inverter in sorted_items(timestep0.inverters):
        inverter_device = inverter_device_info(base_url, name)
        if inverter.pv_kw is not None:
            entities.append(
                HassEnergyPlanValueSensor(
                    coordinator,
                    unique_id=entity_unique_id(base_url, "inverter", name, "pv_power"),
                    suggested_object_id=suggested_object_id(
                        "inverter",
                        name,
                        "pv_power",
                    ),
                    name="PV Power",
                    value_getter=inverter_value_getter(name, "pv_kw"),
                    series_getter=inverter_step_getter(name, "pv_kw"),
                    device_info=inverter_device,
                    unit="kW",
                    icon="mdi:solar-power",
                )
            )
        entities.append(
            HassEnergyPlanValueSensor(
                coordinator,
                unique_id=entity_unique_id(base_url, "inverter", name, "net_power"),
                suggested_object_id=suggested_object_id(
                    "inverter",
                    name,
                    "net_power",
                ),
                name="Inverter Net Power",
                value_getter=inverter_value_getter(name, "ac_net_kw"),
                series_getter=inverter_step_getter(name, "ac_net_kw"),
                device_info=inverter_device,
                unit="kW",
                icon="mdi:current-ac",
            )
        )
        if inverter.battery_soc_kwh is not None:
            entities.append(
                HassEnergyPlanValueSensor(
                    coordinator,
                    unique_id=entity_unique_id(base_url, "inverter", name, "battery_soc"),
                    suggested_object_id=suggested_object_id(
                        "inverter",
                        name,
                        "battery_soc",
                    ),
                    name="Battery Stored Energy",
                    value_getter=inverter_value_getter(name, "battery_soc_kwh"),
                    series_getter=inverter_step_getter(name, "battery_soc_kwh"),
                    device_info=inverter_device,
                    unit=UnitOfEnergy.KILO_WATT_HOUR,
                    icon="mdi:battery",
                )
            )
        if inverter.battery_soc_pct is not None:
            entities.append(
                HassEnergyPlanValueSensor(
                    coordinator,
                    unique_id=entity_unique_id(
                        base_url,
                        "inverter",
                        name,
                        "battery_soc_pct",
                    ),
                    suggested_object_id=suggested_object_id(
                        "inverter",
                        name,
                        "battery_soc_pct",
                    ),
                    name="Battery SoC",
                    value_getter=inverter_value_getter(name, "battery_soc_pct"),
                    series_getter=inverter_step_getter(name, "battery_soc_pct"),
                    device_info=inverter_device,
                    unit=PERCENTAGE,
                    icon="mdi:battery",
                )
            )

    for name, ev in sorted_items(timestep0.loads.evs):
        load_device = load_device_info(base_url, name)
        entities.append(
            HassEnergyPlanValueSensor(
                coordinator,
                unique_id=entity_unique_id(base_url, "ev", name, "charge_power"),
                suggested_object_id=suggested_object_id(
                    "ev",
                    name,
                    "charge_power",
                ),
                name="Charge Power",
                value_getter=ev_value_getter(name, "charge_kw"),
                series_getter=ev_step_getter(name, "charge_kw"),
                device_info=load_device,
                unit="kW",
                icon="mdi:ev-station",
            )
        )
        entities.append(
            HassEnergyPlanValueSensor(
                coordinator,
                unique_id=entity_unique_id(base_url, "ev", name, "soc"),
                suggested_object_id=suggested_object_id("ev", name, "soc"),
                name="Stored Energy",
                value_getter=ev_value_getter(name, "soc_kwh"),
                series_getter=ev_step_getter(name, "soc_kwh"),
                device_info=load_device,
                unit=UnitOfEnergy.KILO_WATT_HOUR,
                icon="mdi:car-electric",
            )
        )
        if ev.soc_pct is not None:
            entities.append(
                HassEnergyPlanValueSensor(
                    coordinator,
                    unique_id=entity_unique_id(base_url, "ev", name, "soc_pct"),
                    suggested_object_id=suggested_object_id("ev", name, "soc_pct"),
                    name="SoC",
                    value_getter=ev_value_getter(name, "soc_pct"),
                    series_getter=ev_step_getter(name, "soc_pct"),
                    device_info=load_device,
                    unit=PERCENTAGE,
                    icon="mdi:car-electric",
                )
            )
    return entities


def _timestep0_getter(
    getter: Callable[[TimestepPlan], Any],
) -> Callable[[PlanLatestResponse], Any]:
    def _get(response: PlanLatestResponse) -> Any:
        step = get_timestep0(response.plan)
        if step is None:
            return None
        return getter(step)

    return _get


def _round_kw(value: float) -> float:
    return round(float(value), 3)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return _round_kw(value)
    return value


def _plan_getter(
    getter: Callable[[EmsPlanOutput], Any],
) -> Callable[[PlanLatestResponse], Any]:
    def _get(response: PlanLatestResponse) -> Any:
        return getter(response.plan)

    return _get


def _plan_last_cumulative_cost(plan: EmsPlanOutput) -> Any:
    if not plan.timesteps:
        return None
    return plan.timesteps[-1].economics.cumulative_cost


def _plan_horizon_hours(plan: EmsPlanOutput) -> float | None:
    if not plan.timesteps:
        return None
    start = plan.timesteps[0].start
    end = plan.timesteps[-1].end
    return (end - start).total_seconds() / 3600.0
