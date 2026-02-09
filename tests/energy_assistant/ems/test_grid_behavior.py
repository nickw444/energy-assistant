from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.system.inputs import EmsInputs
from energy_assistant.ems.system.system import ModelSnapshot
from energy_assistant.ems.topology.graph import EnergyGraphTemplate


def test_grid_forbidden_import_uses_slack_violation() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    inputs = EmsInputs(horizon=horizon)
    inputs.set_float_series("base_load_kw", [5.0])
    inputs.set_bool_series("grid_import_allowed", [False])
    inputs.set_float_series("grid_import_limit_kw", [0.0])
    inputs.set_float_series("grid_import_cost_per_kwh", [0.0])
    inputs.set_float_series("grid_export_cost_per_kwh", [0.0])
    inputs.set_float_series("grid_early_cost_per_kwh", [0.0])

    ctx = ModelContext(horizon=horizon, inputs=inputs)

    graph = EnergyGraphTemplate()
    switchboard = SwitchboardComponent(graph=graph)
    _ = BaseLoadComponent(graph=graph, switchboard_bus_id=switchboard.bus_id)
    grid = GridComponent(
        graph=graph,
        switchboard_bus_id=switchboard.bus_id,
        max_import_kw=10.0,
        max_export_kw=10.0,
    )

    snapshot = ModelSnapshot(ctx=ctx, graph=graph.bind(ctx))
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    step = next(grid.iter_timestep_plan(snapshot))
    assert step.import_allowed is False
    import_kw = value_of(snapshot.graph.connections[grid.connection_id].P_b_to_a[0])
    assert import_kw == pytest.approx(5.0)
    assert step.import_violation_kw == pytest.approx(5.0)
