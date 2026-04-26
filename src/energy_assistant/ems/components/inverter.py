from __future__ import annotations

from dataclasses import dataclass

from energy_assistant.ems.components.component import EmsComponent
from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.context import value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import InverterComponentPlan
from energy_assistant.ems.series import interval_series_points
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import DirectionalLimit
from energy_assistant.models.plant import InverterComponentConfig


@dataclass(frozen=True, slots=True)
class InverterSolveState:
    inverter_connection: Connection


class InverterComponent(EmsComponent[InverterSolveState, InverterComponentPlan]):
    def __init__(
        self,
        *,
        component_id: str,
        switchboard: SwitchboardComponent,
        inverter: InverterComponentConfig,
    ) -> None:
        self.id = component_id
        self.switchboard = switchboard
        self._config = inverter

        self.name = self._config.name
        self.dc_bus_id = NodeId(f"{self.id}_dc")

    @property
    def peak_power_kw(self) -> float:
        return self._config.peak_power_kw

    @property
    def curtailment(self) -> str | None:
        return self._config.curtailment

    def create_graph_elements(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], InverterSolveState]:
        _ = inputs, build_ctx
        inverter_connection = Connection(
            horizon=horizon,
            id=f"{self.id}_acdc",
            a_node_id=self.dc_bus_id,
            b_node_id=self.switchboard.bus_id,
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=self._config.peak_power_kw,
                    max_b_to_a_kw=self._config.peak_power_kw,
                    exclusive=True,
                ),
            },
        )
        elements: list[GraphElement] = [
            Node(
                horizon=horizon,
                id=self.dc_bus_id,
                name=f"DC Bus {self.id}",
                node_role="bus",
            ),
            inverter_connection,
        ]
        solve_state = InverterSolveState(
            inverter_connection=inverter_connection,
        )
        return elements, solve_state

    def extract_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: InverterSolveState,
        plan_ctx: PlanContext,
    ) -> InverterComponentPlan:
        _ = plan_ctx
        horizon = snapshot.ctx.horizon
        inverter_connection = solve_state.inverter_connection
        ac_net_kw = [
            value_of(inverter_connection.flow_into_node(self.switchboard.bus_id).get(t))
            - value_of(inverter_connection.flow_out_of_node(self.switchboard.bus_id).get(t))
            for t in horizon.T
        ]
        return InverterComponentPlan(
            ac_net_kw=interval_series_points(horizon, ac_net_kw),
        )
