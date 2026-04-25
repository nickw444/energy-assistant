from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import BaseLoadComponentPlan
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.series import interval_series_points
from energy_assistant.ems.system.component import EmsComponent
from energy_assistant.ems.system.context import GraphBuildContext, PlanContext
from energy_assistant.ems.system.types import ComponentType
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import DirectionalLimit, FixedFlow
from energy_assistant.models.inputs import InputValueKind
from energy_assistant.models.plant import LoadComponentConfig


@dataclass(frozen=True, slots=True)
class BaseLoadSolveState:
    connection: Connection
    base_load_kw: list[float]


class BaseLoadComponent(EmsComponent[BaseLoadSolveState, BaseLoadComponentPlan]):
    """Fixed baseline plant load (kW) on the AC bus."""

    component_type: ClassVar[ComponentType] = "load"

    def __init__(
        self,
        *,
        component_id: str,
        switchboard: SwitchboardComponent,
        load: LoadComponentConfig,
    ) -> None:
        self.id = component_id
        self._switchboard = switchboard
        self._config = load

        self.name = self._config.name
        self.node_id = NodeId(component_id)

    def _base_load_kw_from_inputs(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
    ) -> list[float]:
        series = inputs.forecast(self._config.power.key, kind=InputValueKind.POWER)
        if len(series) != horizon.num_intervals:
            raise ValueError("Base load series length does not match horizon")
        return list(series)

    def create_graph_elements(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], BaseLoadSolveState]:
        _ = build_ctx
        base_load_kw = self._base_load_kw_from_inputs(horizon=horizon, inputs=inputs)

        node = Node(
            horizon=horizon,
            id=self.node_id,
            name=self.name,
            node_role="consumer",
        )
        connection = Connection(
            horizon=horizon,
            id=f"{self.id}_link",
            a_node_id=self._switchboard.bus_id,
            b_node_id=self.node_id,
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=None,
                    max_b_to_a_kw=0.0,
                ),
                "fixed_flow": FixedFlow(
                    direction="a_to_b",
                    values_kw=base_load_kw,
                    name=self.id,
                ),
            },
        )
        solve_state = BaseLoadSolveState(connection=connection, base_load_kw=base_load_kw)
        return [node, connection], solve_state

    def extract_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: BaseLoadSolveState,
        plan_ctx: PlanContext,
    ) -> BaseLoadComponentPlan:
        _ = plan_ctx
        horizon = snapshot.ctx.horizon
        return BaseLoadComponentPlan(
            power_kw=interval_series_points(horizon, solve_state.base_load_kw),
        )
