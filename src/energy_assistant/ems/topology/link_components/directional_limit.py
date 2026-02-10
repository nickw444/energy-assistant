from __future__ import annotations

from energy_assistant.ems.milp.context import ConstraintDescriptor
from energy_assistant.ems.topology.link_components.base import ConnectionBinding, LinkComponent


class DirectionalLimit(LinkComponent):
    """Hard directional limit on connection flows (kW), optionally enforcing exclusivity."""

    def __init__(
        self,
        *,
        max_a_to_b_kw: float,
        max_b_to_a_kw: float,
        exclusive: bool = False,
    ) -> None:
        self.max_a_to_b_kw = float(max_a_to_b_kw)
        self.max_b_to_a_kw = float(max_b_to_a_kw)
        self.exclusive = bool(exclusive)

        if self.max_a_to_b_kw < 0:
            raise ValueError(f"max_a_to_b_kw must be >= 0; got {self.max_a_to_b_kw}")
        if self.max_b_to_a_kw < 0:
            raise ValueError(f"max_b_to_a_kw must be >= 0; got {self.max_b_to_a_kw}")

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintDescriptor]:
        dir_select = None
        if self.exclusive:
            dir_select = connection.binary_series(f"Dir_{connection.id}")

        constraints: list[ConstraintDescriptor] = []
        for t in connection.T:
            constraints.append(
                ConstraintDescriptor(
                    f"limit_{connection.id}_a_to_b_t{t}",
                    connection.P_a_to_b[t] <= float(self.max_a_to_b_kw),
                )
            )
            constraints.append(
                ConstraintDescriptor(
                    f"limit_{connection.id}_b_to_a_t{t}",
                    connection.P_b_to_a[t] <= float(self.max_b_to_a_kw),
                )
            )

        if dir_select is not None:
            for t in connection.T:
                constraints.append(
                    ConstraintDescriptor(
                        f"exclusive_{connection.id}_a_to_b_t{t}",
                        connection.P_a_to_b[t] <= float(self.max_a_to_b_kw) * dir_select[t],
                    )
                )
                constraints.append(
                    ConstraintDescriptor(
                        f"exclusive_{connection.id}_b_to_a_t{t}",
                        connection.P_b_to_a[t]
                        <= float(self.max_b_to_a_kw) * (1 - dir_select[t]),
                    )
                )

        return list(constraints)
