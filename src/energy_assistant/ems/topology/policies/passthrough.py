from __future__ import annotations

from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
    TransferConnectionPolicy,
)


class Passthrough(TransferConnectionPolicy):
    """Lossless transfer mapping: `flow_out == flow_in` per direction."""

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        constraints: list[ConstraintSpec] = []
        for t in connection.horizon.T:
            constraints.append(
                ConstraintSpec(
                    f"passthrough_{connection.id}_a_to_b_t{t}",
                    connection.flow_in_ab[t] == connection.flow_out_ab[t],
                )
            )
            constraints.append(
                ConstraintSpec(
                    f"passthrough_{connection.id}_b_to_a_t{t}",
                    connection.flow_in_ba[t] == connection.flow_out_ba[t],
                )
            )
        return constraints
