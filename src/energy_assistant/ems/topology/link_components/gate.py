from __future__ import annotations

import math

from energy_assistant.ems.milp.context import ConstraintDescriptor
from energy_assistant.ems.topology.deferred import DeferredSeries
from energy_assistant.ems.topology.link_components.base import (
    ConnectionBinding,
    FlowDirection,
    LinkComponent,
)


class Gate(LinkComponent):
    """Gate one directional flow by a per-slot [0,1] series with a fixed max power."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        gate: DeferredSeries[float],
        max_kw: float,
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.gate = gate
        self.max_kw = float(max_kw)
        self.name = str(name)
        if self.max_kw < 0:
            raise ValueError(f"max_kw must be >= 0; got {self.max_kw}")

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintDescriptor]:
        gate = self.gate.get_for_len(len(connection.T))

        for t, v in enumerate(gate):
            fv = float(v)
            if not math.isfinite(fv) or fv < -1e-9 or fv > 1.0 + 1e-9:
                raise ValueError(f"gate[{t}] must be in [0,1]; got {v}")

        flow = connection.P_a_to_b if self.direction == "a_to_b" else connection.P_b_to_a
        constraints: list[ConstraintDescriptor] = []
        for t in connection.T:
            constraints.append(
                ConstraintDescriptor(
                    f"gate_{self.name}_{connection.id}_{self.direction}_t{t}",
                    flow[t] <= float(self.max_kw) * float(gate[t]),
                )
            )
        return list(constraints)
