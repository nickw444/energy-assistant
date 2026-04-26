from __future__ import annotations

from energy_assistant.ems.components.component import EmsComponent
from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import SwitchboardComponentPlan
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node


class SwitchboardComponent(EmsComponent[None, SwitchboardComponentPlan]):
    """AC bus representing the main switchboard."""

    def __init__(self, *, component_id: str) -> None:
        self.id = component_id
        self.bus_id = NodeId(component_id)

    def create_graph_elements(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], None]:
        _ = inputs, build_ctx
        bus = Node(
            horizon=horizon,
            id=self.bus_id,
            name="AC Switchboard",
            node_role="bus",
        )
        return [bus], None

    def extract_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: None,
        plan_ctx: PlanContext,
    ) -> SwitchboardComponentPlan:
        _ = snapshot, solve_state, plan_ctx
        return SwitchboardComponentPlan()
