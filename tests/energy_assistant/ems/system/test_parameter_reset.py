from __future__ import annotations

from datetime import UTC, datetime

from energy_assistant.ems.components.component import EmsComponent
from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import Horizon, HorizonFactory
from energy_assistant.ems.inputs.models import AppliedForecastInput, AppliedInputRegistry
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import BaseLoadComponentPlan
from energy_assistant.ems.system.system import EmsSystem
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import DirectionalLimit, FixedFlow
from energy_assistant.models.inputs import InputValueKind


class _SnapshotLabelLoad(EmsComponent[None, BaseLoadComponentPlan]):

    def __init__(self, *, component_id: str, switchboard: SwitchboardComponent) -> None:
        self.id = component_id
        self.switchboard = switchboard
        self._label_input_key = f"{component_id}_label"

    def create_graph_elements(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], None]:
        _ = build_ctx
        label_series = inputs.forecast(self._label_input_key, kind=InputValueKind.POWER)
        if len(label_series) != horizon.num_intervals:
            raise ValueError("Label series length does not match horizon")

        label_value = label_series[0]
        load_node_id = NodeId(f"{self.id}_node")
        node = Node(
            horizon=horizon,
            id=load_node_id,
            name=f"load-{int(label_value)}",
            node_role="consumer",
        )
        connection = Connection(
            horizon=horizon,
            id=f"{self.id}_link",
            a_node_id=self.switchboard.bus_id,
            b_node_id=load_node_id,
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=None,
                    max_b_to_a_kw=0.0,
                ),
                "fixed_flow": FixedFlow(
                    direction="a_to_b",
                    values_kw=[label_value] * int(horizon.num_intervals),
                    name=self.id,
                ),
            },
        )
        return [node, connection], None

    def extract_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: None,
        plan_ctx: PlanContext,
    ) -> BaseLoadComponentPlan:
        _ = snapshot, solve_state, plan_ctx
        return BaseLoadComponentPlan(power_kw=[])


def test_build_snapshot_uses_fresh_inputs_each_solve() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    )
    switchboard = SwitchboardComponent(component_id="switchboard")
    load = _SnapshotLabelLoad(component_id="load", switchboard=switchboard)
    system = EmsSystem(
        components={"switchboard": switchboard, "load": load},
        ordered_components=(switchboard, load),
    )

    first_snapshot, _ = system.build_snapshot(
        horizon=horizon,
        inputs=AppliedInputRegistry(
            forecasts={
                "load_label": AppliedForecastInput(
                    key="load_label",
                    kind=InputValueKind.POWER,
                    series=[1.0],
                )
            }
        ),
    )
    second_snapshot, _ = system.build_snapshot(
        horizon=horizon,
        inputs=AppliedInputRegistry(
            forecasts={
                "load_label": AppliedForecastInput(
                    key="load_label",
                    kind=InputValueKind.POWER,
                    series=[2.0],
                )
            }
        ),
    )

    first_node = next(
        fragment
        for fragment in first_snapshot.graph.fragments
        if isinstance(fragment, Node) and fragment.id == "load_node"
    )
    second_node = next(
        fragment
        for fragment in second_snapshot.graph.fragments
        if isinstance(fragment, Node) and fragment.id == "load_node"
    )
    first_connection = next(
        fragment
        for fragment in first_snapshot.graph.fragments
        if isinstance(fragment, Connection) and fragment.id == "load_link"
    )
    second_connection = next(
        fragment
        for fragment in second_snapshot.graph.fragments
        if isinstance(fragment, Connection) and fragment.id == "load_link"
    )

    first_fixed_flow = first_connection.policy("fixed_flow", FixedFlow)
    second_fixed_flow = second_connection.policy("fixed_flow", FixedFlow)

    assert first_node.name == "load-1"
    assert second_node.name == "load-2"
    assert first_fixed_flow.values_kw == [1.0]
    assert second_fixed_flow.values_kw == [2.0]
