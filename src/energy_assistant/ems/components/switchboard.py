from __future__ import annotations

from energy_assistant.ems.topology.graph import EnergyGraphTemplate
from energy_assistant.ems.topology.nodes import BusNodeTemplate


class SwitchboardComponent:
    """AC junction bus representing the main switchboard."""

    def __init__(self, *, graph: EnergyGraphTemplate, bus_id: str = "ac_switchboard") -> None:
        self.bus_id = str(bus_id)
        graph.add_bus(BusNodeTemplate(id=self.bus_id, name="AC Switchboard", domain="ac"))

