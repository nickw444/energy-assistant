from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.horizon import HorizonFactory
from energy_assistant.ems.milp.context import ConstraintSpec, ModelContext, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import (
    FixedTerminalSocValue,
    ForecastPercentileTerminalSocValue,
    Node,
    StorageNode,
)
from energy_assistant.ems.topology.policies import (
    DirectionalEfficiency,
    DirectionalLimit,
    FixedFlow,
)


def test_storage_soc_dynamics_applies_efficiency() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(now=now)

    soc0 = 2.0
    charge_kw = [1.0, 0.0]
    discharge_kw = [0.0, 1.0]

    graph = EnergyGraph()
    graph.add_element(
        Node(horizon=horizon, id=NodeId("p"), name="Port", node_role="prosumer")
    )
    node = StorageNode(
        horizon=horizon,
        id=NodeId("bat"),
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
            a_node_id=NodeId("p"),
            b_node_id=NodeId("bat"),
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


def test_storage_hard_terminal_mode_enforces_final_soc_floor() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(now=now)

    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("bus"), name="Bus", node_role="bus"))
    storage = StorageNode(
        horizon=horizon,
        id=NodeId("bat"),
        name="Battery",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=5.0,
        terminal_mode="hard",
    )
    graph.add_element(storage)
    graph.add_element(
        Connection(
            horizon=horizon,
            id="link",
            a_node_id=NodeId("bus"),
            b_node_id=NodeId("bat"),
            policies={
                "directional_limit": DirectionalLimit(max_a_to_b_kw=0.0, max_b_to_a_kw=5.0),
                "fixed_discharge_flow": FixedFlow(
                    direction="b_to_a",
                    values_kw=[1.0],
                    name="discharge",
                ),
            },
        )
    )

    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Infeasible"


def test_storage_forecast_terminal_value_requires_price_series() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    node = StorageNode(
        horizon=horizon,
        id=NodeId("bat"),
        name="Battery",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=5.0,
        terminal_soc_value=ForecastPercentileTerminalSocValue(),
    )
    with pytest.raises(ValueError, match="requires price_import_raw"):
        _ = node.objective


class _ObjectiveFragment:
    def __init__(
        self,
        *,
        objective: pulp.LpAffineExpression,
        terminal_soc: pulp.LpVariable,
        final_soc_kwh: float,
    ) -> None:
        self._objective = objective
        self._terminal_soc = terminal_soc
        self._final_soc_kwh = final_soc_kwh

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return [ConstraintSpec("force_terminal_soc", self._terminal_soc == self._final_soc_kwh)]

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return self._objective


def test_storage_fixed_terminal_value_rewards_terminal_soc() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    node = StorageNode(
        horizon=horizon,
        id=NodeId("bat"),
        name="Battery",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=5.0,
        terminal_soc_value=FixedTerminalSocValue(value_per_kwh=0.25),
    )
    graph = EnergyGraph()
    graph.add_element(
        _ObjectiveFragment(
            objective=node.objective,
            terminal_soc=node.terminal_soc,
            final_soc_kwh=4.0,
        )
    )
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"
    assert value_of(snapshot.objective) == pytest.approx(-1.0)


def test_storage_forecast_terminal_value_uses_tail_percentile_window() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=240).build(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    node = StorageNode(
        horizon=horizon,
        id=NodeId("bat"),
        name="Battery",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=5.0,
        price_import_raw=[1.0, 2.0, 4.0, 8.0],
        terminal_soc_value=ForecastPercentileTerminalSocValue(
            percentile=50.0,
            lookahead_window_minutes=180,
        ),
    )
    graph = EnergyGraph()
    graph.add_element(
        _ObjectiveFragment(
            objective=node.objective,
            terminal_soc=node.terminal_soc,
            final_soc_kwh=2.0,
        )
    )
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"
    assert value_of(snapshot.objective) == pytest.approx(-8.0)


def test_storage_forecast_terminal_value_applies_price_floor() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=180).build(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    node = StorageNode(
        horizon=horizon,
        id=NodeId("bat"),
        name="Battery",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=5.0,
        price_import_raw=[-5.0, -3.0, -1.0],
        terminal_soc_value=ForecastPercentileTerminalSocValue(
            percentile=50.0,
            lookahead_window_minutes=180,
            price_floor_per_kwh=0.0,
        ),
    )
    graph = EnergyGraph()
    graph.add_element(
        _ObjectiveFragment(
            objective=node.objective,
            terminal_soc=node.terminal_soc,
            final_soc_kwh=2.0,
        )
    )
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"
    assert value_of(snapshot.objective) == pytest.approx(0.0)
