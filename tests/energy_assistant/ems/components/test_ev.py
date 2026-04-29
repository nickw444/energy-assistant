from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.components.ev import (
    EvChargeControl,
    EvComponent,
    EvStorageSegment,
)
from energy_assistant.ems.components.lib.time_windows import TimeWindowMatcher
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import Horizon, HorizonFactory
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node, StorageNode
from energy_assistant.ems.topology.policies import DirectionalLimit, LinearCost
from energy_assistant.models.plant import (
    ControlledEvComponentConfig,
    EvSoftDeadline,
    InputReference,
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


def _ev_segment(
    *,
    horizon: Horizon,
    name: str,
    capacity_kwh: float,
    initial_kwh: float = 0.0,
    value_per_kwh: float,
) -> EvStorageSegment:
    node_id = NodeId(f"ev_segment_{name}")
    storage = StorageNode(
        horizon=horizon,
        id=node_id,
        name=f"EV segment {name}",
        capacity_kwh=capacity_kwh,
        soc_min_kwh=0.0,
        soc_max_kwh=capacity_kwh,
        initial_soc_kwh=initial_kwh,
        stored_energy_value_per_kwh=value_per_kwh,
    )
    connection = Connection(
        horizon=horizon,
        id=f"ev_segment_{name}_link",
        a_node_id=NodeId("charger"),
        b_node_id=node_id,
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=0.0),
        },
    )
    return EvStorageSegment(node=storage, connection=connection)


def test_ev_segmented_storage_incentives_increase_terminal_soc() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    )
    segments = (
        _ev_segment(
            horizon=horizon,
            name="0",
            capacity_kwh=5.0,
            initial_kwh=2.0,
            value_per_kwh=1.0,
        ),
        _ev_segment(horizon=horizon, name="1", capacity_kwh=3.0, value_per_kwh=0.5),
        _ev_segment(horizon=horizon, name="2", capacity_kwh=2.0, value_per_kwh=0.0),
    )
    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("grid"), name="Grid", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("charger"), name="Charger", node_role="bus"))
    graph.add_element(
        Connection(
            horizon=horizon,
            id="charger_link",
            a_node_id=NodeId("grid"),
            b_node_id=NodeId("charger"),
            policies={
                "directional_limit": DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=0.0),
            },
        )
    )
    for segment in segments:
        graph.add_element(segment.node)
        graph.add_element(segment.connection)
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"
    # Incentivized segments stop at 80% (the tail segment has zero incentive).
    segment_soc = sum(value_of(segment.node.E_by_i[horizon.num_intervals]) for segment in segments)
    assert segment_soc == pytest.approx(8.0)


def test_ev_soc_incentives_terminal_value_prefers_cheaper_later_charge() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=240).build(
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    )
    segment = _ev_segment(
        horizon=horizon,
        capacity_kwh=10.0,
        value_per_kwh=0.12,
        name="0",
    )
    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("grid"), name="Grid", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("charger"), name="Charger", node_role="bus"))
    graph.add_element(segment.node)
    conn = Connection(
        horizon=horizon,
        id="charger_link",
        a_node_id=NodeId("grid"),
        b_node_id=NodeId("charger"),
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=0.0),
            "grid_cost": LinearCost(
                cost_a_to_b_per_kwh=[0.03, 0.01, 0.01, 0.01],
                cost_b_to_a_per_kwh=[0.0, 0.0, 0.0, 0.0],
                name="grid",
            ),
        },
    )
    graph.add_element(conn)
    graph.add_element(segment.connection)
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"
    assert value_of(conn.flow_into_node(NodeId("charger"))[0]) == pytest.approx(0.0)
    assert value_of(segment.node.E_by_i[horizon.num_intervals]) == pytest.approx(10.0)


def test_ev_soft_deadline_encourages_early_charge_when_penalty_high() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=240).build(
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    )
    segment = _ev_segment(
        horizon=horizon,
        capacity_kwh=10.0,
        value_per_kwh=0.0,
        name="0",
    )
    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("grid"), name="Grid", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("charger"), name="Charger", node_role="bus"))
    graph.add_element(segment.node)
    conn = Connection(
        horizon=horizon,
        id="charger_link",
        a_node_id=NodeId("grid"),
        b_node_id=NodeId("charger"),
        policies={
            "directional_limit": DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=0.0),
            "grid_cost": LinearCost(
                cost_a_to_b_per_kwh=[0.3, 0.3, 0.01, 0.01],
                cost_b_to_a_per_kwh=[0.0, 0.0, 0.0, 0.0],
                name="grid",
            ),
        },
    )
    graph.add_element(conn)
    graph.add_element(segment.connection)
    component = EvComponent(
        component_id="ev1",
        switchboard=SwitchboardComponent(component_id="switchboard"),
        load=ControlledEvComponentConfig(
            type="load_controlled_ev",
            connection="switchboard",
            name="EV",
            min_power_kw=0.0,
            max_power_kw=10.0,
            energy_kwh=10.0,
            connected=InputReference(source="connected"),
            realtime_power=InputReference(source="rt_power"),
            state_of_charge_pct=InputReference(source="soc"),
            soft_deadlines=[
                EvSoftDeadline(by_time="02:00", target_soc_pct=40.0, shortfall_penalty=5.0)
            ],
        ),
        grid_export_bias_pct=0.0,
        time_window_matcher=TimeWindowMatcher(),
    )
    graph.add_element(
        component._build_soft_deadline_fragment(  # pyright: ignore[reportPrivateUsage]
            horizon=horizon,
            storages=(segment.node,),
        )
    )
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"
    # Deadline at 02:00 corresponds to state index 2. With high penalty, early charge is preferred.
    assert value_of(segment.node.E_by_i[2]) == pytest.approx(4.0)


def test_ev_soft_deadline_allows_shortfall_when_unreachable() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    )
    segment = _ev_segment(
        horizon=horizon,
        capacity_kwh=10.0,
        value_per_kwh=0.0,
        name="0",
    )
    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("grid"), name="Grid", node_role="prosumer"))
    graph.add_element(Node(horizon=horizon, id=NodeId("charger"), name="Charger", node_role="bus"))
    graph.add_element(segment.node)
    graph.add_element(
        Connection(
            horizon=horizon,
            id="charger_link",
            a_node_id=NodeId("grid"),
            b_node_id=NodeId("charger"),
            policies={
                "directional_limit": DirectionalLimit(max_a_to_b_kw=1.0, max_b_to_a_kw=0.0),
            },
        )
    )
    graph.add_element(segment.connection)
    component = EvComponent(
        component_id="ev1",
        switchboard=SwitchboardComponent(component_id="switchboard"),
        load=ControlledEvComponentConfig(
            type="load_controlled_ev",
            connection="switchboard",
            name="EV",
            min_power_kw=0.0,
            max_power_kw=1.0,
            energy_kwh=10.0,
            connected=InputReference(source="connected"),
            realtime_power=InputReference(source="rt_power"),
            state_of_charge_pct=InputReference(source="soc"),
            soft_deadlines=[
                EvSoftDeadline(by_time="01:00", target_soc_pct=40.0, shortfall_penalty=5.0)
            ],
        ),
        grid_export_bias_pct=0.0,
        time_window_matcher=TimeWindowMatcher(),
    )
    graph.add_element(
        component._build_soft_deadline_fragment(  # pyright: ignore[reportPrivateUsage]
            horizon=horizon,
            storages=(segment.node,),
        )
    )
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"
    # Only 1 kWh can be charged by the deadline, leaving 3 kWh shortfall for a 4 kWh target.
    assert value_of(segment.node.E_by_i[1]) == pytest.approx(1.0)
    assert value_of(snapshot.objective) == pytest.approx(15.0)
