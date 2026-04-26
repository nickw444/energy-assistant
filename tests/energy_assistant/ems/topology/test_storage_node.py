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


def test_storage_adaptive_mode_requires_price_series() -> None:
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
        terminal_mode="adaptive",
    )
    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("bus"), name="Bus", node_role="bus"))
    graph.add_element(node)
    graph.add_element(
        Connection(
            horizon=horizon,
            id="link",
            a_node_id=NodeId("bus"),
            b_node_id=NodeId("bat"),
            policies={"directional_limit": DirectionalLimit(max_a_to_b_kw=0.0, max_b_to_a_kw=0.0)},
        )
    )
    with pytest.raises(ValueError, match="requires price_import_raw"):
        _ = node.constraints


def test_storage_adaptive_penalty_supports_mean_and_median() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    median_node = StorageNode(
        horizon=horizon,
        id=NodeId("bat_median"),
        name="Battery",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=5.0,
        terminal_mode="adaptive",
        price_import_raw=[10.0, 30.0],
        terminal_penalty_per_kwh="median",
    )
    mean_node = StorageNode(
        horizon=horizon,
        id=NodeId("bat_mean"),
        name="Battery",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=5.0,
        terminal_mode="adaptive",
        price_import_raw=[10.0, 30.0],
        terminal_penalty_per_kwh="mean",
    )

    assert value_of(median_node.objective) == pytest.approx(value_of(mean_node.objective))


class _ObjectiveFragment:
    def __init__(self, *, objective: pulp.LpAffineExpression, shortfall: pulp.LpVariable) -> None:
        self._objective = objective
        self._shortfall = shortfall

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return [ConstraintSpec("force_shortfall", self._shortfall == 1.0)]

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return self._objective


def test_storage_adaptive_penalty_scales_with_horizon_ratio() -> None:
    short_horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ref_horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=1440).build(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    short = StorageNode(
        horizon=short_horizon,
        id=NodeId("short"),
        name="Short",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=5.0,
        terminal_mode="adaptive",
        price_import_raw=[20.0] * short_horizon.num_intervals,
        terminal_penalty_per_kwh="mean",
    )
    ref = StorageNode(
        horizon=ref_horizon,
        id=NodeId("ref"),
        name="Ref",
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        initial_soc_kwh=5.0,
        terminal_mode="adaptive",
        price_import_raw=[20.0] * ref_horizon.num_intervals,
        terminal_penalty_per_kwh="mean",
    )
    short_shortfall = short._adaptive_shortfall_var()  # pyright: ignore[reportPrivateUsage]
    ref_shortfall = ref._adaptive_shortfall_var()  # pyright: ignore[reportPrivateUsage]
    short_graph = EnergyGraph()
    short_graph.add_element(
        _ObjectiveFragment(objective=short.objective, shortfall=short_shortfall)
    )
    ref_graph = EnergyGraph()
    ref_graph.add_element(_ObjectiveFragment(objective=ref.objective, shortfall=ref_shortfall))

    short_snapshot = ModelSnapshot(ctx=ModelContext(horizon=short_horizon), graph=short_graph)
    ref_snapshot = ModelSnapshot(ctx=ModelContext(horizon=ref_horizon), graph=ref_graph)
    short_snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    ref_snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    short_value = value_of(short_snapshot.objective)
    ref_value = value_of(ref_snapshot.objective)
    assert short_value < ref_value
