from __future__ import annotations

from typing import Literal, Protocol

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintSpec

FlowDirection = Literal["a_to_b", "b_to_a"]


class ConnectionBinding(Protocol):
    id: str
    horizon: Horizon

    @property
    def flow_in_ab(self) -> dict[int, pulp.LpVariable]: ...

    @property
    def flow_out_ab(self) -> dict[int, pulp.LpVariable]: ...

    @property
    def flow_in_ba(self) -> dict[int, pulp.LpVariable]: ...

    @property
    def flow_out_ba(self) -> dict[int, pulp.LpVariable]: ...


class ConnectionPolicy:
    """Connection augmentation: query-only constraints/objective."""

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        _ = connection
        return []

    def objective(self, connection: ConnectionBinding) -> pulp.LpAffineExpression:
        _ = connection
        return pulp.LpAffineExpression()


class TransferConnectionPolicy(ConnectionPolicy):
    """Defines the connection's transfer law between endpoint flows.

    A transfer policy maps source-side flow to sink-side flow per direction,
    for example:
    - lossless transport (`Passthrough`):
      `flow_in_ab == flow_out_ab` and `flow_in_ba == flow_out_ba`
    - lossy transport (`DirectionalEfficiency`):
      `flow_out_ab == eta_a_to_b * flow_in_ab`
      and `flow_out_ba == eta_b_to_a * flow_in_ba`

    Exactly one transfer policy must exist per connection:
    - none => source/sink variables are not tied together (physically incomplete)
    - more than one => conflicting/duplicated transfer equations (ambiguous model)
    """


def validate_eta(name: str, value: float) -> float:
    v = float(value)
    if v <= 0 or v > 1.0:
        raise ValueError(f"{name} must be in (0, 1]; got {v}")
    return v
