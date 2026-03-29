from __future__ import annotations

from datetime import datetime

from energy_assistant.ems.forecast_alignment import PowerForecastAligner, forecast_coverage_slots
from energy_assistant.ems.horizon import Horizon, floor_to_interval_boundary
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import DirectionalLimit, FixedFlow
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.plant import PlantLoadConfig


class BaseLoadRun:
    def __init__(self, *, base_load_kw: list[float], connection: Connection) -> None:
        self.base_load_kw = [float(v) for v in base_load_kw]
        self.connection = connection


class BaseLoadComponent:
    """Fixed baseline plant load (kW) on the AC bus."""

    def __init__(
        self,
        *,
        bus_id: str,
        load: PlantLoadConfig,
        node_id: str = "base_load",
        connection_id: str = "base_load_link",
    ) -> None:
        self.bus_id = str(bus_id)
        self.node_id = str(node_id)
        self.connection_id = str(connection_id)
        self._load = load

        self._aligner = PowerForecastAligner()
        self._latest: BaseLoadRun | None = None

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

    def build(
        self,
        *,
        horizon: Horizon,
        resolver: ValueResolver,
    ) -> list[GraphElement]:
        realtime_load = float(resolver.resolve(self._load.realtime_load_power))
        intervals = resolver.resolve(self._load.forecast)
        series = self._aligner.align(
            horizon,
            intervals,
            first_slot_override=realtime_load,
        )
        base_load_kw = [float(x) for x in series]

        node = Node(
            horizon=horizon,
            id=self.node_id,
            name="Base Load",
            node_role="consumer",
        )
        connection = Connection(
            horizon=horizon,
            id=self.connection_id,
            a_node_id=self.bus_id,
            b_node_id=self.node_id,
            policies={
                # One-way consumption (AC -> Load).
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=None,
                    max_b_to_a_kw=0.0,
                ),
                "fixed_flow": FixedFlow(
                    direction="a_to_b",
                    values_kw=base_load_kw,
                    name="base_load",
                ),
            },
        )
        self._latest = BaseLoadRun(base_load_kw=base_load_kw, connection=connection)
        return [node, connection]

    def latest_base_load_kw(self) -> list[float]:
        if self._latest is None:
            raise ValueError("BaseLoadComponent has not been built for this run")
        return list(self._latest.base_load_kw)
