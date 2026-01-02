from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pulp

from hass_energy.ems.forecast_alignment import PowerForecastAligner, PriceForecastAligner
from hass_energy.ems.horizon import Horizon
from hass_energy.lib.source_resolver.hass_source import HomeAssistantEntitySource
from hass_energy.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval
from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.lib.source_resolver.sources import EntitySource
from hass_energy.models.loads import ControlledEvLoad, LoadConfig, NonVariableLoad
from hass_energy.models.plant import PlantConfig, TimeWindow

_EV_RAMP_PENALTY_COST = 1e-4
_EV_ANCHOR_PENALTY_COST = 0.05


def _new_var_dict() -> dict[int, pulp.LpVariable]:
    return {}


def _new_inverter_var_dict() -> dict[str, dict[int, pulp.LpVariable]]:
    return {}


def _new_load_var_dict() -> dict[str, dict[int, pulp.LpVariable]]:
    return {}


def _new_load_scalar_dict() -> dict[str, pulp.LpVariable]:
    return {}


def _new_ev_incentive_dict() -> dict[str, list[tuple[pulp.LpVariable, float]]]:
    return {}


def _new_bool_dict() -> dict[str, bool]:
    return {}


def _new_float_dict() -> dict[str, float]:
    return {}


def _new_float_list() -> list[float]:
    return []


@dataclass(slots=True)
class ModelVars:
    # Grid import decision variables (kW) keyed by slot index.
    P_grid_import: dict[int, pulp.LpVariable] = field(default_factory=_new_var_dict)
    # Grid export decision variables (kW) keyed by slot index.
    P_grid_export: dict[int, pulp.LpVariable] = field(default_factory=_new_var_dict)
    # Import violation slack variables (kW) keyed by slot index.
    P_grid_import_violation_kw: dict[int, pulp.LpVariable] = field(default_factory=_new_var_dict)
    # Per-inverter PV output variables (kW) keyed by inverter name then slot index.
    P_pv_kw: dict[str, dict[int, pulp.LpVariable]] = field(default_factory=_new_inverter_var_dict)
    # Per-inverter net AC flow (kW) keyed by inverter name then slot index.
    P_inv_ac_net_kw: dict[str, dict[int, pulp.LpVariable]] = field(
        default_factory=_new_inverter_var_dict
    )
    # Per-inverter curtailment flags (0/1) keyed by inverter name then slot index.
    Curtail_inv: dict[str, dict[int, pulp.LpVariable]] = field(
        default_factory=_new_inverter_var_dict
    )
    # Per-inverter battery charge power (kW) keyed by inverter name then slot index.
    P_batt_charge_kw: dict[str, dict[int, pulp.LpVariable]] = field(
        default_factory=_new_inverter_var_dict
    )
    # Per-inverter battery discharge power (kW) keyed by inverter name then slot index.
    P_batt_discharge_kw: dict[str, dict[int, pulp.LpVariable]] = field(
        default_factory=_new_inverter_var_dict
    )
    # Per-inverter battery state of charge (kWh) keyed by inverter name then slot index.
    E_batt_kwh: dict[str, dict[int, pulp.LpVariable]] = field(
        default_factory=_new_inverter_var_dict
    )
    # Per-EV charge power (kW) keyed by EV name then slot index.
    P_ev_charge_kw: dict[str, dict[int, pulp.LpVariable]] = field(
        default_factory=_new_load_var_dict
    )
    # Per-EV state of charge (kWh) keyed by EV name then slot index.
    E_ev_kwh: dict[str, dict[int, pulp.LpVariable]] = field(default_factory=_new_load_var_dict)
    # Per-EV charge ramp magnitude (kW change) keyed by EV name then slot index.
    Ev_charge_ramp_kw: dict[str, dict[int, pulp.LpVariable]] = field(
        default_factory=_new_load_var_dict
    )
    # Per-EV realtime anchor deviation (kW) keyed by EV name.
    Ev_charge_anchor_kw: dict[str, pulp.LpVariable] = field(
        default_factory=_new_load_scalar_dict
    )
    # Per-EV incentive segments (kWh) with their per-kWh rewards.
    E_ev_incentive_segments: dict[str, list[tuple[pulp.LpVariable, float]]] = field(
        default_factory=_new_ev_incentive_dict
    )


@dataclass(slots=True)
class ModelSeries:
    # Resolved load series (kW) aligned to horizon slots.
    load_kw: list[float] = field(default_factory=_new_float_list)
    # Resolved import price series ($/kWh) aligned to horizon slots.
    price_import: list[float] = field(default_factory=_new_float_list)
    # Resolved export price series ($/kWh) aligned to horizon slots.
    price_export: list[float] = field(default_factory=_new_float_list)
    # Resolved EV connection state (static for the solve).
    ev_connected: dict[str, bool] = field(default_factory=_new_bool_dict)
    # Resolved EV realtime power (kW) for reporting.
    ev_realtime_power_kw: dict[str, float] = field(default_factory=_new_float_dict)
    # Battery capacities (kWh) keyed by inverter name for reporting.
    battery_capacity_kwh: dict[str, float] = field(default_factory=_new_float_dict)
    # EV capacities (kWh) keyed by EV name for reporting.
    ev_capacity_kwh: dict[str, float] = field(default_factory=_new_float_dict)


@dataclass(slots=True)
class MILPModel:
    problem: pulp.LpProblem
    vars: ModelVars
    series: ModelSeries


class MILPBuilder:
    def __init__(
        self,
        plant: PlantConfig,
        loads: list[LoadConfig],
        horizon: Horizon,
        resolver: ValueResolver,
    ):
        self._plant = plant
        self._loads = loads
        self._horizon = horizon
        self._resolver = resolver
        self._power_aligner = PowerForecastAligner()
        self._price_aligner = PriceForecastAligner()

    def build(self) -> MILPModel:
        problem = pulp.LpProblem("ems_optimisation", pulp.LpMinimize)
        vars = ModelVars()
        series = self._resolve_series(self._horizon)

        self._build_grid(problem, vars, self._horizon)
        self._build_inverters(problem, vars, series, self._horizon)
        load_contribs = self._build_loads(problem, vars, series, self._horizon)
        self._build_ac_balance(problem, vars, series, load_contribs, self._horizon)
        self._build_objective(problem, vars, series, self._horizon)

        return MILPModel(problem, vars, series)

    def _build_grid(self, problem: pulp.LpProblem, vars: ModelVars, horizon: Horizon) -> None:
        T = horizon.T
        cfg = self._plant.grid

        vars.P_grid_import = pulp.LpVariable.dicts(
            "P_grid_import", T, lowBound=0, upBound=cfg.max_import_kw
        )
        vars.P_grid_export = pulp.LpVariable.dicts(
            "P_grid_export", T, lowBound=0, upBound=cfg.max_export_kw
        )
        vars.P_grid_import_violation_kw = pulp.LpVariable.dicts(
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

        for t in T:
            # Prevent simultaneous grid import/export by selecting an import or export mode.
            problem += (
                vars.P_grid_import[t] <= cfg.max_import_kw * grid_import_on[t],
                f"grid_import_exclusive_t{t}",
            )
            problem += (
                vars.P_grid_export[t] <= cfg.max_export_kw * (1 - grid_import_on[t]),
                f"grid_export_exclusive_t{t}",
            )
            # Enforce the per-slot import cap. When imports are forbidden, the RHS becomes 0,
            # so only the violation variable can satisfy the constraint. It is heavily penalized
            # in the objective, keeping the model feasible while discouraging forbidden imports.
            problem += (
                vars.P_grid_import[t]
                <= cfg.max_import_kw * float(horizon.import_allowed[t])
                + vars.P_grid_import_violation_kw[t],
                f"grid_import_forbidden_or_violation_t{t}",
            )

    def _build_inverters(
        self,
        problem: pulp.LpProblem,
        vars: ModelVars,
        series: ModelSeries,
        horizon: Horizon,
    ) -> None:
        T = horizon.T

        for inverter in self._plant.inverters:
            inv_name = inverter.name
            inv_slug = _slug(inv_name)

            pv_kw = pulp.LpVariable.dicts(
                f"P_pv_{inv_slug}_kw",
                T,
                lowBound=0,
                upBound=inverter.peak_power_kw,
            )
            vars.P_pv_kw[inv_name] = pv_kw
            inv_ac_net_kw = pulp.LpVariable.dicts(
                f"P_inv_{inv_slug}_ac_net_kw",
                T,
                lowBound=-inverter.peak_power_kw,
                upBound=inverter.peak_power_kw,
            )
            vars.P_inv_ac_net_kw[inv_name] = inv_ac_net_kw

            pv_available_kw_series = self._resolve_power_series(
                horizon,
                forecast_source=inverter.pv.forecast,
                realtime_source=inverter.pv.realtime_power,
            )

            curtailment = inverter.curtailment
            if curtailment is None:
                for t in T:
                    problem += (
                        # No curtailment: inverter AC output must equal available PV.
                        pv_kw[t] == pv_available_kw_series[t],
                        f"inverter_pv_total_{inv_slug}_t{t}",
                    )
            else:
                curtail = pulp.LpVariable.dicts(
                    f"Curtail_inv_{inv_slug}",
                    T,
                    lowBound=0,
                    upBound=1,
                    cat="Binary",
                )
                vars.Curtail_inv[inv_name] = curtail
                for t in T:
                    if curtailment == "binary":
                        problem += (
                            # Binary curtailment: either full PV or fully off.
                            pv_kw[t] == pv_available_kw_series[t] * (1 - curtail[t]),
                            f"inverter_pv_binary_{inv_slug}_t{t}",
                        )
                    else:
                        problem += (
                            # Load-aware: output cannot exceed available PV.
                            pv_kw[t] <= pv_available_kw_series[t],
                            f"inverter_pv_max_{inv_slug}_t{t}",
                        )
                        problem += (
                            # Load-aware: curtail flag reduces minimum output (allows export block).
                            pv_kw[t]
                            >= pv_available_kw_series[t] * (1 - curtail[t]),
                            f"inverter_pv_min_{inv_slug}_t{t}",
                        )
                        problem += (
                            # Load-aware: when curtailing, block grid export.
                            vars.P_grid_export[t]
                            <= self._plant.grid.max_export_kw * (1 - curtail[t]),
                            f"inverter_export_block_{inv_slug}_t{t}",
                        )

            battery = inverter.battery
            if battery is None:
                for t in T:
                    problem += (
                        # Net AC flow equals PV output when no battery is present.
                        inv_ac_net_kw[t] == pv_kw[t],
                        f"inverter_ac_net_{inv_slug}_t{t}",
                    )
                continue

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

            soc_min_pct = max(battery.min_soc_pct, battery.reserve_soc_pct)
            soc_min_kwh = battery.capacity_kwh * soc_min_pct / 100.0
            soc_max_kwh = battery.capacity_kwh * battery.max_soc_pct / 100.0
            storage_efficiency = battery.storage_efficiency_pct / 100.0

            P_batt_charge = pulp.LpVariable.dicts(
                f"P_batt_{inv_slug}_charge_kw",
                T,
                lowBound=0,
                upBound=charge_limit,
            )
            P_batt_discharge = pulp.LpVariable.dicts(
                f"P_batt_{inv_slug}_discharge_kw",
                T,
                lowBound=0,
                upBound=discharge_limit,
            )
            batt_charge_mode = pulp.LpVariable.dicts(
                f"Batt_{inv_slug}_charge_mode",
                T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
            # SoC is defined at slot boundaries, so we need N+1 points for N intervals.
            soc_indices = range(horizon.num_intervals + 1)
            E_batt_kwh = pulp.LpVariable.dicts(
                f"E_batt_{inv_slug}_kwh",
                soc_indices,
                lowBound=soc_min_kwh,
                upBound=soc_max_kwh,
            )
            vars.P_batt_charge_kw[inv_name] = P_batt_charge
            vars.P_batt_discharge_kw[inv_name] = P_batt_discharge
            vars.E_batt_kwh[inv_name] = E_batt_kwh
            series.battery_capacity_kwh[inv_name] = float(battery.capacity_kwh)

            initial_soc_pct = self._resolver.resolve(battery.state_of_charge_pct)
            initial_soc_kwh = battery.capacity_kwh * float(initial_soc_pct) / 100.0
            problem += (
                E_batt_kwh[0] == initial_soc_kwh,
                f"batt_soc_initial_{inv_slug}",
            )
            problem += (
                E_batt_kwh[horizon.num_intervals] >= initial_soc_kwh,
                f"batt_soc_terminal_{inv_slug}",
            )

            for t in T:
                # Select charge vs discharge mode (idle is allowed in either mode).
                problem += (
                    P_batt_charge[t] <= charge_limit * batt_charge_mode[t],
                    f"batt_charge_limit_{inv_slug}_t{t}",
                )
                problem += (
                    P_batt_discharge[t] <= discharge_limit * (1 - batt_charge_mode[t]),
                    f"batt_discharge_limit_{inv_slug}_t{t}",
                )
                # Net AC flow combines PV and battery charge/discharge.
                problem += (
                    inv_ac_net_kw[t]
                    == pv_kw[t] + P_batt_discharge[t] - P_batt_charge[t],
                    f"inverter_ac_net_{inv_slug}_t{t}",
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
                    f"batt_soc_step_{inv_slug}_t{t}",
                )

    def _build_ac_balance(
        self,
        problem: pulp.LpProblem,
        vars: ModelVars,
        series: ModelSeries,
        load_contribs: dict[int, pulp.LpAffineExpression],
        horizon: Horizon,
    ) -> None:
        P_import = vars.P_grid_import
        P_export = vars.P_grid_export
        P_inv_ac_net_kw = vars.P_inv_ac_net_kw

        for t in horizon.T:
            inv_total = pulp.lpSum(
                inv_series[t] for inv_series in P_inv_ac_net_kw.values()
            )
            extra_load = load_contribs.get(t, 0.0)
            problem += (
                P_import[t] + inv_total - P_export[t]
                == float(series.load_kw[t]) + extra_load,
                f"ac_balance_t{t}",
            )

    def _build_objective(
        self,
        problem: pulp.LpProblem,
        vars: ModelVars,
        series: ModelSeries,
        horizon: Horizon,
    ) -> None:
        P_import = vars.P_grid_import
        P_export = vars.P_grid_export
        P_import_violation = vars.P_grid_import_violation_kw
        price_import = series.price_import
        price_export = series.price_export
        Curtail_inv = vars.Curtail_inv
        P_batt_charge = vars.P_batt_charge_kw
        P_batt_discharge = vars.P_batt_discharge_kw

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
            wear_cost = battery.throughput_cost_per_kwh
            if wear_cost <= 0:
                continue
            charge_series = P_batt_charge.get(inverter.name)
            discharge_series = P_batt_discharge.get(inverter.name)
            if not isinstance(charge_series, dict) or not isinstance(discharge_series, dict):
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
            series = Curtail_inv.get(inverter.name)
            if not isinstance(series, dict):
                continue
            weight = w_curtail_tie * (total - idx)
            # Consistent ordering bias avoids multiple equivalent curtailment choices.
            objective += pulp.lpSum(
                weight * series[t] * horizon.dt_hours(t) for t in horizon.T
            )
        # EV terminal SoC incentives (piecewise per-kWh rewards).
        for segments in vars.E_ev_incentive_segments.values():
            for segment_var, incentive in segments:
                if abs(float(incentive)) <= 1e-12:
                    continue
                objective += -float(incentive) * segment_var
        # EV ramp penalties (discourage large per-slot changes in charge power).
        ramp_penalty = _EV_RAMP_PENALTY_COST
        for load in self._loads:
            if not isinstance(load, ControlledEvLoad):
                continue
            ramp_series = vars.Ev_charge_ramp_kw.get(load.name)
            if not isinstance(ramp_series, dict):
                continue
            objective += pulp.lpSum(
                ramp_penalty * ramp_series[t] for t in horizon.T if t > 0
            )
        # EV soft anchor to realtime power for slot 0.
        anchor_penalty = _EV_ANCHOR_PENALTY_COST
        if horizon.num_intervals > 0:
            for load in self._loads:
                if not isinstance(load, ControlledEvLoad):
                    continue
                anchor_var = vars.Ev_charge_anchor_kw.get(load.name)
                if anchor_var is None:
                    continue
                objective += anchor_penalty * anchor_var * horizon.dt_hours(0)
        problem += objective

    def _build_loads(
        self,
        problem: pulp.LpProblem,
        vars: ModelVars,
        series: ModelSeries,
        horizon: Horizon,
    ) -> dict[int, pulp.LpAffineExpression]:
        load_contribs: dict[int, pulp.LpAffineExpression] = {
            t: pulp.LpAffineExpression() for t in horizon.T
        }
        handlers = {
            "controlled_ev": self._build_controlled_ev_load,
            "nonvariable_load": self._build_nonvariable_load,
        }
        for load in self._loads:
            handler = handlers.get(load.load_type)
            if handler is None:
                raise ValueError(f"Unsupported load type: {load.load_type}")
            handler(problem, vars, series, horizon, load, load_contribs)
        return load_contribs

    def _build_nonvariable_load(
        self,
        _problem: pulp.LpProblem,
        _vars: ModelVars,
        _series: ModelSeries,
        _horizon: Horizon,
        _load: NonVariableLoad,
        _load_contribs: dict[int, pulp.LpAffineExpression],
    ) -> None:
        # Placeholder for future fixed/deferrable loads; plant load already covers baseline demand.
        return None

    def _build_controlled_ev_load(
        self,
        problem: pulp.LpProblem,
        vars: ModelVars,
        series: ModelSeries,
        horizon: Horizon,
        load: ControlledEvLoad,
        load_contribs: dict[int, pulp.LpAffineExpression],
    ) -> None:
        T = horizon.T
        ev_name = load.name
        ev_slug = _slug(ev_name)

        connected = bool(self._resolver.resolve(load.connected))
        series.ev_connected[ev_name] = connected
        realtime_power = float(self._resolver.resolve(load.realtime_power))
        series.ev_realtime_power_kw[ev_name] = realtime_power
        initial_soc_pct = float(self._resolver.resolve(load.state_of_charge_pct))
        can_connect = True
        if load.can_connect is not None:
            can_connect = bool(self._resolver.resolve(load.can_connect))

        capacity_kwh = float(load.energy_kwh)
        initial_soc_kwh = capacity_kwh * initial_soc_pct / 100.0
        initial_soc_kwh = max(0.0, min(capacity_kwh, initial_soc_kwh))
        series.ev_capacity_kwh[ev_name] = capacity_kwh

        P_ev_charge = pulp.LpVariable.dicts(
            f"P_ev_{ev_slug}_charge_kw",
            T,
            lowBound=0,
            upBound=load.max_power_kw,
        )
        soc_indices = range(horizon.num_intervals + 1)
        E_ev_kwh = pulp.LpVariable.dicts(
            f"E_ev_{ev_slug}_kwh",
            soc_indices,
            lowBound=0,
            upBound=capacity_kwh,
        )
        vars.P_ev_charge_kw[ev_name] = P_ev_charge
        vars.E_ev_kwh[ev_name] = E_ev_kwh

        problem += (
            E_ev_kwh[0] == initial_soc_kwh,
            f"ev_soc_initial_{ev_slug}",
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
                f"Ev_{ev_slug}_charge_on",
                T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
        ramp_vars = pulp.LpVariable.dicts(
            f"Ev_{ev_slug}_ramp_kw",
            T,
            lowBound=0,
        )
        vars.Ev_charge_ramp_kw[ev_name] = ramp_vars
        anchor_var = pulp.LpVariable(
            f"Ev_{ev_slug}_anchor_kw",
            lowBound=0,
        )
        vars.Ev_charge_anchor_kw[ev_name] = anchor_var
        problem += (
            ramp_vars[0] == 0,
            f"ev_charge_ramp_init_{ev_slug}",
        )
        problem += (
            anchor_var >= P_ev_charge[0] - realtime_power,
            f"ev_anchor_up_{ev_slug}",
        )
        problem += (
            anchor_var >= realtime_power - P_ev_charge[0],
            f"ev_anchor_down_{ev_slug}",
        )

        for t in T:
            connected_allow = connected_allow_by_slot[t]
            # Enforce connection gating.
            problem += (
                P_ev_charge[t] <= load.max_power_kw * connected_allow,
                f"ev_connected_limit_{ev_slug}_t{t}",
            )
            if charge_on is not None:
                problem += (
                    charge_on[t] <= connected_allow,
                    f"ev_charge_on_connected_{ev_slug}_t{t}",
                )
                problem += (
                    P_ev_charge[t] >= load.min_power_kw * charge_on[t],
                    f"ev_charge_min_{ev_slug}_t{t}",
                )
                problem += (
                    P_ev_charge[t] <= load.max_power_kw * charge_on[t],
                    f"ev_charge_max_{ev_slug}_t{t}",
                )
            if t > 0:
                problem += (
                    ramp_vars[t] >= P_ev_charge[t] - P_ev_charge[t - 1],
                    f"ev_charge_ramp_up_{ev_slug}_t{t}",
                )
                problem += (
                    ramp_vars[t] >= P_ev_charge[t - 1] - P_ev_charge[t],
                    f"ev_charge_ramp_down_{ev_slug}_t{t}",
                )
            # SoC dynamics (charge-only).
            problem += (
                E_ev_kwh[t + 1]
                == E_ev_kwh[t] + P_ev_charge[t] * horizon.dt_hours(t),
                f"ev_soc_step_{ev_slug}_t{t}",
            )
            load_contribs[t] += P_ev_charge[t]

        self._build_ev_soc_incentives(problem, vars, horizon, load, ev_slug, ev_name)

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
        vars: ModelVars,
        horizon: Horizon,
        load: ControlledEvLoad,
        ev_slug: str,
        ev_name: str,
    ) -> None:
        incentives = sorted(load.soc_incentives, key=lambda item: item.target_soc_pct)
        if not incentives:
            return

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
                f"E_ev_{ev_slug}_incentive_{idx}_kwh",
                lowBound=0,
                upBound=segment_size,
            )
            segments.append((segment_var, float(incentive.incentive)))
            prev_target_kwh = target_kwh

        final_size = max(0.0, capacity_kwh - prev_target_kwh)
        if final_size > 0:
            segment_var = pulp.LpVariable(
                f"E_ev_{ev_slug}_incentive_final_kwh",
                lowBound=0,
                upBound=final_size,
            )
            segments.append((segment_var, 0.0))

        terminal_soc = vars.E_ev_kwh[ev_name][horizon.num_intervals]
        problem += (
            pulp.lpSum(segment for segment, _ in segments) == terminal_soc,
            f"ev_incentive_total_{ev_slug}",
        )
        vars.E_ev_incentive_segments[ev_name] = segments

    def _resolve_price_series(
        self,
        horizon: Horizon,
        forecast_source: HomeAssistantEntitySource[list[PriceForecastInterval]],
        realtime_source: HomeAssistantEntitySource[float],
    ) -> list[float]:
        """Resolve price forecast into a horizon-aligned series and override the current slot.

        Uses the forecast to fill all slots, then replaces the slot containing
        ``horizon.now`` with the realtime price value.
        """
        resolved = self._resolver.resolve(forecast_source)
        realtime_value = self._resolver.resolve(realtime_source)
        return self._price_aligner.align(
            horizon,
            resolved,
            first_slot_override=realtime_value,
        )

    def _resolve_power_series[TForecast, TRealtime](
        self,
        horizon: Horizon,
        *,
        forecast_source: EntitySource[TForecast, list[PowerForecastInterval]] | None,
        realtime_source: EntitySource[TRealtime, float] | None,
    ) -> list[float]:
        """Resolve a power forecast into a horizon-aligned series.

        If a forecast is provided, align it to the horizon and optionally replace
        the current slot with realtime power when available. If no forecast is
        provided, fill the horizon from realtime power.
        """
        if forecast_source is None:
            if realtime_source is None:
                raise ValueError("realtime power source is required when forecast is missing")
            value = self._resolver.resolve(realtime_source)
            return [value] * horizon.num_intervals

        resolved = self._resolver.resolve(forecast_source)
        realtime_value = None
        if realtime_source is not None:
            realtime_value = self._resolver.resolve(realtime_source)
        return self._power_aligner.align(
            horizon,
            resolved,
            first_slot_override=realtime_value,
        )

    def _resolve_series(self, horizon: Horizon) -> ModelSeries:
        load_forecast = self._plant.load.forecast
        if load_forecast.interval_duration != horizon.interval_minutes:
            raise ValueError(
                "Load forecast interval_duration must match EMS interval_duration"
            )
        load_series = self._resolve_power_series(
            horizon,
            forecast_source=load_forecast,
            realtime_source=self._plant.load.realtime_load_power,
        )
        return ModelSeries(
            load_kw=load_series,
            price_import=self._resolve_price_series(
                horizon,
                self._plant.grid.price_import_forecast,
                self._plant.grid.realtime_price_import,
            ),
            price_export=self._resolve_price_series(
                horizon,
                self._plant.grid.price_export_forecast,
                self._plant.grid.realtime_price_export,
            ),
        )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip())
    slug = slug.strip("_")
    return slug or "inv"


def _parse_hhmm(value: str) -> int:
    hour, minute = value.split(":", maxsplit=1)
    return int(hour) * 60 + int(minute)


def _minute_in_window(minute_of_day: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= minute_of_day < end
    return minute_of_day >= start or minute_of_day < end
