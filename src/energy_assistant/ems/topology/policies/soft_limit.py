from __future__ import annotations

from collections.abc import Sequence

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
    ConnectionPolicy,
    FlowDirection,
)


class SoftDirectionalLimit(ConnectionPolicy):
    """Soft upper limit (kW) on one direction with slack + penalty."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        limit_kw: Sequence[float],
        penalty_per_kwh: float,
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.limit_kw = [float(v) for v in limit_kw]
        self.penalty_per_kwh = float(penalty_per_kwh)
        self.name = str(name)
        if self.penalty_per_kwh < 0:
            raise ValueError(f"penalty_per_kwh must be >= 0; got {self.penalty_per_kwh}")
        self._slack_by_connection: dict[str, dict[int, pulp.LpVariable]] = {}

    def slack_kw(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        # Expose slack vars for plan extraction.
        if connection.id not in self._slack_by_connection:
            self._slack_by_connection[connection.id] = pulp.LpVariable.dicts(
                f"S_{connection.id}_{self.direction}_kw",
                connection.horizon.T,
                lowBound=0,
            )
        return self._slack_by_connection[connection.id]

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        limit_series = _series_for_connection(self.limit_kw, connection, name=self.name)
        slack = self.slack_kw(connection)
        flow = connection.flow_out_ab if self.direction == "a_to_b" else connection.flow_out_ba

        constraints: list[ConstraintSpec] = []
        for t in connection.horizon.T:
            constraints.append(
                ConstraintSpec(
                    f"soft_limit_{self.name}_{connection.segment_key}_{self.direction}_t{t}",
                    flow[t] <= float(limit_series[t]) + slack[t],
                )
            )
        return list(constraints)

    def objective(self, connection: ConnectionBinding) -> pulp.LpAffineExpression:
        if self.penalty_per_kwh <= 0:
            return pulp.LpAffineExpression()

        slack = self.slack_kw(connection)
        expr = pulp.lpSum(
            float(self.penalty_per_kwh) * slack[t] * float(connection.horizon.dt_hours(t))
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
            f"SoftDirectionalLimit series {name!r} length {len(series)} does not match "
            f"connection {connection.id!r} horizon length {len(connection.horizon.T)}"
        )
    return series
