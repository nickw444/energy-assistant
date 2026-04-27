from __future__ import annotations

from datetime import datetime
from pathlib import Path

from energy_assistant.config import load_app_config
from energy_assistant.ems.fixtures.graph_viz import (
    write_logical_component_graph_svg,
    write_topology_graph_svg,
)
from energy_assistant.ems.horizon import HorizonFactory
from energy_assistant.ems.inputs.alignment import PowerForecastAligner, PriceForecastAligner
from energy_assistant.ems.inputs.application import EmsInputApplicator
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.planner import EmsMilpPlanner
from energy_assistant.ems.system.factory import EmsSystemFactory
from energy_assistant.inputs.fixtures import load_fixture_input_provider
from energy_assistant.models.config import AppConfig

FIXTURE_SCENARIO = Path("tests/fixtures/ems/nwhass/short-horizon-low-pv")


def _solve_fixture_snapshot() -> tuple[AppConfig, ModelSnapshot]:
    app_config = load_app_config(FIXTURE_SCENARIO / "config.yaml")
    input_provider, captured_at = load_fixture_input_provider(path=FIXTURE_SCENARIO / "input.json")
    now = datetime.fromisoformat(captured_at) if captured_at else None
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
        system=EmsSystemFactory.create().build(app_config),
    )
    run = planner.generate_ems_run(now=now)
    return app_config, run.snapshot


def test_write_logical_component_graph_svg(tmp_path: Path) -> None:
    app_config, _snapshot = _solve_fixture_snapshot()
    output = tmp_path / "logical-graph.svg"

    write_logical_component_graph_svg(app_config, output)

    content = output.read_text()
    assert content.startswith("<?xml")
    assert "switchboard" in content


def test_write_topology_graph_svg(tmp_path: Path) -> None:
    _app_config, snapshot = _solve_fixture_snapshot()
    output = tmp_path / "topology-graph.svg"

    write_topology_graph_svg(snapshot, output)

    content = output.read_text()
    assert content.startswith("<?xml")
    assert "passthrough" in content or "[" in content
