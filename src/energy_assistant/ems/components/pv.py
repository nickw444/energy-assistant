from __future__ import annotations

from datetime import datetime
from typing import Literal

import pulp

from energy_assistant.ems.forecast_alignment import PowerForecastAligner, forecast_coverage_slots
from energy_assistant.ems.forecast_multiplier import ForecastMultiplier
from energy_assistant.ems.horizon import Horizon, floor_to_interval_boundary
from energy_assistant.ems.milp.context import ConstraintDescriptor, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.deferred import DeferredSeries
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.link_components import (
    DirectionalLimit,
    FixedFlow,
    LinkComponent,
    UpperBound,
)
from energy_assistant.ems.topology.link_components.base import ConnectionBinding, FlowDirection
from energy_assistant.ems.topology.nodes import PortNode
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.plant import InverterConfig

CurtailmentMode = Literal["load-aware", "binary"] | None

_CURTAIL_POWER_THRESHOLD_KW = 0.01


class PvCurtailTracking(LinkComponent):
    """Expose curtailment as a derived nonnegative series: available - actual."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        available_kw: DeferredSeries[float],
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.available_kw = available_kw
        self.name = str(name)

    def curtail_kw(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        return connection.nonnegative_series(f"P_curtail_{self.name}_{connection.id}_kw")

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintDescriptor]:
        available = self.available_kw.get_for_len(len(connection.T))
        flow = connection.P_a_to_b if self.direction == "a_to_b" else connection.P_b_to_a
        curtail = self.curtail_kw(connection)
        return [
            ConstraintDescriptor(
                f"pv_curtail_track_{self.name}_{connection.id}_t{t}",
                curtail[t] == float(available[t]) - flow[t],
            )
            for t in connection.T
        ]


class PvBinaryCurtailment(LinkComponent):
    """Binary curtailment: either produce full available or zero."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        available_kw: DeferredSeries[float],
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.available_kw = available_kw
        self.name = str(name)

    def curtail_binary(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        return connection.binary_series(f"Curtail_{self.name}_{connection.id}")

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintDescriptor]:
        available = self.available_kw.get_for_len(len(connection.T))
        flow = connection.P_a_to_b if self.direction == "a_to_b" else connection.P_b_to_a
        curtail = self.curtail_binary(connection)
        return [
            ConstraintDescriptor(
                f"pv_binary_{self.name}_{connection.id}_t{t}",
                flow[t] == float(available[t]) * (1 - curtail[t]),
            )
            for t in connection.T
        ]


class PvComponent:
    def __init__(
        self,
        *,
        graph: EnergyGraph,
        inverter: InverterConfig,
        dc_bus_id: str,
    ) -> None:
        self.inverter_id = str(inverter.id)
        self.name = str(inverter.name)
        self.dc_bus_id = str(dc_bus_id)
        self.peak_power_kw = float(inverter.peak_power_kw)
        self.curtailment: CurtailmentMode = inverter.curtailment
        self._pv_cfg = inverter.pv

        self.node_id = f"pv_{self.inverter_id}"
        self.connection_id = f"pv_{self.inverter_id}_link"

        self.available_kw = DeferredSeries[float](name=f"pv_available:{self.inverter_id}")
        self._aligner = PowerForecastAligner()

        self.connection: Connection
        self._curtail_tracking: PvCurtailTracking | None = None
        self._binary_curtail: PvBinaryCurtailment | None = None

        graph.add_port(PortNode(id=self.node_id, name=f"PV {self.inverter_id}"))

        link_components: list[LinkComponent] = [
            DirectionalLimit(max_a_to_b_kw=self.peak_power_kw, max_b_to_a_kw=0.0),
        ]

        if self.curtailment is None:
            link_components.append(
                FixedFlow(
                    direction="a_to_b",
                    values_kw=self.available_kw,
                    name=f"pv_fixed_{self.inverter_id}",
                )
            )
        else:
            link_components.append(
                UpperBound(
                    direction="a_to_b",
                    upper_bounds_kw=self.available_kw,
                    name=f"pv_ub_{self.inverter_id}",
                )
            )
            self._curtail_tracking = PvCurtailTracking(
                direction="a_to_b",
                available_kw=self.available_kw,
                name=f"pv_{self.inverter_id}",
            )
            link_components.append(self._curtail_tracking)

            if self.curtailment == "binary":
                self._binary_curtail = PvBinaryCurtailment(
                    direction="a_to_b",
                    available_kw=self.available_kw,
                    name=f"pv_{self.inverter_id}",
                )
                link_components.append(self._binary_curtail)

        self.connection = Connection(
            id=self.connection_id,
            a_node_id=self.node_id,
            b_node_id=self.dc_bus_id,
            link_components=link_components,
        )
        graph.add_connection(self.connection)

    def mark_for_hydration(self, resolver: ValueResolver) -> None:
        if self._pv_cfg.realtime_power is not None:
            resolver.mark_for_hydration(self._pv_cfg.realtime_power)
        resolver.mark_for_hydration(self._pv_cfg.forecast)

    def forecast_coverage_intervals(
        self, *, now: datetime, interval_minutes: int, resolver: ValueResolver
    ) -> int:
        start = floor_to_interval_boundary(now, interval_minutes)
        intervals = resolver.resolve(self._pv_cfg.forecast)
        allow_first_slot_missing = self._pv_cfg.realtime_power is not None
        return int(
            forecast_coverage_slots(
                start,
                interval_minutes,
                intervals,
                allow_first_slot_missing=allow_first_slot_missing,
            )
        )

    def update(self, *, horizon: Horizon, resolver: ValueResolver) -> None:
        realtime_pv = None
        if self._pv_cfg.realtime_power is not None:
            realtime_pv = float(resolver.resolve(self._pv_cfg.realtime_power))

        intervals = resolver.resolve(self._pv_cfg.forecast)
        pv_series = self._aligner.align(
            horizon,
            intervals,
            first_slot_override=realtime_pv,
        )

        pv_series = [max(0.0, min(float(v), float(self.peak_power_kw))) for v in pv_series]
        pv_series = ForecastMultiplier(self._pv_cfg.forecast_multiplier).apply(
            pv_series,
            skip_first_slot=realtime_pv is not None,
        )
        self.available_kw.set([float(x) for x in pv_series])

    def pv_kw(self, snapshot: ModelSnapshot, t: int) -> float:
        _ = snapshot
        return value_of(self.connection.P_a_to_b.get(t))

    def curtail_kw(self, snapshot: ModelSnapshot, t: int) -> float | None:
        _ = snapshot
        if self._curtail_tracking is None:
            return None
        v = pulp.value(self._curtail_tracking.curtail_kw(self.connection).get(t))
        return None if v is None else float(v)

    def curtailment_active(self, snapshot: ModelSnapshot, t: int) -> bool | None:
        curtail_kw = self.curtail_kw(snapshot, t)
        if curtail_kw is None:
            return None
        return bool(float(curtail_kw) > _CURTAIL_POWER_THRESHOLD_KW)
