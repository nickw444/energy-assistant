from __future__ import annotations

from datetime import datetime
from pathlib import Path

from energy_assistant.config import load_app_config
from energy_assistant.ems.inputs.alignment import PowerForecastAligner, PriceForecastAligner
from energy_assistant.ems.inputs.application import EmsInputApplicator
from energy_assistant.ems.models import (
    BatteryComponentPlan,
    GridComponentPlan,
    InverterComponentPlan,
    LoadControlledEvComponentPlan,
    PvComponentPlan,
)
from energy_assistant.ems.planner import EmsMilpPlanner
from energy_assistant.ems.planning.horizon import HorizonFactory
from energy_assistant.ems.system.factory import EmsSystemFactory
from energy_assistant.inputs.fixtures import load_fixture_input_provider


def test_plan_exports_flat_component_series_from_fixture() -> None:
    fixture_dir = Path("tests/fixtures/ems/nwhass/short-horizon-low-pv")
    config_path = fixture_dir / "config.yaml"
    fixture_path = fixture_dir / "input.json"

    app_config = load_app_config(config_path)
    input_provider, captured_at = load_fixture_input_provider(path=fixture_path)
    now = datetime.fromisoformat(captured_at) if captured_at else None

    plan = EmsMilpPlanner(
        input_provider=input_provider,
        horizon_factory=HorizonFactory(
            timestep_minutes=app_config.ems.timestep_minutes,
            horizon_minutes=app_config.ems.horizon_minutes,
            high_res_timestep_minutes=app_config.ems.high_res_timestep_minutes,
            high_res_horizon_minutes=app_config.ems.high_res_horizon_minutes,
        ),
        input_applicator=EmsInputApplicator(
            input_configs=app_config.inputs,
            power_aligner=PowerForecastAligner(),
            price_aligner=PriceForecastAligner(),
        ),
        system=EmsSystemFactory.create().build(app_config),
    ).generate_ems_run(now=now).plan

    assert set(plan.components) == {
        "switchboard",
        "grid",
        "base_load",
        "primary",
        "pv_primary",
        "battery_primary",
        "tessie",
    }
    switchboard = plan.components["switchboard"]
    assert switchboard.type == "switchboard"
    assert switchboard.model_dump() == {"type": "switchboard"}

    inverter_plan = plan.components["primary"]
    assert isinstance(inverter_plan, InverterComponentPlan)
    assert inverter_plan.type == "inverter"
    assert "intent" not in inverter_plan.model_dump()

    grid_plan = plan.components["grid"]
    assert isinstance(grid_plan, GridComponentPlan)
    assert grid_plan.type == "grid"

    timestep_count = len(grid_plan.price_import_raw)
    battery_plan = plan.components["battery_primary"]
    assert isinstance(battery_plan, BatteryComponentPlan)
    horizon_start = battery_plan.soc_kwh[0].time
    first_step_start = grid_plan.price_import_raw[0].time

    assert len(grid_plan.price_import_raw) == timestep_count
    assert grid_plan.price_import_raw[0].time == first_step_start
    assert isinstance(grid_plan.import_allowed[0].value, bool)

    assert battery_plan.type == "battery"
    assert "intent" not in battery_plan.model_dump()
    assert len(battery_plan.soc_kwh) == timestep_count + 1
    assert battery_plan.soc_kwh[0].time == horizon_start
    assert battery_plan.soc_kwh[1].time > battery_plan.soc_kwh[0].time

    pv_plan = plan.components["pv_primary"]
    assert isinstance(pv_plan, PvComponentPlan)
    assert pv_plan.type == "pv"
    assert "intent" not in pv_plan.model_dump()
    assert len(pv_plan.available_kw) == timestep_count

    ev_plan = plan.components["tessie"]
    assert isinstance(ev_plan, LoadControlledEvComponentPlan)
    assert ev_plan.type == "load_controlled_ev"
    assert "intent" not in ev_plan.model_dump()
    assert len(ev_plan.soc_pct) == timestep_count + 1
