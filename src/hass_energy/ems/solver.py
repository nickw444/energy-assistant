from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pulp

from hass_energy.ems.builder import MILPBuilder
from hass_energy.ems.horizon import Horizon, build_horizon
from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.models.config import AppConfig


def solve_once(
    app_config: AppConfig,
    *,
    resolver: ValueResolver | None = None,
    now: datetime | None = None,
    solver_msg: bool = False,
) -> dict[str, Any]:
    if resolver is None:
        raise ValueError("resolver is required")

    solve_time = now or datetime.now().astimezone()
    horizon = build_horizon(app_config.ems, app_config.plant, now=solve_time)

    builder = MILPBuilder(
        plant=app_config.plant,
        loads=app_config.loads,
        horizon=horizon,
        resolver=resolver,
    )
    model = builder.build()

    model.problem.solve(pulp.PULP_CBC_CMD(msg=solver_msg))

    return _extract_plan(model, horizon)


def _extract_plan(model: Any, horizon: Horizon) -> dict[str, Any]:
    status = pulp.LpStatus.get(model.problem.status, "Unknown")
    objective = pulp.value(model.problem.objective)

    vars = model.vars
    series = model.series
    P_import = vars.P_grid_import
    P_export = vars.P_grid_export
    P_import_violation = vars.P_grid_import_violation_kw
    P_inv_ac = vars.P_inv_ac
    load_kw = series.load_kw
    price_import = series.price_import
    price_export = series.price_export
    Curtail_inv = vars.Curtail_inv

    cumulative_cost = 0.0
    slots: list[dict[str, Any]] = []
    for t, slot in enumerate(horizon.slots):
        import_kw = _value(P_import.get(t))
        export_kw = _value(P_export.get(t))
        import_violation_kw = _value(P_import_violation.get(t))

        pv_inverters: dict[str, float] = {}
        for name, series in P_inv_ac.items():
            pv_inverters[str(name)] = _value(series.get(t))

        pv_kw = sum(pv_inverters.values())

        pv_available_inverters = dict(pv_inverters)
        pv_available_kw = pv_kw
        price_import_value = float(price_import[t]) if t < len(price_import) else 0.0
        price_export_value = float(price_export[t]) if t < len(price_export) else 0.0
        segment_cost = (
            (import_kw * price_import_value - export_kw * price_export_value) * slot.duration_h
        )
        cumulative_cost += segment_cost
        curtail_inverters: dict[str, bool] = {}
        for inv_name, series in Curtail_inv.items():
            curtail_inverters[str(inv_name)] = _value(series.get(t)) > 0.5

        slots.append(
            {
                "index": t,
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
                "duration_h": slot.duration_h,
                "grid_import_kw": import_kw,
                "grid_export_kw": export_kw,
                "grid_import_violation_kw": import_violation_kw,
                "grid_kw": import_kw - export_kw,
                "load_kw": float(load_kw[t]) if t < len(load_kw) else 0.0,
                "price_import": price_import_value,
                "price_export": price_export_value,
                "segment_cost": segment_cost,
                "cumulative_cost": cumulative_cost,
                "pv_kw": pv_kw,
                "pv_available_kw": pv_available_kw,
                "pv_inverters": pv_inverters,
                "pv_inverters_available": pv_available_inverters,
                "curtail_inverters": curtail_inverters,
                "curtail_any": any(curtail_inverters.values()),
                "import_allowed": bool(horizon.import_allowed[t]),
            }
        )

    return {
        "generated_at": time.time(),
        "status": status,
        "objective": objective,
        "slots": slots,
    }


def _value(var: Any) -> float:
    if var is None:
        return 0.0
    value = pulp.value(var)
    if value is None:
        return 0.0
    return float(value)
