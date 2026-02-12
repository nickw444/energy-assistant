from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.horizon import Horizon, build_horizon
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
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


def test_directional_limit_caps_flow() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    cost_ab = [-1.0]
    cost_ba = [0.0]

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id="a", name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id="b", name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id="a",
        b_node_id="b",
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=3.0, max_b_to_a_kw=0.0),
            "flow_cost": LinearCost(
                cost_a_to_b_per_kwh=cost_ab,
                cost_b_to_a_per_kwh=cost_ba,
                name="flow",
            ),
            "transfer": Passthrough(),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_out_ab[0]) == pytest.approx(3.0)
    assert value_of(conn.power_out_ba[0]) == pytest.approx(0.0)


def test_exclusive_direction_chooses_cheaper_direction() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    cost_ab = [-1.0]
    cost_ba = [-0.5]

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id="a", name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id="b", name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id="a",
        b_node_id="b",
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
            "transfer": Passthrough(),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_out_ab[0]) == pytest.approx(5.0)
    assert value_of(conn.power_out_ba[0]) == pytest.approx(0.0)


def test_soft_limit_penalty_can_dominate_reward() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

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
    graph.add_element(Node(horizon=horizon, id="a", name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id="b", name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id="a",
        b_node_id="b",
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=5.0, max_b_to_a_kw=0.0),
            "soft_limit": soft,
            "flow_cost": LinearCost(
                cost_a_to_b_per_kwh=cost_ab,
                cost_b_to_a_per_kwh=cost_ba,
                name="flow",
            ),
            "transfer": Passthrough(),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_out_ab[0]) == pytest.approx(0.0)
    assert value_of(soft.slack_kw(conn)[0]) == pytest.approx(0.0)


def test_connection_requires_exactly_one_transfer_policy() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id="a", name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id="b", name="B", node_role="prosumer"))
    with pytest.raises(ValueError, match="requires exactly one TransferConnectionPolicy"):
        Connection(
            horizon=horizon,
            id="c_no_transfer",
            a_node_id="a",
            b_node_id="b",
            policies={
                "directional_limit": DirectionalLimit(max_a_to_b_kw=5.0, max_b_to_a_kw=5.0)
            },
        )
    with pytest.raises(ValueError, match="requires exactly one TransferConnectionPolicy"):
        Connection(
            horizon=horizon,
            id="c_two_transfer",
            a_node_id="a",
            b_node_id="b",
            policies={
                "directional_limit": DirectionalLimit(max_a_to_b_kw=5.0, max_b_to_a_kw=5.0),
                "transfer_1": Passthrough(),
                "transfer_2": DirectionalEfficiency(eta_a_to_b=0.95, eta_b_to_a=0.95),
            },
        )

def test_connection_supports_explicit_non_passthrough_transfer() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id="a", name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id="b", name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id="a",
        b_node_id="b",
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=5.0, max_b_to_a_kw=5.0),
            "transfer": DirectionalEfficiency(eta_a_to_b=0.95, eta_b_to_a=0.95),
        },
    )
    graph.add_element(conn)

    assert not any(isinstance(policy, Passthrough) for policy in conn.policies.values())
    assert isinstance(conn.policy("transfer", DirectionalEfficiency), DirectionalEfficiency)
    with pytest.raises(KeyError, match="has no policy named"):
        conn.policy("missing", Passthrough)
    with pytest.raises(TypeError, match="expected Passthrough"):
        conn.policy("directional_limit", Passthrough)


def test_directional_limit_supports_unbounded_with_none() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    fixed_flow = [12.5]

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id="a", name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id="b", name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id="a",
        b_node_id="b",
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=None, max_b_to_a_kw=0.0),
            "fixed_flow": FixedFlow(direction="a_to_b", values_kw=fixed_flow, name="fixed"),
            "transfer": Passthrough(),
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
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id="producer", name="Producer", node_role="producer"))
    graph.add_element(Node(horizon=horizon, id="peer", name="Peer", node_role="prosumer"))

    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id="producer",
        b_node_id="peer",
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
            "transfer": Passthrough(),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_out_ba[0]) == pytest.approx(0.0)


def test_consumer_role_disallows_net_export() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id="consumer", name="Consumer", node_role="consumer"))
    graph.add_element(Node(horizon=horizon, id="peer", name="Peer", node_role="prosumer"))

    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id="consumer",
        b_node_id="peer",
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
            "transfer": Passthrough(),
        },
    )
    graph.add_element(conn)

    _solve(graph, horizon=horizon)

    assert value_of(conn.power_out_ab[0]) == pytest.approx(0.0)
