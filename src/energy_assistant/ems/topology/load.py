from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pulp

from energy_assistant.ems.forecast_alignment import PowerForecastAligner
from energy_assistant.ems.topology.base import EnergyComponent

if TYPE_CHECKING:
    from energy_assistant.ems.horizon import Horizon
    from energy_assistant.lib.source_resolver.resolver import ValueResolver
    from energy_assistant.models.plant import PlantLoadConfig


class PlantLoadComponent(EnergyComponent):
    def __init__(self, config: PlantLoadConfig):
        super().__init__(id="plant_load", name="Plant Load")
        self._config = config
        self._power_aligner = PowerForecastAligner()
        self.base_load_kw: list[float] = []

    def resolve_data(
        self,
        resolver: ValueResolver,
        horizon_start: Any,
        interval_minutes: int,
    ) -> dict[str, Any]:
        load_intervals = resolver.resolve(self._config.forecast)
        return {"load_intervals": load_intervals}

    def align_data(self, horizon: Horizon, resolver: ValueResolver, resolved_forecasts: dict[str, Any]) -> None:
        realtime_load = resolver.resolve(self._config.realtime_load_power)
        self.base_load_kw = self._power_aligner.align(
            horizon,
            resolved_forecasts["load_intervals"],
            first_slot_override=realtime_load,
        )

    def add_variables(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        # No decision variables for base plant load.
        pass

    def add_constraints(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        # No constraints for base plant load.
        pass

    def get_objective_terms(self, horizon: Horizon) -> pulp.LpAffineExpression:
        # Base load cost is implicitly handled by the Grid component's objective
        # which balances total load (including plant load).
        return pulp.LpAffineExpression()

    def get_pcc_load_kw(self, t: int) -> float:
        return float(self.base_load_kw[t]) if t < len(self.base_load_kw) else 0.0
