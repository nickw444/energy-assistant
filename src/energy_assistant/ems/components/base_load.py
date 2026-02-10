from __future__ import annotations

from datetime import datetime

from energy_assistant.ems.forecast_alignment import PowerForecastAligner, forecast_coverage_slots
from energy_assistant.ems.horizon import Horizon, floor_to_interval_boundary
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.deferred import DeferredSeries
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.link_components import DirectionalLimit, FixedFlow
from energy_assistant.ems.topology.nodes import PortNode
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.plant import PlantLoadConfig


class BaseLoadComponent:
    """Fixed baseline plant load (kW) on the AC bus."""

    def __init__(
        self,
        *,
        graph: EnergyGraph,
        bus_id: str,
        load: PlantLoadConfig,
        node_id: str = "base_load",
        connection_id: str = "base_load_link",
    ) -> None:
        self.bus_id = str(bus_id)
        self.node_id = str(node_id)
        self.connection_id = str(connection_id)
        self._load = load

        self.base_load_kw = DeferredSeries[float](name="base_load_kw")
        self._aligner = PowerForecastAligner()

        graph.add_port(PortNode(id=self.node_id, name="Base Load"))
        graph.add_connection(
            Connection(
                id=self.connection_id,
                a_node_id=self.bus_id,
                b_node_id=self.node_id,
                link_components=[
                    # One-way consumption (AC -> Load).
                    DirectionalLimit(
                        max_a_to_b_kw=1e9,
                        max_b_to_a_kw=0.0,
                    ),
                    FixedFlow(
                        direction="a_to_b",
                        values_kw=self.base_load_kw,
                        name="base_load",
                    ),
                ],
            )
        )

    def mark_for_hydration(self, resolver: ValueResolver) -> None:
        resolver.mark_for_hydration(self._load.realtime_load_power)
        resolver.mark_for_hydration(self._load.forecast)

    def forecast_coverage_intervals(
        self, *, now: datetime, interval_minutes: int, resolver: ValueResolver
    ) -> int:
        start = floor_to_interval_boundary(now, interval_minutes)
        intervals = resolver.resolve(self._load.forecast)
        return forecast_coverage_slots(
            start,
            interval_minutes,
            intervals,
            allow_first_slot_missing=True,
        )

    def update(self, *, horizon: Horizon, resolver: ValueResolver) -> None:
        realtime_load = float(resolver.resolve(self._load.realtime_load_power))
        intervals = resolver.resolve(self._load.forecast)
        series = self._aligner.align(
            horizon,
            intervals,
            first_slot_override=realtime_load,
        )
        self.base_load_kw.set([float(x) for x in series])
