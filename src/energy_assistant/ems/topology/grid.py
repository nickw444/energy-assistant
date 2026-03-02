from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pulp

from energy_assistant.ems.forecast_alignment import PriceForecastAligner
from energy_assistant.ems.topology.base import EnergyComponent

if TYPE_CHECKING:
    from energy_assistant.ems.horizon import Horizon
    from energy_assistant.ems.pricing import PriceSeriesBuilder
    from energy_assistant.ems.time_windows import TimeWindowMatcher
    from energy_assistant.lib.source_resolver.resolver import ValueResolver
    from energy_assistant.models.plant import GridConfig


class GridComponent(EnergyComponent):
    def __init__(
        self,
        config: GridConfig,
        time_window_matcher: TimeWindowMatcher,
        price_series_builder: PriceSeriesBuilder,
    ):
        super().__init__(id="grid", name="Grid")
        self._config = config
        self._time_window_matcher = time_window_matcher
        self._price_series_builder = price_series_builder
        self._price_aligner = PriceForecastAligner()

        # Data and Variables
        self.price_import: list[float] = []
        self.price_export: list[float] = []
        self.price_import_effective: list[float] = []
        self.price_export_effective: list[float] = []
        self.import_allowed: list[bool] = []

        self.P_import: dict[int, pulp.LpVariable] = {}
        self.P_export: dict[int, pulp.LpVariable] = {}
        self.P_import_violation_kw: dict[int, pulp.LpVariable] = {}
        self.grid_import_on: dict[int, pulp.LpVariable] = {}

    def resolve_data(
        self,
        resolver: ValueResolver,
        horizon_start: Any,
        interval_minutes: int,
    ) -> dict[str, Any]:
        # This will be called by MILPBuilder during resolve_forecasts
        price_import_intervals = resolver.resolve(self._config.price_import_forecast)
        price_export_intervals = resolver.resolve(self._config.price_export_forecast)
        return {
            "price_import_intervals": price_import_intervals,
            "price_export_intervals": price_export_intervals,
        }

    def align_data(self, horizon: Horizon, resolver: ValueResolver, resolved_forecasts: dict[str, Any]) -> None:
        realtime_import = resolver.resolve(self._config.realtime_price_import)
        realtime_export = resolver.resolve(self._config.realtime_price_export)

        self.price_import = self._price_aligner.align(
            horizon,
            resolved_forecasts["price_import_intervals"],
            first_slot_override=realtime_import,
        )
        self.price_export = self._price_aligner.align(
            horizon,
            resolved_forecasts["price_export_intervals"],
            first_slot_override=realtime_export,
        )

        price_series = self._price_series_builder.build_series(
            horizon=horizon,
            price_import=self.price_import,
            price_export=self.price_export,
        )
        self.price_import_effective = price_series.import_effective
        self.price_export_effective = price_series.export_effective

        # Resolve forbidden periods
        self.import_allowed = []
        forbidden = self._config.import_forbidden_periods
        if not forbidden:
            self.import_allowed = [True] * horizon.num_intervals
        else:
            for slot in horizon.slots:
                self.import_allowed.append(not self._time_window_matcher.matches(forbidden, slot.start))

    def add_variables(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        T = horizon.T
        cfg = self._config
        self.P_import = pulp.LpVariable.dicts("P_grid_import", T, lowBound=0, upBound=cfg.max_import_kw)
        self.P_export = pulp.LpVariable.dicts("P_grid_export", T, lowBound=0, upBound=cfg.max_export_kw)
        self.P_import_violation_kw = pulp.LpVariable.dicts(
            "P_grid_import_violation_kw",
            T,
            lowBound=0,
        )
        self.grid_import_on = pulp.LpVariable.dicts(
            "Grid_import_on",
            T,
            lowBound=0,
            upBound=1,
            cat="Binary",
        )

    def add_constraints(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        T = horizon.T
        cfg = self._config
        for t in T:
            problem += (
                self.P_import[t] <= cfg.max_import_kw * self.grid_import_on[t],
                f"grid_import_exclusive_t{t}",
            )
            problem += (
                self.P_export[t] <= cfg.max_export_kw * (1 - self.grid_import_on[t]),
                f"grid_export_limit_t{t}",
            )
            problem += (
                self.P_import[t]
                <= cfg.max_import_kw * float(self.import_allowed[t]) + self.P_import_violation_kw[t],
                f"grid_import_forbidden_or_violation_t{t}",
            )

    def get_objective_terms(self, horizon: Horizon) -> pulp.LpAffineExpression:
        T = horizon.T
        export_bonus = (
            1e-4 if self._config.zero_price_export_preference == "export" else -1e-4
        )
        export_price_eff = [
            (
                export_bonus
                if abs(float(self.price_export_effective[t])) <= 1e-9
                else float(self.price_export_effective[t])
            )
            for t in T
        ]

        objective = pulp.lpSum(
            (
                self.P_import[t] * float(self.price_import_effective[t])
                - self.P_export[t] * export_price_eff[t]
            )
            * horizon.dt_hours(t)
            for t in T
        )

        w_violation = 1e3
        objective += pulp.lpSum(
            w_violation * self.P_import_violation_kw[t] * horizon.dt_hours(t) for t in T
        )

        w_early = 1e-4
        objective += pulp.lpSum(
            (-w_early * (self.P_import[t] + self.P_export[t]) * (1.0 / (t + 1)) * horizon.dt_hours(t))
            for t in T
        )

        return objective

    def get_pcc_load_kw(self, t: int) -> pulp.LpAffineExpression | pulp.LpVariable | float:
        # Net grid load at PCC is export - import
        return self.P_export[t] - self.P_import[t]
