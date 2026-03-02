from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import pulp

if TYPE_CHECKING:
    from energy_assistant.ems.horizon import Horizon
    from energy_assistant.lib.source_resolver.resolver import ValueResolver


class EnergyComponent(ABC):
    """Base class for all energy system components (Grid, PV, Battery, EV, Load)."""

    def __init__(self, id: str, name: str):
        self.id = id
        self.name = name

    @abstractmethod
    def resolve_data(self, resolver: ValueResolver, horizon_start: Any, interval_minutes: int) -> dict[str, Any]:
        """Resolve any forecasts or realtime data needed for this component."""
        pass

    @abstractmethod
    def add_variables(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        """Add decision variables for this component to the MILP problem."""
        pass

    @abstractmethod
    def add_constraints(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        """Add physical and operational constraints for this component."""
        pass

    def set_initial_conditions(self, problem: pulp.LpProblem, horizon: Horizon, resolver: ValueResolver) -> None:
        """Set initial conditions for this component based on realtime data."""
        pass

    @abstractmethod
    def get_objective_terms(self, horizon: Horizon) -> pulp.LpAffineExpression:
        """Return the contribution of this component to the MILP objective function."""
        pass

    @abstractmethod
    def get_pcc_load_kw(self, t: int) -> pulp.LpAffineExpression | pulp.LpVariable | float:
        """Return the net AC power contribution of this component at the Point of Common Coupling (PCC).

        Positive values represent power consumption (load), negative values represent power generation.
        """
        pass
