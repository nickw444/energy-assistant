from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import pulp

from energy_assistant.ems.forecast_alignment import forecast_coverage_slots
from energy_assistant.ems.horizon import Horizon, floor_to_interval_boundary
from energy_assistant.ems.models import ResolvedForecasts
from energy_assistant.ems.pricing import PriceSeriesBuilder
from energy_assistant.ems.time_windows import TimeWindowMatcher
from energy_assistant.ems.topology.ev import EvComponent
from energy_assistant.ems.topology.grid import GridComponent
from energy_assistant.ems.topology.inverter import InverterComponent
from energy_assistant.ems.topology.load import PlantLoadComponent
from energy_assistant.ems.topology.registry import Topology
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.config import EmsConfig
from energy_assistant.models.loads import ControlledEvLoad, LoadConfig, NonVariableLoad
from energy_assistant.models.plant import PlantConfig

_TERMINAL_SOC_REFERENCE_MINUTES = 1440.0

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MILPModel:
    problem: pulp.LpProblem
    topology: Topology


class MILPBuilder:
    def __init__(
        self,
        plant: PlantConfig,
        loads: list[LoadConfig],
        resolver: ValueResolver,
        ems_config: EmsConfig,
        time_window_matcher: TimeWindowMatcher,
        price_series_builder: PriceSeriesBuilder,
    ):
        self._plant = plant
        self._loads = loads
        self._resolver = resolver
        self._ems_config = ems_config
        self._time_window_matcher = time_window_matcher
        self._price_series_builder = price_series_builder

        # Build the physical topology
        self.topology = Topology()

        # 1. Grid
        self.grid_component = GridComponent(
            self._plant.grid,
            self._time_window_matcher,
            self._price_series_builder,
        )
        self.topology.add_component(self.grid_component)

        # 2. Plant Load
        self.load_component = PlantLoadComponent(self._plant.load)
        self.topology.add_component(self.load_component)

        # 3. Inverters
        self.inverter_components: dict[str, InverterComponent] = {}
        for inverter_cfg in self._plant.inverters:
            inv_comp = InverterComponent(inverter_cfg, self._ems_config)
            self.inverter_components[inverter_cfg.id] = inv_comp
            self.topology.add_component(inv_comp)

        # 4. EV Loads
        self.ev_components: dict[str, EvComponent] = {}
        for load_cfg in self._loads:
            if isinstance(load_cfg, ControlledEvLoad):
                ev_comp = EvComponent(load_cfg, self._time_window_matcher)
                self.ev_components[load_cfg.id] = ev_comp
                self.topology.add_component(ev_comp)
            elif isinstance(load_cfg, NonVariableLoad):
                # NonVariableLoad is a placeholder in the original code
                pass

    def resolve_forecasts(
        self,
        *,
        now: datetime,
        interval_minutes: int,
    ) -> ResolvedForecasts:
        start = floor_to_interval_boundary(now, interval_minutes)

        # For backward compatibility with ResolvedForecasts and existing tests,
        # we still perform the resolution and return the structure,
        # but components also resolve their own data.

        # Grid price forecasts
        price_import_intervals = self._resolver.resolve(self._plant.grid.price_import_forecast)
        price_export_intervals = self._resolver.resolve(self._plant.grid.price_export_forecast)

        # Plant load forecast
        load_intervals = self._resolver.resolve(self._plant.load.forecast)

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

        inverter_forecasts = {}
        for inv_id, inv_comp in self.inverter_components.items():
            # Delegate to component?
            # For now, let's keep it here to build the ResolvedForecasts object
            inv_cfg = inv_comp._config
            pv_intervals = self._resolver.resolve(inv_cfg.pv.forecast)
            allow_first_slot_missing = inv_cfg.pv.realtime_power is not None
            inverter_forecasts[inv_id] = pv_intervals
            coverage_by_series[f"pv:{inv_id}"] = forecast_coverage_slots(
                start,
                interval_minutes,
                pv_intervals,
                allow_first_slot_missing=allow_first_slot_missing,
            )

        if not coverage_by_series:
            raise ValueError("No forecasts available to determine planning horizon")

        min_coverage = min(coverage_by_series.values())
        return ResolvedForecasts(
            grid_price_import=price_import_intervals,
            grid_price_export=price_export_intervals,
            load=load_intervals,
            inverters_pv=inverter_forecasts,
            min_coverage_intervals=min_coverage,
        )

    def build(self, *, horizon: Horizon, forecasts: ResolvedForecasts) -> MILPModel:
        problem = pulp.LpProblem("ems_optimisation", pulp.LpMinimize)

        # Data alignment
        # 1. Grid
        self.grid_component.align_data(horizon, self._resolver, {
            "price_import_intervals": forecasts.grid_price_import,
            "price_export_intervals": forecasts.grid_price_export,
        })
        # 2. Plant Load
        self.load_component.align_data(horizon, self._resolver, {
            "load_intervals": forecasts.load,
        })
        # 3. Inverters
        for inv_id, inv_comp in self.inverter_components.items():
            inv_comp.align_data(horizon, self._resolver, {
                "pv_intervals": forecasts.inverters_pv[inv_id],
            })

        # Lifecycle
        self.topology.add_variables(problem, horizon)
        self.topology.set_initial_conditions(problem, horizon, self._resolver)
        self.topology.add_constraints(problem, horizon)

        # AC Power Balance at PCC
        for t in horizon.T:
            problem += (
                self.topology.get_total_pcc_load_kw(t) == 0,
                f"pcc_ac_balance_t{t}",
            )

        # Battery reserve vs Grid Export cross-component constraint
        for t in horizon.T:
            for inv_comp in self.inverter_components.values():
                if inv_comp.export_ok is not None:
                    problem += (
                        self.grid_component.P_export[t] <= self._plant.grid.max_export_kw * inv_comp.export_ok[t],
                        f"grid_export_reserve_{inv_comp.id}_t{t}",
                    )

        # Objective
        objective = self.topology.get_objective_terms(horizon)

        # Cross-component objective terms (e.g., adaptive terminal SoC penalty)
        terminal_penalty = self._terminal_soc_penalty_per_kwh(horizon, self.grid_component.price_import)
        if terminal_penalty > 0:
            for inv_comp in self.inverter_components.values():
                if inv_comp.E_batt_terminal_shortfall_kwh is not None:
                    objective += terminal_penalty * inv_comp.E_batt_terminal_shortfall_kwh

        # EV incentive rewards (need export bias from grid)
        grid_price_bias = self._plant.grid.grid_price_bias_pct / 100.0
        def _apply_export_bias(value: float) -> float:
            if grid_price_bias == 0:
                return value
            if value >= 0:
                return value * (1.0 - grid_price_bias)
            return value * (1.0 + grid_price_bias)

        for ev_comp in self.ev_components.values():
            for segment_var, incentive in ev_comp.Ev_incentive_segments:
                if abs(float(incentive)) <= 1e-12:
                    continue
                biased_incentive = _apply_export_bias(float(incentive))
                objective += -biased_incentive * segment_var

            # Switch penalty
            switch_penalty = ev_comp._config.switch_penalty
            if switch_penalty > 0 and ev_comp.Ev_charge_switch:
                objective += switch_penalty * pulp.lpSum(ev_comp.Ev_charge_switch.values())

        problem += objective

        return MILPModel(problem, self.topology)

    def _terminal_soc_penalty_per_kwh(
        self,
        horizon: Horizon,
        price_import: list[float],
    ) -> float:
        cfg = self._ems_config.terminal_soc
        penalty = cfg.penalty_per_kwh
        if penalty is None or penalty == "median":
            penalty = _median_price(price_import)
        elif penalty == "mean":
            penalty = _average_price(price_import)
        penalty = max(0.0, float(penalty))

        # Ratio logic
        horizon_minutes = (horizon.slots[-1].end - horizon.start).total_seconds() / 60.0
        reference_minutes = _TERMINAL_SOC_REFERENCE_MINUTES
        shorter = min(horizon_minutes, reference_minutes)
        longer = max(horizon_minutes, reference_minutes)
        ratio = shorter / longer if self._ems_config.terminal_soc.mode == "adaptive" else 1.0

        penalty *= ratio
        return penalty


def _average_price(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _median_price(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2 == 1:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0
