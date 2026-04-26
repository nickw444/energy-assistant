from __future__ import annotations

from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
    ConnectionPolicy,
)


class DirectionalEfficiency(ConnectionPolicy):
    """Directional energy transfer efficiency: `power_out = eta * power_in`."""

    def __init__(
        self,
        *,
        eta_a_to_b: float,
        eta_b_to_a: float,
    ) -> None:
        self.eta_a_to_b = validate_eta("eta_a_to_b", eta_a_to_b)
        self.eta_b_to_a = validate_eta("eta_b_to_a", eta_b_to_a)

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        constraints: list[ConstraintSpec] = []
        for t in connection.horizon.T:
            constraints.append(
                ConstraintSpec(
                    f"eff_{connection.segment_key}_a_to_b_t{t}",
                    connection.flow_out_ab[t] == self.eta_a_to_b * connection.flow_in_ab[t],
                )
            )
            constraints.append(
                ConstraintSpec(
                    f"eff_{connection.segment_key}_b_to_a_t{t}",
                    connection.flow_out_ba[t] == self.eta_b_to_a * connection.flow_in_ba[t],
                )
            )
        return constraints


def validate_eta(name: str, value: float) -> float:
    if value <= 0 or value > 1.0:
        raise ValueError(f"{name} must be in (0, 1]; got {value}")
    return value
