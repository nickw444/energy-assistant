from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.system.inputs import EmsInputs
from energy_assistant.ems.system.system import ModelSnapshot
from energy_assistant.ems.topology.connection import ConnectionTemplate
from energy_assistant.ems.topology.graph import EnergyGraphTemplate
from energy_assistant.ems.topology.link_components import (
    DirectionalLimit,
    ExclusiveDirection,
    LinearCostSeries,
    SoftDirectionalLimitSeries,
    SoftDirectionalLimitSeriesModel,
)
from energy_assistant.ems.topology.nodes import PortNodeTemplate


def _solve_snapshot(snapshot: ModelSnapshot) -> None:
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"


def _make_ctx(*, num_intervals: int = 1, timestep_minutes: int = 60) -> ModelContext:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=timestep_minutes, num_intervals=num_intervals)
    inputs = EmsInputs(horizon=horizon)
    return ModelContext(horizon=horizon, inputs=inputs)


def test_directional_limit_caps_flow() -> None:
    ctx = _make_ctx()
    ctx.inputs.set_float_series("cost_ab", [-1.0])
    ctx.inputs.set_float_series("cost_ba", [0.0])

    graph = EnergyGraphTemplate()
    graph.add_port(PortNodeTemplate(id="a", name="A"))
    graph.add_port(PortNodeTemplate(id="b", name="B"))
    graph.add_connection(
        ConnectionTemplate(
            id="c",
            a_node_id="a",
            b_node_id="b",
            link_components=[
                DirectionalLimit(max_a_to_b_kw=3.0, max_b_to_a_kw=0.0),
                LinearCostSeries(
                    cost_a_to_b_key="cost_ab",
                    cost_b_to_a_key="cost_ba",
                    name="flow",
                ),
            ],
        )
    )

    snapshot = ModelSnapshot(ctx=ctx, graph=graph.bind(ctx))
    _solve_snapshot(snapshot)

    conn = snapshot.graph.connections["c"]
    assert value_of(conn.P_a_to_b[0]) == pytest.approx(3.0)
    assert value_of(conn.P_b_to_a[0]) == pytest.approx(0.0)


def test_exclusive_direction_chooses_cheaper_direction() -> None:
    ctx = _make_ctx()
    ctx.inputs.set_float_series("cost_ab", [-1.0])
    ctx.inputs.set_float_series("cost_ba", [-0.5])

    graph = EnergyGraphTemplate()
    graph.add_port(PortNodeTemplate(id="a", name="A"))
    graph.add_port(PortNodeTemplate(id="b", name="B"))
    graph.add_connection(
        ConnectionTemplate(
            id="c",
            a_node_id="a",
            b_node_id="b",
            link_components=[
                DirectionalLimit(max_a_to_b_kw=5.0, max_b_to_a_kw=5.0),
                ExclusiveDirection(),
                LinearCostSeries(
                    cost_a_to_b_key="cost_ab",
                    cost_b_to_a_key="cost_ba",
                    name="flow",
                ),
            ],
        )
    )

    snapshot = ModelSnapshot(ctx=ctx, graph=graph.bind(ctx))
    _solve_snapshot(snapshot)

    conn = snapshot.graph.connections["c"]
    assert value_of(conn.P_a_to_b[0]) == pytest.approx(5.0)
    assert value_of(conn.P_b_to_a[0]) == pytest.approx(0.0)


def test_soft_limit_penalty_can_dominate_reward() -> None:
    ctx = _make_ctx()
    ctx.inputs.set_float_series("lim", [0.0])
    ctx.inputs.set_float_series("cost_ab", [-1.0])
    ctx.inputs.set_float_series("cost_ba", [0.0])

    graph = EnergyGraphTemplate()
    graph.add_port(PortNodeTemplate(id="a", name="A"))
    graph.add_port(PortNodeTemplate(id="b", name="B"))
    graph.add_connection(
        ConnectionTemplate(
            id="c",
            a_node_id="a",
            b_node_id="b",
            link_components=[
                DirectionalLimit(max_a_to_b_kw=5.0, max_b_to_a_kw=0.0),
                SoftDirectionalLimitSeries(
                    direction="a_to_b",
                    limit_key="lim",
                    penalty_per_kwh=10.0,
                    name="soft",
                ),
                LinearCostSeries(
                    cost_a_to_b_key="cost_ab",
                    cost_b_to_a_key="cost_ba",
                    name="flow",
                ),
            ],
        )
    )

    snapshot = ModelSnapshot(ctx=ctx, graph=graph.bind(ctx))
    _solve_snapshot(snapshot)

    conn = snapshot.graph.connections["c"]
    assert value_of(conn.P_a_to_b[0]) == pytest.approx(0.0)

    slack_kw = None
    for comp in conn.components:
        if isinstance(comp, SoftDirectionalLimitSeriesModel) and comp.name == "soft":
            slack_kw = value_of(comp.slack_kw[0])
            break
    assert slack_kw == pytest.approx(0.0)

