from __future__ import annotations

from energy_assistant.ems.topology.connection import ConnectionTemplate
from energy_assistant.ems.topology.graph import EnergyGraphTemplate
from energy_assistant.ems.topology.link_components import DirectionalLimit, FixedFlowSeries
from energy_assistant.ems.topology.nodes import PortNodeTemplate


class BaseLoadComponent:
    """Fixed baseline plant load (kW) on the AC bus."""

    def __init__(
        self,
        *,
        graph: EnergyGraphTemplate,
        switchboard_bus_id: str,
        node_id: str = "base_load",
        connection_id: str = "base_load_link",
        series_key: str = "base_load_kw",
    ) -> None:
        self.switchboard_bus_id = str(switchboard_bus_id)
        self.node_id = str(node_id)
        self.connection_id = str(connection_id)
        self.series_key = str(series_key)

        graph.add_port(PortNodeTemplate(id=self.node_id, name="Base Load"))
        graph.add_connection(
            ConnectionTemplate(
                id=self.connection_id,
                a_node_id=self.switchboard_bus_id,
                b_node_id=self.node_id,
                link_components=[
                    # One-way consumption (AC -> Load).
                    DirectionalLimit(max_a_to_b_kw=1e9, max_b_to_a_kw=0.0),
                    FixedFlowSeries(
                        direction="a_to_b",
                        value_key=self.series_key,
                        name="base_load",
                    ),
                ],
            )
        )
