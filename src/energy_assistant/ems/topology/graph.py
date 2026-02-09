from __future__ import annotations

from typing import Protocol, runtime_checkable

from energy_assistant.ems.milp.context import ConstraintSpec, ModelContext, ObjectiveTerm
from energy_assistant.ems.topology.connection import ConnectionModel, ConnectionTemplate
from energy_assistant.ems.topology.nodes import (
    BusNodeModel,
    BusNodeTemplate,
    NodeModel,
    PortNodeModel,
    PortNodeTemplate,
    StorageNodeModel,
    StorageNodeTemplate,
)


@runtime_checkable
class FragmentModel(Protocol):
    @property
    def constraints(self) -> list[ConstraintSpec]: ...

    @property
    def objective_terms(self) -> list[ObjectiveTerm]: ...


class FragmentTemplate(Protocol):
    def bind(self, graph: EnergyGraphModel) -> FragmentModel: ...


class EnergyGraphTemplate:
    def __init__(self) -> None:
        self._bus_nodes: dict[str, BusNodeTemplate] = {}
        self._port_nodes: dict[str, PortNodeTemplate] = {}
        self._storage_nodes: dict[str, StorageNodeTemplate] = {}
        self._connections: dict[str, ConnectionTemplate] = {}
        self._fragments: list[FragmentTemplate] = []

    @property
    def bus_node_templates(self) -> dict[str, BusNodeTemplate]:
        return dict(self._bus_nodes)

    @property
    def port_node_templates(self) -> dict[str, PortNodeTemplate]:
        return dict(self._port_nodes)

    @property
    def storage_node_templates(self) -> dict[str, StorageNodeTemplate]:
        return dict(self._storage_nodes)

    @property
    def connection_templates(self) -> dict[str, ConnectionTemplate]:
        return dict(self._connections)

    @property
    def fragment_templates(self) -> list[FragmentTemplate]:
        return list(self._fragments)

    def add_bus(self, node: BusNodeTemplate) -> None:
        self._add_node(node.id)
        self._bus_nodes[node.id] = node

    def add_port(self, node: PortNodeTemplate) -> None:
        self._add_node(node.id)
        self._port_nodes[node.id] = node

    def add_storage(self, node: StorageNodeTemplate) -> None:
        self._add_node(node.id)
        self._storage_nodes[node.id] = node

    def add_connection(self, connection: ConnectionTemplate) -> None:
        if connection.id in self._connections:
            raise ValueError(f"Duplicate connection id: {connection.id}")
        # Nodes must exist.
        if not self.has_node(connection.a_node_id):
            raise ValueError(
                f"Unknown node id for connection {connection.id}: {connection.a_node_id}"
            )
        if not self.has_node(connection.b_node_id):
            raise ValueError(
                f"Unknown node id for connection {connection.id}: {connection.b_node_id}"
            )
        self._connections[connection.id] = connection

    def add_fragment(self, fragment: FragmentTemplate) -> None:
        self._fragments.append(fragment)

    def has_node(self, node_id: str) -> bool:
        nid = str(node_id)
        return (
            nid in self._bus_nodes
            or nid in self._port_nodes
            or nid in self._storage_nodes
        )

    def bind(self, ctx: ModelContext) -> EnergyGraphModel:
        return EnergyGraphModel(template=self, ctx=ctx)

    def _add_node(self, node_id: str) -> None:
        if self.has_node(node_id):
            raise ValueError(f"Duplicate node id: {node_id}")


class EnergyGraphModel:
    def __init__(self, *, template: EnergyGraphTemplate, ctx: ModelContext) -> None:
        self.ctx = ctx

        self.connections: dict[str, ConnectionModel] = {
            cid: ctpl.bind(ctx) for cid, ctpl in template.connection_templates.items()
        }

        self._connections_by_node: dict[str, list[ConnectionModel]] = {}
        for conn in self.connections.values():
            self._connections_by_node.setdefault(conn.a_node_id, []).append(conn)
            self._connections_by_node.setdefault(conn.b_node_id, []).append(conn)

        self.bus_nodes: dict[str, BusNodeModel] = {
            nid: ntpl.bind(self) for nid, ntpl in template.bus_node_templates.items()
        }
        self.port_nodes: dict[str, PortNodeModel] = {
            nid: ntpl.bind(self) for nid, ntpl in template.port_node_templates.items()
        }
        self.storage_nodes: dict[str, StorageNodeModel] = {
            nid: ntpl.bind(self) for nid, ntpl in template.storage_node_templates.items()
        }

        self.fragments: list[FragmentModel] = []
        # Order is not semantically important, but keep it stable for debuggability.
        self.fragments.extend(self.connections.values())
        self.fragments.extend(self.bus_nodes.values())
        self.fragments.extend(self.port_nodes.values())
        self.fragments.extend(self.storage_nodes.values())

        self.extra_fragments: list[FragmentModel] = [
            frag.bind(self) for frag in template.fragment_templates
        ]
        self.fragments.extend(self.extra_fragments)

    def connections_for_node(self, node_id: str) -> list[ConnectionModel]:
        return list(self._connections_by_node.get(str(node_id), []))

    def node(self, node_id: str) -> NodeModel:
        nid = str(node_id)
        if nid in self.bus_nodes:
            return self.bus_nodes[nid]
        if nid in self.port_nodes:
            return self.port_nodes[nid]
        if nid in self.storage_nodes:
            return self.storage_nodes[nid]
        raise KeyError(nid)
