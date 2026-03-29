from __future__ import annotations

from energy_assistant.ems.models import (
    InverterIntent,
    LoadControlledEvIntent,
    PlanIntentMode,
)
from energy_assistant.models.plant import BatteryComponentConfig, ControlledEvComponentConfig

EPSILON_KW = 0.15
BATTERY_FULL_TOLERANCE_PCT = 1.0


def build_inverter_intent(
    *,
    ac_net_kw: float,
    charge_kw: float,
    discharge_kw: float,
    battery_soc_pct: float | None,
    grid_import_kw: float,
    grid_export_kw: float,
    price_export: float,
    export_limit_normal_kw: float,
    battery: BatteryComponentConfig | None,
    near_zero_tolerence_kw: float = EPSILON_KW,
) -> InverterIntent:
    no_export = float(price_export) < 0.0
    max_charge_kw = battery.max_charge_kw if battery is not None else None
    max_discharge_kw = battery.max_discharge_kw if battery is not None else None
    battery_full = _battery_full(battery_soc_pct, battery)

    mode = _inverter_mode(
        ac_net_kw=ac_net_kw,
        charge_kw=charge_kw,
        discharge_kw=discharge_kw,
        grid_import_kw=grid_import_kw,
        grid_export_kw=grid_export_kw,
        no_export=no_export,
        battery_full=battery_full,
        near_zero_tolerence_kw=near_zero_tolerence_kw,
    )
    export_limit_kw = _export_limit_target(
        mode=mode,
        ac_net_kw=ac_net_kw,
        grid_export_kw=grid_export_kw,
        max_discharge_kw=max_discharge_kw,
        export_limit_normal_kw=export_limit_normal_kw,
        no_export=no_export,
        near_zero_tolerence_kw=near_zero_tolerence_kw,
    )
    return InverterIntent(
        mode=mode,
        export_limit_kw=export_limit_kw,
        force_charge_kw=_clamp_kw(charge_kw, max_charge_kw),
        force_discharge_kw=_clamp_kw(discharge_kw, max_discharge_kw),
    )


def build_load_controlled_ev_intent(
    *,
    charge_kw: float,
    connected: bool,
    ev_config: ControlledEvComponentConfig,
) -> LoadControlledEvIntent:
    charge_on = bool(connected) and float(charge_kw) >= float(ev_config.min_power_kw)
    return LoadControlledEvIntent(charge_kw=charge_kw, charge_on=charge_on)


def _inverter_mode(
    *,
    ac_net_kw: float,
    charge_kw: float,
    discharge_kw: float,
    grid_import_kw: float,
    grid_export_kw: float,
    no_export: bool,
    battery_full: bool,
    near_zero_tolerence_kw: float,
) -> PlanIntentMode:
    if (
        discharge_kw <= near_zero_tolerence_kw
        and grid_import_kw > near_zero_tolerence_kw
        and ac_net_kw >= -near_zero_tolerence_kw
    ):
        return PlanIntentMode.BACKUP
    if no_export:
        return (
            PlanIntentMode.FORCE_CHARGE
            if ac_net_kw < -near_zero_tolerence_kw
            else PlanIntentMode.SELF_USE
        )
    if ac_net_kw < -near_zero_tolerence_kw:
        return PlanIntentMode.FORCE_CHARGE
    if discharge_kw > near_zero_tolerence_kw and grid_export_kw > near_zero_tolerence_kw:
        return PlanIntentMode.FORCE_DISCHARGE
    if grid_export_kw > near_zero_tolerence_kw and discharge_kw <= near_zero_tolerence_kw:
        return PlanIntentMode.SELF_USE if battery_full else PlanIntentMode.EXPORT_PRIORITY
    return PlanIntentMode.SELF_USE


def _export_limit_target(
    *,
    mode: PlanIntentMode,
    ac_net_kw: float,
    grid_export_kw: float,
    max_discharge_kw: float | None,
    export_limit_normal_kw: float,
    no_export: bool,
    near_zero_tolerence_kw: float,
) -> float:
    if no_export:
        return 0.0
    if mode != PlanIntentMode.FORCE_DISCHARGE:
        return export_limit_normal_kw
    at_max_discharge = (
        max_discharge_kw is not None
        and ac_net_kw >= (max_discharge_kw - near_zero_tolerence_kw)
    )
    if at_max_discharge:
        return export_limit_normal_kw
    return min(export_limit_normal_kw, max(0.0, grid_export_kw))


def _clamp_kw(value: float, max_kw: float | None) -> float:
    clamped = max(0.0, float(value))
    if max_kw is not None:
        clamped = min(clamped, float(max_kw))
    return clamped


def _battery_full(
    battery_soc_pct: float | None,
    battery: BatteryComponentConfig | None,
) -> bool:
    if battery_soc_pct is None or battery is None:
        return False
    full_threshold = max(0.0, float(battery.max_soc_pct) - BATTERY_FULL_TOLERANCE_PCT)
    return float(battery_soc_pct) >= full_threshold
