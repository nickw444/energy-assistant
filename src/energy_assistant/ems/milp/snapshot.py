from __future__ import annotations

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec, ModelContext
from energy_assistant.ems.topology.graph import EnergyGraph


class ModelSnapshot:
    """A solved MILP instance for a single EMS planning solve."""

    def __init__(self, *, ctx: ModelContext, graph: EnergyGraph) -> None:
        self.ctx = ctx
        self.graph = graph

        self.problem = pulp.LpProblem("ems_optimisation", pulp.LpMinimize)

        constraints: list[ConstraintSpec] = []
        objective_terms: list[pulp.LpAffineExpression] = []
        for fragment in self.graph.fragments:
            constraints.extend(fragment.constraints)
            objective_terms.append(fragment.objective)

        _attach_constraints(self.problem, constraints)
        self.objective = (
            pulp.lpSum(objective_terms) if objective_terms else pulp.LpAffineExpression()
        )
        self.problem += self.objective


def _attach_constraints(problem: pulp.LpProblem, constraints: list[ConstraintSpec]) -> None:
    seen: set[str] = set()
    for spec in constraints:
        name = spec.name
        if name in seen:
            raise ValueError(f"Duplicate constraint name: {name}")
        seen.add(name)
        problem += (spec.constraint, name)
