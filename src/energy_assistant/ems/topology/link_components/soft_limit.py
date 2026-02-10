from __future__ import annotations

import pulp

from energy_assistant.ems.milp.context import ConstraintDescriptor
from energy_assistant.ems.topology.deferred import DeferredSeries
from energy_assistant.ems.topology.link_components.base import (
    ConnectionBinding,
    FlowDirection,
    LinkComponent,
)


class SoftDirectionalLimit(LinkComponent):
    """Soft upper limit (kW) on one direction with slack + penalty."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        limit_kw: DeferredSeries[float],
        penalty_per_kwh: float,
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.limit_kw = limit_kw
        self.penalty_per_kwh = float(penalty_per_kwh)
        self.name = str(name)
        if self.penalty_per_kwh < 0:
            raise ValueError(f"penalty_per_kwh must be >= 0; got {self.penalty_per_kwh}")

    def slack_kw(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        # Expose slack vars for plan extraction.
        return connection.nonnegative_series(f"S_{connection.id}_{self.direction}_kw")

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintDescriptor]:
        limit_series = self.limit_kw.get_for_len(len(connection.T))
        slack = self.slack_kw(connection)
        flow = connection.P_a_to_b if self.direction == "a_to_b" else connection.P_b_to_a

        constraints: list[ConstraintDescriptor] = []
        for t in connection.T:
            constraints.append(
                ConstraintDescriptor(
                    f"soft_limit_{self.name}_{connection.id}_{self.direction}_t{t}",
                    flow[t] <= float(limit_series[t]) + slack[t],
                )
            )
        return list(constraints)

    def objective(self, connection: ConnectionBinding) -> pulp.LpAffineExpression:
        if self.penalty_per_kwh <= 0:
            return pulp.LpAffineExpression()

        slack = self.slack_kw(connection)
        expr = pulp.lpSum(
            float(self.penalty_per_kwh) * slack[t] * float(connection.dt_hours[t])
            for t in connection.T
        )
        return expr
