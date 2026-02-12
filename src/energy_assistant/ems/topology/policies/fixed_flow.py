from __future__ import annotations

from collections.abc import Sequence

from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
    ConnectionPolicy,
    FlowDirection,
)


class FixedFlow(ConnectionPolicy):
    """Fix one directional flow to a per-slot series (kW)."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        values_kw: Sequence[float],
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.values_kw = [float(v) for v in values_kw]
        self.name = str(name)

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        values = _series_for_connection(self.values_kw, connection, name=self.name)
        flow = connection.flow_in_ab if self.direction == "a_to_b" else connection.flow_in_ba

        constraints: list[ConstraintSpec] = []
        for t in connection.horizon.T:
            constraints.append(
                ConstraintSpec(
                    f"fixed_flow_{self.name}_{connection.id}_{self.direction}_t{t}",
                    flow[t] == float(values[t]),
                )
            )
        return list(constraints)


def _series_for_connection(
    series: list[float],
    connection: ConnectionBinding,
    *,
    name: str,
) -> list[float]:
    if len(series) != len(connection.horizon.T):
        raise ValueError(
            f"FixedFlow series {name!r} length {len(series)} does not match "
            f"connection {connection.id!r} horizon length {len(connection.horizon.T)}"
        )
    return series
