from __future__ import annotations

from collections.abc import Sequence

import pulp

from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
)
from energy_assistant.ems.topology.policies.passthrough import Passthrough


class LinearCost(Passthrough):
    """Linear objective cost for directional flows ($/kWh), with per-slot coefficients."""

    def __init__(
        self,
        *,
        cost_a_to_b_per_kwh: Sequence[float],
        cost_b_to_a_per_kwh: Sequence[float],
        name: str,
    ) -> None:
        self.cost_a_to_b_per_kwh = list(cost_a_to_b_per_kwh)
        self.cost_b_to_a_per_kwh = list(cost_b_to_a_per_kwh)
        self.name = name

    def objective(self, connection: ConnectionBinding) -> pulp.LpAffineExpression:
        cost_ab = _series_for_connection(self.cost_a_to_b_per_kwh, connection, name=self.name)
        cost_ba = _series_for_connection(self.cost_b_to_a_per_kwh, connection, name=self.name)
        flow_ab = connection.flow_out_ab
        flow_ba = connection.flow_out_ba

        expr = pulp.lpSum(
            (flow_ab[t] * cost_ab[t] + flow_ba[t] * cost_ba[t]) * connection.horizon.dt_hours(t)
            for t in connection.horizon.T
        )
        return expr


def _series_for_connection(
    series: list[float],
    connection: ConnectionBinding,
    *,
    name: str,
) -> list[float]:
    if len(series) != len(connection.horizon.T):
        raise ValueError(
            f"LinearCost series {name!r} length {len(series)} does not match "
            f"connection {connection.id!r} horizon length {len(connection.horizon.T)}"
        )
    return series
