from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import pulp

from hass_energy.ems.forecast_alignment import (
    PowerForecastAligner,
    PriceForecastAligner,
    forecast_coverage_slots,
)
from hass_energy.ems.horizon import Horizon, floor_to_interval_boundary
from hass_energy.ems.models import ResolvedForecasts
from hass_energy.lib.source_resolver.models import PowerForecastInterval
from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.models.loads import ControlledEvLoad, LoadConfig, NonVariableLoad
from hass_energy.models.plant import PlantConfig, TimeWindow

_EV_RAMP_PENALTY_COST = 1e-4
_EV_ANCHOR_PENALTY_COST = 0.05
_EV_ANCHOR_ACTIVE_THRESHOLD_KW = 0.1
_NEGATIVE_EXPORT_PRICE_THRESHOLD = -1e-9

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GridBuild:
    # Grid import decision variables per timestep t in Horizon.T.
    P_import: dict[int, pulp.LpVariable]
    # Grid export decision variables per timestep t in Horizon.T.
    P_export: dict[int, pulp.LpVariable]
    # Import violation slack per timestep t (used when imports are forbidden).
    P_import_violation_kw: dict[int, pulp.LpVariable]
    # Import price series aligned to Horizon.T (slot 0 may be realtime override).
    price_import: list[float]
    # Export price series aligned to Horizon.T (slot 0 may be realtime override).
    price_export: list[float]
    # Import permission flags aligned to Horizon.T from forbidden time windows.
    import_allowed: list[bool]


@dataclass(slots=True)
class InverterVars:
    # Human-readable inverter name from config.
    name: str
    # Battery capacity for SoC normalization (None if no battery).
    battery_capacity_kwh: float | None
    # PV output variables per timestep t in Horizon.T (None if no PV).
    P_pv_kw: dict[int, pulp.LpVariable] | None
    # Net AC power variables per timestep t in Horizon.T.
    P_inv_ac_net_kw: dict[int, pulp.LpVariable]
    # Battery charge power variables per timestep t (None if no battery).
    P_batt_charge_kw: dict[int, pulp.LpVariable] | None
    # Battery discharge power variables per timestep t (None if no battery).
    P_batt_discharge_kw: dict[int, pulp.LpVariable] | None
    # Battery SoC variables at slot boundaries (indexed 0..N).
    E_batt_kwh: dict[int, pulp.LpVariable] | None
    # Curtailment binary variables per timestep t (None if curtailment disabled).
    Curtail_inv: dict[int, pulp.LpVariable] | None


@dataclass(slots=True)
class InverterBuild:
    # Inverter vars keyed by inverter id from config.
    inverters: dict[str, InverterVars]


@dataclass(slots=True)
class EvVars:
    # Human-readable load name from config.
    name: str
    # EV battery capacity for SoC normalization.
    capacity_kwh: float
    # Connectivity flag resolved at solve time (applies to all timesteps).
    connected: bool
    # EV charge power variables per timestep t in Horizon.T.
    P_ev_charge_kw: dict[int, pulp.LpVariable]
    # EV SoC variables at slot boundaries (indexed 0..N).
    E_ev_kwh: dict[int, pulp.LpVariable]
    # EV charge ramp magnitude per timestep t (t>0 has ramp constraints).
    Ev_charge_ramp_kw: dict[int, pulp.LpVariable]
    # EV anchor deviation variable for slot 0 vs realtime power.
    Ev_charge_anchor_kw: pulp.LpVariable


@dataclass(slots=True)
class LoadBuild:
    # Baseline plant load series aligned to Horizon.T.
    base_load_kw: list[float]
    # Controllable load contributions per timestep t in Horizon.T.
    load_contribs: dict[int, pulp.LpAffineExpression]
    # EV vars keyed by load id from config.
    evs: dict[str, EvVars]
    # EV SoC incentive segments keyed by load id.
    ev_incentive_segments: dict[str, list[tuple[pulp.LpVariable, float]]]


@dataclass(slots=True)
class MILPModel:
    problem: pulp.LpProblem
    grid: GridBuild
    inverters: InverterBuild
    loads: LoadBuild


class MILPBuilder:
    def __init__(
        self,
        plant: PlantConfig,
        loads: list[LoadConfig],
        resolver: ValueResolver,
    ):
        self._plant = plant
        self._loads = loads
        self._resolver = resolver
        self._power_aligner = PowerForecastAligner()
        self._price_aligner = PriceForecastAligner()

    def resolve_forecasts(
        self,
        *,
        now: datetime,
        interval_minutes: int,
    ) -> ResolvedForecasts:
        start = floor_to_interval_boundary(now, interval_minutes)

        load_forecast = self._plant.load.forecast
        load_intervals = self._resolver.resolve(load_forecast)
        price_import_intervals = self._resolver.resolve(
            self._plant.grid.price_import_forecast
        )
        price_export_intervals = self._resolver.resolve(
            self._plant.grid.price_export_forecast
        )

        coverage_by_series: dict[str, int] = {}
        coverage_by_series["load"] = forecast_coverage_slots(
            start,
            interval_minutes,
            load_intervals,
            allow_first_slot_missing=True,
        )
        coverage_by_series["price_import"] = forecast_coverage_slots(
            start,
            interval_minutes,
            price_import_intervals,
            allow_first_slot_missing=True,
        )
        coverage_by_series["price_export"] = forecast_coverage_slots(
            start,
            interval_minutes,
            price_export_intervals,
            allow_first_slot_missing=True,
        )

        inverter_forecasts: dict[str, list[PowerForecastInterval]] = {}
        for inverter in self._plant.inverters:
            pv_intervals = self._resolver.resolve(inverter.pv.forecast)
            allow_first_slot_missing = False
            if inverter.pv.realtime_power is not None:
                allow_first_slot_missing = True
            inverter_forecasts[inverter.id] = pv_intervals
            coverage_by_series[f"pv:{inverter.id}"] = forecast_coverage_slots(
                start,
                interval_minutes,
                pv_intervals,
                allow_first_slot_missing=allow_first_slot_missing,
            )

        if not coverage_by_series:
            raise ValueError("No forecasts available to determine planning horizon")

        min_coverage = min(coverage_by_series.values())
        limiting = sorted(
            name for name, length in coverage_by_series.items() if length == min_coverage
        )
        coverage_summary = ", ".join(
            f"{name}={length}" for name, length in sorted(coverage_by_series.items())
        )
        logger.info(
            "Forecast coverage (intervals): %s; limiting=%s",
            coverage_summary,
            ", ".join(limiting),
        )
        return ResolvedForecasts(
            grid_price_import=price_import_intervals,
            grid_price_export=price_export_intervals,
            load=load_intervals,
            inverters_pv=inverter_forecasts,
            min_coverage_intervals=min_coverage,
        )

    def build(self, *, horizon: Horizon, forecasts: ResolvedForecasts) -> MILPModel:
        problem = pulp.LpProblem("ems_optimisation", pulp.LpMinimize)
        realtime_load = self._resolver.resolve(self._plant.load.realtime_load_power)
        base_load_kw = self._power_aligner.align(
            horizon,
            forecasts.load,
            first_slot_override=realtime_load,
        )
        grid = self._build_grid(problem, horizon, forecasts)
        inverters = self._build_inverters(problem, grid, horizon, forecasts)
        loads = self._build_loads(problem, horizon, base_load_kw)
        self._build_ac_balance(problem, grid, inverters, loads, horizon)
        self._build_battery_export_price_limit(problem, grid, inverters, horizon)
        self._build_objective(problem, grid, inverters, loads, horizon)

        return MILPModel(problem, grid, inverters, loads)

    def _build_grid(
        self,
        problem: pulp.LpProblem,
        horizon: Horizon,
        forecasts: ResolvedForecasts,
    ) -> GridBuild:
        T = horizon.T
        cfg = self._plant.grid

        P_import = pulp.LpVariable.dicts(
            "P_grid_import", T, lowBound=0, upBound=cfg.max_import_kw
        )
        P_export = pulp.LpVariable.dicts(
            "P_grid_export", T, lowBound=0, upBound=cfg.max_export_kw
        )
        P_import_violation_kw = pulp.LpVariable.dicts(
            "P_grid_import_violation_kw",
            T,
            lowBound=0,
        )
        grid_import_on = pulp.LpVariable.dicts(
            "Grid_import_on",
            T,
            lowBound=0,
            upBound=1,
            cat="Binary",
        )
        import_allowed = self._resolve_import_allowed(horizon)

        for t in T:
            # Prevent simultaneous grid import/export by selecting an import or export mode.
            problem += (
                P_import[t] <= cfg.max_import_kw * grid_import_on[t],
                f"grid_import_exclusive_t{t}",
            )
            problem += (
                P_export[t] <= cfg.max_export_kw * (1 - grid_import_on[t]),
                f"grid_export_exclusive_t{t}",
            )
            # Enforce the per-slot import cap. When imports are forbidden, the RHS becomes 0,
            # so only the violation variable can satisfy the constraint. It is heavily penalized
            # in the objective, keeping the model feasible while discouraging forbidden imports.
            problem += (
                P_import[t]
                <= cfg.max_import_kw * float(import_allowed[t]) + P_import_violation_kw[t],
                f"grid_import_forbidden_or_violation_t{t}",
            )

        realtime_import = self._resolver.resolve(self._plant.grid.realtime_price_import)
        realtime_export = self._resolver.resolve(self._plant.grid.realtime_price_export)
        price_import = self._price_aligner.align(
            horizon,
            forecasts.grid_price_import,
            first_slot_override=realtime_import,
        )
        price_export = self._price_aligner.align(
            horizon,
            forecasts.grid_price_export,
            first_slot_override=realtime_export,
        )

        return GridBuild(
            P_import=P_import,
            P_export=P_export,
            P_import_violation_kw=P_import_violation_kw,
            price_import=price_import,
            price_export=price_export,
            import_allowed=import_allowed,
        )

    def _build_inverters(
        self,
        problem: pulp.LpProblem,
        grid: GridBuild,
        horizon: Horizon,
        forecasts: ResolvedForecasts,
    ) -> InverterBuild:
        T = horizon.T
        price_export = grid.price_export
        inverters: dict[str, InverterVars] = {}

        for inverter in self._plant.inverters:
            inv_name = inverter.name
            inv_id = inverter.id
            pv_intervals = forecasts.inverters_pv[inv_id]
            realtime_pv = None
            if inverter.pv.realtime_power is not None:
                realtime_pv = self._resolver.resolve(inverter.pv.realtime_power)

            pv_kw = pulp.LpVariable.dicts(
                f"P_pv_{inv_id}_kw",
                T,
                lowBound=0,
                upBound=inverter.peak_power_kw,
            )
            inv_ac_net_kw = pulp.LpVariable.dicts(
                f"P_inv_{inv_id}_ac_net_kw",
                T,
                lowBound=-inverter.peak_power_kw,
                upBound=inverter.peak_power_kw,
            )

            pv_available_kw_series = self._power_aligner.align(
                horizon,
                pv_intervals,
                first_slot_override=realtime_pv,
            )
            # Clamp to [0, peak_power_kw] to avoid infeasible PV generation.
            pv_available_kw_series = [
                max(0.0, min(float(value), inverter.peak_power_kw))
                for value in pv_available_kw_series
            ]

            curtailment = inverter.curtailment
            curtail_vars: dict[int, pulp.LpVariable] | None = None
            if curtailment is None:
                for t in T:
                    problem += (
                        # No curtailment: inverter AC output must equal available PV.
                        pv_kw[t] == pv_available_kw_series[t],
                        f"inverter_pv_total_{inv_id}_t{t}",
                    )
            else:
                curtail = pulp.LpVariable.dicts(
                    f"Curtail_inv_{inv_id}",
                    T,
                    lowBound=0,
                    upBound=1,
                    cat="Binary",
                )
                curtail_vars = curtail
                for t in T:
                    if curtailment == "binary":
                        problem += (
                            # Binary curtailment: either full PV or fully off.
                            pv_kw[t] == pv_available_kw_series[t] * (1 - curtail[t]),
                            f"inverter_pv_binary_{inv_id}_t{t}",
                        )
                    else:
                        problem += (
                            # Load-aware: output cannot exceed available PV.
                            pv_kw[t] <= pv_available_kw_series[t],
                            f"inverter_pv_max_{inv_id}_t{t}",
                        )
                        problem += (
                            # Load-aware: curtail flag reduces minimum output (allows export block).
                            pv_kw[t]
                            >= pv_available_kw_series[t] * (1 - curtail[t]),
                            f"inverter_pv_min_{inv_id}_t{t}",
                        )
                        problem += (
                            # Load-aware: when curtailing, block grid export.
                            grid.P_export[t]
                            <= self._plant.grid.max_export_kw * (1 - curtail[t]),
                            f"inverter_export_block_{inv_id}_t{t}",
                        )
                        if float(price_export[t]) < _NEGATIVE_EXPORT_PRICE_THRESHOLD:
                            # Negative export prices always activate load-following curtailment.
                            # We want PV to drop to match load and prevent any export,
                            # even when PV is already below load (which keeps curtail off).
                            problem += (
                                curtail[t] == 1,
                                f"inverter_curtail_neg_export_{inv_id}_t{t}",
                            )

            battery = inverter.battery
            if battery is None:
                for t in T:
                    problem += (
                        # Net AC flow equals PV output when no battery is present.
                        inv_ac_net_kw[t] == pv_kw[t],
                        f"inverter_ac_net_{inv_id}_t{t}",
                    )
                inverters[inv_id] = InverterVars(
                    name=inv_name,
                    battery_capacity_kwh=None,
                    P_pv_kw=pv_kw,
                    P_inv_ac_net_kw=inv_ac_net_kw,
                    P_batt_charge_kw=None,
                    P_batt_discharge_kw=None,
                    E_batt_kwh=None,
                    Curtail_inv=curtail_vars,
                )
                continue

            battery_capacity_kwh = float(battery.capacity_kwh)
            charge_limit = (
                battery.max_charge_kw
                if battery.max_charge_kw is not None
                else inverter.peak_power_kw
            )
            discharge_limit = (
                battery.max_discharge_kw
                if battery.max_discharge_kw is not None
                else inverter.peak_power_kw
            )
            discharge_limit = min(discharge_limit, inverter.peak_power_kw)

            soc_min_pct = battery.min_soc_pct
            soc_min_kwh = battery.capacity_kwh * soc_min_pct / 100.0
            soc_max_kwh = battery.capacity_kwh * battery.max_soc_pct / 100.0
            reserve_kwh = battery.capacity_kwh * battery.reserve_soc_pct / 100.0
            storage_efficiency = battery.storage_efficiency_pct / 100.0

            P_batt_charge = pulp.LpVariable.dicts(
                f"P_batt_{inv_id}_charge_kw",
                T,
                lowBound=0,
                upBound=charge_limit,
            )
            P_batt_discharge = pulp.LpVariable.dicts(
                f"P_batt_{inv_id}_discharge_kw",
                T,
                lowBound=0,
                upBound=discharge_limit,
            )
            batt_charge_mode = pulp.LpVariable.dicts(
                f"Batt_{inv_id}_charge_mode",
                T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
            # SoC is defined at slot boundaries, so we need N+1 points for N intervals.
            soc_indices = range(horizon.num_intervals + 1)
            E_batt_kwh = pulp.LpVariable.dicts(
                f"E_batt_{inv_id}_kwh",
                soc_indices,
                lowBound=soc_min_kwh,
                upBound=soc_max_kwh,
            )
            export_ok = pulp.LpVariable.dicts(
                f"Export_ok_{inv_id}",
                T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
            export_soc_m = soc_max_kwh - soc_min_kwh

            initial_soc_pct = self._resolver.resolve(battery.state_of_charge_pct)
            initial_soc_kwh = battery.capacity_kwh * float(initial_soc_pct) / 100.0
            problem += (
                E_batt_kwh[0] == initial_soc_kwh,
                f"batt_soc_initial_{inv_id}",
            )
            problem += (
                E_batt_kwh[horizon.num_intervals] >= initial_soc_kwh,
                f"batt_soc_terminal_{inv_id}",
            )

            for t in T:
                # Block grid export unless battery stays above reserve SoC for this slot.
                problem += (
                    E_batt_kwh[t]
                    >= reserve_kwh - export_soc_m * (1 - export_ok[t]),
                    f"batt_export_reserve_start_{inv_id}_t{t}",
                )
                problem += (
                    E_batt_kwh[t + 1]
                    >= reserve_kwh - export_soc_m * (1 - export_ok[t]),
                    f"batt_export_reserve_end_{inv_id}_t{t}",
                )
                problem += (
                    grid.P_export[t]
                    <= self._plant.grid.max_export_kw * export_ok[t],
                    f"grid_export_reserve_{inv_id}_t{t}",
                )
                # Select charge vs discharge mode (idle is allowed in either mode).
                problem += (
                    P_batt_charge[t] <= charge_limit * batt_charge_mode[t],
                    f"batt_charge_limit_{inv_id}_t{t}",
                )
                problem += (
                    P_batt_discharge[t] <= discharge_limit * (1 - batt_charge_mode[t]),
                    f"batt_discharge_limit_{inv_id}_t{t}",
                )
                # Net AC flow combines PV and battery charge/discharge.
                problem += (
                    inv_ac_net_kw[t]
                    == pv_kw[t] + P_batt_discharge[t] - P_batt_charge[t],
                    f"inverter_ac_net_{inv_id}_t{t}",
                )
                # Battery energy balance with storage efficiency.
                problem += (
                    E_batt_kwh[t + 1]
                    == E_batt_kwh[t]
                    + (
                        P_batt_charge[t] * storage_efficiency
                        - P_batt_discharge[t] / storage_efficiency
                    )
                    * horizon.dt_hours(t),
                    f"batt_soc_step_{inv_id}_t{t}",
                )

            inverters[inv_id] = InverterVars(
                name=inv_name,
                battery_capacity_kwh=battery_capacity_kwh,
                P_pv_kw=pv_kw,
                P_inv_ac_net_kw=inv_ac_net_kw,
                P_batt_charge_kw=P_batt_charge,
                P_batt_discharge_kw=P_batt_discharge,
                E_batt_kwh=E_batt_kwh,
                Curtail_inv=curtail_vars,
            )

        return InverterBuild(inverters=inverters)

    def _build_ac_balance(
        self,
        problem: pulp.LpProblem,
        grid: GridBuild,
        inverters: InverterBuild,
        loads: LoadBuild,
        horizon: Horizon,
    ) -> None:
        P_import = grid.P_import
        P_export = grid.P_export
        inverter_values = inverters.inverters.values()

        for t in horizon.T:
            inv_total = pulp.lpSum(inv.P_inv_ac_net_kw[t] for inv in inverter_values)
            extra_load = loads.load_contribs.get(t, 0.0)
            base_load = float(loads.base_load_kw[t]) if t < len(loads.base_load_kw) else 0.0
            problem += (
                P_import[t] + inv_total - P_export[t] == base_load + extra_load,
                f"ac_balance_t{t}",
            )

    def _build_battery_export_price_limit(
        self,
        problem: pulp.LpProblem,
        grid: GridBuild,
        inverters: InverterBuild,
        horizon: Horizon,
    ) -> None:
        min_price = self._plant.grid.min_battery_export_price
        if min_price is None:
            return
        inverter_values = list(inverters.inverters.values())
        for t in horizon.T:
            if float(grid.price_export[t]) < min_price:
                pv_total = pulp.lpSum(
                    inv.P_pv_kw[t] for inv in inverter_values if inv.P_pv_kw is not None
                )
                charge_total = pulp.lpSum(
                    inv.P_batt_charge_kw[t]
                    for inv in inverter_values
                    if inv.P_batt_charge_kw is not None
                )
                problem += (
                    grid.P_export[t] <= pv_total - charge_total,
                    f"grid_export_price_floor_t{t}",
                )

    def _build_objective(
        self,
        problem: pulp.LpProblem,
        grid: GridBuild,
        inverters: InverterBuild,
        loads: LoadBuild,
        horizon: Horizon,
    ) -> None:
        P_import = grid.P_import
        P_export = grid.P_export
        P_import_violation = grid.P_import_violation_kw
        price_import = grid.price_import
        price_export = grid.price_export
        inverter_by_id = inverters.inverters
        ev_by_id = loads.evs

        # Price-aware objective: minimize net cost (import cost minus export revenue).
        # When export price is exactly zero, add a tiny bonus to prefer exporting over curtailment.
        export_bonus = 1e-4
        objective = pulp.lpSum(
            (
                P_import[t] * float(price_import[t])
                - P_export[t]
                * (
                    export_bonus
                    if abs(float(price_export[t])) <= 1e-9
                    else float(price_export[t])
                )
            )
            * horizon.dt_hours(t)
            for t in horizon.T
        )
        w_violation = 1e3
        # Penalize forbidden import violations to keep solutions feasible but discouraged.
        objective += pulp.lpSum(
            w_violation * P_import_violation[t] * horizon.dt_hours(t) for t in horizon.T
        )
        # Tiny early-flow bonus to bias any grid flow toward earlier slots.
        w_early = 1e-4
        objective += pulp.lpSum(
            (
                -w_early * (P_import[t] + P_export[t]) * (1.0 / (t + 1))
                * horizon.dt_hours(t)
            )
            for t in horizon.T
        )
        # Battery throughput penalty (wear cost) from config per inverter.
        for inverter in self._plant.inverters:
            battery = inverter.battery
            if battery is None:
                continue
            inv_vars = inverter_by_id.get(inverter.id)
            if inv_vars is None:
                continue
            wear_cost = battery.throughput_cost_per_kwh
            if wear_cost <= 0:
                continue
            charge_series = inv_vars.P_batt_charge_kw
            discharge_series = inv_vars.P_batt_discharge_kw
            if charge_series is None or discharge_series is None:
                continue
            objective += pulp.lpSum(
                wear_cost
                * (charge_series[t] + discharge_series[t])
                * horizon.dt_hours(t)
                for t in horizon.T
            )
        # Tiny tie-breaker to keep binary curtailment decisions stable across inverters.
        w_curtail_tie = 1e-6
        total = len(self._plant.inverters)
        for idx, inverter in enumerate(self._plant.inverters):
            inv_vars = inverter_by_id.get(inverter.id)
            if inv_vars is None or inv_vars.Curtail_inv is None:
                continue
            series = inv_vars.Curtail_inv
            weight = w_curtail_tie * (total - idx)
            # Consistent ordering bias avoids multiple equivalent curtailment choices.
            objective += pulp.lpSum(
                weight * series[t] * horizon.dt_hours(t) for t in horizon.T
            )
        # EV terminal SoC incentives (piecewise per-kWh rewards).
        for segments in loads.ev_incentive_segments.values():
            for segment_var, incentive in segments:
                if abs(float(incentive)) <= 1e-12:
                    continue
                objective += -float(incentive) * segment_var
        # EV ramp penalties (discourage large per-slot changes in charge power).
        ramp_penalty = _EV_RAMP_PENALTY_COST
        for load in self._loads:
            if not isinstance(load, ControlledEvLoad):
                continue
            ev_vars = ev_by_id.get(load.id)
            if ev_vars is None:
                continue
            ramp_series = ev_vars.Ev_charge_ramp_kw
            objective += pulp.lpSum(
                ramp_penalty * ramp_series[t] for t in horizon.T if t > 0
            )
        # EV soft anchor to realtime power for slot 0.
        anchor_penalty = _EV_ANCHOR_PENALTY_COST
        if horizon.num_intervals > 0:
            for load in self._loads:
                if not isinstance(load, ControlledEvLoad):
                    continue
                realtime_power = float(self._resolver.resolve(load.realtime_power))
                if abs(realtime_power) < _EV_ANCHOR_ACTIVE_THRESHOLD_KW:
                    continue
                ev_vars = ev_by_id.get(load.id)
                if ev_vars is None:
                    continue
                anchor_var = ev_vars.Ev_charge_anchor_kw
                objective += anchor_penalty * anchor_var * horizon.dt_hours(0)
        problem += objective

    def _build_loads(
        self,
        problem: pulp.LpProblem,
        horizon: Horizon,
        base_load_kw: list[float],
    ) -> LoadBuild:
        load_contribs: dict[int, pulp.LpAffineExpression] = {
            t: pulp.LpAffineExpression() for t in horizon.T
        }
        evs: dict[str, EvVars] = {}
        ev_incentive_segments: dict[str, list[tuple[pulp.LpVariable, float]]] = {}
        for load in self._loads:
            if isinstance(load, ControlledEvLoad):
                ev_id = load.id
                ev_vars, segments = self._build_controlled_ev_load(
                    problem, horizon, load, load_contribs, ev_id
                )
                evs[ev_id] = ev_vars
                ev_incentive_segments[ev_id] = segments
                continue
            elif isinstance(load, NonVariableLoad):
                self._build_nonvariable_load(problem, horizon, load, load_contribs)
                continue
            raise ValueError(f"Unsupported load type: {load.load_type}")

        return LoadBuild(
            base_load_kw=base_load_kw,
            load_contribs=load_contribs,
            evs=evs,
            ev_incentive_segments=ev_incentive_segments,
        )

    def _build_nonvariable_load(
        self,
        _problem: pulp.LpProblem,
        _horizon: Horizon,
        _load: NonVariableLoad,
        _load_contribs: dict[int, pulp.LpAffineExpression],
    ) -> None:
        # Placeholder for future fixed/deferrable loads; plant load already covers baseline demand.
        return None

    def _build_controlled_ev_load(
        self,
        problem: pulp.LpProblem,
        horizon: Horizon,
        load: ControlledEvLoad,
        load_contribs: dict[int, pulp.LpAffineExpression],
        ev_id: str,
    ) -> tuple[EvVars, list[tuple[pulp.LpVariable, float]]]:
        T = horizon.T
        ev_name = load.name

        connected = bool(self._resolver.resolve(load.connected))
        realtime_power = float(self._resolver.resolve(load.realtime_power))
        initial_soc_pct = float(self._resolver.resolve(load.state_of_charge_pct))
        can_connect = True
        if load.can_connect is not None:
            can_connect = bool(self._resolver.resolve(load.can_connect))

        capacity_kwh = float(load.energy_kwh)
        initial_soc_kwh = capacity_kwh * initial_soc_pct / 100.0
        initial_soc_kwh = max(0.0, min(capacity_kwh, initial_soc_kwh))

        P_ev_charge = pulp.LpVariable.dicts(
            f"P_ev_{ev_id}_charge_kw",
            T,
            lowBound=0,
            upBound=load.max_power_kw,
        )
        soc_indices = range(horizon.num_intervals + 1)
        E_ev_kwh = pulp.LpVariable.dicts(
            f"E_ev_{ev_id}_kwh",
            soc_indices,
            lowBound=0,
            upBound=capacity_kwh,
        )

        problem += (
            E_ev_kwh[0] == initial_soc_kwh,
            f"ev_soc_initial_{ev_id}",
        )

        connected_allow_by_slot = self._ev_connected_allowance(
            horizon=horizon,
            connected=connected,
            can_connect=can_connect,
            connect_times=load.allowed_connect_times,
            grace_minutes=load.connect_grace_minutes,
        )
        charge_on = None
        if load.min_power_kw > 0:
            charge_on = pulp.LpVariable.dicts(
                f"Ev_{ev_id}_charge_on",
                T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
        ramp_vars = pulp.LpVariable.dicts(
            f"Ev_{ev_id}_ramp_kw",
            T,
            lowBound=0,
        )
        anchor_var = pulp.LpVariable(
            f"Ev_{ev_id}_anchor_kw",
            lowBound=0,
        )
        problem += (
            ramp_vars[0] == 0,
            f"ev_charge_ramp_init_{ev_id}",
        )
        problem += (
            anchor_var >= P_ev_charge[0] - realtime_power,
            f"ev_anchor_up_{ev_id}",
        )
        problem += (
            anchor_var >= realtime_power - P_ev_charge[0],
            f"ev_anchor_down_{ev_id}",
        )

        for t in T:
            connected_allow = connected_allow_by_slot[t]
            # Enforce connection gating.
            problem += (
                P_ev_charge[t] <= load.max_power_kw * connected_allow,
                f"ev_connected_limit_{ev_id}_t{t}",
            )
            if charge_on is not None:
                problem += (
                    charge_on[t] <= connected_allow,
                    f"ev_charge_on_connected_{ev_id}_t{t}",
                )
                problem += (
                    P_ev_charge[t] >= load.min_power_kw * charge_on[t],
                    f"ev_charge_min_{ev_id}_t{t}",
                )
                problem += (
                    P_ev_charge[t] <= load.max_power_kw * charge_on[t],
                    f"ev_charge_max_{ev_id}_t{t}",
                )
            if t > 0:
                problem += (
                    ramp_vars[t] >= P_ev_charge[t] - P_ev_charge[t - 1],
                    f"ev_charge_ramp_up_{ev_id}_t{t}",
                )
                problem += (
                    ramp_vars[t] >= P_ev_charge[t - 1] - P_ev_charge[t],
                    f"ev_charge_ramp_down_{ev_id}_t{t}",
                )
            # SoC dynamics (charge-only).
            problem += (
                E_ev_kwh[t + 1]
                == E_ev_kwh[t] + P_ev_charge[t] * horizon.dt_hours(t),
                f"ev_soc_step_{ev_id}_t{t}",
            )
            load_contribs[t] += P_ev_charge[t]

        segments = self._build_ev_soc_incentives(
            problem,
            load,
            ev_id,
            ev_name,
            E_ev_kwh[horizon.num_intervals],
        )

        ev_vars = EvVars(
            name=ev_name,
            capacity_kwh=capacity_kwh,
            connected=connected,
            P_ev_charge_kw=P_ev_charge,
            E_ev_kwh=E_ev_kwh,
            Ev_charge_ramp_kw=ramp_vars,
            Ev_charge_anchor_kw=anchor_var,
        )
        return ev_vars, segments

    def _ev_connected_allowance(
        self,
        *,
        horizon: Horizon,
        connected: bool,
        can_connect: bool,
        connect_times: list[TimeWindow],
        grace_minutes: int,
    ) -> list[float]:
        if connected:
            return [1.0] * horizon.num_intervals

        if not can_connect:
            return [0.0] * horizon.num_intervals

        grace_end = horizon.now + timedelta(minutes=grace_minutes)
        allowed: list[float] = []
        for slot in horizon.slots:
            if slot.start < grace_end:
                allowed.append(0.0)
                continue
            if self._within_time_windows(slot.start, connect_times):
                allowed.append(1.0)
            else:
                allowed.append(0.0)
        return allowed

    @staticmethod
    def _within_time_windows(slot_start: datetime, windows: list[TimeWindow]) -> bool:
        if not windows:
            return True
        minute_of_day = slot_start.hour * 60 + slot_start.minute
        for window in windows:
            start = _parse_hhmm(window.start)
            end = _parse_hhmm(window.end)
            if _minute_in_window(minute_of_day, start, end):
                return True
        return False

    def _build_ev_soc_incentives(
        self,
        problem: pulp.LpProblem,
        load: ControlledEvLoad,
        ev_id: str,
        ev_name: str,
        terminal_soc: pulp.LpVariable,
    ) -> list[tuple[pulp.LpVariable, float]]:
        incentives = sorted(load.soc_incentives, key=lambda item: item.target_soc_pct)
        if not incentives:
            return []

        capacity_kwh = float(load.energy_kwh)
        segments: list[tuple[pulp.LpVariable, float]] = []
        prev_target_kwh = 0.0

        for idx, incentive in enumerate(incentives):
            target_kwh = capacity_kwh * float(incentive.target_soc_pct) / 100.0
            if target_kwh < prev_target_kwh:
                raise ValueError(
                    f"EV incentive targets must be non-decreasing (got {target_kwh} <"
                    f" {prev_target_kwh}) for {ev_name}"
                )
            segment_size = target_kwh - prev_target_kwh
            segment_var = pulp.LpVariable(
                f"E_ev_{ev_id}_incentive_{idx}_kwh",
                lowBound=0,
                upBound=segment_size,
            )
            segments.append((segment_var, float(incentive.incentive)))
            prev_target_kwh = target_kwh

        final_size = max(0.0, capacity_kwh - prev_target_kwh)
        if final_size > 0:
            segment_var = pulp.LpVariable(
                f"E_ev_{ev_id}_incentive_final_kwh",
                lowBound=0,
                upBound=final_size,
            )
            segments.append((segment_var, 0.0))

        problem += (
            pulp.lpSum(segment for segment, _ in segments) == terminal_soc,
            f"ev_incentive_total_{ev_id}",
        )
        return segments

    def _resolve_import_allowed(self, horizon: Horizon) -> list[bool]:
        forbidden = self._plant.grid.import_forbidden_periods
        if not forbidden:
            return [True] * horizon.num_intervals
        allowed: list[bool] = []
        for slot in horizon.slots:
            minute_of_day = slot.start.hour * 60 + slot.start.minute
            is_forbidden = False
            for window in forbidden:
                start = _parse_hhmm(window.start)
                end = _parse_hhmm(window.end)
                if _minute_in_window(minute_of_day, start, end):
                    is_forbidden = True
                    break
            allowed.append(not is_forbidden)
        if len(allowed) != horizon.num_intervals:
            raise ValueError("import_allowed series length mismatch")
        return allowed


def _parse_hhmm(value: str) -> int:
    hour, minute = value.split(":", maxsplit=1)
    return int(hour) * 60 + int(minute)


def _minute_in_window(minute_of_day: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= minute_of_day < end
    return minute_of_day >= start or minute_of_day < end
