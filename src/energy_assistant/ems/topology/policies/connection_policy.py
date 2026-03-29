from __future__ import annotations

from typing import Literal, Protocol

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.planning.horizon import Horizon

FlowDirection = Literal["a_to_b", "b_to_a"]


class ConnectionBinding(Protocol):
    id: str
    horizon: Horizon

    @property
    def segment_key(self) -> str: ...

    @property
    def flow_in_ab(self) -> dict[int, pulp.LpVariable]: ...

    @property
    def flow_out_ab(self) -> dict[int, pulp.LpVariable]: ...

    @property
    def flow_in_ba(self) -> dict[int, pulp.LpVariable]: ...

    @property
    def flow_out_ba(self) -> dict[int, pulp.LpVariable]: ...


class ConnectionPolicy:
    """Composable connection policy.

    Policies are composed as an ordered chain within a connection. Each policy
    sees a segment-scoped input/output flow pair per direction, can define how
    flow transfers across that segment, and can also add extra constraints or
    objective terms. The default segment law is passthrough, so every policy
    participates through the same `constraints(...)` interface regardless of
    whether it is lossless, lossy, limiting, or purely economic.
    """

    def _passthrough_constraints(
        self,
        connection: ConnectionBinding,
    ) -> list[ConstraintSpec]:
        return [
            ConstraintSpec(
                f"policy_transfer_{connection.segment_key}_a_to_b_t{t}",
                connection.flow_in_ab[t] == connection.flow_out_ab[t],
            )
            for t in connection.horizon.T
        ] + [
            ConstraintSpec(
                f"policy_transfer_{connection.segment_key}_b_to_a_t{t}",
                connection.flow_in_ba[t] == connection.flow_out_ba[t],
            )
            for t in connection.horizon.T
        ]

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        return self._passthrough_constraints(connection)

    def objective(self, connection: ConnectionBinding) -> pulp.LpAffineExpression:
        _ = connection
        return pulp.LpAffineExpression()


def validate_eta(name: str, value: float) -> float:
    v = float(value)
    if v <= 0 or v > 1.0:
        raise ValueError(f"{name} must be in (0, 1]; got {v}")
    return v
