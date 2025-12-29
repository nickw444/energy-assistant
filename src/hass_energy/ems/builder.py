from __future__ import annotations

import re
from dataclasses import dataclass, field

import pulp

from hass_energy.ems.forecast_alignment import PowerForecastAligner, PriceForecastAligner
from hass_energy.ems.horizon import Horizon
from hass_energy.lib.source_resolver.hass_source import HomeAssistantEntitySource
from hass_energy.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval
from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.lib.source_resolver.sources import EntitySource
from hass_energy.models.loads import LoadConfig
from hass_energy.models.plant import PlantConfig


def _new_var_dict() -> dict[int, pulp.LpVariable]:
    return {}


def _new_inverter_var_dict() -> dict[str, dict[int, pulp.LpVariable]]:
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
    # Per-inverter AC output variables (kW) keyed by inverter name then slot index.
    P_inv_ac: dict[str, dict[int, pulp.LpVariable]] = field(default_factory=_new_inverter_var_dict)
    # Per-inverter curtailment flags (0/1) keyed by inverter name then slot index.
    Curtail_inv: dict[str, dict[int, pulp.LpVariable]] = field(
        default_factory=_new_inverter_var_dict
    )


@dataclass(slots=True)
class ModelSeries:
    # Resolved load series (kW) aligned to horizon slots.
    load_kw: list[float] = field(default_factory=_new_float_list)
    # Resolved import price series ($/kWh) aligned to horizon slots.
    price_import: list[float] = field(default_factory=_new_float_list)
    # Resolved export price series ($/kWh) aligned to horizon slots.
    price_export: list[float] = field(default_factory=_new_float_list)


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
        self._build_inverters(problem, vars, self._horizon)
        self._build_ac_balance(problem, vars, series, self._horizon)
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

        for t in T:
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
        horizon: Horizon,
    ) -> None:
        T = horizon.T

        for inverter in self._plant.inverters:
            inv_name = inverter.name
            inv_slug = _slug(inv_name)

            inv_ac = pulp.LpVariable.dicts(
                f"P_inv_{inv_slug}_ac_kw",
                T,
                lowBound=0,
                upBound=inverter.peak_power_kw,
            )
            vars.P_inv_ac[inv_name] = inv_ac

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
                        inv_ac[t] == pv_available_kw_series[t],
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
                            inv_ac[t] == pv_available_kw_series[t] * (1 - curtail[t]),
                            f"inverter_pv_binary_{inv_slug}_t{t}",
                        )
                    else:
                        problem += (
                            # Load-aware: output cannot exceed available PV.
                            inv_ac[t] <= pv_available_kw_series[t],
                            f"inverter_pv_max_{inv_slug}_t{t}",
                        )
                        problem += (
                            # Load-aware: curtail flag reduces minimum output (allows export block).
                            inv_ac[t]
                            >= pv_available_kw_series[t] * (1 - curtail[t]),
                            f"inverter_pv_min_{inv_slug}_t{t}",
                        )
                        problem += (
                            # Load-aware: when curtailing, block grid export.
                            vars.P_grid_export[t]
                            <= self._plant.grid.max_export_kw * (1 - curtail[t]),
                            f"inverter_export_block_{inv_slug}_t{t}",
                        )

    def _build_ac_balance(
        self,
        problem: pulp.LpProblem,
        vars: ModelVars,
        series: ModelSeries,
        horizon: Horizon,
    ) -> None:
        P_import = vars.P_grid_import
        P_export = vars.P_grid_export
        P_inv_ac = vars.P_inv_ac

        for t in horizon.T:
            inv_total = pulp.lpSum(inv_series[t] for inv_series in P_inv_ac.values())
            problem += (
                P_import[t] + inv_total - P_export[t] == float(series.load_kw[t]),
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
        problem += objective

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
