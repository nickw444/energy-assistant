from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.planning.horizon import build_horizon
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.nodes import Node, StorageNode
from energy_assistant.ems.topology.policies import (
    DirectionalEfficiency,
    DirectionalLimit,
    FixedFlow,
)


def test_storage_soc_dynamics_applies_efficiency() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=2)

    soc0 = 2.0
    charge_kw = [1.0, 0.0]
    discharge_kw = [0.0, 1.0]

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id="p", name="Port", node_role="prosumer"))
    node = StorageNode(
        horizon=horizon,
        id="bat",
        name="Battery",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=soc0,
    )
    graph.add_element(node)
    graph.add_element(
        Connection(
            horizon=horizon,
            id="link",
            a_node_id="p",
            b_node_id="bat",
            policies={
                "directional_limit": DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=10.0),
                "fixed_charge_flow": FixedFlow(
                    direction="a_to_b",
                    values_kw=charge_kw,
                    name="charge",
                ),
                "fixed_discharge_flow": FixedFlow(
                    direction="b_to_a",
                    values_kw=discharge_kw,
                    name="discharge",
                ),
                "efficiency": DirectionalEfficiency(
                    eta_a_to_b=0.9,
                    eta_b_to_a=0.9,
                ),
            },
        )
    )

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    assert value_of(node.E_by_i[0]) == pytest.approx(2.0)
    assert value_of(node.E_by_i[1]) == pytest.approx(2.9)
    assert value_of(node.E_by_i[2]) == pytest.approx(1.9)
