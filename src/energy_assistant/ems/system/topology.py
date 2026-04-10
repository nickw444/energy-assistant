from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Any, Literal

from energy_assistant.ems.system.state import SolveStateStore
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement

AttachmentKind = Literal["ac", "dc"]


@dataclass(frozen=True, slots=True)
class ComponentTopology:
    component_id: str
    component_type: str
    connection_target_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedAttachment:
    component_id: str
    component_type: str
    target_component_id: str
    target_component_type: str
    attachment_kind: AttachmentKind


_TOPOLOGY_TYPE_PRIORITY: dict[str, int] = {
    "switchboard": 0,
    "grid": 1,
    "load": 1,
    "load_controlled_ev": 2,
    "inverter": 2,
    "battery": 3,
    "pv": 3,
}


def infer_attachment_kind(source_component_type: str, target_component_type: str) -> AttachmentKind:
    source = str(source_component_type)
    target = str(target_component_type)

    if source == "switchboard":
        raise ValueError("switchboard components must not declare a parent attachment")

    if source in {"grid", "load", "load_controlled_ev", "inverter"}:
        if target != "switchboard":
            raise ValueError(
                f"{source} components must connect to a switchboard; got {target!r}"
            )
        return "ac"

    if source in {"battery", "pv"}:
        if target != "inverter":
            raise ValueError(f"{source} components must connect to an inverter; got {target!r}")
        return "dc"

    raise ValueError(f"Unsupported component type for topology inference: {source!r}")


class PlantTopology:
    def __init__(
        self,
        *,
        descriptions: dict[str, ComponentTopology],
        attachments: dict[str, ResolvedAttachment],
        children_by_parent: dict[str, tuple[str, ...]],
        component_order: tuple[str, ...],
    ) -> None:
        self._descriptions = descriptions
        self._attachments = attachments
        self._children_by_parent = children_by_parent
        self._component_order = component_order

    @classmethod
    def from_descriptions(cls, descriptions: list[ComponentTopology]) -> PlantTopology:
        by_id: dict[str, ComponentTopology] = {}
        for description in descriptions:
            if description.component_id in by_id:
                raise ValueError(f"Duplicate component id: {description.component_id}")
            by_id[description.component_id] = description

        attachments: dict[str, ResolvedAttachment] = {}
        children: dict[str, list[str]] = defaultdict(list)
        incoming: dict[str, int] = {component_id: 0 for component_id in by_id}

        for description in by_id.values():
            target_id = description.connection_target_id
            if target_id is None:
                continue
            if target_id not in by_id:
                raise ValueError(
                    f"component {description.component_id!r} references missing "
                    f"connection target {target_id!r}"
                )
            target_description = by_id[target_id]
            attachment_kind = infer_attachment_kind(
                description.component_type,
                target_description.component_type,
            )
            attachments[description.component_id] = ResolvedAttachment(
                component_id=description.component_id,
                component_type=description.component_type,
                target_component_id=target_id,
                target_component_type=target_description.component_type,
                attachment_kind=attachment_kind,
            )
            children[target_id].append(description.component_id)
            incoming[description.component_id] += 1

        ready: list[tuple[int, str]] = []
        for component_id, count in incoming.items():
            if count == 0:
                heappush(
                    ready,
                    (
                        _TOPOLOGY_TYPE_PRIORITY.get(by_id[component_id].component_type, 50),
                        component_id,
                    ),
                )

        ordered: list[str] = []
        while ready:
            _priority, component_id = heappop(ready)
            ordered.append(component_id)
            for child_id in sorted(children.get(component_id, [])):
                incoming[child_id] -= 1
                if incoming[child_id] == 0:
                    heappush(
                        ready,
                        (
                            _TOPOLOGY_TYPE_PRIORITY.get(by_id[child_id].component_type, 50),
                            child_id,
                        ),
                    )

        if len(ordered) != len(by_id):
            raise ValueError("Plant topology contains a cycle or unresolved attachment loop")

        return cls(
            descriptions=by_id,
            attachments=attachments,
            children_by_parent={key: tuple(sorted(value)) for key, value in children.items()},
            component_order=tuple(ordered),
        )

    @property
    def component_order(self) -> tuple[str, ...]:
        return self._component_order

    def description_for(self, component_id: str) -> ComponentTopology:
        try:
            return self._descriptions[component_id]
        except KeyError as exc:  # pragma: no cover - defensive
            raise KeyError(f"Unknown component id: {component_id!r}") from exc

    def attachment_for(self, component_id: str) -> ResolvedAttachment | None:
        return self._attachments.get(component_id)

    def children_of(self, component_id: str) -> tuple[str, ...]:
        return self._children_by_parent.get(component_id, ())

    def component_ids_of_type(self, component_type: str) -> tuple[str, ...]:
        matching = [
            component_id
            for component_id in self._component_order
            if self._descriptions[component_id].component_type == component_type
        ]
        return tuple(matching)

    @property
    def component_ids(self) -> tuple[str, ...]:
        return self._component_order


class GraphBuildContext:
    def __init__(
        self,
        *,
        topology: PlantTopology,
        components: dict[str, Any],
        solve_states: SolveStateStore,
    ) -> None:
        self.topology = topology
        self.components = components
        self.solve_states = solve_states
        self._connections_by_component_id: dict[str, Connection] = {}

    def register(self, component_id: str, elements: list[GraphElement]) -> None:
        connection = next(
            (element for element in elements if isinstance(element, Connection)),
            None,
        )
        if connection is not None:
            self._connections_by_component_id[component_id] = connection

    def connection(self, component_id: str) -> Connection | None:
        return self._connections_by_component_id.get(component_id)

    def connections_of_type(self, component_type: str) -> list[Connection]:
        return [
            self._connections_by_component_id[component_id]
            for component_id in self.topology.component_ids_of_type(component_type)
            if component_id in self._connections_by_component_id
        ]

    def component_ids_of_type(self, component_type: str) -> tuple[str, ...]:
        return self.topology.component_ids_of_type(component_type)


class PlanContext:
    def __init__(
        self,
        *,
        topology: PlantTopology,
        components: dict[str, Any],
        solve_states: SolveStateStore,
    ) -> None:
        self.topology = topology
        self.components = components
        self.solve_states = solve_states

    def children_of(self, component_id: str) -> tuple[str, ...]:
        return self.topology.children_of(component_id)

    def component_ids_of_type(self, component_type: str) -> tuple[str, ...]:
        return self.topology.component_ids_of_type(component_type)
