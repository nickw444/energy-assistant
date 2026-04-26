from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from energy_assistant.config import load_app_config
from energy_assistant.ems.components.battery import BatteryComponent, BatteryExportReservePolicy
from energy_assistant.ems.components.context import GraphBuildContext
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.grid.price_bindings import PriceBindingApplicator
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.components.lib.time_windows import TimeWindowMatcher
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import HorizonFactory
from energy_assistant.ems.inputs.alignment import PowerForecastAligner, PriceForecastAligner
from energy_assistant.ems.inputs.application import EmsInputApplicator
from energy_assistant.ems.inputs.models import AppliedForecastInput, AppliedInputRegistry
from energy_assistant.ems.models import (
    BatteryComponentPlan,
    InverterComponentPlan,
    PvComponentPlan,
)
from energy_assistant.ems.planner import EmsMilpPlanner
from energy_assistant.ems.system.factory import EmsSystemFactory
from energy_assistant.ems.system.state import SolveStateStore
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.policies import DirectionalLimit
from energy_assistant.inputs.fixtures import load_fixture_input_provider
from energy_assistant.inputs.registry import ResolvedScalarInput
from energy_assistant.models.config import AppConfig
from energy_assistant.models.inputs import InputValueKind
from energy_assistant.models.plant import (
    BatteryComponentConfig,
    GridComponentConfig,
    GridConstraintsConfig,
    InputReference,
    InverterComponentConfig,
    PriceBindingConfig,
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


def _grid_config(*, name: str, connection: str, max_export_kw: float) -> GridComponentConfig:
    return GridComponentConfig(
        type="grid",
        connection=connection,
        constraints=GridConstraintsConfig(max_import_kw=10.0, max_export_kw=max_export_kw),
        price_import=PriceBindingConfig(source=InputReference(source=f"{name}_import")),
        price_export=PriceBindingConfig(source=InputReference(source=f"{name}_export")),
    )


def test_app_config_allows_multiple_pvs_and_batteries_per_inverter() -> None:
    app_config = _multi_attachment_app_config()
    pv_secondary = app_config.plant["pv_secondary"]
    battery_secondary = app_config.plant["battery_secondary"]

    assert isinstance(pv_secondary, PvComponentConfig)
    assert isinstance(battery_secondary, BatteryComponentConfig)
    assert pv_secondary.connection == "primary"
    assert battery_secondary.connection == "primary"


def test_factory_keeps_all_inverter_connections() -> None:
    app_config = _multi_attachment_app_config()

    system = EmsSystemFactory.create().build(app_config)
    switchboard = system.components["switchboard"]
    inverter = system.components["primary"]
    grid = system.components["grid"]
    pv_primary = system.components["pv_primary"]
    pv_secondary = system.components["pv_secondary"]
    battery_primary = system.components["battery_primary"]
    battery_secondary = system.components["battery_secondary"]
    tessie = system.components["tessie"]

    assert not hasattr(inverter, "pvs")
    assert not hasattr(inverter, "batteries")
    assert inverter.switchboard is switchboard
    assert grid.switchboard is switchboard
    assert pv_primary.inverter is inverter
    assert pv_secondary.inverter is inverter
    assert battery_primary.inverter is inverter
    assert battery_secondary.inverter is inverter
    assert tessie.switchboard is switchboard


def test_factory_supports_multiple_grids_on_one_switchboard() -> None:
    app_config = _multi_attachment_app_config(include_secondary_grid=True)
    system = EmsSystemFactory.create().build(app_config)
    assert tuple(component.id for component in system.ordered_components) == tuple(
        app_config.plant.keys()
    )
    assert system.components["grid_secondary"].switchboard is system.components["switchboard"]
    assert system.components["grid"].switchboard is system.components["switchboard"]
    assert system.components["primary"].switchboard is system.components["switchboard"]

    input_provider, captured_at = load_fixture_input_provider(path=FIXTURE_DIR / "input.json")
    now = datetime.fromisoformat(captured_at) if captured_at else None
    system = EmsSystemFactory.create().build(app_config)
    planner = EmsMilpPlanner(
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
        system=system,
    )
    plan = planner.generate_ems_run(now=now).plan

    assert "grid_secondary" in plan.components
    assert "primary" in plan.components
    assert plan.components["grid_secondary"].type == "grid"


def test_battery_component_omits_directional_limit_when_limits_are_unset() -> None:
    battery_config = BatteryComponentConfig(
        type="battery",
        connection="inverter",
        name="Battery",
        capacity_kwh=10.0,
        storage_efficiency_pct=95.0,
        min_soc_pct=10.0,
        max_soc_pct=95.0,
        reserve_soc_pct=20.0,
        max_charge_kw=None,
        max_discharge_kw=None,
        state_of_charge_pct=InputReference(source="battery_soc"),
        realtime_power=InputReference(source="battery_power"),
    )
    switchboard = SwitchboardComponent(component_id="switchboard")
    inverter = InverterComponent(
        component_id="inverter",
        switchboard=switchboard,
        inverter=InverterComponentConfig(
            type="inverter",
            connection="switchboard",
            name="Inverter",
            peak_power_kw=13.0,
            curtailment=None,
        ),
    )
    component = BatteryComponent(
        component_id="battery",
        inverter=inverter,
        battery=battery_config,
        grid_max_export_kw=13.0,
    )
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    )
    build_ctx = GraphBuildContext(
        components={"switchboard": switchboard, "inverter": inverter, component.id: component},
        solve_states=SolveStateStore(),
    )

    elements, _ = component.create_graph_elements(
        horizon=horizon,
        inputs=AppliedInputRegistry(
            scalars={
                "battery_soc": ResolvedScalarInput(
                    key="battery_soc",
                    kind=InputValueKind.PERCENTAGE,
                    value=50.0,
                )
            }
        ),
        build_ctx=build_ctx,
    )
    connection = next(element for element in elements if isinstance(element, Connection))

    assert connection.find_policy("directional_limit", DirectionalLimit) is None
    assert battery_config.max_charge_kw is None
    assert battery_config.max_discharge_kw is None
    assert connection.a_node_id == inverter.dc_bus_id
    assert connection.b_node_id == component.node_id


def test_battery_reserve_fragment_uses_only_same_switchboard_grids() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    )
    switchboard = SwitchboardComponent(component_id="switchboard")
    remote_switchboard = SwitchboardComponent(component_id="remote_switchboard")
    inverter = InverterComponent(
        component_id="inverter",
        switchboard=switchboard,
        inverter=InverterComponentConfig(
            type="inverter",
            connection="switchboard",
            name="Inverter",
            peak_power_kw=13.0,
            curtailment=None,
        ),
    )
    battery_config = BatteryComponentConfig(
        type="battery",
        connection="inverter",
        name="Battery",
        capacity_kwh=10.0,
        storage_efficiency_pct=95.0,
        min_soc_pct=10.0,
        max_soc_pct=95.0,
        reserve_soc_pct=20.0,
        max_charge_kw=None,
        max_discharge_kw=None,
        state_of_charge_pct=InputReference(source="battery_soc"),
        realtime_power=InputReference(source="battery_power"),
    )
    battery = BatteryComponent(
        component_id="battery",
        inverter=inverter,
        battery=battery_config,
        grid_max_export_kw=13.0,
    )
    same_grid = GridComponent(
        component_id="grid",
        switchboard=switchboard,
        grid=_grid_config(name="grid", connection="switchboard", max_export_kw=10.0),
        time_window_matcher=TimeWindowMatcher(),
        price_binding_applicator=PriceBindingApplicator(),
    )
    remote_grid = GridComponent(
        component_id="remote_grid",
        switchboard=remote_switchboard,
        grid=_grid_config(
            name="remote_grid",
            connection="remote_switchboard",
            max_export_kw=7.0,
        ),
        time_window_matcher=TimeWindowMatcher(),
        price_binding_applicator=PriceBindingApplicator(),
    )
    solve_states = SolveStateStore()
    build_ctx = GraphBuildContext(
        components={
            "switchboard": switchboard,
            "remote_switchboard": remote_switchboard,
            "inverter": inverter,
            "grid": same_grid,
            "remote_grid": remote_grid,
            "battery": battery,
        },
        solve_states=solve_states,
    )

    inputs = AppliedInputRegistry(
        forecasts={
            "grid_import": AppliedForecastInput(
                key="grid_import",
                kind=InputValueKind.PRICE,
                series=[0.20, 0.21],
            ),
            "grid_export": AppliedForecastInput(
                key="grid_export",
                kind=InputValueKind.PRICE,
                series=[0.05, 0.06],
            ),
            "remote_grid_import": AppliedForecastInput(
                key="remote_grid_import",
                kind=InputValueKind.PRICE,
                series=[0.30, 0.31],
            ),
            "remote_grid_export": AppliedForecastInput(
                key="remote_grid_export",
                kind=InputValueKind.PRICE,
                series=[0.08, 0.09],
            ),
        },
        scalars={
            "battery_soc": ResolvedScalarInput(
                key="battery_soc",
                kind=InputValueKind.PERCENTAGE,
                value=50.0,
            ),
            "battery_power": ResolvedScalarInput(
                key="battery_power",
                kind=InputValueKind.POWER,
                value=0.0,
            ),
        },
    )

    same_grid_elements, same_grid_state = same_grid.create_graph_elements(
        horizon=horizon,
        inputs=inputs,
        build_ctx=build_ctx,
    )
    solve_states.put(same_grid, same_grid_state)
    build_ctx.register(same_grid.id, same_grid_elements)

    remote_grid_elements, remote_grid_state = remote_grid.create_graph_elements(
        horizon=horizon,
        inputs=inputs,
        build_ctx=build_ctx,
    )
    solve_states.put(remote_grid, remote_grid_state)
    build_ctx.register(remote_grid.id, remote_grid_elements)

    battery_elements, battery_state = battery.create_graph_elements(
        horizon=horizon,
        inputs=inputs,
        build_ctx=build_ctx,
    )
    solve_states.put(battery, battery_state)
    build_ctx.register(battery.id, battery_elements)

    fragments = battery.create_graph_fragments(
        graph=EnergyGraph(),
        build_ctx=build_ctx,
        solve_states=solve_states,
    )

    assert len(fragments) == 1
    fragment = fragments[0]
    assert isinstance(fragment, BatteryExportReservePolicy)
    constraint_names = {constraint.name for constraint in fragment.constraints}
    assert any(same_grid_state.connection.id in name for name in constraint_names)
    assert all(remote_grid_state.connection.id not in name for name in constraint_names)


def test_plan_exports_all_inverter_connections_from_fixture_inputs() -> None:
    app_config = _multi_attachment_app_config()
    input_provider, captured_at = load_fixture_input_provider(path=FIXTURE_DIR / "input.json")
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
        "pv_secondary",
        "battery_primary",
        "battery_secondary",
        "tessie",
    }

    inverter_plan = plan.components["primary"]
    assert isinstance(inverter_plan, InverterComponentPlan)
    assert "intent" not in inverter_plan.model_dump()

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
