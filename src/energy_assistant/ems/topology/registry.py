from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pulp

if TYPE_CHECKING:
    from energy_assistant.ems.horizon import Horizon
    from energy_assistant.ems.topology.base import EnergyComponent
    from energy_assistant.lib.source_resolver.resolver import ValueResolver

logger = logging.getLogger(__name__)


class Topology:
    """A collection of energy components and their connections."""

    def __init__(self):
        self._components: dict[str, EnergyComponent] = {}

    def add_component(self, component: EnergyComponent) -> None:
        if component.id in self._components:
            raise ValueError(f"Component with id '{component.id}' already exists")
        self._components[component.id] = component

    @property
    def components(self) -> list[EnergyComponent]:
        return list(self._components.values())

    def resolve_data(self, resolver: ValueResolver, horizon_start: Any, interval_minutes: int) -> dict[str, Any]:
        """Resolve data for all components."""
        data = {}
        for component in self._components.values():
            data[component.id] = component.resolve_data(resolver, horizon_start, interval_minutes)
        return data

    def add_variables(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        """Add variables for all components."""
        for component in self._components.values():
            component.add_variables(problem, horizon)

    def add_constraints(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        """Add constraints for all components."""
        for component in self._components.values():
            component.add_constraints(problem, horizon)

    def set_initial_conditions(self, problem: pulp.LpProblem, horizon: Horizon, resolver: ValueResolver) -> None:
        """Set initial conditions for all components."""
        for component in self._components.values():
            component.set_initial_conditions(problem, horizon, resolver)

    def get_objective_terms(self, horizon: Horizon) -> pulp.LpAffineExpression:
        """Sum the objective terms from all components."""
        total_objective = pulp.LpAffineExpression()
        for component in self._components.values():
            total_objective += component.get_objective_terms(horizon)
        return total_objective

    def get_total_pcc_load_kw(self, t: int) -> pulp.LpAffineExpression:
        """Sum the net AC power contribution from all components at the Point of Common Coupling (PCC)."""
        total_pcc_load = pulp.LpAffineExpression()
        for component in self._components.values():
            total_pcc_load += component.get_pcc_load_kw(t)
        return total_pcc_load
