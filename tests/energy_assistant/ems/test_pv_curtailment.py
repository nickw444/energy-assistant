from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.components.pv import PvBinaryCurtailment, PvCurtailTracking
from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import (
    DirectionalLimit,
    LinearCost,
    Passthrough,
    UpperBound,
)


def test_pv_binary_curtailment_can_force_all_or_nothing() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    available = [5.0]

    # Penalize production so the solver prefers curtailment.
    cost_ab = [100.0]
    cost_ba = [0.0]

    tracking = PvCurtailTracking(direction="a_to_b", available_kw=available, name="pv")
    binary = PvBinaryCurtailment(direction="a_to_b", available_kw=available, name="pv")

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id="pv", name="PV", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id="bus", name="Bus", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="pv_link",
        a_node_id="pv",
        b_node_id="bus",
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
            "transfer": Passthrough(),
        },
    )
    graph.add_element(conn)

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    assert value_of(conn.power_out_ab[0]) == pytest.approx(0.0)
    assert value_of(binary.curtail_binary(conn)[0]) == pytest.approx(1.0)
    assert value_of(tracking.curtail_kw(conn)[0]) == pytest.approx(5.0)
