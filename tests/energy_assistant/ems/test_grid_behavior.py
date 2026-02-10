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
    FixedFlow,
    SoftDirectionalLimit,
)
from energy_assistant.ems.topology.nodes import BusNode, PortNode


def test_soft_import_limit_uses_slack_violation() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    load_kw = DeferredSeries[float](name="load_kw", initial=[5.0])
    import_limit_kw = DeferredSeries[float](name="import_limit_kw", initial=[0.0])

    soft = SoftDirectionalLimit(
        direction="b_to_a",
        limit_kw=import_limit_kw,
        penalty_per_kwh=1.0,
        name="import_allowed",
    )

    graph = EnergyGraph()
    graph.add_bus(BusNode(id="bus", name="Bus"))
    graph.add_port(PortNode(id="grid", name="Grid"))
    graph.add_port(PortNode(id="load", name="Load"))

    # Fixed load draws 5kW from the bus.
    graph.add_connection(
        Connection(
            id="bus_load",
            a_node_id="bus",
            b_node_id="load",
            link_components=[
                DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=0.0),
                FixedFlow(direction="a_to_b", values_kw=load_kw, name="load"),
            ],
        )
    )

    # Grid import must cover load, but the soft limit is 0.
    grid_conn = Connection(
        id="grid_bus",
        a_node_id="bus",
        b_node_id="grid",
        link_components=[
            DirectionalLimit(max_a_to_b_kw=0.0, max_b_to_a_kw=10.0),
            soft,
        ],
    )
    graph.add_connection(grid_conn)

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    assert value_of(grid_conn.P_b_to_a[0]) == pytest.approx(5.0)
    assert value_of(soft.slack_kw(grid_conn)[0]) == pytest.approx(5.0)
