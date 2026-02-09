from __future__ import annotations

from typing import TYPE_CHECKING

import pulp

from energy_assistant.ems.horizon import Horizon

if TYPE_CHECKING:
    from energy_assistant.ems.system.inputs import EmsInputs


class ConstraintSpec:
    """A named constraint returned by a topology fragment.

    Fragments are query-only: they return constraints as values rather than mutating the PuLP
    problem directly. The assembly step attaches these constraints to the final problem.
    """

    def __init__(self, name: str, constraint: pulp.LpConstraint) -> None:
        self.name = str(name)
        self.constraint = constraint


class ObjectiveTerm:
    """An objective contribution returned by a topology fragment."""

    def __init__(self, expr: pulp.LpAffineExpression, *, name: str | None = None) -> None:
        self.expr = expr
        self.name = None if name is None else str(name)


class ModelContext:
    """Per-run context shared by all bound topology fragments."""

    def __init__(self, *, horizon: Horizon, inputs: EmsInputs) -> None:
        self.horizon = horizon
        self.inputs = inputs


def value_of(expr: pulp.LpVariable | pulp.LpAffineExpression | None) -> float:
    """Safe numeric extraction for solved variables/expressions."""

    if expr is None:
        return 0.0
    v = pulp.value(expr)
    return 0.0 if v is None else float(v)

