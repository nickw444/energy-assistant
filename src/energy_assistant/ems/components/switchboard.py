from __future__ import annotations

from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.nodes import BusNode


class SwitchboardComponent:
    """AC junction bus representing the main switchboard."""

    def __init__(self, *, graph: EnergyGraph, bus_id: str = "ac_bus") -> None:
        self.bus_id = str(bus_id)
        graph.add_bus(BusNode(id=self.bus_id, name="AC Switchboard", domain="ac"))
