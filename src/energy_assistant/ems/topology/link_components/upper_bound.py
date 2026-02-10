from __future__ import annotations

from energy_assistant.ems.milp.context import ConstraintDescriptor
from energy_assistant.ems.topology.deferred import DeferredSeries
from energy_assistant.ems.topology.link_components.base import (
    ConnectionBinding,
    FlowDirection,
    LinkComponent,
)


class UpperBound(LinkComponent):
    """Per-slot upper bound on one directional flow (kW)."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        upper_bounds_kw: DeferredSeries[float],
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.upper_bounds_kw = upper_bounds_kw
        self.name = str(name)

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintDescriptor]:
        ub = self.upper_bounds_kw.get_for_len(len(connection.T))
        flow = connection.P_a_to_b if self.direction == "a_to_b" else connection.P_b_to_a

        constraints: list[ConstraintDescriptor] = []
        for t in connection.T:
            constraints.append(
                ConstraintDescriptor(
                    f"ub_{self.name}_{connection.id}_{self.direction}_t{t}",
                    flow[t] <= float(ub[t]),
                )
            )
        return list(constraints)
