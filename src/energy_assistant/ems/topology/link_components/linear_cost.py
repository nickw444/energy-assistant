from __future__ import annotations

import pulp

from energy_assistant.ems.topology.deferred import DeferredSeries
from energy_assistant.ems.topology.link_components.base import ConnectionBinding, LinkComponent


class LinearCost(LinkComponent):
    """Linear objective cost for directional flows ($/kWh), with per-slot coefficients."""

    def __init__(
        self,
        *,
        cost_a_to_b_per_kwh: DeferredSeries[float],
        cost_b_to_a_per_kwh: DeferredSeries[float],
        name: str,
    ) -> None:
        self.cost_a_to_b_per_kwh = cost_a_to_b_per_kwh
        self.cost_b_to_a_per_kwh = cost_b_to_a_per_kwh
        self.name = str(name)

    def objective(self, connection: ConnectionBinding) -> pulp.LpAffineExpression:
        cost_ab = self.cost_a_to_b_per_kwh.get_for_len(len(connection.T))
        cost_ba = self.cost_b_to_a_per_kwh.get_for_len(len(connection.T))

        expr = pulp.lpSum(
            (
                connection.P_a_to_b[t] * float(cost_ab[t])
                + connection.P_b_to_a[t] * float(cost_ba[t])
            )
            * float(connection.dt_hours[t])
            for t in connection.T
        )
        return expr
