from __future__ import annotations

from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
    ConnectionPolicy,
)


class Passthrough(ConnectionPolicy):
    """Lossless segment transfer policy: `flow_out == flow_in` in both directions."""

    def _passthrough_constraints(
        self,
        connection: ConnectionBinding,
    ) -> list[ConstraintSpec]:
        return [
            ConstraintSpec(
                f"policy_transfer_{connection.segment_key}_a_to_b_t{t}",
                connection.flow_in_ab[t] == connection.flow_out_ab[t],
            )
            for t in connection.horizon.T
        ] + [
            ConstraintSpec(
                f"policy_transfer_{connection.segment_key}_b_to_a_t{t}",
                connection.flow_in_ba[t] == connection.flow_out_ba[t],
            )
            for t in connection.horizon.T
        ]

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        return self._passthrough_constraints(connection)
