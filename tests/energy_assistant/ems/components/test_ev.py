from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.components.ev import EvChargeControl, EvComponent, EvSocIncentivesFragment
from energy_assistant.ems.components.lib.time_windows import TimeWindowMatcher
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import HorizonFactory
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node, StorageNode
from energy_assistant.ems.topology.policies import DirectionalLimit, LinearCost
from energy_assistant.models.plant import (
    ControlledEvComponentConfig,
    InputReference,
    SocIncentive,
    TimeWindow,
)


def test_ev_switch_penalty_t0_seeded_from_realtime_state() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(now=now)

    gate = [1.0]
    connected = True
    realtime_power = 1.0

    # Encourage charging: negative cost on a_to_b.
    cost_ab = [-1.0]
    cost_ba = [0.0]

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
    graph.add_element(Node(horizon=horizon, id=NodeId("a"), name="A", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("b"), name="B", node_role="prosumer"))
    conn = Connection(
        horizon=horizon,
        id="c",
        a_node_id=NodeId("a"),
        b_node_id=NodeId("b"),
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=7.0, max_b_to_a_kw=0.0),
            "charge_control": control,
            "reward_cost": LinearCost(
                cost_a_to_b_per_kwh=cost_ab,
                cost_b_to_a_per_kwh=cost_ba,
                name="reward",
            ),
        },
    )
    graph.add_element(conn)

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    # Since the EV is already "on" (connected + realtime_power above threshold),
    # charging is selected at t0 and unconstrained by a switch penalty spike.
    assert value_of(conn.power_out_ab[0]) == pytest.approx(7.0)


def test_connected_allowance_blocks_when_cannot_connect() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    )
    component = EvComponent(
        component_id="ev1",
        switchboard=SwitchboardComponent(component_id="sb"),
        load=ControlledEvComponentConfig(
            type="load_controlled_ev",
            connection="sb",
            name="EV",
            min_power_kw=0.0,
            max_power_kw=7.0,
            energy_kwh=50.0,
            connected=InputReference(source="connected"),
            can_connect=InputReference(source="can_connect"),
            allowed_connect_times=[],
            connect_grace_minutes=0,
            realtime_power=InputReference(source="rt_power"),
            state_of_charge_pct=InputReference(source="soc"),
        ),
        grid_export_bias_pct=0.0,
        time_window_matcher=TimeWindowMatcher(),
    )

    assert component._connected_allowance(  # pyright: ignore[reportPrivateUsage]
        horizon=horizon,
        connected=False,
        can_connect=False,
    ) == [0.0, 0.0]


def test_connected_allowance_respects_grace_and_allowed_windows() -> None:
    now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=180).build(now=now)
    component = EvComponent(
        component_id="ev1",
        switchboard=SwitchboardComponent(component_id="sb"),
        load=ControlledEvComponentConfig(
            type="load_controlled_ev",
            connection="sb",
            name="EV",
            min_power_kw=0.0,
            max_power_kw=7.0,
            energy_kwh=50.0,
            connected=InputReference(source="connected"),
            can_connect=InputReference(source="can_connect"),
            allowed_connect_times=[TimeWindow(start="01:00", end="03:00")],
            connect_grace_minutes=60,
            realtime_power=InputReference(source="rt_power"),
            state_of_charge_pct=InputReference(source="soc"),
        ),
        grid_export_bias_pct=0.0,
        time_window_matcher=TimeWindowMatcher(),
    )

    assert component._connected_allowance(  # pyright: ignore[reportPrivateUsage]
        horizon=horizon,
        connected=False,
        can_connect=True,
    ) == [0.0, 1.0, 1.0]


def test_ev_soc_incentives_fragment_increases_terminal_soc() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    )
    storage = StorageNode(
        horizon=horizon,
        id=NodeId("ev"),
        name="EV",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=2.0,
    )
    incentives = EvSocIncentivesFragment(
        horizon=horizon,
        ev_id="ev1",
        storage=storage,
        initial_soc_kwh=2.0,
        capacity_kwh=10.0,
        incentives=[
            SocIncentive(target_soc_pct=50.0, incentive=1.0),
            SocIncentive(target_soc_pct=80.0, incentive=0.5),
        ],
        grid_price_bias=0.0,
    )
    graph = EnergyGraph()
    graph.add_element(incentives)
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"
    # Incentivized segments stop at 80% (final tail segment has zero incentive).
    assert value_of(storage.E_by_i[horizon.num_intervals]) == pytest.approx(8.0)
