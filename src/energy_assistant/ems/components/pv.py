from __future__ import annotations

from typing import Literal

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec, ObjectiveTerm
from energy_assistant.ems.system.system import ModelSnapshot
from energy_assistant.ems.topology.connection import ConnectionTemplate
from energy_assistant.ems.topology.graph import EnergyGraphModel, EnergyGraphTemplate, FragmentModel
from energy_assistant.ems.topology.link_components import (
    DirectionalLimit,
    FixedFlowSeries,
    LinkComponentTemplate,
    UpperBoundSeries,
)
from energy_assistant.ems.topology.nodes import PortNodeTemplate

CurtailmentMode = Literal["load-aware", "binary"] | None


class PvComponent:
    def __init__(
        self,
        *,
        graph: EnergyGraphTemplate,
        inverter_id: str,
        dc_bus_id: str,
        peak_power_kw: float,
        curtailment: CurtailmentMode,
        available_series_key: str,
    ) -> None:
        self.inverter_id = str(inverter_id)
        self.dc_bus_id = str(dc_bus_id)
        self.peak_power_kw = float(peak_power_kw)
        self.curtailment = curtailment
        self.available_series_key = str(available_series_key)

        self.node_id = f"pv_{self.inverter_id}"
        self.connection_id = f"pv_{self.inverter_id}_link"

        self._tracking_enabled = curtailment is not None
        self._binary = curtailment == "binary"

        graph.add_port(PortNodeTemplate(id=self.node_id, name=f"PV {self.inverter_id}"))

        link_components: list[LinkComponentTemplate] = [
            DirectionalLimit(max_a_to_b_kw=self.peak_power_kw, max_b_to_a_kw=0.0),
        ]
        if curtailment is None:
            link_components.append(
                FixedFlowSeries(
                    direction="a_to_b",
                    value_key=self.available_series_key,
                    name=f"pv_fixed_{self.inverter_id}",
                )
            )
        else:
            link_components.append(
                UpperBoundSeries(
                    direction="a_to_b",
                    ub_key=self.available_series_key,
                    name=f"pv_ub_{self.inverter_id}",
                )
            )

        graph.add_connection(
            ConnectionTemplate(
                id=self.connection_id,
                a_node_id=self.node_id,
                b_node_id=self.dc_bus_id,
                link_components=link_components,
            )
        )

        if self._tracking_enabled:
            graph.add_fragment(
                PvCurtailTrackingTemplate(
                    connection_id=self.connection_id,
                    available_key=self.available_series_key,
                )
            )
        if self._binary:
            graph.add_fragment(
                PvBinaryCurtailmentTemplate(
                    connection_id=self.connection_id,
                    available_key=self.available_series_key,
                )
            )

    def pv_kw(self, snapshot: ModelSnapshot, t: int) -> float:
        conn = snapshot.graph.connections[self.connection_id]
        return float(pulp.value(conn.P_a_to_b[t]) or 0.0)

    def curtail_kw(self, snapshot: ModelSnapshot, t: int) -> float | None:
        if not self._tracking_enabled:
            return None
        model = _find_fragment(snapshot.graph, PvCurtailTrackingModel, self.connection_id)
        if model is None:
            return None
        v = pulp.value(model.P_curtail_kw[t])
        return None if v is None else float(v)


class PvCurtailTrackingTemplate:
    def __init__(self, *, connection_id: str, available_key: str) -> None:
        self.connection_id = str(connection_id)
        self.available_key = str(available_key)

    def bind(self, graph: EnergyGraphModel) -> PvCurtailTrackingModel:
        return PvCurtailTrackingModel(
            graph=graph,
            connection_id=self.connection_id,
            available_key=self.available_key,
        )


class PvCurtailTrackingModel:
    def __init__(self, *, graph: EnergyGraphModel, connection_id: str, available_key: str) -> None:
        self.connection_id = str(connection_id)
        self.available_key = str(available_key)

        ctx = graph.ctx
        conn = graph.connections[self.connection_id]
        available = ctx.inputs.float_series(self.available_key)

        self.P_curtail_kw: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"P_curtail_{self.connection_id}_kw",
            ctx.horizon.T,
            lowBound=0,
        )
        self._constraints: list[ConstraintSpec] = []
        for t in ctx.horizon.T:
            self._constraints.append(
                ConstraintSpec(
                    f"pv_curtail_track_{self.connection_id}_t{t}",
                    self.P_curtail_kw[t] == float(available[t]) - conn.P_a_to_b[t],
                )
            )
        self._objective_terms: list[ObjectiveTerm] = []

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)

    @property
    def objective_terms(self) -> list[ObjectiveTerm]:
        return list(self._objective_terms)


class PvBinaryCurtailmentTemplate:
    def __init__(self, *, connection_id: str, available_key: str) -> None:
        self.connection_id = str(connection_id)
        self.available_key = str(available_key)

    def bind(self, graph: EnergyGraphModel) -> PvBinaryCurtailmentModel:
        return PvBinaryCurtailmentModel(
            graph=graph,
            connection_id=self.connection_id,
            available_key=self.available_key,
        )


class PvBinaryCurtailmentModel:
    def __init__(self, *, graph: EnergyGraphModel, connection_id: str, available_key: str) -> None:
        self.connection_id = str(connection_id)
        self.available_key = str(available_key)

        ctx = graph.ctx
        conn = graph.connections[self.connection_id]
        available = ctx.inputs.float_series(self.available_key)

        self.curtail_binary: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"Curtail_{self.connection_id}",
            ctx.horizon.T,
            lowBound=0,
            upBound=1,
            cat="Binary",
        )

        self._constraints: list[ConstraintSpec] = []
        for t in ctx.horizon.T:
            self._constraints.append(
                ConstraintSpec(
                    f"pv_binary_{self.connection_id}_t{t}",
                    conn.P_a_to_b[t] == float(available[t]) * (1 - self.curtail_binary[t]),
                )
            )
        self._objective_terms: list[ObjectiveTerm] = []

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)

    @property
    def objective_terms(self) -> list[ObjectiveTerm]:
        return list(self._objective_terms)


def _find_fragment[TFragment: FragmentModel](
    graph: EnergyGraphModel,
    cls: type[TFragment],
    connection_id: str,
) -> TFragment | None:
    cid = str(connection_id)
    for frag in graph.extra_fragments:
        if isinstance(frag, cls) and getattr(frag, "connection_id", None) == cid:
            return frag
    return None
