from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.components.pv import PvBinaryCurtailment, PvComponent, PvCurtailTracking
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
    LinearCost,
    UpperBound,
)
from energy_assistant.models.inputs import InputValueKind
from energy_assistant.models.plant import InputReference, InverterComponentConfig, PvComponentConfig


def test_pv_binary_curtailment_can_force_all_or_nothing() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(now=now)

    available = [5.0]

    # Penalize production so the solver prefers curtailment.
    cost_ab = [100.0]
    cost_ba = [0.0]

    tracking = PvCurtailTracking(direction="a_to_b", available_kw=available, name="pv")
    binary = PvBinaryCurtailment(direction="a_to_b", available_kw=available, name="pv")

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("pv"), name="PV", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("bus"), name="Bus", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="pv_link",
        a_node_id=NodeId("pv"),
        b_node_id=NodeId("bus"),
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=5.0, max_b_to_a_kw=0.0),
            "upper_bound": UpperBound(
                direction="a_to_b",
                upper_bounds_kw=available,
                name="pv_avail",
            ),
            "curtail_tracking": tracking,
            "binary_curtailment": binary,
            "penalty_cost": LinearCost(
                cost_a_to_b_per_kwh=cost_ab,
                cost_b_to_a_per_kwh=cost_ba,
                name="penalty",
            ),
        },
    )
    graph.add_element(conn)

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    assert value_of(conn.power_out_ab[0]) == pytest.approx(0.0)
    assert value_of(tracking.curtail_kw(conn)[0]) == pytest.approx(5.0)


def test_pv_component_without_curtailment_uses_fixed_flow() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(now=now)
    inverter = InverterComponent(
        component_id="inv",
        switchboard=SwitchboardComponent(component_id="sb"),
        inverter=InverterComponentConfig(
            type="inverter",
            connection="sb",
            name="Inverter",
            peak_power_kw=5.0,
            curtailment=None,
        ),
    )
    pv = PvComponent(
        component_id="pv",
        inverter=inverter,
        pv=PvComponentConfig(
            type="pv",
            connection="inv",
            forecast=InputReference(source="pv_forecast"),
            forecast_multiplier=1.0,
        ),
    )
    elements, _ = pv.create_graph_elements(
        horizon=horizon,
        inputs=AppliedInputRegistry(
            forecasts={
                "pv_forecast": AppliedForecastInput(
                    key="pv_forecast",
                    kind=InputValueKind.POWER,
                    series=[2.0, 3.0],
                )
            }
        ),
        build_ctx=GraphBuildContext(components={}, solve_states=SolveStateStore()),
    )
    connection = next(element for element in elements if isinstance(element, Connection))

    assert "fixed_flow" in connection.policies
    assert "curtail_tracking" not in connection.policies


def test_pv_extract_plan_applies_multiplier_and_clipping() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(now=now)
    inverter = InverterComponent(
        component_id="inv",
        switchboard=SwitchboardComponent(component_id="sb"),
        inverter=InverterComponentConfig(
            type="inverter",
            connection="sb",
            name="Inverter",
            peak_power_kw=4.0,
            curtailment=None,
        ),
    )
    pv = PvComponent(
        component_id="pv",
        inverter=inverter,
        pv=PvComponentConfig(
            type="pv",
            connection="inv",
            forecast=InputReference(source="pv_forecast"),
            forecast_multiplier=0.5,
        ),
    )
    elements, solve_state = pv.create_graph_elements(
        horizon=horizon,
        inputs=AppliedInputRegistry(
            forecasts={
                "pv_forecast": AppliedForecastInput(
                    key="pv_forecast",
                    kind=InputValueKind.POWER,
                    series=[10.0, 2.0],
                )
            }
        ),
        build_ctx=GraphBuildContext(components={}, solve_states=SolveStateStore()),
    )
    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("sb"), name="SB", node_role="prosumer"))
    graph.add_elements(
        inverter.create_graph_elements(
            horizon=horizon,
            inputs=AppliedInputRegistry(),
            build_ctx=GraphBuildContext(components={}, solve_states=SolveStateStore()),
        )[0]
    )
    graph.add_elements(elements)
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    plan = pv.extract_plan(
        snapshot,
        solve_state=solve_state,
        plan_ctx=PlanContext(components={}, solve_states=SolveStateStore()),
    )

    assert [point.value for point in plan.available_kw] == pytest.approx([2.0, 1.0])
    assert [point.value for point in plan.actual_kw] == pytest.approx([2.0, 1.0])
    assert [point.value for point in plan.curtail_kw] == pytest.approx([0.0, 0.0])
    assert [point.value for point in plan.curtailment] == [False, False]


def test_pv_component_binary_mode_adds_binary_policy_and_extracts_curtailment() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    inverter = InverterComponent(
        component_id="inv",
        switchboard=SwitchboardComponent(component_id="sb"),
        inverter=InverterComponentConfig(
            type="inverter",
            connection="sb",
            name="Inverter",
            peak_power_kw=5.0,
            curtailment="binary",
        ),
    )
    pv = PvComponent(
        component_id="pv",
        inverter=inverter,
        pv=PvComponentConfig(
            type="pv",
            connection="inv",
            forecast=InputReference(source="pv_forecast"),
            forecast_multiplier=1.0,
        ),
    )
    elements, solve_state = pv.create_graph_elements(
        horizon=horizon,
        inputs=AppliedInputRegistry(
            forecasts={
                "pv_forecast": AppliedForecastInput(
                    key="pv_forecast",
                    kind=InputValueKind.POWER,
                    series=[4.0],
                )
            }
        ),
        build_ctx=GraphBuildContext(components={}, solve_states=SolveStateStore()),
    )
    connection = next(element for element in elements if isinstance(element, Connection))
    assert "binary_curtailment" in connection.policies
    assert "curtail_tracking" in connection.policies

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("sb"), name="SB", node_role="prosumer"))
    graph.add_elements(
        inverter.create_graph_elements(
            horizon=horizon,
            inputs=AppliedInputRegistry(),
            build_ctx=GraphBuildContext(components={}, solve_states=SolveStateStore()),
        )[0]
    )
    graph.add_elements(elements)
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    plan = pv.extract_plan(
        snapshot,
        solve_state=solve_state,
        plan_ctx=PlanContext(components={}, solve_states=SolveStateStore()),
    )
    actual = float(plan.actual_kw[0].value)
    available = float(plan.available_kw[0].value)
    assert actual == pytest.approx(0.0) or actual == pytest.approx(available)
    assert bool(plan.curtailment[0].value) == (actual < available - 1e-6)
