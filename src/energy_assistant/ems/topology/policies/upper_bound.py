from __future__ import annotations

from collections.abc import Sequence

from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
    FlowDirection,
)
from energy_assistant.ems.topology.policies.passthrough import Passthrough


class UpperBound(Passthrough):
    """Per-slot upper bound on one directional flow (kW)."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        upper_bounds_kw: Sequence[float],
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.upper_bounds_kw = list(upper_bounds_kw)
        self.name = name

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        ub = _series_for_connection(self.upper_bounds_kw, connection, name=self.name)
        flow = connection.flow_out_ab if self.direction == "a_to_b" else connection.flow_out_ba

        constraints = list(self._passthrough_constraints(connection))
        for t in connection.horizon.T:
            constraints.append(
                ConstraintSpec(
                    f"ub_{self.name}_{connection.segment_key}_{self.direction}_t{t}",
                    flow[t] <= ub[t],
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
            f"UpperBound series {name!r} length {len(series)} does not match "
            f"connection {connection.id!r} horizon length {len(connection.horizon.T)}"
        )
    return series
