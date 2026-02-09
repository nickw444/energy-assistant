from __future__ import annotations

from datetime import UTC, datetime

import pulp
import pytest

from energy_assistant.ems.components.ev import EvChargeControlModel, EvComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.milp.context import ModelContext, value_of
from energy_assistant.ems.system.inputs import EmsInputs
from energy_assistant.ems.system.system import ModelSnapshot
from energy_assistant.ems.topology.graph import EnergyGraphTemplate
from energy_assistant.lib.source_resolver.hass_source import (
    HomeAssistantBinarySensorEntitySource,
    HomeAssistantPercentageEntitySource,
    HomeAssistantPowerKwEntitySource,
)
from energy_assistant.models.loads import ControlledEvLoad


def test_ev_switch_penalty_prefers_staying_on_at_t0() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)

    inputs = EmsInputs(horizon=horizon)
    inputs.set_bool_series("grid_import_allowed", [True])
    inputs.set_float_series("grid_import_limit_kw", [10.0])
    inputs.set_float_series("grid_import_cost_per_kwh", [1.0])
    inputs.set_float_series("grid_export_cost_per_kwh", [0.0])
    inputs.set_float_series("grid_early_cost_per_kwh", [0.0])

    inputs.set_float_series("ev_gate:ev1", [1.0])
    inputs.set_bool("ev_connected:ev1", True)
    inputs.set_float("ev_realtime_power_kw:ev1", 1.0)
    inputs.set_float("ev_initial_soc_kwh:ev1", 0.0)

    ctx = ModelContext(horizon=horizon, inputs=inputs)

    graph = EnergyGraphTemplate()
    switchboard = SwitchboardComponent(graph=graph)
    _ = GridComponent(
        graph=graph,
        switchboard_bus_id=switchboard.bus_id,
        max_import_kw=10.0,
        max_export_kw=10.0,
    )

    ev_load = ControlledEvLoad(
        id="ev1",
        name="EV 1",
        load_type="controlled_ev",
        min_power_kw=0.0,
        max_power_kw=7.0,
        energy_kwh=1.0,
        connected=HomeAssistantBinarySensorEntitySource(
            type="home_assistant",
            entity="ev_connected",
        ),
        realtime_power=HomeAssistantPowerKwEntitySource(type="home_assistant", entity="ev_power"),
        state_of_charge_pct=HomeAssistantPercentageEntitySource(
            type="home_assistant",
            entity="ev_soc",
        ),
        soc_incentives=[],
        switch_penalty=10.0,
    )
    ev = EvComponent(
        graph=graph,
        switchboard_bus_id=switchboard.bus_id,
        load=ev_load,
        gate_series_key="ev_gate:ev1",
        connected_bool_key="ev_connected:ev1",
        realtime_power_kw_key="ev_realtime_power_kw:ev1",
        initial_soc_kwh_key="ev_initial_soc_kwh:ev1",
        grid_price_bias_pct=0.0,
    )

    snapshot = ModelSnapshot(ctx=ctx, graph=graph.bind(ctx))
    snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    assert pulp.LpStatus.get(snapshot.problem.status) == "Optimal"

    # With a high switch penalty and positive import cost, the cheapest choice is:
    # keep the EV "on" at t0 and charge only the minimum (0.1kW threshold).
    conn = snapshot.graph.connections[ev.connection_id]
    assert value_of(conn.P_a_to_b[0]) == pytest.approx(0.1, abs=1e-6)

    charge_control = None
    for frag in snapshot.graph.extra_fragments:
        if isinstance(frag, EvChargeControlModel):
            charge_control = frag
            break
    assert charge_control is not None
    assert value_of(charge_control.charge_on[0]) == pytest.approx(1.0)
    assert value_of(charge_control.switch[0]) == pytest.approx(0.0)
