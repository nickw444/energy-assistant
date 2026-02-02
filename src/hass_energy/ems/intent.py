from __future__ import annotations

from typing import cast

from hass_energy.ems.models import (
    EmsPlanOutput,
    InverterPlanIntent,
    LoadPlanIntent,
    PlanIntent,
    PlanIntentMode,
)
from hass_energy.models.config import AppConfig
from hass_energy.models.loads import ControlledEvLoad, LoadConfig
from hass_energy.models.plant import InverterConfig

DEFAULT_EPS = 0.15


def build_plan_intent(
    plan: EmsPlanOutput,
    app_config: AppConfig,
    *,
    eps: float = DEFAULT_EPS,
) -> PlanIntent:
    if not plan.timesteps:
        return PlanIntent(inverters={}, loads={})

    step = plan.timesteps[0]
    grid_import_kw = float(step.grid.import_kw)
    grid_export_kw = float(step.grid.export_kw)
    price_export = float(step.economics.price_export)
    no_export = price_export < 0.0
    export_limit_normal_kw = float(app_config.plant.grid.max_export_kw)

    inverter_configs = _inverter_config_map(app_config.plant.inverters)
    inverters: dict[str, InverterPlanIntent] = {}

    for inverter_id, inverter in step.inverters.items():
        config = inverter_configs.get(inverter_id)
        battery = config.battery if config is not None else None
        max_charge_kw = battery.max_charge_kw if battery is not None else None
        max_discharge_kw = battery.max_discharge_kw if battery is not None else None

        ac_net_kw = float(inverter.ac_net_kw)
        charge_kw = _safe_kw(inverter.battery_charge_kw)
        discharge_kw = _safe_kw(inverter.battery_discharge_kw)

        mode = _inverter_mode(
            ac_net_kw=ac_net_kw,
            charge_kw=charge_kw,
            discharge_kw=discharge_kw,
            grid_import_kw=grid_import_kw,
            grid_export_kw=grid_export_kw,
            no_export=no_export,
            eps=eps,
        )

        export_limit_kw = _export_limit_target(
            mode=mode,
            ac_net_kw=ac_net_kw,
            grid_export_kw=grid_export_kw,
            max_discharge_kw=max_discharge_kw,
            export_limit_normal_kw=export_limit_normal_kw,
            no_export=no_export,
            eps=eps,
        )

        inverters[inverter_id] = InverterPlanIntent(
            mode=mode,
            export_limit_kw=export_limit_kw,
            force_charge_kw=_clamp_kw(charge_kw, max_charge_kw),
            force_discharge_kw=_clamp_kw(discharge_kw, max_discharge_kw),
        )

    load_configs = _load_config_map(app_config.loads)
    loads: dict[str, LoadPlanIntent] = {}
    for ev_id, ev in step.loads.evs.items():
        ev_config = load_configs.get(ev_id)
        min_power_kw = ev_config.min_power_kw if ev_config is not None else 0.0
        charge_kw = float(ev.charge_kw)
        charge_on = ev.connected and charge_kw >= min_power_kw
        loads[ev_id] = LoadPlanIntent(charge_kw=charge_kw, charge_on=charge_on)

    return PlanIntent(inverters=inverters, loads=loads)


def _inverter_mode(
    *,
    ac_net_kw: float,
    charge_kw: float,
    discharge_kw: float,
    grid_import_kw: float,
    grid_export_kw: float,
    no_export: bool,
    eps: float,
) -> PlanIntentMode:
    if discharge_kw <= eps and grid_import_kw > eps and ac_net_kw >= -eps:
        return "Backup"
    if no_export:
        return "Force Charge" if ac_net_kw < -eps else "Self Consumption"
    if ac_net_kw < -eps:
        return "Force Charge"
    if discharge_kw > eps and grid_export_kw > eps:
        return "Force Discharge"
    if grid_export_kw > eps and discharge_kw <= eps:
        return "Export Priority"
    return "Self Consumption"


def _export_limit_target(
    *,
    mode: PlanIntentMode,
    ac_net_kw: float,
    grid_export_kw: float,
    max_discharge_kw: float | None,
    export_limit_normal_kw: float,
    no_export: bool,
    eps: float,
) -> float:
    if no_export:
        return 0.0
    if mode != "Force Discharge":
        return export_limit_normal_kw
    at_max_discharge = (
        max_discharge_kw is not None and ac_net_kw >= (max_discharge_kw - eps)
    )
    if at_max_discharge:
        return export_limit_normal_kw
    return min(export_limit_normal_kw, max(0.0, grid_export_kw))


def _clamp_kw(value: float, max_kw: float | None) -> float:
    clamped = max(0.0, float(value))
    if max_kw is not None:
        clamped = min(clamped, float(max_kw))
    return clamped


def _safe_kw(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def _inverter_config_map(
    inverters: list[InverterConfig],
) -> dict[str, InverterConfig]:
    return {inv.id: inv for inv in inverters}


def _load_config_map(loads: list[LoadConfig]) -> dict[str, ControlledEvLoad]:
    evs: dict[str, ControlledEvLoad] = {}
    for load in loads:
        if getattr(load, "load_type", None) != "controlled_ev":
            continue
        ev = cast(ControlledEvLoad, load)
        evs[ev.id] = ev
    return evs
