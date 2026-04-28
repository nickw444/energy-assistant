from __future__ import annotations

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes.node import Node


class StorageNode(Node):
    """Energy storage node with SoC dynamics and optional terminal constraints/objective."""

    def __init__(
        self,
        *,
        horizon: Horizon,
        id: NodeId,
        name: str,
        capacity_kwh: float,
        soc_min_kwh: float,
        soc_max_kwh: float,
        initial_soc_kwh: float,
        stored_energy_value_per_kwh: float | None = None,
    ) -> None:
        super().__init__(
            horizon=horizon,
            id=id,
            name=name,
            node_role="prosumer",
        )
        self.capacity_kwh = capacity_kwh
        self.soc_min_kwh = soc_min_kwh
        self.soc_max_kwh = soc_max_kwh
        self.initial_soc_kwh = initial_soc_kwh
        self.stored_energy_value_per_kwh = stored_energy_value_per_kwh

        soc_indices = range(int(horizon.num_intervals) + 1)
        self.E_by_i: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"E_{self.id}_kwh",
            soc_indices,
            lowBound=self.soc_min_kwh,
            upBound=self.soc_max_kwh,
        )

    @property
    def _storage_connection(self) -> Connection:
        incident = self.connections
        if len(incident) != 1:
            raise ValueError(
                f"Storage node {self.id!r} must have exactly 1 incident connection; "
                f"got {len(incident)}"
            )
        conn = incident[0]

        if self.id not in {conn.a_node_id, conn.b_node_id}:
            raise ValueError("Graph adjacency invariant violated")
        return conn

    @property
    def charge_power(self) -> dict[int, pulp.LpVariable]:
        return self._storage_connection.flow_into_node(self.id)

    @property
    def discharge_power(self) -> dict[int, pulp.LpVariable]:
        return self._storage_connection.flow_out_of_node(self.id)

    @property
    def net_storage_power(self) -> dict[int, pulp.LpAffineExpression]:
        charge = self.charge_power
        discharge = self.discharge_power
        return {
            t: charge[t] - discharge[t]
            for t in self.horizon.T
        }

    @property
    def terminal_index(self) -> int:
        return int(self.horizon.num_intervals)

    @property
    def terminal_soc(self) -> pulp.LpVariable:
        return self.E_by_i[self.terminal_index]

    def _terminal_soc_value_objective(self) -> pulp.LpAffineExpression | None:
        value_per_kwh = max(0.0, self.stored_energy_value_per_kwh or 0.0)
        if value_per_kwh <= 0:
            return None
        return -value_per_kwh * self.terminal_soc

    @property
    def constraints(self) -> list[ConstraintSpec]:
        constraints: list[ConstraintSpec] = [
            ConstraintSpec(
                f"soc_initial_{self.id}",
                self.E_by_i[0] == self.initial_soc_kwh,
            )
        ]
        net_power = self.net_storage_power
        constraints.extend(
            ConstraintSpec(
                f"soc_step_{self.id}_t{t}",
                self.E_by_i[t + 1]
                == self.E_by_i[t] + net_power[t] * self.horizon.dt_hours(t),
            )
            for t in self.horizon.T
        )

        return constraints

    @property
    def objective(self) -> pulp.LpAffineExpression:
        objective_parts: list[pulp.LpAffineExpression] = []

        terminal_value = self._terminal_soc_value_objective()
        if terminal_value is not None:
            objective_parts.append(terminal_value)

        return pulp.lpSum(objective_parts) if objective_parts else pulp.LpAffineExpression()

