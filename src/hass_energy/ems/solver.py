from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pulp

from hass_energy.ems.builder import MILPBuilder
from hass_energy.ems.horizon import Horizon, build_horizon
from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.models.config import AppConfig
from hass_energy.ems.models import (
    EconomicsTimestepPlan,
    EmsPlanOutput,
    EvTimestepPlan,
    GridTimestepPlan,
    InverterTimestepPlan,
    LoadsTimestepPlan,
    TimestepPlan,
)
from hass_energy.lib.slug import slug_map


def solve_once(
    app_config: AppConfig,
    *,
    resolver: ValueResolver | None = None,
    now: datetime | None = None,
    solver_msg: bool = False,
) -> EmsPlanOutput:
    if resolver is None:
        raise ValueError("resolver is required")

    solve_time = now or datetime.now().astimezone()
    if solve_time.tzinfo is None:
        solve_time = solve_time.astimezone()
    horizon = build_horizon(app_config.ems, now=solve_time)

    builder = MILPBuilder(
        plant=app_config.plant,
        loads=app_config.loads,
        horizon=horizon,
        resolver=resolver,
    )
    model = builder.build()

    model.problem.solve(pulp.PULP_CBC_CMD(msg=solver_msg))

    return _extract_plan(model, horizon, generated_at=solve_time)


def _extract_plan(model: Any, horizon: Horizon, *, generated_at: datetime) -> EmsPlanOutput:
    status = pulp.LpStatus.get(model.problem.status, "Unknown")

    vars = model.vars
    model_series = model.series
    P_import = vars.P_grid_import
    P_export = vars.P_grid_export
    P_import_violation = vars.P_grid_import_violation_kw
    P_pv_kw = vars.P_pv_kw
    P_inv_ac_net_kw = vars.P_inv_ac_net_kw
    P_batt_charge = vars.P_batt_charge_kw
    P_batt_discharge = vars.P_batt_discharge_kw
    E_batt_kwh = vars.E_batt_kwh
    P_ev_charge = vars.P_ev_charge_kw
    E_ev_kwh = vars.E_ev_kwh
    load_kw = model_series.load_kw
    price_import = model_series.price_import
    price_export = model_series.price_export
    Curtail_inv = vars.Curtail_inv
    import_allowed = model_series.import_allowed

    inverter_names = {
        *P_inv_ac_net_kw.keys(),
        *P_pv_kw.keys(),
        *P_batt_charge.keys(),
        *P_batt_discharge.keys(),
        *E_batt_kwh.keys(),
        *Curtail_inv.keys(),
    }
    ev_names = {
        *P_ev_charge.keys(),
        *E_ev_kwh.keys(),
        *model_series.ev_connected.keys(),
    }
    inverter_keys = slug_map(inverter_names, fallback="inverter")
    ev_keys = slug_map(ev_names, fallback="ev")
    inverter_capacities = model_series.battery_capacity_kwh
    ev_capacities = model_series.ev_capacity_kwh

    cumulative_cost = 0.0
    timesteps: list[TimestepPlan] = []
    for t, slot in enumerate(horizon.slots):
        import_kw = _value(P_import.get(t))
        export_kw = _value(P_export.get(t))
        import_violation_kw = _value(P_import_violation.get(t))

        price_import_value = float(price_import[t]) if t < len(price_import) else 0.0
        price_export_value = float(price_export[t]) if t < len(price_export) else 0.0
        segment_cost = (
            (import_kw * price_import_value - export_kw * price_export_value) * slot.duration_h
        )
        cumulative_cost += segment_cost

        inverter_plans: dict[str, InverterTimestepPlan] = {}
        for name in sorted(inverter_names):
            key = inverter_keys.get(name, str(name))
            pv_series = P_pv_kw.get(name)
            ac_net_series = P_inv_ac_net_kw.get(name)
            charge_series = P_batt_charge.get(name)
            discharge_series = P_batt_discharge.get(name)
            soc_series = E_batt_kwh.get(name)
            curtail_series = Curtail_inv.get(name)
            battery_soc_kwh = _value(soc_series.get(t)) if soc_series is not None else None
            battery_soc_pct = None
            if battery_soc_kwh is not None:
                capacity_kwh = inverter_capacities.get(name)
                if capacity_kwh:
                    battery_soc_pct = (battery_soc_kwh / float(capacity_kwh)) * 100.0
            inverter_plans[key] = InverterTimestepPlan(
                name=str(name),
                pv_kw=_value(pv_series.get(t)) if pv_series is not None else None,
                ac_net_kw=_value(ac_net_series.get(t)) if ac_net_series is not None else 0.0,
                battery_charge_kw=(
                    _value(charge_series.get(t)) if charge_series is not None else None
                ),
                battery_discharge_kw=(
                    _value(discharge_series.get(t)) if discharge_series is not None else None
                ),
                battery_soc_kwh=battery_soc_kwh,
                battery_soc_pct=battery_soc_pct,
                curtailment=(
                    _value(curtail_series.get(t)) > 0.5 if curtail_series is not None else None
                ),
            )

        ev_plans: dict[str, EvTimestepPlan] = {}
        for name in sorted(ev_names):
            key = ev_keys.get(name, str(name))
            ev_series = P_ev_charge.get(name)
            ev_soc_series = E_ev_kwh.get(name)
            connected = bool(model_series.ev_connected.get(name, False))
            ev_soc_kwh = _value(ev_soc_series.get(t)) if ev_soc_series is not None else 0.0
            ev_soc_pct = None
            capacity_kwh = ev_capacities.get(name)
            if capacity_kwh:
                ev_soc_pct = (ev_soc_kwh / float(capacity_kwh)) * 100.0
            ev_plans[key] = EvTimestepPlan(
                name=str(name),
                charge_kw=_value(ev_series.get(t)) if ev_series is not None else 0.0,
                soc_kwh=ev_soc_kwh,
                soc_pct=ev_soc_pct,
                connected=connected,
            )

        base_load_kw = float(load_kw[t]) if t < len(load_kw) else 0.0
        total_load_kw = base_load_kw + sum(ev.charge_kw for ev in ev_plans.values())

        timesteps.append(
            TimestepPlan(
                index=t,
                start=slot.start,
                end=slot.end,
                duration_s=(slot.end - slot.start).total_seconds(),
                grid=GridTimestepPlan(
                    import_kw=import_kw,
                    export_kw=export_kw,
                    net_kw=import_kw - export_kw,
                    import_allowed=(
                        bool(import_allowed[t]) if t < len(import_allowed) else None
                    ),
                    import_violation_kw=import_violation_kw,
                ),
                inverters=inverter_plans,
                loads=LoadsTimestepPlan(
                    base_kw=base_load_kw,
                    evs=ev_plans,
                    total_kw=total_load_kw,
                ),
                economics=EconomicsTimestepPlan(
                    price_import=price_import_value,
                    price_export=price_export_value,
                    segment_cost=segment_cost,
                    cumulative_cost=cumulative_cost,
                ),
            )
        )

    return EmsPlanOutput(
        generated_at=generated_at.astimezone(timezone.utc),
        status=status,
        timesteps=timesteps,
    )


def _value(var: Any) -> float:
    if var is None:
        return 0.0
    value = pulp.value(var)
    if value is None:
        return 0.0
    return float(value)
