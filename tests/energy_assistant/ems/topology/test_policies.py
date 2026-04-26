from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.horizon import Horizon, HorizonFactory
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import (
    DirectionalEfficiency,
    DirectionalLimit,
    FixedFlow,
    LinearCost,
    Passthrough,
    SoftDirectionalLimit,
)


def _solve(graph: EnergyGraph, *, horizon: Horizon) -> None:
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"


def _make_horizon(*, now: datetime) -> Horizon:
    return HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(now=now)


def test_directional_limit_caps_flow() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = _make_horizon(now=now)

    cost_ab = [-1.0]
    cost_ba = [0.0]

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("a"), name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("b"), name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id=NodeId("a"),
        b_node_id=NodeId("b"),
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=3.0, max_b_to_a_kw=0.0),
            "flow_cost": LinearCost(
                cost_a_to_b_per_kwh=cost_ab,
                cost_b_to_a_per_kwh=cost_ba,
                name="flow",
            ),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_out_ab[0]) == pytest.approx(3.0)
    assert value_of(conn.power_out_ba[0]) == pytest.approx(0.0)


def test_exclusive_direction_chooses_cheaper_direction() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = _make_horizon(now=now)

    cost_ab = [-1.0]
    cost_ba = [-0.5]

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("a"), name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("b"), name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id=NodeId("a"),
        b_node_id=NodeId("b"),
        policies={
            "directional_limit": DirectionalLimit(
                max_a_to_b_kw=5.0,
                max_b_to_a_kw=5.0,
                exclusive=True,
            ),
            "flow_cost": LinearCost(
                cost_a_to_b_per_kwh=cost_ab,
                cost_b_to_a_per_kwh=cost_ba,
                name="flow",
            ),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_out_ab[0]) == pytest.approx(5.0)
    assert value_of(conn.power_out_ba[0]) == pytest.approx(0.0)


def test_soft_limit_penalty_can_dominate_reward() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = _make_horizon(now=now)

    lim = [0.0]
    cost_ab = [-1.0]
    cost_ba = [0.0]

    soft = SoftDirectionalLimit(
        direction="a_to_b",
        limit_kw=lim,
        penalty_per_kwh=10.0,
        name="soft",
    )

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("a"), name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("b"), name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id=NodeId("a"),
        b_node_id=NodeId("b"),
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=5.0, max_b_to_a_kw=0.0),
            "soft_limit": soft,
            "flow_cost": LinearCost(
                cost_a_to_b_per_kwh=cost_ab,
                cost_b_to_a_per_kwh=cost_ba,
                name="flow",
            ),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_out_ab[0]) == pytest.approx(0.0)


def test_connection_defaults_to_passthrough_when_no_policies_are_defined() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = _make_horizon(now=now)

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("a"), name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("b"), name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c_default_transfer",
        a_node_id=NodeId("a"),
        b_node_id=NodeId("b"),
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=4.0, max_b_to_a_kw=0.0),
            "fixed_flow": FixedFlow(direction="a_to_b", values_kw=[4.0], name="fixed"),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_in_ab[0]) == pytest.approx(4.0)
    assert value_of(conn.power_out_ab[0]) == pytest.approx(4.0)
    assert value_of(conn.power_out_ba[0]) == pytest.approx(0.0)


def test_connection_composes_multiple_policies_as_segments() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = _make_horizon(now=now)

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("a"), name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("b"), name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id=NodeId("a"),
        b_node_id=NodeId("b"),
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=0.0),
            "fixed_flow": FixedFlow(direction="a_to_b", values_kw=[10.0], name="fixed"),
            "efficiency_1": DirectionalEfficiency(eta_a_to_b=0.9, eta_b_to_a=1.0),
            "efficiency_2": DirectionalEfficiency(eta_a_to_b=0.8, eta_b_to_a=1.0),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_in_ab[0]) == pytest.approx(10.0)
    assert value_of(conn.power_out_ab[0]) == pytest.approx(7.2)
    assert isinstance(conn.policy("efficiency_1", DirectionalEfficiency), DirectionalEfficiency)
    with pytest.raises(KeyError, match="has no policy named"):
        conn.policy("missing", Passthrough)
    with pytest.raises(TypeError, match="expected DirectionalEfficiency"):
        conn.policy("directional_limit", DirectionalEfficiency)


def test_directional_limit_supports_unbounded_with_none() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = _make_horizon(now=now)

    fixed_flow = [12.5]

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("a"), name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("b"), name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id=NodeId("a"),
        b_node_id=NodeId("b"),
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=None, max_b_to_a_kw=0.0),
            "fixed_flow": FixedFlow(direction="a_to_b", values_kw=fixed_flow, name="fixed"),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_out_ab[0]) == pytest.approx(12.5)
    assert value_of(conn.power_out_ba[0]) == pytest.approx(0.0)


def test_directional_limit_exclusive_requires_finite_bounds() -> None:
    with pytest.raises(ValueError, match="exclusive=True requires finite bounds"):
        DirectionalLimit(max_a_to_b_kw=None, max_b_to_a_kw=1.0, exclusive=True)


def test_producer_role_disallows_net_import() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = _make_horizon(now=now)

    graph = EnergyGraph()
    graph.add_element(
        Node(horizon=horizon, id=NodeId("producer"), name="Producer", node_role="producer")
    )
    graph.add_element(Node(horizon=horizon, id=NodeId("peer"), name="Peer", node_role="prosumer"))

    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id=NodeId("producer"),
        b_node_id=NodeId("peer"),
        policies={
            "directional_limit": DirectionalLimit(
                max_a_to_b_kw=5.0,
                max_b_to_a_kw=5.0,
                exclusive=True,
            ),
            "prefer_import_cost": LinearCost(
                cost_a_to_b_per_kwh=[0.0],
                cost_b_to_a_per_kwh=[-1.0],  # reward flow into producer
                name="prefer_import",
            ),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_out_ba[0]) == pytest.approx(0.0)


def test_consumer_role_disallows_net_export() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = _make_horizon(now=now)

    graph = EnergyGraph()
    graph.add_element(
        Node(horizon=horizon, id=NodeId("consumer"), name="Consumer", node_role="consumer")
    )
    graph.add_element(Node(horizon=horizon, id=NodeId("peer"), name="Peer", node_role="prosumer"))

    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id=NodeId("consumer"),
        b_node_id=NodeId("peer"),
        policies={
            "directional_limit": DirectionalLimit(
                max_a_to_b_kw=5.0,
                max_b_to_a_kw=5.0,
                exclusive=True,
            ),
            "prefer_export_cost": LinearCost(
                cost_a_to_b_per_kwh=[-1.0],  # reward flow out of consumer
                cost_b_to_a_per_kwh=[0.0],
                name="prefer_export",
            ),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_out_ab[0]) == pytest.approx(0.0)
