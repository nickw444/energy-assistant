from __future__ import annotations

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
)
from energy_assistant.ems.topology.policies.passthrough import Passthrough


class DirectionalLimit(Passthrough):
    """Hard directional limit on connection flows (kW), optionally enforcing exclusivity."""

    def __init__(
        self,
        *,
        max_a_to_b_kw: float | None,
        max_b_to_a_kw: float | None,
        exclusive: bool = False,
    ) -> None:
        self.max_a_to_b_kw = _validate_limit("max_a_to_b_kw", max_a_to_b_kw)
        self.max_b_to_a_kw = _validate_limit("max_b_to_a_kw", max_b_to_a_kw)
        self.exclusive = bool(exclusive)

        if self.exclusive and (self.max_a_to_b_kw is None or self.max_b_to_a_kw is None):
            raise ValueError(
                "exclusive=True requires finite bounds for both directions; "
                "use numeric max_a_to_b_kw and max_b_to_a_kw"
            )
        self._dir_select_by_segment: dict[str, dict[int, pulp.LpVariable]] = {}

    def dir_select(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        if connection.segment_key not in self._dir_select_by_segment:
            self._dir_select_by_segment[connection.segment_key] = pulp.LpVariable.dicts(
                f"Dir_{connection.segment_key}",
                connection.horizon.T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
        return self._dir_select_by_segment[connection.segment_key]

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        constraints = list(self._passthrough_constraints(connection))
        dir_select = None
        if self.exclusive:
            dir_select = self.dir_select(connection)
        flow_ab = connection.flow_out_ab
        flow_ba = connection.flow_out_ba

        for t in connection.horizon.T:
            if self.max_a_to_b_kw is not None:
                constraints.append(
                    ConstraintSpec(
                        f"limit_{connection.segment_key}_a_to_b_t{t}",
                        flow_ab[t] <= self.max_a_to_b_kw,
                    )
                )
            if self.max_b_to_a_kw is not None:
                constraints.append(
                    ConstraintSpec(
                        f"limit_{connection.segment_key}_b_to_a_t{t}",
                        flow_ba[t] <= self.max_b_to_a_kw,
                    )
                )

        if dir_select is not None:
            if self.max_a_to_b_kw is None or self.max_b_to_a_kw is None:
                raise ValueError(
                    "exclusive=True requires finite bounds for both directions; "
                    "use numeric max_a_to_b_kw and max_b_to_a_kw"
                )
            max_a_to_b_kw = self.max_a_to_b_kw
            max_b_to_a_kw = self.max_b_to_a_kw
            for t in connection.horizon.T:
                constraints.append(
                    ConstraintSpec(
                        f"exclusive_{connection.segment_key}_a_to_b_t{t}",
                        flow_ab[t] <= max_a_to_b_kw * dir_select[t],
                    )
                )
                constraints.append(
                    ConstraintSpec(
                        f"exclusive_{connection.segment_key}_b_to_a_t{t}",
                        flow_ba[t] <= max_b_to_a_kw * (1 - dir_select[t]),
                    )
                )

        return list(constraints)


def _validate_limit(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    if value < 0:
        raise ValueError(f"{name} must be >= 0 or None; got {value}")
    return value
