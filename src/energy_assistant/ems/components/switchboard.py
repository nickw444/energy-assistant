from __future__ import annotations

from energy_assistant.ems.models import SwitchboardComponentPlan
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.nodes import Node


class SwitchboardComponent:
    """AC bus representing the main switchboard."""

    def __init__(self, *, component_id: str) -> None:
        self.id = str(component_id)
        self.bus_id = self.id

    def graph_elements(self, *, horizon: Horizon) -> list[GraphElement]:
        bus = Node(
            horizon=horizon,
            id=self.bus_id,
            name="AC Switchboard",
            node_role="bus",
        )
        return [bus]

    def build_plan(self) -> SwitchboardComponentPlan:
        return SwitchboardComponentPlan()
