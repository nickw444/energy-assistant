from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import (
    DirectionalLimit,
    FixedFlow,
    Passthrough,
    SoftDirectionalLimit,
)


def test_soft_import_limit_uses_slack_violation() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    load_kw = [5.0]
    import_limit_kw = [0.0]

    soft = SoftDirectionalLimit(
        direction="b_to_a",
        limit_kw=import_limit_kw,
        penalty_per_kwh=1.0,
        name="import_allowed",
    )

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id="bus", name="Bus", node_role="bus"))
    graph.add_element(Node(horizon=horizon, id="grid", name="Grid", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id="load", name="Load", node_role="consumer"))

    # Fixed load draws 5kW from the bus.
    graph.add_element(
        Connection(
            horizon=horizon,
            id="bus_load",
            a_node_id="bus",
            b_node_id="load",
            policies={
                "directional_limit": DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=0.0),
                "fixed_flow": FixedFlow(direction="a_to_b", values_kw=load_kw, name="load"),
                "transfer": Passthrough(),
            },
        )
    )

    # Grid import must cover load, but the soft limit is 0.
    grid_conn = Connection(
        horizon=horizon,
        id="grid_bus",
        a_node_id="bus",
        b_node_id="grid",
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=0.0, max_b_to_a_kw=10.0),
            "import_soft_limit": soft,
            "transfer": Passthrough(),
        },
    )
    graph.add_element(grid_conn)

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    assert value_of(grid_conn.power_out_ba[0]) == pytest.approx(5.0)
    assert value_of(soft.slack_kw(grid_conn)[0]) == pytest.approx(5.0)
