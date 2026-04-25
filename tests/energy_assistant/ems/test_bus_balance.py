from __future__ import annotations

from datetime import UTC, datetime

import pulp

from energy_assistant.ems.milp.context import ModelContext
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.planning.horizon import HorizonFactory
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import (
    DirectionalEfficiency,
    DirectionalLimit,
    FixedFlow,
)


def test_bus_balance_enforces_conservation_with_efficiency() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(now=now)

    producer_kw = [10.0]
    load_kw = [9.0]

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("bus"), name="Bus", node_role="bus"))
    graph.add_element(
        Node(horizon=horizon, id=NodeId("producer"), name="Producer", node_role="producer")
    )
    graph.add_element(Node(horizon=horizon, id=NodeId("load"), name="Load", node_role="consumer"))

    # Producer injects 10kW into the bus with 90% transport efficiency (bus receives 9kW).
    graph.add_element(
        Connection(
            horizon=horizon,
            id="prod_bus",
            a_node_id=NodeId("producer"),
            b_node_id=NodeId("bus"),
            policies={
                "directional_limit": DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=0.0),
                "fixed_flow": FixedFlow(direction="a_to_b", values_kw=producer_kw, name="producer"),
                "efficiency": DirectionalEfficiency(
                    eta_a_to_b=0.9,
                    eta_b_to_a=1.0,
                ),
            },
        )
    )

    # Load draws 9kW from the bus.
    graph.add_element(
        Connection(
            horizon=horizon,
            id="bus_load",
            a_node_id=NodeId("bus"),
            b_node_id=NodeId("load"),
            policies={
                "directional_limit": DirectionalLimit(max_a_to_b_kw=9.0, max_b_to_a_kw=0.0),
                "fixed_flow": FixedFlow(direction="a_to_b", values_kw=load_kw, name="load"),
            },
        )
    )

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"
