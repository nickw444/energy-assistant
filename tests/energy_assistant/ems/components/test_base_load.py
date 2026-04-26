from __future__ import annotations

from datetime import UTC, datetime

import pytest

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import Horizon, HorizonFactory
from energy_assistant.ems.inputs.models import AppliedForecastInput, AppliedInputRegistry
from energy_assistant.ems.milp.context import ModelContext
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.system.state import SolveStateStore
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.models.inputs import InputValueKind
from energy_assistant.models.plant import InputReference, LoadComponentConfig


def _horizon() -> Horizon:
    return HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    )


def test_base_load_rejects_forecast_length_mismatch() -> None:
    horizon = _horizon()
    switchboard = SwitchboardComponent(component_id="switchboard")
    component = BaseLoadComponent(
        component_id="load",
        switchboard=switchboard,
        load=LoadComponentConfig(
            type="load",
            connection="switchboard",
            name="Base load",
            power=InputReference(source="base_load_kw"),
        ),
    )
    inputs = AppliedInputRegistry(
        forecasts={
            "base_load_kw": AppliedForecastInput(
                key="base_load_kw",
                kind=InputValueKind.POWER,
                series=[1.0],
            )
        }
    )

    with pytest.raises(ValueError, match="series length"):
        component.create_graph_elements(
            horizon=horizon,
            inputs=inputs,
            build_ctx=GraphBuildContext(components={}, solve_states=SolveStateStore()),
        )


def test_base_load_builds_consumer_node_fixed_flow_and_plan_series() -> None:
    horizon = _horizon()
    switchboard = SwitchboardComponent(component_id="switchboard")
    component = BaseLoadComponent(
        component_id="load",
        switchboard=switchboard,
        load=LoadComponentConfig(
            type="load",
            connection="switchboard",
            name="Base load",
            power=InputReference(source="base_load_kw"),
        ),
    )
    inputs = AppliedInputRegistry(
        forecasts={
            "base_load_kw": AppliedForecastInput(
                key="base_load_kw",
                kind=InputValueKind.POWER,
                series=[1.5, 2.0],
            )
        }
    )
    build_ctx = GraphBuildContext(components={}, solve_states=SolveStateStore())

    elements, solve_state = component.create_graph_elements(
        horizon=horizon,
        inputs=inputs,
        build_ctx=build_ctx,
    )

    connection = next(element for element in elements if isinstance(element, Connection))
    assert solve_state.base_load_kw == [1.5, 2.0]
    assert connection.a_node_id == switchboard.bus_id
    assert connection.b_node_id == component.node_id

    graph = EnergyGraph()
    graph.add_element(
        switchboard.create_graph_elements(
            horizon=horizon,
            inputs=AppliedInputRegistry(),
            build_ctx=GraphBuildContext(components={}, solve_states=SolveStateStore()),
        )[0][0]
    )
    graph.add_elements(elements)
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    plan = component.extract_plan(
        snapshot,
        solve_state=solve_state,
        plan_ctx=PlanContext(components={}, solve_states=SolveStateStore()),
    )

    assert [point.value for point in plan.power_kw] == [1.5, 2.0]
    assert [point.time for point in plan.power_kw] == [slot.start for slot in horizon.slots]
