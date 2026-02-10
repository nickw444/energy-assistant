from __future__ import annotations

from datetime import UTC, datetime

import pulp

from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.milp.context import ModelContext
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.deferred import DeferredSeries
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.link_components import (
    DirectionalLimit,
    FixedFlow,
    TransportEfficiency,
)
from energy_assistant.ems.topology.nodes import BusNode, PortNode


def test_bus_balance_enforces_conservation_with_efficiency() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    producer_kw = DeferredSeries[float](name="producer_kw", initial=[10.0])
    load_kw = DeferredSeries[float](name="load_kw", initial=[9.0])

    graph = EnergyGraph()
    graph.add_bus(BusNode(id="bus", name="Bus"))
    graph.add_port(PortNode(id="producer", name="Producer"))
    graph.add_port(PortNode(id="load", name="Load"))

    # Producer injects 10kW into the bus with 90% transport efficiency (bus receives 9kW).
    graph.add_connection(
        Connection(
            id="prod_bus",
            a_node_id="producer",
            b_node_id="bus",
            link_components=[
                DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=0.0),
                FixedFlow(direction="a_to_b", values_kw=producer_kw, name="producer"),
                TransportEfficiency(eta_a_to_b=0.9, eta_b_to_a=1.0),
            ],
        )
    )

    # Load draws 9kW from the bus.
    graph.add_connection(
        Connection(
            id="bus_load",
            a_node_id="bus",
            b_node_id="load",
            link_components=[
                DirectionalLimit(max_a_to_b_kw=9.0, max_b_to_a_kw=0.0),
                FixedFlow(direction="a_to_b", values_kw=load_kw, name="load"),
            ],
        )
    )

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

