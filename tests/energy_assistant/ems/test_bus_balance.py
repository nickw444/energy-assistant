from __future__ import annotations

from datetime import UTC, datetime

import pulp

from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.milp.context import ModelContext
from energy_assistant.ems.system.inputs import EmsInputs
from energy_assistant.ems.system.system import ModelSnapshot
from energy_assistant.ems.topology.connection import ConnectionTemplate
from energy_assistant.ems.topology.graph import EnergyGraphTemplate
from energy_assistant.ems.topology.link_components import (
    DirectionalLimit,
    Efficiency,
    FixedFlowSeries,
)
from energy_assistant.ems.topology.nodes import BusNodeTemplate, PortNodeTemplate


def test_bus_balance_enforces_conservation_with_efficiency() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)
    inputs = EmsInputs(horizon=horizon)
    inputs.set_float_series("producer_kw", [10.0])
    inputs.set_float_series("load_kw", [9.0])

    ctx = ModelContext(horizon=horizon, inputs=inputs)

    graph = EnergyGraphTemplate()
    graph.add_bus(BusNodeTemplate(id="bus", name="Bus"))
    graph.add_port(PortNodeTemplate(id="producer", name="Producer"))
    graph.add_port(PortNodeTemplate(id="load", name="Load"))

    # Producer injects 10kW into the bus with 90% transport efficiency (bus receives 9kW).
    graph.add_connection(
        ConnectionTemplate(
            id="prod_bus",
            a_node_id="producer",
            b_node_id="bus",
            link_components=[
                DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=0.0),
                FixedFlowSeries(direction="a_to_b", value_key="producer_kw", name="producer"),
                Efficiency(eta_a_to_b=0.9, eta_b_to_a=1.0),
            ],
        )
    )

    # Load draws 9kW from the bus.
    graph.add_connection(
        ConnectionTemplate(
            id="bus_load",
            a_node_id="bus",
            b_node_id="load",
            link_components=[
                DirectionalLimit(max_a_to_b_kw=9.0, max_b_to_a_kw=0.0),
                FixedFlowSeries(direction="a_to_b", value_key="load_kw", name="load"),
            ],
        )
    )

    snapshot = ModelSnapshot(ctx=ctx, graph=graph.bind(ctx))
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

