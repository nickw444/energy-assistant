from __future__ import annotations

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.nodes import Node


class SwitchboardComponent:
    """AC bus representing the main switchboard."""

    def __init__(self, *, component_id: str) -> None:
        self.id = str(component_id)
        self.bus_id = self.id
        self._latest_bus: Node | None = None

    def graph_elements(self, *, horizon: Horizon) -> list[GraphElement]:
        bus = Node(
            horizon=horizon,
            id=self.bus_id,
            name="AC Switchboard",
            node_role="bus",
        )
        self._latest_bus = bus
        return [bus]
