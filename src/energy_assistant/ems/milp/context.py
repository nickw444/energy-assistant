from __future__ import annotations

import pulp

from energy_assistant.ems.horizon import Horizon


class ConstraintSpec:
    """A named constraint returned by a topology fragment.

    Fragments are query-only: they return constraints as values rather than mutating the PuLP
    problem directly. The assembly step attaches these constraints to the final problem.
    """

    def __init__(self, name: str, constraint: pulp.LpConstraint) -> None:
        self.name = name
        self.constraint = constraint


class ModelContext:
    """Per-solve context shared by all bound topology fragments."""

    def __init__(self, *, horizon: Horizon) -> None:
        self.horizon = horizon


def value_of(expr: pulp.LpVariable | pulp.LpAffineExpression | None) -> float:
    """Safe numeric extraction for solved variables/expressions."""

    if expr is None:
        return 0.0
    v = pulp.value(expr)
    return 0.0 if v is None else float(v)
