from __future__ import annotations

from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import SwitchboardComponentPlan
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.system.component import EmsComponent
from energy_assistant.ems.system.topology import ComponentTopology, GraphBuildContext, PlanContext
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.nodes import Node


class SwitchboardComponent(EmsComponent[None, SwitchboardComponentPlan]):
    """AC bus representing the main switchboard."""

    def __init__(self, *, component_id: str) -> None:
        self.id = str(component_id)
        self.bus_id = self.id

    def describe_topology(self) -> ComponentTopology:
        return ComponentTopology(component_id=self.id, component_type="switchboard")

    def update_inputs(self, *, horizon: Horizon, inputs: AppliedInputRegistry) -> None:
        _ = horizon, inputs

    def build_graph(
        self,
        *,
        horizon: Horizon,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], None]:
        _ = build_ctx
        return self.graph_elements(horizon=horizon), None

    def graph_elements(self, *, horizon: Horizon) -> list[GraphElement]:
        bus = Node(
            horizon=horizon,
            id=self.bus_id,
            name="AC Switchboard",
            node_role="bus",
        )
        return [bus]

    def build_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: None,
        plan_ctx: PlanContext,
    ) -> SwitchboardComponentPlan:
        _ = snapshot, solve_state, plan_ctx
        return SwitchboardComponentPlan()
