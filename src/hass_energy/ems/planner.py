from __future__ import annotations

import logging
import math
import time
from datetime import UTC, datetime
from typing import get_args

import pulp

from hass_energy.ems.builder import MILPBuilder, MILPModel
from hass_energy.ems.horizon import Horizon, build_horizon
from hass_energy.ems.models import (
    EconomicsTimestepPlan,
    EmsPlanOutput,
    EmsPlanStatus,
    EmsPlanTimings,
    EvTimestepPlan,
    GridTimestepPlan,
    InverterTimestepPlan,
    LoadsTimestepPlan,
    TimestepPlan,
)
from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.models.config import AppConfig

logger = logging.getLogger(__name__)

_CURTAIL_POWER_THRESHOLD_KW = 0.01


def _derive_curtailment(
    curtail_power: dict[int, pulp.LpVariable] | None,
    t: int,
) -> bool | None:
    """Derive curtailment status from continuous power variable."""
    if curtail_power is None:
        return None
    val = curtail_power.get(t)
    if val is None:
        return None
    v = pulp.value(val)
    return v > _CURTAIL_POWER_THRESHOLD_KW if v is not None else None


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
        builder = MILPBuilder(
            plant=self._app_config.plant,
            loads=self._app_config.loads,
            resolver=self._resolver,
            ems_config=self._app_config.ems,
        )
        high_res_timestep = self._app_config.ems.high_res_timestep_minutes
        high_res_horizon = self._app_config.ems.high_res_horizon_minutes
        # Base interval used to size the forecast horizon and align forecasts into slots.
        base_interval_minutes = high_res_timestep or self._app_config.ems.timestep_minutes
        forecasts = builder.resolve_forecasts(
            now=solve_time,
            interval_minutes=base_interval_minutes,
        )
        horizon_intervals = self._validate_min_horizon_intervals(
            forecasts.min_coverage_intervals,
            base_interval_minutes,
        )
        total_minutes = horizon_intervals * base_interval_minutes
        horizon = build_horizon(
            now=solve_time,
            timestep_minutes=self._app_config.ems.timestep_minutes,
            num_intervals=horizon_intervals,
            high_res_timestep_minutes=high_res_timestep,
            high_res_horizon_minutes=high_res_horizon,
            total_minutes=total_minutes,
        )
        schedule_info = _format_schedule(
            high_res_timestep,
            high_res_horizon,
            self._app_config.ems.timestep_minutes,
        )
        horizon_msg = (
            "EMS horizon: intervals=%s base_interval_minutes=%s total_minutes=%s "
            "start=%s schedule=%s"
        )
        logger.info(
            horizon_msg,
            horizon.num_intervals,
            base_interval_minutes,
            total_minutes,
            horizon.start.isoformat(),
            schedule_info,
        )
        build_start = time.perf_counter()
        model = builder.build(horizon=horizon, forecasts=forecasts)
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
            generated_at=solve_time.astimezone(UTC),
            status=status,
            objective_value=objective_value,
            timings=timings,
            timesteps=timesteps,
        )

    @property
    def last_timings(self) -> EmsPlanTimings | None:
        return self._last_timings

    def _validate_min_horizon_intervals(
        self,
        min_coverage_intervals: int,
        base_interval_minutes: int,
    ) -> int:
        min_minutes = self._app_config.ems.min_horizon_minutes
        min_intervals = math.ceil(min_minutes / base_interval_minutes)
        if min_coverage_intervals < min_intervals:
            coverage_minutes = min_coverage_intervals * base_interval_minutes
            raise ValueError(
                "Shortest forecast horizon "
                f"({min_coverage_intervals} intervals, {coverage_minutes} minutes) "
                f"is below min_horizon_minutes={min_minutes}"
            )
        return min_coverage_intervals


_VALID_STATUSES: frozenset[str] = frozenset(get_args(EmsPlanStatus))


def _map_status(status_text: str) -> EmsPlanStatus:
    if status_text in _VALID_STATUSES:
        return status_text  # type: ignore[return-value]
    return "Unknown"


def _extract_plan(model: MILPModel, horizon: Horizon) -> tuple[EmsPlanStatus, list[TimestepPlan]]:
    status_text = pulp.LpStatus.get(model.problem.status, "Unknown")
    status = _map_status(status_text)

    grid = model.grid
    inverters = model.inverters.inverters
    loads = model.loads

    cumulative_cost = 0.0
    timesteps: list[TimestepPlan] = []
    for t, slot in enumerate(horizon.slots):
        import_kw = _value(grid.P_import.get(t))
        export_kw = _value(grid.P_export.get(t))
        import_violation_kw = _value(grid.P_import_violation_kw.get(t))

        price_import_value = float(grid.price_import[t]) if t < len(grid.price_import) else 0.0
        price_export_value = float(grid.price_export[t]) if t < len(grid.price_export) else 0.0
        segment_cost = (
            import_kw * price_import_value - export_kw * price_export_value
        ) * slot.duration_h
        cumulative_cost += segment_cost

        inverter_plans: dict[str, InverterTimestepPlan] = {}
        for key, inv in sorted(inverters.items()):
            pv_series = inv.P_pv_kw
            ac_net_series = inv.P_inv_ac_net_kw
            charge_series = inv.P_batt_charge_kw
            discharge_series = inv.P_batt_discharge_kw
            soc_series = inv.E_batt_kwh
            curtail_power_series = inv.P_curtail_kw
            battery_soc_kwh = _value(soc_series.get(t)) if soc_series is not None else None
            battery_soc_pct = None
            if battery_soc_kwh is not None and inv.battery_capacity_kwh:
                battery_soc_pct = (battery_soc_kwh / float(inv.battery_capacity_kwh)) * 100.0
            curtail_kw_val = _value(curtail_power_series.get(t)) if curtail_power_series else None
            inverter_plans[key] = InverterTimestepPlan(
                name=str(inv.name),
                pv_kw=_value(pv_series.get(t)) if pv_series is not None else None,
                pv_curtail_kw=curtail_kw_val,
                ac_net_kw=_value(ac_net_series.get(t)),
                battery_charge_kw=(
                    _value(charge_series.get(t)) if charge_series is not None else None
                ),
                battery_discharge_kw=(
                    _value(discharge_series.get(t)) if discharge_series is not None else None
                ),
                battery_soc_kwh=battery_soc_kwh,
                battery_soc_pct=battery_soc_pct,
                curtailment=_derive_curtailment(curtail_power_series, t),
            )

        ev_plans: dict[str, EvTimestepPlan] = {}
        for key, ev in sorted(loads.evs.items()):
            ev_series = ev.P_ev_charge_kw
            ev_soc_series = ev.E_ev_kwh
            connected = ev.connected
            ev_soc_kwh = _value(ev_soc_series.get(t))
            ev_soc_pct = None
            if ev.capacity_kwh:
                ev_soc_pct = (ev_soc_kwh / float(ev.capacity_kwh)) * 100.0
            ev_plans[key] = EvTimestepPlan(
                name=str(ev.name),
                charge_kw=_value(ev_series.get(t)),
                soc_kwh=ev_soc_kwh,
                soc_pct=ev_soc_pct,
                connected=connected,
            )

        base_load_kw = float(loads.base_load_kw[t]) if t < len(loads.base_load_kw) else 0.0
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


def _value(var: pulp.LpVariable | pulp.LpAffineExpression | None) -> float:
    if var is None:
        return 0.0
    v = pulp.value(var)
    if v is None:
        return 0.0
    return float(v)


def _objective_value(model: MILPModel) -> float | None:
    v = pulp.value(model.problem.objective)
    if v is None:
        return None
    return float(v)


def _format_schedule(
    high_res_interval: int | None,
    high_res_horizon: int | None,
    timestep_minutes: int,
) -> str:
    if high_res_interval is None or high_res_horizon is None:
        return f"{timestep_minutes}m/rest"
    return f"{high_res_interval}m/{high_res_horizon}m, {timestep_minutes}m/rest"
