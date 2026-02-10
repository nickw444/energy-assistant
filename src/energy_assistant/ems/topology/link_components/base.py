from __future__ import annotations

from typing import Literal, Protocol

import pulp

from energy_assistant.ems.milp.context import ConstraintDescriptor

FlowDirection = Literal["a_to_b", "b_to_a"]


class ConnectionBinding(Protocol):
    id: str
    P_a_to_b: dict[int, pulp.LpVariable]
    P_b_to_a: dict[int, pulp.LpVariable]
    dt_hours: dict[int, float]
    T: list[int]

    def binary_series(self, name: str) -> dict[int, pulp.LpVariable]: ...

    def nonnegative_series(self, name: str) -> dict[int, pulp.LpVariable]: ...

    def unit_series(self, name: str) -> dict[int, pulp.LpVariable]: ...


class LinkComponent:
    """Connection augmentation: query-only constraints/objective and efficiency multipliers."""

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintDescriptor]:
        _ = connection
        return []

    def objective(self, connection: ConnectionBinding) -> pulp.LpAffineExpression:
        _ = connection
        return pulp.LpAffineExpression()

    def transport_efficiency(self, direction: FlowDirection) -> float:
        _ = direction
        return 1.0

    def storage_efficiency(self, direction: FlowDirection) -> float:
        _ = direction
        return 1.0


def validate_eta(name: str, value: float) -> float:
    v = float(value)
    if v <= 0 or v > 1.0:
        raise ValueError(f"{name} must be in (0, 1]; got {v}")
    return v
