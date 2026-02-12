from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.nodes import Node


class Fragment(Protocol):
    @property
    def constraints(self) -> list[ConstraintSpec]: ...

    @property
    def objective(self) -> pulp.LpAffineExpression: ...

type GraphElement = Node | Connection | Fragment


class EnergyGraph:
    """Topology definition for a single planning run."""

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._connections: dict[str, Connection] = {}
        self._extra_fragments: list[Fragment] = []

    def _add_node(self, node: Node) -> None:
        if node.id in self._nodes:
            raise ValueError(f"Duplicate node id: {node.id}")
        self._nodes[node.id] = node

    def _add_connection(self, connection: Connection) -> None:
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
        self._nodes[connection.a_node_id].attach_connection(connection)
        self._nodes[connection.b_node_id].attach_connection(connection)

    def _add_fragment(self, fragment: Fragment) -> None:
        self._extra_fragments.append(fragment)

    def add_element(self, element: GraphElement) -> None:
        if isinstance(element, Node):
            self._add_node(element)
            return
        if isinstance(element, Connection):
            self._add_connection(element)
            return
        self._add_fragment(element)

    def add_elements(self, elements: Iterable[GraphElement]) -> None:
        for element in elements:
            self.add_element(element)

    @property
    def fragments(self) -> list[Fragment]:
        # Keep stable ordering for debugging.
        fragments: list[Fragment] = []
        fragments.extend(self._connections[cid] for cid in sorted(self._connections))
        fragments.extend(self._nodes[nid] for nid in sorted(self._nodes))
        fragments.extend(self._extra_fragments)
        return fragments
