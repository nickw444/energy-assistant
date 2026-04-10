from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pulp

from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.inputs.transforms import ForecastMultiplier
from energy_assistant.ems.milp.context import ConstraintSpec, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import PvComponentPlan
from energy_assistant.ems.parameters import SeriesParameter
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.series import bool_series, interval_series_points
from energy_assistant.ems.system.component import EmsComponent
from energy_assistant.ems.system.topology import ComponentTopology, GraphBuildContext, PlanContext
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import (
    ConnectionPolicy,
    DirectionalLimit,
    FixedFlow,
    UpperBound,
)
from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
    FlowDirection,
)
from energy_assistant.models.inputs import InputValueKind
from energy_assistant.models.plant import InverterComponentConfig, PvComponentConfig

CurtailmentMode = Literal["load-aware", "binary"] | None

_CURTAIL_POWER_THRESHOLD_KW = 0.01


@dataclass(frozen=True, slots=True)
class PvSolveState:
    available_kw: list[float]
    connection: Connection


class PvComponent(EmsComponent[PvSolveState, PvComponentPlan]):
    def __init__(
        self,
        *,
        component_id: str,
        inverter_id: str,
        inverter: InverterComponentConfig,
        pv: PvComponentConfig,
        dc_bus_id: str,
    ) -> None:
        self.id = str(component_id)
        self.inverter_id = str(inverter_id)
        self.name = str(inverter.name)
        self.dc_bus_id = str(dc_bus_id)
        self.peak_power_kw = float(inverter.peak_power_kw)
        self.curtailment: CurtailmentMode = inverter.curtailment
        self._pv_cfg = pv

        self.node_id = self.id
        self.connection_id = f"{self.id}_link"

        self._available_kw = SeriesParameter[float](f"{self.id}_available_kw")

    def describe_topology(self) -> ComponentTopology:
        return ComponentTopology(
            component_id=self.id,
            component_type="pv",
            connection_target_id=self.inverter_id,
        )

    def update_inputs(self, *, horizon: Horizon, inputs: AppliedInputRegistry) -> None:
        pv_series = inputs.forecast(self._pv_cfg.forecast.key, kind=InputValueKind.POWER)
        if len(pv_series) != horizon.num_intervals:
            raise ValueError("PV forecast series length does not match horizon")
        pv_series = [max(0.0, min(float(v), float(self.peak_power_kw))) for v in pv_series]
        pv_series = ForecastMultiplier(self._pv_cfg.forecast_multiplier).apply(
            pv_series,
            skip_first_slot=False,
        )
        self._available_kw.set([float(x) for x in pv_series])

    def build_graph(
        self,
        *,
        horizon: Horizon,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], PvSolveState]:
        _ = build_ctx
        return self.graph_elements(horizon=horizon)

    def graph_elements(self, *, horizon: Horizon) -> tuple[list[GraphElement], PvSolveState]:
        available_kw = self._available_kw.get()

        node = Node(
            horizon=horizon,
            id=self.node_id,
            name=self._pv_cfg.name or f"PV {self.inverter_id}",
            node_role="producer",
        )

        policies: dict[str, ConnectionPolicy] = {
            "directional_limit": DirectionalLimit(
                max_a_to_b_kw=self.peak_power_kw,
                max_b_to_a_kw=0.0,
            )
        }

        if self.curtailment is None:
            policies["fixed_flow"] = (
                FixedFlow(
                    direction="a_to_b",
                    values_kw=available_kw,
                    name=f"pv_fixed_{self.inverter_id}",
                )
            )
        else:
            policies["upper_bound"] = (
                UpperBound(
                    direction="a_to_b",
                    upper_bounds_kw=available_kw,
                    name=f"pv_ub_{self.inverter_id}",
                )
            )
            policies["curtail_tracking"] = PvCurtailTracking(
                direction="a_to_b",
                available_kw=available_kw,
                name=f"pv_{self.inverter_id}",
            )

            if self.curtailment == "binary":
                policies["binary_curtailment"] = (
                    PvBinaryCurtailment(
                        direction="a_to_b",
                        available_kw=available_kw,
                        name=f"pv_{self.inverter_id}",
                    )
                )
        connection = Connection(
            horizon=horizon,
            id=self.connection_id,
            a_node_id=self.node_id,
            b_node_id=self.dc_bus_id,
            policies=policies,
        )

        solve_state = PvSolveState(
            available_kw=available_kw,
            connection=connection,
        )
        return [node, connection], solve_state

    def pv_kw(self, snapshot: ModelSnapshot, t: int, *, solve_state: PvSolveState) -> float:
        _ = snapshot
        return value_of(solve_state.connection.flow_out_of_node(self.node_id).get(t))

    def curtail_kw(
        self,
        snapshot: ModelSnapshot,
        t: int,
        *,
        solve_state: PvSolveState,
    ) -> float | None:
        _ = snapshot
        curtail_tracking = solve_state.connection.find_policy(
            "curtail_tracking",
            PvCurtailTracking,
        )
        if curtail_tracking is None:
            return None
        v = pulp.value(curtail_tracking.curtail_kw(solve_state.connection).get(t))
        return None if v is None else float(v)

    def curtailment_active(
        self,
        snapshot: ModelSnapshot,
        t: int,
        *,
        solve_state: PvSolveState,
    ) -> bool | None:
        curtail_kw = self.curtail_kw(snapshot, t, solve_state=solve_state)
        if curtail_kw is None:
            return None
        return bool(float(curtail_kw) > _CURTAIL_POWER_THRESHOLD_KW)

    def build_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: PvSolveState,
        plan_ctx: PlanContext,
    ) -> PvComponentPlan:
        _ = plan_ctx
        horizon = snapshot.ctx.horizon
        actual_kw = [self.pv_kw(snapshot, t, solve_state=solve_state) for t in horizon.T]
        curtail_kw = [
            self.curtail_kw(snapshot, t, solve_state=solve_state) or 0.0 for t in horizon.T
        ]
        curtailment = [
            self.curtailment_active(snapshot, t, solve_state=solve_state) or False
            for t in horizon.T
        ]
        return PvComponentPlan(
            available_kw=interval_series_points(horizon, solve_state.available_kw),
            actual_kw=interval_series_points(horizon, actual_kw),
            curtail_kw=interval_series_points(horizon, curtail_kw),
            curtailment=interval_series_points(horizon, bool_series(curtailment)),
        )


class PvCurtailTracking(ConnectionPolicy):
    """Expose curtailment as a derived nonnegative series: available - actual."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        available_kw: list[float],
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.available_kw = [float(v) for v in available_kw]
        self.name = str(name)
        self._curtail_by_connection: dict[str, dict[int, pulp.LpVariable]] = {}

    def curtail_kw(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        if connection.id not in self._curtail_by_connection:
            self._curtail_by_connection[connection.id] = pulp.LpVariable.dicts(
                f"P_curtail_{self.name}_{connection.id}_kw",
                connection.horizon.T,
                lowBound=0,
            )
        return self._curtail_by_connection[connection.id]

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        if len(self.available_kw) != len(connection.horizon.T):
            raise ValueError(
                f"PV available series {self.name!r} length {len(self.available_kw)} does not match "
                f"connection {connection.id!r} horizon length {len(connection.horizon.T)}"
            )
        flow = connection.flow_in_ab if self.direction == "a_to_b" else connection.flow_in_ba
        curtail = self.curtail_kw(connection)
        return list(self._passthrough_constraints(connection)) + [
            ConstraintSpec(
                f"pv_curtail_track_{self.name}_{connection.segment_key}_t{t}",
                curtail[t] == float(self.available_kw[t]) - flow[t],
            )
            for t in connection.horizon.T
        ]


class PvBinaryCurtailment(ConnectionPolicy):
    """Binary curtailment: either produce full available or zero."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        available_kw: list[float],
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.available_kw = [float(v) for v in available_kw]
        self.name = str(name)
        self._curtail_binary_by_connection: dict[str, dict[int, pulp.LpVariable]] = {}

    def curtail_binary(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        if connection.id not in self._curtail_binary_by_connection:
            self._curtail_binary_by_connection[connection.id] = pulp.LpVariable.dicts(
                f"Curtail_{self.name}_{connection.id}",
                connection.horizon.T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
        return self._curtail_binary_by_connection[connection.id]

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        if len(self.available_kw) != len(connection.horizon.T):
            raise ValueError(
                f"PV available series {self.name!r} length {len(self.available_kw)} does not match "
                f"connection {connection.id!r} horizon length {len(connection.horizon.T)}"
            )
        flow = connection.flow_in_ab if self.direction == "a_to_b" else connection.flow_in_ba
        curtail = self.curtail_binary(connection)
        return list(self._passthrough_constraints(connection)) + [
            ConstraintSpec(
                f"pv_binary_{self.name}_{connection.segment_key}_t{t}",
                flow[t] == float(self.available_kw[t]) * (1 - curtail[t]),
            )
            for t in connection.horizon.T
        ]
