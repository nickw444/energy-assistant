from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.horizon import Horizon, HorizonFactory
from energy_assistant.ems.milp.context import ConstraintSpec, ModelContext
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import DirectionalLimit


class _DuplicateConstraintFragment:
    @property
    def constraints(self) -> list[ConstraintSpec]:
        variable = pulp.LpVariable("x")
        return [
            ConstraintSpec("dup_name", variable >= 0),
            ConstraintSpec("dup_name", variable <= 1),
        ]

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return pulp.LpAffineExpression()


def _horizon() -> Horizon:
    return HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    )


def test_graph_rejects_duplicate_node_ids() -> None:
    horizon = _horizon()
    graph = EnergyGraph()

    graph.add_element(Node(horizon=horizon, id=NodeId("bus"), name="Bus", node_role="bus"))
    with pytest.raises(ValueError, match="Duplicate node id"):
        graph.add_element(Node(horizon=horizon, id=NodeId("bus"), name="Bus2", node_role="bus"))


def test_graph_rejects_unknown_connection_endpoint() -> None:
    horizon = _horizon()
    graph = EnergyGraph()
    graph.add_element(Node(horizon=horizon, id=NodeId("a"), name="A", node_role="bus"))

    with pytest.raises(ValueError, match="Unknown node id"):
        graph.add_element(
            Connection(
                horizon=horizon,
                id="a_b",
                a_node_id=NodeId("a"),
                b_node_id=NodeId("missing"),
                policies={"limit": DirectionalLimit(max_a_to_b_kw=1.0, max_b_to_a_kw=1.0)},
            )
        )


def test_snapshot_rejects_duplicate_constraint_names() -> None:
    horizon = _horizon()
    graph = EnergyGraph()
    graph.add_element(_DuplicateConstraintFragment())

    with pytest.raises(ValueError, match="Duplicate constraint name"):
        ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph)
