from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.system.inputs import EmsInputs
from energy_assistant.ems.system.system import ModelSnapshot
from energy_assistant.ems.topology.connection import ConnectionTemplate
from energy_assistant.ems.topology.graph import EnergyGraphTemplate
from energy_assistant.ems.topology.link_components import DirectionalLimit, FixedFlowSeries
from energy_assistant.ems.topology.nodes import PortNodeTemplate, StorageNodeTemplate


def test_storage_soc_dynamics_applies_efficiency() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=2)

    inputs = EmsInputs(horizon=horizon)
    inputs.set_float("soc0", 2.0)
    inputs.set_float_series("charge_kw", [1.0, 0.0])
    inputs.set_float_series("discharge_kw", [0.0, 1.0])

    ctx = ModelContext(horizon=horizon, inputs=inputs)

    graph = EnergyGraphTemplate()
    graph.add_port(PortNodeTemplate(id="p", name="Port"))
    graph.add_storage(
        StorageNodeTemplate(
            id="bat",
            name="Battery",
            capacity_kwh=10.0,
            soc_min_kwh=0.0,
            soc_max_kwh=10.0,
            storage_efficiency=0.9,
            initial_soc_kwh_key="soc0",
        )
    )
    graph.add_connection(
        ConnectionTemplate(
            id="link",
            a_node_id="p",
            b_node_id="bat",
            link_components=[
                DirectionalLimit(max_a_to_b_kw=10.0, max_b_to_a_kw=10.0),
                FixedFlowSeries(direction="a_to_b", value_key="charge_kw", name="charge"),
                FixedFlowSeries(
                    direction="b_to_a",
                    value_key="discharge_kw",
                    name="discharge",
                ),
            ],
        )
    )

    snapshot = ModelSnapshot(ctx=ctx, graph=graph.bind(ctx))
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    node = snapshot.graph.storage_nodes["bat"]
    assert value_of(node.E_by_i[0]) == pytest.approx(2.0)
    assert value_of(node.E_by_i[1]) == pytest.approx(2.9)
    assert value_of(node.E_by_i[2]) == pytest.approx(1.789, abs=1e-3)

