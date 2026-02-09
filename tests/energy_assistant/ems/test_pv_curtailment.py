from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.pv import PvComponent
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.milp.context import ModelContext
from energy_assistant.ems.system.inputs import EmsInputs
from energy_assistant.ems.system.system import ModelSnapshot
from energy_assistant.ems.topology.graph import EnergyGraphTemplate


@pytest.mark.parametrize(
    ("mode", "expected_pv_kw", "expected_curtail_kw"),
    [
        ("load-aware", 2.0, 3.0),
        ("binary", 0.0, 5.0),
    ],
)
def test_pv_curtailment_modes(mode: str, expected_pv_kw: float, expected_curtail_kw: float) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    inputs = EmsInputs(horizon=horizon)
    inputs.set_float_series("base_load_kw", [2.0])
    inputs.set_float_series("pv_available", [5.0])
    inputs.set_bool_series("grid_import_allowed", [True])
    inputs.set_float_series("grid_import_limit_kw", [10.0])
    inputs.set_float_series("grid_import_cost_per_kwh", [1.0])
    inputs.set_float_series("grid_export_cost_per_kwh", [10.0])
    inputs.set_float_series("grid_early_cost_per_kwh", [0.0])

    ctx = ModelContext(horizon=horizon, inputs=inputs)

    graph = EnergyGraphTemplate()
    switchboard = SwitchboardComponent(graph=graph, bus_id="bus")
    _ = BaseLoadComponent(graph=graph, switchboard_bus_id=switchboard.bus_id)
    _ = GridComponent(
        graph=graph,
        switchboard_bus_id=switchboard.bus_id,
        max_import_kw=10.0,
        max_export_kw=10.0,
    )
    pv = PvComponent(
        graph=graph,
        inverter_id="inv",
        dc_bus_id=switchboard.bus_id,
        peak_power_kw=10.0,
        curtailment=mode,  # type: ignore[arg-type]
        available_series_key="pv_available",
    )

    snapshot = ModelSnapshot(ctx=ctx, graph=graph.bind(ctx))
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    pv_kw = pv.pv_kw(snapshot, 0)
    curtail_kw = pv.curtail_kw(snapshot, 0)

    assert pv_kw == pytest.approx(expected_pv_kw)
    assert curtail_kw == pytest.approx(expected_curtail_kw)

