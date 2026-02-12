from __future__ import annotations

from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
    TransferConnectionPolicy,
    validate_eta,
)


class DirectionalEfficiency(TransferConnectionPolicy):
    """Directional energy transfer efficiency: `power_out = eta * power_in`."""

    def __init__(
        self,
        *,
        eta_a_to_b: float,
        eta_b_to_a: float,
    ) -> None:
        self.eta_a_to_b = validate_eta("eta_a_to_b", float(eta_a_to_b))
        self.eta_b_to_a = validate_eta("eta_b_to_a", float(eta_b_to_a))

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        constraints: list[ConstraintSpec] = []
        for t in connection.horizon.T:
            constraints.append(
                ConstraintSpec(
                    f"eff_{connection.id}_a_to_b_t{t}",
                    connection.flow_out_ab[t]
                    == float(self.eta_a_to_b) * connection.flow_in_ab[t],
                )
            )
            constraints.append(
                ConstraintSpec(
                    f"eff_{connection.id}_b_to_a_t{t}",
                    connection.flow_out_ba[t]
                    == float(self.eta_b_to_a) * connection.flow_in_ba[t],
                )
            )
        return constraints
