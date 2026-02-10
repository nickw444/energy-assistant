from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.components.ev import EvChargeControl
from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.deferred import Deferred, DeferredSeries
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.link_components import DirectionalLimit, LinearCost
from energy_assistant.ems.topology.nodes import PortNode


def test_ev_switch_penalty_t0_seeded_from_realtime_state() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    gate = DeferredSeries[float](name="gate", initial=[1.0])
    connected = Deferred[bool](name="connected", initial=True)
    realtime_power = Deferred[float](name="realtime_power", initial=1.0)

    # Encourage charging: negative cost on a_to_b.
    cost_ab = DeferredSeries[float](name="cost_ab", initial=[-1.0])
    cost_ba = DeferredSeries[float](name="cost_ba", initial=[0.0])

    control = EvChargeControl(
        gate=gate,
        connected=connected,
        realtime_power_kw=realtime_power,
        min_power_kw=0.0,
        max_power_kw=7.0,
        switch_penalty=10.0,
        name="ev",
    )

    graph = EnergyGraph()
    graph.add_port(PortNode(id="a", name="A"))
    graph.add_port(PortNode(id="b", name="B"))
    conn = Connection(
        id="c",
        a_node_id="a",
        b_node_id="b",
        link_components=[
            DirectionalLimit(max_a_to_b_kw=7.0, max_b_to_a_kw=0.0),
            control,
            LinearCost(cost_a_to_b_per_kwh=cost_ab, cost_b_to_a_per_kwh=cost_ba, name="reward"),
        ],
    )
    graph.add_connection(conn)

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    # Since the EV is already "on" (connected + realtime_power above threshold),
    # switching at t0 is free.
    assert value_of(control.charge_on(conn)[0]) == pytest.approx(1.0)
    assert value_of(control.switch(conn)[0]) == pytest.approx(0.0)
