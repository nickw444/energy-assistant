from __future__ import annotations

from typing import Protocol

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintDescriptor
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.nodes import Node


class Fragment(Protocol):
    @property
    def constraints(self) -> list[ConstraintDescriptor]: ...

    @property
    def objective(self) -> pulp.LpAffineExpression: ...


class GraphFragment(Fragment, Protocol):
    def set_horizon(self, horizon: Horizon, graph: EnergyGraph) -> None: ...


class EnergyGraph:
    """Persistent topology definition (nodes, connections, and extra fragments)."""

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._connections: dict[str, Connection] = {}
        self._extra_fragments: list[GraphFragment] = []

        self._connections_by_node: dict[str, list[Connection]] = {}
        self._horizon: Horizon | None = None

    @property
    def horizon(self) -> Horizon:
        if self._horizon is None:
            raise ValueError("EnergyGraph has no active horizon; call set_horizon()")
        return self._horizon

    @property
    def nodes(self) -> dict[str, Node]:
        return dict(self._nodes)

    @property
    def connections(self) -> dict[str, Connection]:
        return dict(self._connections)

    def add_node(self, node: Node) -> None:
        if node.id in self._nodes:
            raise ValueError(f"Duplicate node id: {node.id}")
        self._nodes[node.id] = node

    def add_bus(self, node: Node) -> None:
        self.add_node(node)

    def add_port(self, node: Node) -> None:
        self.add_node(node)

    def add_storage(self, node: Node) -> None:
        self.add_node(node)

    def add_connection(self, connection: Connection) -> None:
        if connection.id in self._connections:
            raise ValueError(f"Duplicate connection id: {connection.id}")
        if connection.a_node_id not in self._nodes:
            raise ValueError(
                f"Unknown node id for connection {connection.id}: {connection.a_node_id}"
            )
        if connection.b_node_id not in self._nodes:
            raise ValueError(
                f"Unknown node id for connection {connection.id}: {connection.b_node_id}"
            )
        self._connections[connection.id] = connection
        self._connections_by_node.setdefault(connection.a_node_id, []).append(connection)
        self._connections_by_node.setdefault(connection.b_node_id, []).append(connection)

    def add_fragment(self, fragment: GraphFragment) -> None:
        self._extra_fragments.append(fragment)

    def connections_for_node(self, node_id: str) -> list[Connection]:
        return list(self._connections_by_node.get(str(node_id), []))

    @property
    def fragments(self) -> list[Fragment]:
        # Keep stable ordering for debugging.
        fragments: list[Fragment] = []
        fragments.extend(self._connections[cid] for cid in sorted(self._connections))
        fragments.extend(self._nodes[nid] for nid in sorted(self._nodes))
        fragments.extend(self._extra_fragments)
        return fragments

    def set_horizon(self, horizon: Horizon) -> None:
        """Activate per-run vars/constraints for a given horizon."""
        self._horizon = horizon
        for conn in self._connections.values():
            conn.set_horizon(horizon)
        for node in self._nodes.values():
            node.set_horizon(horizon, self)
        for frag in self._extra_fragments:
            frag.set_horizon(horizon, self)
