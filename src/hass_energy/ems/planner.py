from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any, cast

import pulp

from hass_energy.ems.builder import MILPBuilder, MILPModel
from hass_energy.ems.horizon import Horizon, build_horizon
from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.models.config import AppConfig
from hass_energy.ems.models import (
    EconomicsTimestepPlan,
    EmsPlanOutput,
    EmsPlanTimings,
    EmsPlanStatus,
    EvTimestepPlan,
    GridTimestepPlan,
    InverterTimestepPlan,
    LoadsTimestepPlan,
    TimestepPlan,
)

logger = logging.getLogger(__name__)


class EmsMilpPlanner:
    def __init__(self, app_config: AppConfig, *, resolver: ValueResolver) -> None:
        self._app_config = app_config
        self._resolver = resolver
        self._last_timings: EmsPlanTimings | None = None

    def generate_ems_plan(
        self,
        *,
        now: datetime | None = None,
        solver_msg: bool = False,
    ) -> EmsPlanOutput:
        total_start = time.perf_counter()
        solve_time = now or datetime.now().astimezone()
        if solve_time.tzinfo is None:
            solve_time = solve_time.astimezone()
        horizon = build_horizon(self._app_config.ems, now=solve_time)

        builder = MILPBuilder(
            plant=self._app_config.plant,
            loads=self._app_config.loads,
            horizon=horizon,
            resolver=self._resolver,
        )
        build_start = time.perf_counter()
        model = builder.build()
        build_seconds = time.perf_counter() - build_start

        solve_start = time.perf_counter()
        model.problem.solve(pulp.PULP_CBC_CMD(msg=solver_msg))
        solve_seconds = time.perf_counter() - solve_start

        objective_value = _objective_value(model)
        status, timesteps = _extract_plan(model, horizon)
        total_seconds = time.perf_counter() - total_start
        timings = EmsPlanTimings(
            build_seconds=build_seconds,
            solve_seconds=solve_seconds,
            total_seconds=total_seconds,
        )
        self._last_timings = timings
        logger.info(
            "EMS plan timings: build=%.3fs solve=%.3fs total=%.3fs",
            build_seconds,
            solve_seconds,
            total_seconds,
        )
        return EmsPlanOutput(
            generated_at=solve_time.astimezone(timezone.utc),
            status=status,
            objective_value=objective_value,
            timings=timings,
            timesteps=timesteps,
        )

    @property
    def last_timings(self) -> EmsPlanTimings | None:
        return self._last_timings


def _extract_plan(
    model: MILPModel, horizon: Horizon
) -> tuple[EmsPlanStatus, list[TimestepPlan]]:
    status = cast(EmsPlanStatus, pulp.LpStatus.get(model.problem.status, "Unknown"))

    grid = model.grid
    inverters = model.inverters.inverters
    loads = model.loads

    cumulative_cost = 0.0
    timesteps: list[TimestepPlan] = []
    for t, slot in enumerate(horizon.slots):
        import_kw = _value(grid.P_import.get(t))
        export_kw = _value(grid.P_export.get(t))
        import_violation_kw = _value(grid.P_import_violation_kw.get(t))

        price_import_value = (
            float(grid.price_import[t]) if t < len(grid.price_import) else 0.0
        )
        price_export_value = (
            float(grid.price_export[t]) if t < len(grid.price_export) else 0.0
        )
        segment_cost = (
            (import_kw * price_import_value - export_kw * price_export_value)
            * slot.duration_h
        )
        cumulative_cost += segment_cost

        inverter_plans: dict[str, InverterTimestepPlan] = {}
        for key, inv in sorted(inverters.items()):
            pv_series = inv.P_pv_kw
            ac_net_series = inv.P_inv_ac_net_kw
            charge_series = inv.P_batt_charge_kw
            discharge_series = inv.P_batt_discharge_kw
            soc_series = inv.E_batt_kwh
            curtail_series = inv.Curtail_inv
            battery_soc_kwh = _value(soc_series.get(t)) if soc_series is not None else None
            battery_soc_pct = None
            if battery_soc_kwh is not None and inv.battery_capacity_kwh:
                battery_soc_pct = (battery_soc_kwh / float(inv.battery_capacity_kwh)) * 100.0
            inverter_plans[key] = InverterTimestepPlan(
                name=str(inv.name),
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
        for key, ev in sorted(loads.evs.items()):
            ev_series = ev.P_ev_charge_kw
            ev_soc_series = ev.E_ev_kwh
            connected = ev.connected
            ev_soc_kwh = _value(ev_soc_series.get(t)) if ev_soc_series is not None else 0.0
            ev_soc_pct = None
            if ev.capacity_kwh:
                ev_soc_pct = (ev_soc_kwh / float(ev.capacity_kwh)) * 100.0
            ev_plans[key] = EvTimestepPlan(
                name=str(ev.name),
                charge_kw=_value(ev_series.get(t)) if ev_series is not None else 0.0,
                soc_kwh=ev_soc_kwh,
                soc_pct=ev_soc_pct,
                connected=connected,
            )

        base_load_kw = (
            float(loads.base_load_kw[t]) if t < len(loads.base_load_kw) else 0.0
        )
        extra_load_kw = _value(loads.load_contribs.get(t))
        total_load_kw = base_load_kw + extra_load_kw

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
                        bool(grid.import_allowed[t]) if t < len(grid.import_allowed) else None
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

    return status, timesteps


def _value(var: Any) -> float:
    if var is None:
        return 0.0
    value = pulp.value(var)
    if value is None:
        return 0.0
    return float(value)


def _objective_value(model: MILPModel) -> float | None:
    value = pulp.value(model.problem.objective)
    if value is None:
        return None
    return float(value)
