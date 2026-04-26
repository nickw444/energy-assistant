from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from energy_assistant.ems.system.state import SolveStateStore
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement

T = TypeVar("T")


class GraphBuildContext:
    def __init__(
        self,
        *,
        components: Mapping[str, Any],
        solve_states: SolveStateStore,
    ) -> None:
        self.components = components
        self.solve_states = solve_states
        self._connections_by_component_id: dict[str, list[Connection]] = {}

    def register(self, component_id: str, elements: list[GraphElement]) -> None:
        connections = [element for element in elements if isinstance(element, Connection)]
        if connections:
            self._connections_by_component_id.setdefault(component_id, []).extend(connections)

    def components_of_type(self, component_type: type[T]) -> tuple[T, ...]:
        return tuple(
            component
            for component in self.components.values()
            if isinstance(component, component_type)
        )

    def connections(self, component_id: str) -> tuple[Connection, ...]:
        return tuple(self._connections_by_component_id.get(component_id, ()))

class PlanContext:
    def __init__(
        self,
        *,
        components: Mapping[str, Any],
        solve_states: SolveStateStore,
    ) -> None:
        self.components = components
        self.solve_states = solve_states

