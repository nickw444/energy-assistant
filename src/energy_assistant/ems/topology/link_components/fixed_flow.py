from __future__ import annotations

from energy_assistant.ems.milp.context import ConstraintDescriptor
from energy_assistant.ems.topology.deferred import DeferredSeries
from energy_assistant.ems.topology.link_components.base import (
    ConnectionBinding,
    FlowDirection,
    LinkComponent,
)


class FixedFlow(LinkComponent):
    """Fix one directional flow to a per-slot series (kW)."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        values_kw: DeferredSeries[float],
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.values_kw = values_kw
        self.name = str(name)

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintDescriptor]:
        values = self.values_kw.get_for_len(len(connection.T))
        flow = connection.P_a_to_b if self.direction == "a_to_b" else connection.P_b_to_a

        constraints: list[ConstraintDescriptor] = []
        for t in connection.T:
            constraints.append(
                ConstraintDescriptor(
                    f"fixed_flow_{self.name}_{connection.id}_{self.direction}_t{t}",
                    flow[t] == float(values[t]),
                )
            )
        return list(constraints)
