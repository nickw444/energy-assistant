from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import pulp
import pytest

from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.components.grid import GridComponent, GridSolveState
from energy_assistant.ems.components.grid.price_bindings import PriceBindingApplicator
from energy_assistant.ems.components.lib.time_windows import TimeWindowMatcher
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import HorizonFactory
from energy_assistant.ems.inputs.models import AppliedForecastInput, AppliedInputRegistry
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.system.state import SolveStateStore
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import (
    DirectionalLimit,
    FixedFlow,
    SoftDirectionalLimit,
)
from energy_assistant.models.inputs import InputValueKind
from energy_assistant.models.plant import (
    GridComponentConfig,
    GridConstraintsConfig,
    InputReference,
    PriceBindingConfig,
    TimeWindow,
)


def test_soft_import_limit_uses_slack_violation() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(now=now)

    load_kw = [5.0]
    import_limit_kw = [0.0]

    soft = SoftDirectionalLimit(
        direction="b_to_a",
        limit_kw=import_limit_kw,
        penalty_per_kwh=1.0,
        name="import_allowed",
    )

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("bus"), name="Bus", node_role="bus"))
    graph.add_element(Node(horizon=horizon, id=NodeId("grid"), name="Grid", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("load"), name="Load", node_role="consumer"))

    # Fixed load draws 5kW from the bus.
    graph.add_element(
        Connection(
            horizon=horizon,
            id="bus_load",
            a_node_id=NodeId("bus"),
            b_node_id=NodeId("load"),
            policies={
                "directional_limit": DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=0.0),
                "fixed_flow": FixedFlow(direction="a_to_b", values_kw=load_kw, name="load"),
            },
        )
    )

    # Grid import must cover load, but the soft limit is 0.
    grid_conn = Connection(
        horizon=horizon,
        id="grid_bus",
        a_node_id=NodeId("bus"),
        b_node_id=NodeId("grid"),
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=0.0, max_b_to_a_kw=10.0),
            "import_soft_limit": soft,
        },
    )
    graph.add_element(grid_conn)

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    assert value_of(grid_conn.power_out_ba[0]) == pytest.approx(5.0)


def test_resolve_import_allowed_applies_forbidden_windows() -> None:
    now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=180).build(now=now)
    component = GridComponent(
        component_id="grid",
        switchboard=SwitchboardComponent(component_id="sb"),
        grid=GridComponentConfig(
            type="grid",
            connection="sb",
            constraints=GridConstraintsConfig(max_import_kw=10.0, max_export_kw=10.0),
            price_import=PriceBindingConfig(source=InputReference(source="price_import")),
            price_export=PriceBindingConfig(source=InputReference(source="price_export")),
            import_forbidden_periods=[TimeWindow(start="01:00", end="02:00")],
        ),
        time_window_matcher=TimeWindowMatcher(),
        price_binding_applicator=PriceBindingApplicator(),
    )

    assert component._resolve_import_allowed(horizon) == [True, False, True]  # pyright: ignore[reportPrivateUsage]


def test_extract_plan_keeps_import_export_net_consistent() -> None:
    now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(now=now)
    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("sb"), name="SB", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("grid"), name="Grid", node_role="prosumer"))
    connection = Connection(
        horizon=horizon,
        id="grid_link",
        a_node_id=NodeId("sb"),
        b_node_id=NodeId("grid"),
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=10.0),
            "fixed_import": FixedFlow(direction="b_to_a", values_kw=[3.0, 1.0], name="imp"),
            "fixed_export": FixedFlow(direction="a_to_b", values_kw=[0.5, 2.0], name="exp"),
        },
    )
    graph.add_element(connection)

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    component = GridComponent(
        component_id="grid",
        switchboard=SwitchboardComponent(component_id="sb"),
        grid=GridComponentConfig(
            type="grid",
            connection="sb",
            constraints=GridConstraintsConfig(max_import_kw=10.0, max_export_kw=10.0),
            price_import=PriceBindingConfig(source=InputReference(source="price_import")),
            price_export=PriceBindingConfig(source=InputReference(source="price_export")),
        ),
        time_window_matcher=TimeWindowMatcher(),
        price_binding_applicator=PriceBindingApplicator(),
    )
    plan = component.extract_plan(
        snapshot,
        solve_state=GridSolveState(
            connection=connection,
            price_import_raw=[1.0, 2.0],
            price_export_raw=[0.5, 0.6],
            price_import_effective=[1.0, 2.0],
            price_export_effective=[0.5, 0.6],
            import_allowed=[True, True],
        ),
        plan_ctx=PlanContext(components={}, solve_states=SolveStateStore()),
    )

    import_values = [point.value for point in plan.import_kw]
    export_values = [point.value for point in plan.export_kw]
    net_values = [point.value for point in plan.net_kw]
    assert net_values == pytest.approx(
        [import_values[i] - export_values[i] for i in range(len(import_values))]
    )


@pytest.mark.parametrize(
    ("preference", "expect_export"),
    [("export", True), ("curtail", False)],
)
def test_zero_price_export_preference_tiebreak(
    preference: Literal["export", "curtail"], expect_export: bool
) -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    )
    component = GridComponent(
        component_id="grid",
        switchboard=SwitchboardComponent(component_id="sb"),
        grid=GridComponentConfig(
            type="grid",
            connection="sb",
            constraints=GridConstraintsConfig(max_import_kw=10.0, max_export_kw=10.0),
            price_import=PriceBindingConfig(source=InputReference(source="price_import")),
            price_export=PriceBindingConfig(source=InputReference(source="price_export")),
            zero_price_export_preference=preference,
        ),
        time_window_matcher=TimeWindowMatcher(),
        price_binding_applicator=PriceBindingApplicator(),
    )
    elements, state = component.create_graph_elements(
        horizon=horizon,
        inputs=AppliedInputRegistry(
            forecasts={
                "price_import": AppliedForecastInput(
                    key="price_import",
                    kind=InputValueKind.PRICE,
                    series=[0.0],
                ),
                "price_export": AppliedForecastInput(
                    key="price_export",
                    kind=InputValueKind.PRICE,
                    series=[0.0],
                ),
            }
        ),
        build_ctx=GraphBuildContext(components={}, solve_states=SolveStateStore()),
    )

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("sb"), name="SB", node_role="prosumer"))
    graph.add_elements(elements)
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    import_kw = value_of(state.connection.flow_into_node(NodeId("sb"))[0])
    export_kw = value_of(state.connection.flow_out_of_node(NodeId("sb"))[0])
    if expect_export:
        assert export_kw > import_kw
    else:
        assert import_kw > export_kw
