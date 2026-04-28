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
from energy_assistant.ems.topology.nodes import Node, StorageNode
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


def test_storage_median_value_requires_price_series() -> None:
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
        stored_energy_value_per_kwh="median",
    )
    with pytest.raises(ValueError, match="requires price_import_raw"):
        _ = node.objective


class _ObjectiveFragment:
    def __init__(self, *, objective: pulp.LpAffineExpression) -> None:
        self._objective = objective

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return []

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return self._objective


def test_storage_terminal_value_uses_median_import_price() -> None:
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
        stored_energy_value_per_kwh="median",
        price_import_raw=[8.0, 50.0, 20.0],
    )
    graph = EnergyGraph()
    graph.add_element(_ObjectiveFragment(objective=node.objective))
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    # Median(8, 50, 20) = 20, objective is -20 * terminal_soc.
    assert value_of(snapshot.objective) == pytest.approx(-200.0)


def test_storage_terminal_value_constant_coefficient() -> None:
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
        stored_energy_value_per_kwh=15.0,
    )
    graph = EnergyGraph()
    graph.add_element(
        _ObjectiveFragment(
            objective=node.objective + (-1.0 * node.terminal_soc),
        )
    )
    snapshot = ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    # Coefficient is -15 from stored energy value and -1 from the forcing term,
    # so terminal_soc is maximized at 10 kWh.
    assert value_of(node.terminal_soc) == pytest.approx(10.0)
    assert value_of(snapshot.objective) == pytest.approx(-160.0)


def test_storage_has_no_terminal_soc_constraint() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("bus"), name="Bus", node_role="bus"))
    node = StorageNode(
        horizon=horizon,
        id=NodeId("bat"),
        name="Battery",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=5.0,
    )
    graph.add_element(node)
    graph.add_element(
        Connection(
            horizon=horizon,
            id="link",
            a_node_id=NodeId("bus"),
            b_node_id=NodeId("bat"),
            policies={
                "directional_limit": DirectionalLimit(max_a_to_b_kw=0.0, max_b_to_a_kw=0.0),
            },
        )
    )

    names = {constraint.name for constraint in node.constraints}
    assert "soc_terminal_bat" not in names
