from __future__ import annotations

from typing import Literal

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.connection import Connection

NodeRole = Literal["bus", "producer", "consumer", "prosumer"]


class Node:
    """Topology node for a single planning run."""

    def __init__(
        self,
        *,
        horizon: Horizon,
        id: str,
        name: str,
        node_role: NodeRole,
    ) -> None:
        self._horizon = horizon
        self.id = str(id)
        self.name = str(name)
        self.node_role: NodeRole = node_role
        self._connections: list[Connection] = []

    @property
    def horizon(self) -> Horizon:
        return self._horizon

    @property
    def connections(self) -> list[Connection]:
        return list(self._connections)

    def attach_connection(self, connection: Connection) -> None:
        self._connections.append(connection)

    @property
    def net_connection_power(self) -> dict[int, pulp.LpAffineExpression]:
        return {
            t: pulp.lpSum(
                conn.flow_into_node(self.id)[t] - conn.flow_out_of_node(self.id)[t]
                for conn in self.connections
            )
            for t in self.horizon.T
        }

    @property
    def constraints(self) -> list[ConstraintSpec]:
        net_power = self.net_connection_power
        if self.node_role == "bus":
            return [
                ConstraintSpec(
                    f"balance_{self.id}_t{t}",
                    net_power[t] == 0,
                )
                for t in self.horizon.T
            ]
        if self.node_role == "producer":
            # Producer net flow should not be importing.
            return [
                ConstraintSpec(
                    f"role_producer_{self.id}_t{t}",
                    net_power[t] <= 0,
                )
                for t in self.horizon.T
            ]
        if self.node_role == "consumer":
            # Consumer net flow should not be exporting.
            return [
                ConstraintSpec(
                    f"role_consumer_{self.id}_t{t}",
                    net_power[t] >= 0,
                )
                for t in self.horizon.T
            ]
        return []

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return pulp.LpAffineExpression()
