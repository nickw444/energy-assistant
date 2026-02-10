from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.deferred import DeferredSeries
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.link_components import (
    DirectionalLimit,
    LinearCost,
    SoftDirectionalLimit,
)
from energy_assistant.ems.topology.nodes import PortNode


def _solve(graph: EnergyGraph, *, num_intervals: int = 1, timestep_minutes: int = 60) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=timestep_minutes, num_intervals=num_intervals)
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"


def test_directional_limit_caps_flow() -> None:
    cost_ab = DeferredSeries[float](name="cost_ab", initial=[-1.0])
    cost_ba = DeferredSeries[float](name="cost_ba", initial=[0.0])

    graph = EnergyGraph()
    graph.add_port(PortNode(id="a", name="A"))
    graph.add_port(PortNode(id="b", name="B"))
    conn = Connection(
        id="c",
        a_node_id="a",
        b_node_id="b",
        link_components=[
            DirectionalLimit(max_a_to_b_kw=3.0, max_b_to_a_kw=0.0),
            LinearCost(
                cost_a_to_b_per_kwh=cost_ab,
                cost_b_to_a_per_kwh=cost_ba,
                name="flow",
            ),
        ],
    )
    graph.add_connection(conn)

    _solve(graph)

    assert value_of(conn.P_a_to_b[0]) == pytest.approx(3.0)
    assert value_of(conn.P_b_to_a[0]) == pytest.approx(0.0)


def test_exclusive_direction_chooses_cheaper_direction() -> None:
    cost_ab = DeferredSeries[float](name="cost_ab", initial=[-1.0])
    cost_ba = DeferredSeries[float](name="cost_ba", initial=[-0.5])

    graph = EnergyGraph()
    graph.add_port(PortNode(id="a", name="A"))
    graph.add_port(PortNode(id="b", name="B"))
    conn = Connection(
        id="c",
        a_node_id="a",
        b_node_id="b",
        link_components=[
            DirectionalLimit(max_a_to_b_kw=5.0, max_b_to_a_kw=5.0, exclusive=True),
            LinearCost(
                cost_a_to_b_per_kwh=cost_ab,
                cost_b_to_a_per_kwh=cost_ba,
                name="flow",
            ),
        ],
    )
    graph.add_connection(conn)

    _solve(graph)

    assert value_of(conn.P_a_to_b[0]) == pytest.approx(5.0)
    assert value_of(conn.P_b_to_a[0]) == pytest.approx(0.0)


def test_soft_limit_penalty_can_dominate_reward() -> None:
    lim = DeferredSeries[float](name="lim", initial=[0.0])
    cost_ab = DeferredSeries[float](name="cost_ab", initial=[-1.0])
    cost_ba = DeferredSeries[float](name="cost_ba", initial=[0.0])

    soft = SoftDirectionalLimit(
        direction="a_to_b",
        limit_kw=lim,
        penalty_per_kwh=10.0,
        name="soft",
    )

    graph = EnergyGraph()
    graph.add_port(PortNode(id="a", name="A"))
    graph.add_port(PortNode(id="b", name="B"))
    conn = Connection(
        id="c",
        a_node_id="a",
        b_node_id="b",
        link_components=[
            DirectionalLimit(max_a_to_b_kw=5.0, max_b_to_a_kw=0.0),
            soft,
            LinearCost(
                cost_a_to_b_per_kwh=cost_ab,
                cost_b_to_a_per_kwh=cost_ba,
                name="flow",
            ),
        ],
    )
    graph.add_connection(conn)

    _solve(graph)

    assert value_of(conn.P_a_to_b[0]) == pytest.approx(0.0)
    assert value_of(soft.slack_kw(conn)[0]) == pytest.approx(0.0)

