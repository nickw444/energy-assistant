from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from energy_assistant.config import load_app_config
from energy_assistant.ems.components.battery import BatteryComponent
from energy_assistant.ems.components.inverter import (
    BatteryIntentSummary,
    aggregate_battery_for_intent,
)
from energy_assistant.ems.models import (
    BatteryComponentPlan,
    InverterComponentPlan,
    PvComponentPlan,
)
from energy_assistant.ems.planner import EmsMilpPlanner
from energy_assistant.ems.system.factory import EmsSystemFactory
from energy_assistant.inputs.fixtures import load_fixture_input_provider
from energy_assistant.models.config import AppConfig
from energy_assistant.models.plant import (
    BatteryComponentConfig,
    InputReference,
    PvComponentConfig,
)

FIXTURE_DIR = Path("tests/fixtures/ems/nwhass/short-horizon-low-pv")


def _multi_attachment_app_config(*, include_secondary_grid: bool = False) -> AppConfig:
    payload = yaml.safe_load((FIXTURE_DIR / "config.yaml").read_text())
    if not isinstance(payload, dict):
        raise AssertionError("Fixture config must be a mapping")

    payload_dict = cast(dict[str, Any], payload)
    plant_obj = payload_dict["plant"]
    if not isinstance(plant_obj, dict):
        raise AssertionError("Fixture plant must be a mapping")
    plant = cast(dict[str, Any], plant_obj)
    plant["pv_secondary"] = {
        **plant["pv_primary"],
        "name": "PV secondary",
    }
    plant["battery_secondary"] = {
        **plant["battery_primary"],
        "name": "Battery Secondary",
        "capacity_kwh": 10.0,
        "reserve_soc_pct": 40.0,
        "max_charge_kw": 4.0,
        "max_discharge_kw": 4.0,
    }
    if include_secondary_grid:
        plant["grid_secondary"] = dict(plant["grid"])
    return AppConfig.model_validate(payload_dict)


def test_app_config_allows_multiple_pvs_and_batteries_per_inverter() -> None:
    app_config = _multi_attachment_app_config()
    pv_secondary = app_config.plant["pv_secondary"]
    battery_secondary = app_config.plant["battery_secondary"]

    assert isinstance(pv_secondary, PvComponentConfig)
    assert isinstance(battery_secondary, BatteryComponentConfig)
    assert pv_secondary.connection == "primary"
    assert battery_secondary.connection == "primary"


def test_factory_keeps_all_inverter_children() -> None:
    app_config = _multi_attachment_app_config()

    system = EmsSystemFactory.create(app_config).system
    inverter = system.inverters["primary"]

    assert not hasattr(inverter, "pvs")
    assert not hasattr(inverter, "batteries")
    assert set(system.topology.children_of("primary")) == {
        "pv_primary",
        "pv_secondary",
        "battery_primary",
        "battery_secondary",
    }


def test_factory_supports_multiple_grids_on_one_switchboard() -> None:
    app_config = _multi_attachment_app_config(include_secondary_grid=True)
    system = EmsSystemFactory.create(app_config).system

    assert set(system.topology.children_of("switchboard")) >= {
        "grid",
        "grid_secondary",
        "base_load",
        "primary",
    }
    assert set(system.topology.component_ids_of_type("grid")) == {
        "grid",
        "grid_secondary",
    }

    input_provider, captured_at = load_fixture_input_provider(path=FIXTURE_DIR / "input.json")
    now = datetime.fromisoformat(captured_at) if captured_at else None
    plan = EmsMilpPlanner(
        input_provider=input_provider,
        system_factory=EmsSystemFactory.create(app_config),
    ).generate_ems_plan(now=now)

    assert "grid_secondary" in plan.components
    assert "primary" in plan.components
    assert plan.components["grid_secondary"].type == "grid"


def test_aggregate_battery_for_intent_uses_weighted_soc_and_actual_limits() -> None:
    battery_a = BatteryComponentConfig(
        type="battery",
        connection="primary",
        name="Battery A",
        capacity_kwh=10.0,
        storage_efficiency_pct=95.0,
        min_soc_pct=10.0,
        max_soc_pct=95.0,
        reserve_soc_pct=20.0,
        max_charge_kw=None,
        max_discharge_kw=None,
        state_of_charge_pct=InputReference(source="battery_a_soc"),
        realtime_power=InputReference(source="battery_a_power"),
    )
    battery_b = BatteryComponentConfig(
        type="battery",
        connection="primary",
        name="Battery B",
        capacity_kwh=5.0,
        storage_efficiency_pct=95.0,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        reserve_soc_pct=40.0,
        max_charge_kw=3.0,
        max_discharge_kw=4.0,
        state_of_charge_pct=InputReference(source="battery_b_soc"),
        realtime_power=InputReference(source="battery_b_power"),
    )

    batteries = [
        BatteryComponent(
            component_id="battery_a",
            inverter_id="primary",
            dc_bus_id="primary_dc",
            inverter_peak_kw=7.0,
            battery=battery_a,
            grid_max_export_kw=13.0,
        ),
        BatteryComponent(
            component_id="battery_b",
            inverter_id="primary",
            dc_bus_id="primary_dc",
            inverter_peak_kw=7.0,
            battery=battery_b,
            grid_max_export_kw=13.0,
        ),
    ]

    aggregate = aggregate_battery_for_intent(
        [
            BatteryIntentSummary(
                connection=battery.battery_config.connection,
                name=battery.name,
                capacity_kwh=float(battery.capacity_kwh),
                reserve_kwh=float(battery.reserve_kwh),
                max_charge_kw=float(battery.max_charge_kw),
                max_discharge_kw=float(battery.max_discharge_kw),
                max_soc_pct=float(battery.battery_config.max_soc_pct),
            )
            for battery in batteries
        ]
    )

    assert aggregate is not None
    assert aggregate.capacity_kwh == pytest.approx(15.0)
    assert aggregate.max_charge_kw == pytest.approx(10.0)
    assert aggregate.max_discharge_kw == pytest.approx(11.0)
    assert aggregate.max_soc_pct == pytest.approx(95.0)
    assert aggregate.reserve_soc_pct == pytest.approx((4.0 / 15.0) * 100.0)


def test_plan_exports_all_inverter_children_from_fixture_inputs() -> None:
    app_config = _multi_attachment_app_config()
    input_provider, captured_at = load_fixture_input_provider(path=FIXTURE_DIR / "input.json")
    now = datetime.fromisoformat(captured_at) if captured_at else None

    plan = EmsMilpPlanner(
        input_provider=input_provider,
        system_factory=EmsSystemFactory.create(app_config),
    ).generate_ems_plan(now=now)

    assert set(plan.components) == {
        "switchboard",
        "grid",
        "base_load",
        "primary",
        "pv_primary",
        "pv_secondary",
        "battery_primary",
        "battery_secondary",
        "tessie",
    }

    inverter_plan = plan.components["primary"]
    assert isinstance(inverter_plan, InverterComponentPlan)
    assert inverter_plan.intent.mode is not None

    pv_primary = plan.components["pv_primary"]
    pv_secondary = plan.components["pv_secondary"]
    assert isinstance(pv_primary, PvComponentPlan)
    assert isinstance(pv_secondary, PvComponentPlan)
    assert len(pv_primary.available_kw) == len(pv_secondary.available_kw)

    battery_primary = plan.components["battery_primary"]
    battery_secondary = plan.components["battery_secondary"]
    assert isinstance(battery_primary, BatteryComponentPlan)
    assert isinstance(battery_secondary, BatteryComponentPlan)
    assert len(battery_primary.soc_kwh) == len(battery_secondary.soc_kwh)


def test_existing_fixture_config_still_loads() -> None:
    app_config = load_app_config(FIXTURE_DIR / "config.yaml")

    assert "primary" in app_config.plant
