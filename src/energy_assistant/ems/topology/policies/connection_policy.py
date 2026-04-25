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
    objective terms.
    """

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        _ = connection
        return []

    def objective(self, connection: ConnectionBinding) -> pulp.LpAffineExpression:
        _ = connection
        return pulp.LpAffineExpression()
